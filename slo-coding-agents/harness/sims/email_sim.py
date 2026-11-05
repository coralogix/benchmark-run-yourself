"""Transactional email provider simulator.

API   :8092+off  POST /v1/messages {to, template, data}: 202 {message_id}, GET /healthz
Admin :9092+off  (header X-Admin-Token == $LARDER_ADMIN_TOKEN)
                 GET /admin/stats, GET|POST /admin/mode {latency_p50_ms, latency_p99_ms, failure_rate}
                 ({"preset": "normal"} restores defaults), POST /admin/reset {keep_mode?},
                 GET /admin/messages?to=<addr>&template=<t>&limit=N

The request is held for the sampled latency before the 202 (that is what makes a slow
provider bite synchronous callers). Injected failures return 503 {"error": "provider_unavailable"}.
Stats are dumped to <artifacts>/email_stats.json.

Usage: python -m harness.sims.email_sim [--port-offset N] [--artifacts DIR] [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections import Counter, deque

from aiohttp import web

from harness.runtime.util import iso_now, port
from harness.sims.common import Dumper, LatencySampler, admin_middleware, read_json, serve, sim_seed

log = logging.getLogger("email-sim")

DEFAULT_MODE = {"latency_p50_ms": 80.0, "latency_p99_ms": 400.0, "failure_rate": 0.0}
MAX_STORED = 50_000
MAX_DATA_BYTES = 16 * 1024


def _has(data: dict, *keys: str) -> bool:
    return any(data.get(k) not in (None, "", [], {}) for k in keys)


class EmailState:
    def __init__(self, seed: int, sleep=asyncio.sleep):
        self.seed = seed
        self.sleep = sleep
        self.mode = dict(DEFAULT_MODE)
        self.reset()

    def reset(self) -> None:
        self.sampler = LatencySampler(f"email-{self.seed}")
        self.n = 0
        self.counters: Counter = Counter()
        self.by_template: Counter = Counter()
        self.failures_by_template: Counter = Counter()
        self.with_items: Counter = Counter()
        self.with_slot: Counter = Counter()
        self.seen: Counter = Counter()  # (template, order_ref) -> accepted messages
        self.recipients: set[str] = set()
        self.messages: deque = deque(maxlen=MAX_STORED)
        self.latency_ms_sum = 0.0
        self.reset_at = iso_now()

    async def send(self, body) -> tuple[int, dict]:
        self.counters["received"] += 1
        if not isinstance(body, dict):
            self.counters["rejected"] += 1
            return 400, {"error": "invalid_request"}
        to, template, data = body.get("to"), body.get("template"), body.get("data", {})
        if not isinstance(to, str) or "@" not in to or len(to) > 320:
            self.counters["rejected"] += 1
            return 400, {"error": "invalid_recipient"}
        if not isinstance(template, str) or not template or len(template) > 80:
            self.counters["rejected"] += 1
            return 400, {"error": "invalid_template"}
        if data is None:
            data = {}
        if not isinstance(data, dict):
            self.counters["rejected"] += 1
            return 400, {"error": "invalid_data"}
        # draw order is fixed (failure, latency) so the stream is deterministic per seed
        fail = self.sampler.chance(float(self.mode["failure_rate"]))
        lat = self.sampler.sample_ms(float(self.mode["latency_p50_ms"]), float(self.mode["latency_p99_ms"]))
        if lat > 0:
            await self.sleep(lat / 1000.0)
        self.latency_ms_sum += lat
        if fail:
            self.counters["failures_injected"] += 1
            self.failures_by_template[template] += 1
            return 503, {"error": "provider_unavailable"}

        self.n += 1
        mid = f"msg_{self.n:08d}"
        self.counters["accepted"] += 1
        self.by_template[template] += 1
        if _has(data, "items", "lines"):
            self.with_items[template] += 1
        if _has(data, "slot", "slot_id", "delivery_slot", "slot_window", "window"):
            self.with_slot[template] += 1
        ref = data.get("order_ref")
        if ref is not None:
            key = (template, str(ref))
            self.seen[key] += 1
            if self.seen[key] > 1:
                self.counters["duplicates"] += 1
        self.recipients.add(to)
        try:
            raw = json.dumps(data, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            raw = "{}"
        stored = data if len(raw) <= MAX_DATA_BYTES else {"_truncated": True, "order_ref": ref}
        self.messages.append(
            {"message_id": mid, "ts": iso_now(), "to": to, "template": template, "data": stored, "latency_ms": round(lat, 1)}
        )
        return 202, {"message_id": mid}

    def stats(self) -> dict:
        c = self.counters
        attempts = c["accepted"] + c["failures_injected"]
        return {
            "received_total": c["received"],
            "messages_total": c["accepted"],
            "messages_by_template": dict(self.by_template),
            "messages_with_items_by_template": dict(self.with_items),
            "messages_with_slot_by_template": dict(self.with_slot),
            "failures_injected": c["failures_injected"],
            "failures_by_template": dict(self.failures_by_template),
            "rejected": c["rejected"],
            "duplicate_messages": c["duplicates"],
            "unique_recipients": len(self.recipients),
            "mean_latency_ms": round(self.latency_ms_sum / attempts, 1) if attempts else 0.0,
            "reset_at": self.reset_at,
        }

    def snapshot(self) -> dict:
        return {"mode": dict(self.mode), "stats": self.stats(), "recent": list(self.messages)[-100:]}


def build_apps(state: EmailState, dumper: Dumper | None = None) -> tuple[web.Application, web.Application]:
    api = web.Application()

    async def messages(request: web.Request):
        body, _ = await read_json(request)
        status, payload = await state.send(body)
        return web.json_response(payload, status=status)

    async def healthz(_r):
        return web.json_response({"ok": True})

    api.router.add_post("/v1/messages", messages)
    api.router.add_get("/healthz", healthz)

    admin = web.Application(middlewares=[admin_middleware()])

    async def a_stats(_r):
        return web.json_response(state.stats())

    async def a_get_mode(_r):
        return web.json_response(state.mode)

    async def a_mode(request: web.Request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid_request"}, status=400)
        if body.get("preset") == "normal":
            state.mode = dict(DEFAULT_MODE)
        for k in DEFAULT_MODE:
            if k in body:
                try:
                    state.mode[k] = float(body[k])
                except (TypeError, ValueError):
                    return web.json_response({"error": f"bad value for {k}"}, status=400)
        log.info("mode -> %s", state.mode)
        return web.json_response(state.mode)

    async def a_reset(request: web.Request):
        try:
            body = await request.json() if request.body_exists else {}
        except Exception:
            body = {}
        if dumper:
            dumper.archive_before_reset()
        state.reset()
        if not (isinstance(body, dict) and body.get("keep_mode")):
            state.mode = dict(DEFAULT_MODE)
        if dumper:
            dumper.dump()
        return web.json_response({"ok": True, "mode": state.mode})

    async def a_messages(request: web.Request):
        to = request.query.get("to")
        tpl = request.query.get("template")
        try:
            n = max(1, min(int(request.query.get("limit", "100")), MAX_STORED))
        except ValueError:
            n = 100
        sel = [m for m in state.messages if (to is None or m["to"] == to) and (tpl is None or m["template"] == tpl)]
        return web.json_response({"messages": sel[-n:], "total": len(sel)})

    admin.router.add_get("/admin/stats", a_stats)
    admin.router.add_get("/admin/mode", a_get_mode)
    admin.router.add_post("/admin/mode", a_mode)
    admin.router.add_post("/admin/reset", a_reset)
    admin.router.add_get("/admin/messages", a_messages)
    return api, admin


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--artifacts", default=None)
    ap.add_argument("--seed", type=int, default=sim_seed(4244))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s email-sim %(levelname)s %(message)s")
    state = EmailState(args.seed)
    dumper = Dumper("email_stats.json", state.snapshot, args.artifacts)
    api, admin = build_apps(state, dumper)
    asyncio.run(
        serve(api, admin, port("email", args.port_offset), port("email_admin", args.port_offset), dumper, "email-sim")
    )


if __name__ == "__main__":
    main()
