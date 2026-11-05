"""Warehouse partner webhook receiver simulator.

API   :8091+off  POST /hooks (JSON with `event`), GET /healthz
Admin :9091+off  (header X-Admin-Token == $LARDER_ADMIN_TOKEN)
                 GET /admin/stats, GET|POST /admin/mode {failure_rate, latency_ms}, POST /admin/reset,
                 GET /admin/deliveries?limit=N (most recent accepted deliveries)

Signature: header `X-Larder-Signature: sha256=<hex hmac-sha256(raw body, $LARDER_PARTNER_SECRET)>`.

Usage: python -m harness.sims.partner_sim [--port-offset N] [--artifacts DIR] [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections import Counter, deque

from aiohttp import web

from harness.runtime.util import iso_now, port
from harness.sims.common import Dumper, LatencySampler, admin_middleware, serve, sim_seed

log = logging.getLogger("partner-sim")

DEFAULT_SECRET = "whsec_larder_partner_2019"
DEFAULT_MODE = {"failure_rate": 0.0, "latency_ms": 40.0}


def sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def verify_signature(raw: bytes, header: str | None, secret: str) -> str:
    """Return 'unsigned' | 'valid' | 'invalid'."""
    if header is None or header == "":
        return "unsigned"
    h = header.strip()
    if not h.startswith("sha256="):
        return "invalid"
    expected = sign(raw, secret)
    return "valid" if hmac.compare_digest(expected.lower(), h.lower()) else "invalid"


class PartnerState:
    def __init__(self, seed: int, secret: str, sleep=asyncio.sleep):
        self.seed = seed
        self.secret = secret
        self.sleep = sleep
        self.mode = dict(DEFAULT_MODE)
        self.reset()

    def reset(self) -> None:
        self.sampler = LatencySampler(f"partner-{self.seed}")
        self.counters: Counter = Counter()
        self.by_event: dict[str, Counter] = {}
        self.seen: Counter = Counter()  # (event, order_ref) -> accepted deliveries
        self.recent: deque = deque(maxlen=500)
        self.reset_at = iso_now()

    async def receive(self, raw: bytes, signature: str | None) -> tuple[int, dict]:
        self.counters["received"] += 1
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("event"), str):
            self.counters["rejected_bad_payload"] += 1
            return 400, {"error": "event field required"}
        fail = self.sampler.chance(float(self.mode["failure_rate"]))
        lat = float(self.mode["latency_ms"])
        if lat > 0:
            await self.sleep(lat / 1000.0)
        if fail:
            self.counters["failures_injected"] += 1
            return 500, {"error": "partner_unavailable"}

        event = body["event"]
        sv = body.get("schema_version")
        sv_key = str(sv) if sv is not None else "none"
        order_ref = body.get("order_ref")
        if order_ref is None and isinstance(body.get("order"), dict):
            order_ref = body["order"].get("order_ref")
        sig = verify_signature(raw, signature, self.secret)

        self.counters["delivered"] += 1
        self.counters[f"sig_{sig}"] += 1
        self.by_event.setdefault(event, Counter())[sv_key] += 1
        key = (event, str(order_ref))
        self.seen[key] += 1
        if self.seen[key] > 1:
            self.counters["duplicate_deliveries"] += 1
        items = body.get("items")
        if items is None and isinstance(body.get("order"), dict):
            items = body["order"].get("items")
        if event == "order.dispatched" and isinstance(items, list) and items:
            self.counters["dispatched_with_items"] += 1
        self.recent.append(
            {
                "ts": iso_now(),
                "event": event,
                "schema_version": sv,
                "order_ref": order_ref,
                "signature": sig,
                "has_items": bool(items),
            }
        )
        return 200, {"ok": True}

    def stats(self) -> dict:
        c = self.counters
        dup_keys = {f"{e}|{o}": n for (e, o), n in self.seen.items() if n > 1}
        return {
            "received_total": c["received"],
            "delivered_total": c["delivered"],
            "deliveries_by_event": {e: dict(v) for e, v in self.by_event.items()},
            "deliveries_by_schema_version": dict(
                sum((Counter(v) for v in self.by_event.values()), Counter())
            ),
            "signed_valid": c["sig_valid"],
            "signed_invalid": c["sig_invalid"],
            "unsigned": c["sig_unsigned"],
            "duplicate_deliveries": c["duplicate_deliveries"],
            "duplicate_keys": len(dup_keys),
            "duplicate_keys_sample": dict(sorted(dup_keys.items())[:50]),
            "unique_event_order_pairs": len(self.seen),
            "failures_injected": c["failures_injected"],
            "rejected_bad_payload": c["rejected_bad_payload"],
            "dispatched_with_items": c["dispatched_with_items"],
            "reset_at": self.reset_at,
        }

    def snapshot(self) -> dict:
        return {"mode": dict(self.mode), "stats": self.stats(), "recent": list(self.recent)[-100:]}


def build_apps(state: PartnerState, dumper: Dumper | None = None) -> tuple[web.Application, web.Application]:
    api = web.Application()

    async def hooks(request: web.Request):
        raw = await request.read()
        status, payload = await state.receive(raw, request.headers.get("X-Larder-Signature"))
        return web.json_response(payload, status=status)

    async def healthz(_r):
        return web.json_response({"ok": True})

    api.router.add_post("/hooks", hooks)
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

    async def a_deliveries(request: web.Request):
        n = int(request.query.get("limit", "100"))
        return web.json_response({"deliveries": list(state.recent)[-n:]})

    admin.router.add_get("/admin/stats", a_stats)
    admin.router.add_get("/admin/mode", a_get_mode)
    admin.router.add_post("/admin/mode", a_mode)
    admin.router.add_post("/admin/reset", a_reset)
    admin.router.add_get("/admin/deliveries", a_deliveries)
    return api, admin


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--artifacts", default=None)
    ap.add_argument("--seed", type=int, default=sim_seed(4243))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s partner-sim %(levelname)s %(message)s")
    state = PartnerState(args.seed, os.environ.get("LARDER_PARTNER_SECRET", DEFAULT_SECRET))
    dumper = Dumper("partner_stats.json", state.snapshot, args.artifacts)
    api, admin = build_apps(state, dumper)
    asyncio.run(
        serve(api, admin, port("partner", args.port_offset), port("partner_admin", args.port_offset), dumper, "partner-sim")
    )


if __name__ == "__main__":
    main()
