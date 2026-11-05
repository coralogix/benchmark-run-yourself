"""PSP simulator (third-party payment provider).

API   :8090+off  POST /v1/charges, POST /v1/refunds, GET /v1/charges/<id>, GET /healthz
Admin :9090+off  (header X-Admin-Token == $LARDER_ADMIN_TOKEN)
                 GET /admin/ledger, GET|POST /admin/mode, POST /admin/reset, GET /admin/stats

Charges are processed in a task detached from the HTTP request, so a charge is
captured even if the client gives up (timeouts on the caller side therefore
produce real duplicate captures when the caller retries without an
Idempotency-Key).

Usage: python -m harness.sims.psp_sim [--port-offset N] [--artifacts DIR] [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections import Counter
from typing import Any

from aiohttp import web

from harness.runtime.util import iso_now, port
from harness.sims.common import Dumper, LatencySampler, admin_middleware, read_json, serve, sim_seed

log = logging.getLogger("psp-sim")

DEFAULT_MODE = {
    "latency_p50_ms": 150.0,
    "latency_p99_ms": 700.0,
    "slow_success_rate": 0.0,
    "slow_success_ms": 9000.0,
    "error_rate": 0.0,
}


def _int_amount(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


class PspState:
    def __init__(self, seed: int, sleep=asyncio.sleep):
        self.seed = seed
        self.sleep = sleep
        self.mode = dict(DEFAULT_MODE)
        self.reset()

    def reset(self) -> None:
        self.sampler = LatencySampler(f"psp-{self.seed}")
        self.charges: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}
        self.idem: dict[str, tuple[dict, asyncio.Future]] = {}
        self.n_charge = 0
        self.n_refund = 0
        self.counters: Counter = Counter()
        self.reset_at = iso_now()

    # ---- helpers -----------------------------------------------------------
    def _draw(self) -> tuple[bool, bool, float]:
        m = self.mode
        err = self.sampler.chance(float(m["error_rate"]))
        slow = self.sampler.chance(float(m["slow_success_rate"]))
        lat = self.sampler.sample_ms(float(m["latency_p50_ms"]), float(m["latency_p99_ms"]))
        if slow:
            lat = float(m["slow_success_ms"])
        return err, slow, lat

    async def _idempotent(self, scope: str, key: str | None, params: dict, work) -> tuple[int, dict]:
        """Run `work()` once per (scope, key); replays get the original result."""
        if not key:
            return await asyncio.shield(asyncio.ensure_future(work()))
        k = f"{scope}:{key}"
        if k in self.idem:
            orig_params, fut = self.idem[k]
            if orig_params != params:
                self.counters["idempotency_mismatches"] += 1
                return 422, {"error": "idempotency_key_reuse", "message": "Idempotency-Key reused with different parameters"}
            self.counters["idempotent_replays"] += 1
            return await asyncio.shield(fut)
        fut = asyncio.ensure_future(work())
        self.idem[k] = (params, fut)

        def _done(f: asyncio.Future) -> None:
            # failed attempts (5xx) are not remembered: a retry with the same key re-executes
            if f.cancelled() or f.exception() is not None or f.result()[0] >= 500:
                if self.idem.get(k, (None, None))[1] is f:
                    del self.idem[k]

        fut.add_done_callback(_done)
        return await asyncio.shield(fut)

    # ---- charges ------------------------------------------------------------
    async def charge(self, body: dict, key: str | None) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return 400, {"error": "invalid_request"}
        amount = _int_amount(body.get("amount_pence"))
        if amount is None or amount <= 0:
            return 400, {"error": "invalid_amount", "message": "amount_pence must be a positive integer"}
        currency = body.get("currency", "GBP")
        if currency != "GBP":
            return 400, {"error": "unsupported_currency"}
        token = body.get("token")
        if not isinstance(token, str) or not token:
            return 400, {"error": "missing_token"}
        order_ref = body.get("order_ref")
        params = {"amount_pence": amount, "order_ref": order_ref, "token": token}
        return await self._idempotent("charge", key, params, lambda: self._do_charge(amount, order_ref, token, key))

    async def _do_charge(self, amount: int, order_ref: Any, token: str, key: str | None) -> tuple[int, dict]:
        self.counters["charge_requests"] += 1
        err, slow, lat = self._draw()
        await self.sleep(lat / 1000.0)
        if token == "tok_decline":
            self.counters["declines"] += 1
            return 402, {"error": "card_declined"}
        if err:
            self.counters["errors_injected"] += 1
            return 500, {"error": "psp_internal_error"}
        if slow:
            self.counters["slow_successes"] += 1
        self.n_charge += 1
        cid = f"ch_{self.n_charge:08d}"
        rec = {
            "charge_id": cid,
            "status": "captured",
            "amount_pence": amount,
            "currency": "GBP",
            "order_ref": order_ref,
            "idempotency_key": key,
            "slow": slow,
            "latency_ms": round(lat, 1),
            "created_at": iso_now(),
            "refunded_pence": 0,
        }
        self.charges[cid] = rec
        return 201, {"charge_id": cid, "status": "captured", "amount_pence": amount, "order_ref": order_ref}

    # ---- refunds -------------------------------------------------------------
    async def refund(self, body: dict, key: str | None) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return 400, {"error": "invalid_request"}
        cid = body.get("charge_id")
        ch = self.charges.get(cid) if isinstance(cid, str) else None
        if ch is None:
            return 404, {"error": "no_such_charge"}
        if "amount_pence" in body and body["amount_pence"] is not None:
            amount = _int_amount(body["amount_pence"])
            if amount is None or amount <= 0:
                return 400, {"error": "invalid_amount"}
        else:
            amount = ch["amount_pence"] - ch["refunded_pence"]
        params = {"charge_id": cid, "amount_pence": amount}
        return await self._idempotent("refund", key, params, lambda: self._do_refund(ch, amount, key))

    async def _do_refund(self, ch: dict, amount: int, key: str | None) -> tuple[int, dict]:
        self.counters["refund_requests"] += 1
        err, _slow, lat = self._draw()
        await self.sleep(lat / 1000.0)
        if err:
            self.counters["errors_injected"] += 1
            return 500, {"error": "psp_internal_error"}
        if amount <= 0 or ch["refunded_pence"] + amount > ch["amount_pence"]:
            self.counters["over_refunds"] += 1
            return 409, {"error": "refund_exceeds_charge", "refundable_pence": ch["amount_pence"] - ch["refunded_pence"]}
        self.n_refund += 1
        rid = f"re_{self.n_refund:08d}"
        ch["refunded_pence"] += amount
        self.refunds[rid] = {
            "refund_id": rid,
            "charge_id": ch["charge_id"],
            "order_ref": ch["order_ref"],
            "amount_pence": amount,
            "idempotency_key": key,
            "created_at": iso_now(),
        }
        return 201, {"refund_id": rid, "charge_id": ch["charge_id"], "amount_pence": amount, "status": "succeeded"}

    # ---- reporting --------------------------------------------------------------
    def stats(self) -> dict:
        per_order: dict[str, list[int]] = {}
        for c in self.charges.values():
            per_order.setdefault(str(c["order_ref"]), []).append(c["amount_pence"])
        dup_orders = {o: len(a) for o, a in per_order.items() if len(a) > 1}
        refunds_per_order: Counter = Counter(str(r["order_ref"]) for r in self.refunds.values())
        return {
            "charges_total": len(self.charges),
            "captured_pence_total": sum(c["amount_pence"] for c in self.charges.values()),
            "orders_charged": len(per_order),
            "orders_with_duplicate_captures": len(dup_orders),
            "duplicate_capture_count": sum(n - 1 for n in dup_orders.values()),
            "duplicate_captured_pence": sum(sum(per_order[o]) - per_order[o][0] for o in dup_orders),
            "refunds_total": len(self.refunds),
            "refunded_pence_total": sum(r["amount_pence"] for r in self.refunds.values()),
            "orders_with_multiple_refunds": sum(1 for n in refunds_per_order.values() if n > 1),
            "over_refunds": self.counters["over_refunds"],
            "idempotent_replays": self.counters["idempotent_replays"],
            "idempotency_mismatches": self.counters["idempotency_mismatches"],
            "charge_requests": self.counters["charge_requests"],
            "refund_requests": self.counters["refund_requests"],
            "declines": self.counters["declines"],
            "errors_injected": self.counters["errors_injected"],
            "slow_successes": self.counters["slow_successes"],
            "duplicate_orders_sample": dict(sorted(dup_orders.items())[:50]),
            "reset_at": self.reset_at,
        }

    def ledger(self) -> dict:
        return {"charges": list(self.charges.values()), "refunds": list(self.refunds.values())}

    def snapshot(self) -> dict:
        return {"mode": dict(self.mode), "stats": self.stats(), **self.ledger()}


def build_apps(state: PspState, dumper: Dumper | None = None) -> tuple[web.Application, web.Application]:
    api = web.Application()

    async def charges(request: web.Request):
        body, _ = await read_json(request)
        status, payload = await state.charge(body, request.headers.get("Idempotency-Key"))
        return web.json_response(payload, status=status)

    async def refunds(request: web.Request):
        body, _ = await read_json(request)
        status, payload = await state.refund(body, request.headers.get("Idempotency-Key"))
        return web.json_response(payload, status=status)

    async def get_charge(request: web.Request):
        ch = state.charges.get(request.match_info["cid"])
        if not ch:
            return web.json_response({"error": "no_such_charge"}, status=404)
        return web.json_response({k: ch[k] for k in ("charge_id", "status", "amount_pence", "order_ref", "refunded_pence", "created_at")})

    async def list_charges(request: web.Request):
        ref = request.query.get("order_ref")
        items = [
            {k: c[k] for k in ("charge_id", "status", "amount_pence", "order_ref", "refunded_pence", "created_at")}
            for c in state.charges.values()
            if ref is None or str(c["order_ref"]) == ref
        ]
        return web.json_response({"data": items[-100:]})

    async def healthz(_r):
        return web.json_response({"ok": True})

    api.router.add_post("/v1/charges", charges)
    api.router.add_get("/v1/charges", list_charges)
    api.router.add_get("/v1/charges/{cid}", get_charge)
    api.router.add_post("/v1/refunds", refunds)
    api.router.add_get("/healthz", healthz)

    admin = web.Application(middlewares=[admin_middleware()])

    async def a_stats(_r):
        return web.json_response(state.stats())

    async def a_ledger(_r):
        return web.json_response(state.ledger())

    async def a_get_mode(_r):
        return web.json_response(state.mode)

    async def a_mode(request: web.Request):
        body, _ = await read_json(request)
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
        body, _ = await read_json(request)
        if dumper:
            dumper.archive_before_reset()
        state.reset()
        if not (isinstance(body, dict) and body.get("keep_mode")):
            state.mode = dict(DEFAULT_MODE)
        if dumper:
            dumper.dump()
        return web.json_response({"ok": True, "mode": state.mode})

    admin.router.add_get("/admin/stats", a_stats)
    admin.router.add_get("/admin/ledger", a_ledger)
    admin.router.add_get("/admin/mode", a_get_mode)
    admin.router.add_post("/admin/mode", a_mode)
    admin.router.add_post("/admin/reset", a_reset)
    return api, admin


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--artifacts", default=None)
    ap.add_argument("--seed", type=int, default=sim_seed(4242))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s psp-sim %(levelname)s %(message)s")
    state = PspState(args.seed)
    dumper = Dumper("psp_ledger.json", state.snapshot, args.artifacts)
    api, admin = build_apps(state, dumper)
    asyncio.run(serve(api, admin, port("psp", args.port_offset), port("psp_admin", args.port_offset), dumper, "psp-sim"))


if __name__ == "__main__":
    main()
