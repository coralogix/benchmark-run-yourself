"""Deterministic synthetic customer workload.

Usage:
  python -m harness.workload.loadgen --target http://127.0.0.1:8000 --warmup-min 15 --run-min 60 \
      --seed 1337 --port-offset 0 --artifacts <dir> [--time-scale 1.0] [--scenario path] [--no-faults]

Writes one JSONL record per request to <artifacts>/loadgen.jsonl, phase changes
to <artifacts>/loadgen_phases.jsonl, a final <artifacts>/loadgen_summary.json,
and a summary line per scenario minute to stdout.

Determinism: the full session schedule (arrival times, users, SKUs, searches,
coupons, slot pick draws, checkout/probe/dispatch decisions) is derived from
--seed before any request is sent. Responses can differ between runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import yaml

from harness.runtime import registry
from harness.runtime.routes import normalise
from harness.runtime.util import JsonlWriter, artifacts_dir, iso_now, port, write_json_atomic

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_SCENARIO = HERE / "scenario.yaml"


# --------------------------------------------------------------------------- scenario
@dataclass(frozen=True)
class Phase:
    name: str
    start_min: float
    end_min: float
    rate: float
    mix: str
    faults: str


def load_scenario(path: str | os.PathLike = DEFAULT_SCENARIO) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def build_phases(scn: dict, warmup_min: float, run_min: float) -> list[Phase]:
    total = warmup_min + run_min
    out: list[Phase] = []
    t = 0.0
    for ph in scn["phases"]:
        dur = warmup_min if ph["duration_min"] == "warmup" else float(ph["duration_min"])
        start, end = t, min(t + dur, total)
        t += dur
        if end <= start:
            continue
        out.append(Phase(ph["name"], start, end, float(ph["rate"]), ph["mix"], ph["faults"]))
        if t >= total:
            break
    # extend the last phase if run_min is longer than the scripted phases
    if out and out[-1].end_min < total:
        last = out[-1]
        out[-1] = Phase(last.name, last.start_min, total, last.rate, last.mix, last.faults)
    return out


def phase_at(phases: list[Phase], t_min: float) -> Phase:
    for ph in phases:
        if t_min < ph.end_min:
            return ph
    return phases[-1]


def load_data() -> dict:
    d = {}
    for name in ("users", "skus", "coupons", "search_terms"):
        with open(DATA / f"{name}.json") as fh:
            d[name] = json.load(fh)
    return d


# --------------------------------------------------------------------------- session plans
@dataclass
class SessionPlan:
    idx: int
    session: str
    arrival_s: float  # wall seconds from workload start
    phase: str
    user_id: str
    postcode: str
    browse: list = field(default_factory=list)  # [(page, category|None)]
    details: list = field(default_factory=list)  # [sku]
    searches: list = field(default_factory=list)  # [(q, filters|None)]
    items: list = field(default_factory=list)  # [(sku, qty)]
    coupon: str | None = None
    slots_next_probe: bool = False
    stale_slot: bool = False
    slot_u: float = 0.0
    slot_retry_u: float = 0.0
    checkout: bool = False
    payment_token: str = "tok_ok"
    receipt_probe: bool = False
    cancel_probe: bool = False
    dispatch: bool = False
    dispatch_delay_s: float = 0.0
    think_s: list = field(default_factory=list)
    substitutions: bool = False
    order_history_probe: bool = False


def _rint(rng: random.Random, lo_hi) -> int:
    return rng.randint(int(lo_hi[0]), int(lo_hi[1]))


def plan_session(idx: int, seed: int, arrival_s: float, phase: Phase, scn: dict, data: dict, time_scale: float) -> SessionPlan:
    P = scn["params"]
    mix = scn["mixes"][phase.mix]
    rng = random.Random(f"larder-session:{seed}:{idx}")
    uw = float(P.get("regular_customer_weight", 1))
    user = rng.choices(data["users"], [uw if u.get("regular") else 1.0 for u in data["users"]])[0]
    sid = f"s{seed}-{idx:06d}"
    sk = data["skus"]
    promo_pool = list(dict.fromkeys(sk["featured"] + sk["promo_3for2"]))
    multibuy = set(sk["promo_3for2"])
    qty_vals = [int(k) for k in P["item_qty_weights"]]
    qty_w = [float(v) for v in P["item_qty_weights"].values()]

    plan = SessionPlan(idx=idx, session=sid, arrival_s=round(arrival_s, 4), phase=phase.name,
                       user_id=user["user_id"], postcode=user["postcode"])
    for _ in range(_rint(rng, P["browse_pages"])):
        page = rng.randint(1, int(P["catalogue_pages"]))
        cat = rng.choice(P["categories"]) if rng.random() < P["browse_category_rate"] else None
        if cat:
            page = 1
        plan.browse.append((page, cat))
    if phase.mix == "promo":
        plan.details = [rng.choice(promo_pool) for _ in range(_rint(rng, P["product_details"]))]
    else:
        popular = set(sk.get("popular", []))
        pw = float(P.get("popular_product_weight", 1))
        plan.details = rng.choices(sk["all"], [pw if s in popular else 1.0 for s in sk["all"]], k=_rint(rng, P["product_details"]))
    for k in range(_rint(rng, P["searches"])):
        q = rng.choice(data["search_terms"])
        if rng.random() < P["search_suffix_rate"]:
            q = f"{q} {sid[-6:]}{k}{rng.randrange(16**4):04x}"
        filters = None
        if rng.random() < P["search_filter_probe_rate"]:
            filters = {}
            kinds = rng.sample(["diet", "max_price_pence", "sort"], rng.randint(1, 3))
            for kind in sorted(kinds):
                if kind == "diet":
                    filters["diet"] = rng.choice(P["search_filter_diets"])
                elif kind == "max_price_pence":
                    lo, hi = P["search_filter_max_price_pence"]
                    filters["max_price_pence"] = rng.randint(int(lo), int(hi))
                else:
                    filters["sort"] = rng.choice(P["search_filter_sorts"])
        plan.searches.append((q, filters))
    n_items = _rint(rng, P["cart_items"])
    force_promo = rng.random() < float(mix["featured_cart_rate"])
    chosen: list = []
    for i in range(n_items):
        if force_promo and i == 0:
            sku = rng.choice(sk["featured"]) if rng.random() < 0.5 else rng.choice(sk["promo_3for2"])
        else:
            sku = rng.choice(sk["all"])
        qty = rng.choices(qty_vals, qty_w)[0]
        if sku in multibuy and rng.random() < P["multibuy_qty3_rate"]:
            qty = 3
        chosen.append((sku, qty))
    plan.items = chosen
    if rng.random() < float(mix["coupon_rate"]):
        plan.coupon = rng.choice(data["coupons"])
    plan.slots_next_probe = rng.random() < P["slots_next_probe_rate"]
    plan.stale_slot = rng.random() < P["stale_slot_rate"]
    plan.slot_u = rng.random()
    plan.slot_retry_u = rng.random()
    plan.checkout = rng.random() < P["checkout_rate"]
    plan.payment_token = "tok_decline" if rng.random() < P["decline_token_rate"] else "tok_ok"
    plan.receipt_probe = rng.random() < P["receipt_probe_rate"]
    plan.cancel_probe = rng.random() < P["cancel_probe_rate"]
    plan.dispatch = rng.random() < P["dispatch_rate"]
    jitter = rng.uniform(-1, 1) * float(P["dispatch_jitter_min"])
    plan.dispatch_delay_s = round((float(P["dispatch_delay_min"]) + jitter) * 60.0 * time_scale, 4)
    lo, hi = P["think_time_s"]
    plan.think_s = [round(rng.uniform(lo, hi) * time_scale, 4) for _ in range(24)]
    # these draws come last so the fields drawn above keep their values for a given seed
    plan.substitutions = rng.random() < float(P.get("substitutions_rate", 0.0))
    plan.order_history_probe = rng.random() < float(P.get("order_history_probe_rate", 0.0))
    return plan


def build_schedule(
    scn: dict, seed: int, warmup_min: float, run_min: float, time_scale: float = 1.0, rate_scale: float = 1.0,
    data: dict | None = None,
) -> tuple[list[Phase], list[SessionPlan]]:
    """Piecewise-Poisson arrivals (rates are sessions per WALL minute)."""
    data = data or load_data()
    phases = build_phases(scn, warmup_min, run_min)
    total = phases[-1].end_min
    rng = random.Random(f"larder-arrivals:{seed}")
    plans: list[SessionPlan] = []
    t = 0.0  # scenario minutes
    for ph in phases:
        t = max(t, ph.start_min)
        lam = ph.rate * rate_scale * time_scale  # sessions per scenario minute
        if lam <= 0:
            continue
        while True:
            t_next = t + rng.expovariate(lam)
            if t_next >= ph.end_min:
                t = ph.end_min  # memoryless: restart at the boundary with the next rate
                break
            t = t_next
            plans.append(plan_session(len(plans), seed, t * 60.0 * time_scale, ph, scn, data, time_scale))
    assert all(p.arrival_s <= total * 60 * time_scale for p in plans)
    return phases, plans


# --------------------------------------------------------------------------- probes
def multibuy_probe(cart: dict, multibuy_skus: set) -> bool | None:
    """True if the cart visibly applies 3-for-2, False if it should but doesn't, None if n/a."""
    items = cart.get("items") or []
    expected = 0
    for it in items:
        if it.get("sku") in multibuy_skus:
            q = int(it.get("qty") or 0)
            unit = it.get("unit_price_pence")
            if q >= 3 and isinstance(unit, (int, float)):
                expected += (q // 3) * int(unit)
    if expected <= 0:
        return None
    for k in ("multibuy_discount_pence", "promotions", "promotion_discount_pence", "offers"):
        v = cart.get(k)
        if v:
            return True
    for it in items:
        q, unit, lt = it.get("qty"), it.get("unit_price_pence"), it.get("line_total_pence")
        if all(isinstance(x, (int, float)) for x in (q, unit, lt)) and lt < q * unit:
            return True
        if it.get("discount_pence") or it.get("multibuy_discount_pence"):
            return True
    disc = cart.get("discount_pence")
    if isinstance(disc, (int, float)) and not cart.get("coupon") and disc >= expected:
        return True
    return False


def search_filter_probe(results: list, filters: dict) -> bool | None:
    if not results:
        return None
    ok = True
    if "max_price_pence" in filters:
        ok &= all(isinstance(r.get("price_pence"), (int, float)) and r["price_pence"] <= filters["max_price_pence"] for r in results)
    if "diet" in filters:
        ok &= all(filters["diet"] in (r.get("diet") or []) for r in results)
    if "sort" in filters and len(results) > 1:
        s = filters["sort"]
        try:
            if s == "price_asc":
                vals = [r["price_pence"] for r in results]
                ok &= vals == sorted(vals)
            elif s == "price_desc":
                vals = [r["price_pence"] for r in results]
                ok &= vals == sorted(vals, reverse=True)
            elif s == "name":
                vals = [str(r["name"]).lower() for r in results]
                ok &= vals == sorted(vals)
        except (KeyError, TypeError):
            ok = False
    return bool(ok)


def low_stock_probe(product) -> bool | None:
    """FEAT-11: product detail carries a low-stock badge field."""
    if not isinstance(product, dict):
        return None
    return any(k in product for k in ("low_stock", "only_n_left"))


def order_history_probe(body) -> bool:
    """FEAT-8: paginated order history."""
    return isinstance(body, dict) and isinstance(body.get("orders"), list)


def substitutions_probe(body) -> bool | None:
    """FEAT-9: checkout response mentions substitutions (any status)."""
    if not isinstance(body, dict):
        return None
    return "substitutions" in body


# --------------------------------------------------------------------------- runtime
def _admin_token(art: Path) -> str | None:
    tok = os.environ.get("LARDER_ADMIN_TOKEN")
    if tok:
        return tok
    f = art / "admin_token"
    if f.exists():
        return f.read_text().strip() or None
    return None


class Window:
    def __init__(self) -> None:
        self.outcomes: Counter = Counter()
        self.lat: list[float] = []
        self.sessions = 0
        self.orders = 0

    @staticmethod
    def pct(vals: list[float], q: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[min(len(s) - 1, int(math.ceil(q * len(s))) - 1)]


class Loadgen:
    def __init__(self, args: argparse.Namespace, scn: dict):
        self.args = args
        self.scn = scn
        self.P = scn["params"]
        self.ts = args.time_scale
        self.art = artifacts_dir(args.artifacts)
        self.target = args.target.rstrip("/")
        u = urlsplit(self.target)
        host = u.hostname or "127.0.0.1"
        warehouse = registry.default().get("warehouse")
        self.dispatch_base = f"{u.scheme or 'http'}://{host}:{warehouse.mesh_port(args.port_offset)}"
        self.sim_admin = {
            sim: f"http://127.0.0.1:{port(sim + '_admin', args.port_offset)}" for sim in ("psp", "partner", "email")
        }
        self.data = load_data()
        self.multibuy = set(self.data["skus"]["promo_3for2"])
        self.phases, self.plans = build_schedule(
            scn, args.seed, args.warmup_min, args.run_min, args.time_scale, args.rate_scale, self.data
        )
        self.end_s = self.phases[-1].end_min * 60.0 * self.ts
        self.out = JsonlWriter(self.art / "loadgen.jsonl")
        self.phase_log = JsonlWriter(self.art / "loadgen_phases.jsonl", line_buffered=True)
        self.sem = asyncio.Semaphore(int(self.P["max_inflight_sessions"]))
        self.win = Window()
        self.total: Counter = Counter()
        self.probes: dict[str, Counter] = {}
        self.inflight = 0
        self.delayed = 0
        self.dispatch_tasks: set[asyncio.Task] = set()
        self.session_tasks: set[asyncio.Task] = set()
        self.t0 = 0.0

    # -- clock
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def scen_min(self) -> float:
        return self.elapsed() / (60.0 * self.ts)

    def phase_now(self) -> str:
        return phase_at(self.phases, self.scen_min()).name

    # -- http
    async def request(
        self, plan: SessionPlan, step: str, method: str, path: str, *, base: str | None = None,
        json_body: Any = None, params: dict | None = None, probe: str | None = None, order_ref: str | None = None,
        defer: bool = False,
    ) -> tuple[int | None, Any, dict]:
        """Send one request and record it. With defer=True the caller must commit(rec)."""
        url = (base or self.target) + path
        t = time.monotonic()
        status: int | None = None
        body: Any = None
        detail: str | None = None
        try:
            # the customer's session identity, as a web/app client would send it (edge traffic only)
            hdrs = {"X-Larder-User": plan.user_id} if base is None else None
            async with self.http.request(method, url, json=json_body, params=params, headers=hdrs) as r:
                status = r.status
                raw = await r.read()
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as e:
            detail = type(e).__name__
        lat = (time.monotonic() - t) * 1000.0
        if status is None:
            outcome = "timeout"
        elif 200 <= status < 300:
            outcome = "ok"
        elif status == 404 and probe:
            outcome = "feature_absent"
        elif 400 <= status < 500:
            outcome = "client_reject"
        elif status >= 500:
            outcome = "error"
        else:
            outcome = "ok"
        if detail is None and status is not None and status >= 400 and isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, str):
                detail = err[:80]
        rec = {
            "ts": iso_now(),
            "phase": self.phase_now(),
            "session": plan.session,
            "user": plan.user_id,
            "step": step,
            "method": method,
            "path": path,
            "route": normalise("any", method, path),
            "status": status,
            "latency_ms": round(lat, 1),
            "outcome": outcome,
        }
        if order_ref:
            rec["order_ref"] = order_ref
        if detail:
            rec["detail"] = detail
        if probe:
            rec["probe"] = probe
            if outcome == "feature_absent":
                rec["feature_live"] = False
        if not defer:
            self.out.write(rec)
        self.win.outcomes[outcome] += 1
        self.win.lat.append(lat)
        self.total[outcome] += 1
        if probe and outcome == "feature_absent":
            self.probes.setdefault(probe, Counter())["absent"] += 1
        return status, body, rec

    def commit(self, rec: dict) -> None:
        self.out.write(rec)

    def mark_probe(self, probe: str, live: bool | None, rec: dict) -> None:
        """Annotate a (deferred, not yet written) 2xx record with feature-liveness."""
        c = self.probes.setdefault(probe, Counter())
        c["live" if live else ("not_live" if live is False else "unknown")] += 1
        rec["probe"] = probe
        rec["feature_live"] = live

    async def think(self, plan: SessionPlan, i: int) -> None:
        await asyncio.sleep(plan.think_s[i % len(plan.think_s)])

    # -- session
    async def session(self, plan: SessionPlan) -> None:
        try:
            await self._session(plan)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # never let one session kill the generator
            print(f"[loadgen] session {plan.session} crashed: {e!r}", file=sys.stderr, flush=True)
        finally:
            self.inflight -= 1
            self.sem.release()

    async def _session(self, plan: SessionPlan) -> None:
        k = 0
        uid = plan.user_id
        for page, cat in plan.browse:
            params = {"page": str(page), "per_page": str(self.P["per_page"])}
            if cat:
                params["category"] = cat
            await self.request(plan, "browse", "GET", "/api/products", params=params)
            await self.think(plan, k); k += 1
        for sku in plan.details:
            st, body, rec = await self.request(plan, "product", "GET", f"/api/products/{sku}", defer=True)
            if st and 200 <= st < 300:
                live = low_stock_probe(body)
                if live is not None:
                    self.mark_probe("low_stock", live, rec)
            self.commit(rec)
            await self.think(plan, k); k += 1
        for q, filters in plan.searches:
            params = {"q": q, "uid": uid}
            if filters:
                params.update({kk: str(v) for kk, v in filters.items()})
            st, body, rec = await self.request(
                plan, "search_filtered" if filters else "search", "GET", "/api/search", params=params,
                probe="search_filters" if filters else None, defer=True,
            )
            if filters and st and 200 <= st < 300 and isinstance(body, dict):
                self.mark_probe("search_filters", search_filter_probe(body.get("results") or [], filters), rec)
            self.commit(rec)
            await self.think(plan, k); k += 1

        st, body, _ = await self.request(plan, "cart_create", "POST", "/api/carts", json_body={"user_id": uid})
        if not (st and 200 <= st < 300 and isinstance(body, dict) and body.get("cart_id")):
            return
        cart_id = str(body["cart_id"])
        for sku, qty in plan.items:
            await self.request(plan, "cart_add", "POST", f"/api/carts/{cart_id}/items", json_body={"sku": sku, "qty": qty})
            await self.think(plan, k); k += 1
        if plan.coupon:
            await self.request(plan, "coupon", "POST", f"/api/carts/{cart_id}/coupon", json_body={"code": plan.coupon})
            await self.think(plan, k); k += 1
        st, cart, rec = await self.request(plan, "cart_view", "GET", f"/api/carts/{cart_id}", defer=True)
        if st and 200 <= st < 300 and isinstance(cart, dict):
            mb = multibuy_probe(cart, self.multibuy)
            if mb is not None:
                self.mark_probe("multibuy", mb, rec)
        self.commit(rec)

        if plan.slots_next_probe:
            st, body, rec = await self.request(
                plan, "slots_next", "GET", "/api/delivery/slots/next", params={"postcode": plan.postcode},
                probe="slots_next", defer=True,
            )
            if st and 200 <= st < 300:
                self.mark_probe("slots_next", isinstance(body, dict) and bool(body.get("slot_id") or body.get("slot")), rec)
            self.commit(rec)
        st, body, _ = await self.request(plan, "slots", "GET", "/api/delivery/slots", params={"postcode": plan.postcode})
        await self.think(plan, k); k += 1
        slots = (body or {}).get("slots") if isinstance(body, dict) else None
        if not (st and 200 <= st < 300 and isinstance(slots, list) and slots):
            return
        avail = [s for s in slots if isinstance(s, dict) and (s.get("available") or 0) > 0]
        full = [s for s in slots if isinstance(s, dict) and (s.get("available") or 0) <= 0]
        pool = full if (plan.stale_slot and full) else (avail or slots)
        slot = pool[min(int(plan.slot_u * len(pool)), len(pool) - 1)]
        st, hold, _ = await self.request(
            plan, "hold", "POST", "/api/delivery/holds", json_body={"cart_id": cart_id, "slot_id": slot.get("slot_id")}
        )
        if st == 409 and avail and pool is not avail:
            # a real customer whose stale slot was full picks another one
            slot = avail[min(int(plan.slot_retry_u * len(avail)), len(avail) - 1)]
            st, hold, _ = await self.request(
                plan, "hold_retry", "POST", "/api/delivery/holds", json_body={"cart_id": cart_id, "slot_id": slot.get("slot_id")}
            )
        if not (st and 200 <= st < 300 and isinstance(hold, dict) and hold.get("hold_id")):
            return
        await self.think(plan, k); k += 1
        if not plan.checkout:
            return

        co_body = {"cart_id": cart_id, "hold_id": hold["hold_id"], "payment_token": plan.payment_token}
        if plan.substitutions:
            co_body["allow_substitutions"] = True
        st, body, rec = await self.request(plan, "checkout", "POST", "/api/checkout", json_body=co_body, defer=True)
        if plan.substitutions and st is not None:
            live = substitutions_probe(body)
            if live or (live is False and 200 <= st < 300):
                self.mark_probe("substitutions", live, rec)
        if not (st and 200 <= st < 300 and isinstance(body, dict) and body.get("order_ref")):
            self.commit(rec)
            await self.request(plan, "account", "GET", f"/api/account/{uid}")
            await self.order_history(plan)
            return
        ref = str(body["order_ref"])
        rec["order_ref"] = ref
        self.commit(rec)
        self.win.orders += 1
        self.total["orders"] += 1
        await self.think(plan, k); k += 1
        await self.request(plan, "order_view", "GET", f"/api/orders/{ref}", order_ref=ref)
        if plan.receipt_probe:
            st, body, rec = await self.request(
                plan, "receipt", "GET", f"/api/orders/{ref}/receipt", probe="receipt", order_ref=ref, defer=True
            )
            if st and 200 <= st < 300:
                live = isinstance(body, dict) and any(k2 in body for k2 in ("lines", "items", "vat", "vat_pence", "vat_total_pence"))
                self.mark_probe("receipt", live, rec)
            self.commit(rec)
        cancelled = False
        if plan.cancel_probe:
            await self.think(plan, k); k += 1
            st, body, rec = await self.request(
                plan, "cancel", "POST", f"/api/orders/{ref}/cancel", probe="cancel", order_ref=ref, defer=True
            )
            if st and 200 <= st < 300:
                cancelled = True
                self.mark_probe("cancel", True, rec)
            self.commit(rec)
        st, acct, rec = await self.request(plan, "account", "GET", f"/api/account/{uid}", defer=True)
        if st and 200 <= st < 300 and isinstance(acct, dict):
            self.mark_probe("loyalty_points", "loyalty_points" in acct, rec)
        self.commit(rec)
        await self.order_history(plan)
        if plan.dispatch and not cancelled:
            t = asyncio.create_task(self.dispatch(plan, ref))
            self.dispatch_tasks.add(t)
            t.add_done_callback(self.dispatch_tasks.discard)

    async def order_history(self, plan: SessionPlan) -> None:
        """FEAT-8 probe: paginated order history on the account page."""
        if not plan.order_history_probe:
            return
        st, body, rec = await self.request(
            plan, "order_history", "GET", f"/api/account/{plan.user_id}/orders", params={"page": "1", "per_page": "10"},
            probe="order_history", defer=True,
        )
        if st and 200 <= st < 300:
            self.mark_probe("order_history", order_history_probe(body), rec)
        self.commit(rec)

    async def dispatch(self, plan: SessionPlan, ref: str) -> None:
        """Models the warehouse floor: dispatch ~3 min after confirmation (warehouse mesh port)."""
        await asyncio.sleep(plan.dispatch_delay_s)
        if self.elapsed() > self.end_s:
            return
        await self.request(plan, "dispatch", "POST", f"/internal/dispatch/{ref}", base=self.dispatch_base, order_ref=ref)

    # -- control
    async def apply_faults(self, ph: Phase) -> None:
        spec = self.scn["faults"].get(ph.faults, {})
        rec: dict[str, Any] = {"ts": iso_now(), "phase": ph.name, "scenario_min": ph.start_min, "rate": ph.rate,
                               "mix": ph.mix, "faults": ph.faults, "applied": {}}
        token = _admin_token(self.art)
        if self.args.no_faults:
            rec["applied"] = "disabled"
        elif not token:
            rec["applied"] = "no_admin_token"
            print("[loadgen] WARNING: no admin token (LARDER_ADMIN_TOKEN or <artifacts>/admin_token); sim faults not applied", flush=True)
        else:
            for sim, base in self.sim_admin.items():
                if sim not in spec:
                    continue
                try:
                    async with self.http.post(base + "/admin/mode", json=spec[sim], headers={"X-Admin-Token": token}) as r:
                        rec["applied"][sim] = r.status
                except Exception as e:
                    rec["applied"][sim] = type(e).__name__
        self.phase_log.write(rec)
        print(f"[loadgen] phase {ph.name} (scenario min {ph.start_min:g}) rate={ph.rate:g}/min mix={ph.mix} faults={ph.faults} applied={rec['applied']}", flush=True)

    async def phase_controller(self) -> None:
        for ph in self.phases:
            await asyncio.sleep(max(0.0, ph.start_min * 60.0 * self.ts - self.elapsed()))
            await self.apply_faults(ph)

    async def reporter(self) -> None:
        period = max(2.0, 60.0 * self.ts)
        minute = 0
        while True:
            await asyncio.sleep(max(0.0, (minute + 1) * period - self.elapsed()))
            minute += 1
            self.print_window(minute)

    def print_window(self, minute: int) -> None:
        w, self.win = self.win, Window()
        o = w.outcomes
        n = sum(o.values())
        print(
            f"[loadgen] min={minute:3d} phase={self.phase_now():12s} sessions={w.sessions:4d} inflight={self.inflight:3d} "
            f"req={n:5d} ok={o['ok']} reject={o['client_reject']} err={o['error']} timeout={o['timeout']} "
            f"absent={o['feature_absent']} p50={Window.pct(w.lat, .5):.0f}ms p95={Window.pct(w.lat, .95):.0f}ms "
            f"p99={Window.pct(w.lat, .99):.0f}ms orders={w.orders}",
            flush=True,
        )
        self.out.flush()

    async def launcher(self) -> None:
        for plan in self.plans:
            await asyncio.sleep(max(0.0, plan.arrival_s - self.elapsed()))
            await self.sem.acquire()
            if self.elapsed() - plan.arrival_s > 1.0:
                self.delayed += 1
            self.inflight += 1
            self.win.sessions += 1
            self.total["sessions"] += 1
            t = asyncio.create_task(self.session(plan))
            self.session_tasks.add(t)
            t.add_done_callback(self.session_tasks.discard)

    async def run(self) -> dict:
        conn = aiohttp.TCPConnector(limit=256, keepalive_timeout=30)
        timeout = aiohttp.ClientTimeout(total=float(self.P["client_timeout_s"]))
        async with aiohttp.ClientSession(
            connector=conn, timeout=timeout, headers={"User-Agent": "larder-loadgen/1.0"},
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as self.http:
            print(
                f"[loadgen] seed={self.args.seed} sessions={len(self.plans)} phases="
                + ",".join(f"{p.name}[{p.start_min:g}-{p.end_min:g}]" for p in self.phases)
                + f" time_scale={self.ts} wall={self.end_s:.0f}s target={self.target}",
                flush=True,
            )
            self.t0 = time.monotonic()
            started = iso_now()
            bg = [asyncio.create_task(self.phase_controller()), asyncio.create_task(self.reporter())]
            await self.launcher()
            await asyncio.sleep(max(0.0, self.end_s - self.elapsed()))
            # drain in-flight sessions (bounded by the client timeout)
            if self.session_tasks:
                await asyncio.wait(set(self.session_tasks), timeout=float(self.P["client_timeout_s"]) + 5)
            for t in list(self.session_tasks) + list(self.dispatch_tasks) + bg:
                t.cancel()
            await asyncio.gather(*self.session_tasks, *self.dispatch_tasks, *bg, return_exceptions=True)
        self.print_window(-1)
        summary = {
            "started_at": started,
            "finished_at": iso_now(),
            "seed": self.args.seed,
            "time_scale": self.ts,
            "warmup_min": self.args.warmup_min,
            "run_min": self.args.run_min,
            "sessions_planned": len(self.plans),
            "sessions_started": self.total["sessions"],
            "sessions_delayed_by_cap": self.delayed,
            "orders_confirmed": self.total["orders"],
            "outcomes": {k: v for k, v in self.total.items() if k not in ("sessions", "orders")},
            "probes": {k: dict(v) for k, v in self.probes.items()},
        }
        write_json_atomic(self.art / "loadgen_summary.json", summary)
        self.out.close()
        self.phase_log.close()
        print("[loadgen] done " + json.dumps(summary), flush=True)
        return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="http://127.0.0.1:8000")
    ap.add_argument("--warmup-min", type=float, default=None)
    ap.add_argument("--run-min", type=float, default=None)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--artifacts", default=None)
    ap.add_argument("--time-scale", type=float, default=1.0, help="<1 compresses the timeline (smoke tests)")
    ap.add_argument("--rate-scale", type=float, default=1.0, help="multiplier on sessions/min (default 1)")
    ap.add_argument("--scenario", default=str(DEFAULT_SCENARIO))
    ap.add_argument("--no-faults", action="store_true", help="do not touch sim fault modes")
    ap.add_argument("--dry-run", action="store_true", help="print the schedule digest and exit")
    args = ap.parse_args(argv)
    scn = load_scenario(args.scenario)
    if args.warmup_min is None:
        args.warmup_min = float(scn["defaults"]["warmup_min"])
    if args.run_min is None:
        args.run_min = float(scn["defaults"]["run_min"])
    if args.dry_run:
        import hashlib

        phases, plans = build_schedule(scn, args.seed, args.warmup_min, args.run_min, args.time_scale, args.rate_scale)
        digest = hashlib.sha256(json.dumps([asdict(p) for p in plans], sort_keys=True).encode()).hexdigest()
        print(json.dumps({"sessions": len(plans), "digest": digest, "phases": [asdict(p) for p in phases]}, indent=1))
        return
    lg = Loadgen(args, scn)
    try:
        asyncio.run(lg.run())
    except KeyboardInterrupt:
        lg.out.close()


if __name__ == "__main__":
    main()
