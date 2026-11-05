"""Helpers for the FEAT-001..FEAT-050 acceptance tests (evaluator-only).

Black-box only: everything goes through the public edge, the mesh ports, the
simulator admin APIs or the mesh ingress log in $LARDER_EVAL_ARTIFACTS.

Conventions used by the FEAT tests:
  * ordinary orders use products that are NOT tagged `3for2` and keep goods
    between 1000 and 3999 pence, so that other backlog features (minimum order
    value, multi-buy, free delivery) do not change their totals;
  * no line ever exceeds 24 units (FEAT-019);
  * carts with a percentage coupon are checked for internal consistency before
    checkout (`consistent_total`) so the seeded pricing defect (SF-1) can't make
    an unrelated test flaky.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import time
import uuid
from pathlib import Path

import httpx

OFFSET = int(os.environ.get("LARDER_EVAL_OFFSET", "0"))
ARTIFACTS = Path(os.environ.get("LARDER_EVAL_ARTIFACTS", "/nonexistent"))
PSP_API = f"http://127.0.0.1:{8090 + OFFSET}"

FEATURE_MISSING = (404, 405)

# state shared between test modules (e.g. a slot hold created early in the run
# and checked once it has expired, FEAT-040)
SHARED: dict = {}


# ------------------------------------------------------------------ small utils

def p95(samples):
    s = sorted(samples)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def timed(fn, n, warmup=3):
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def jbody(r):
    try:
        return r.json()
    except ValueError:
        return {"_raw": r.text[:300]}


def err(r):
    b = jbody(r)
    return b.get("error") if isinstance(b, dict) else None


def round_half_up_div(n, d):
    return (2 * n + d) // (2 * d)


def canonical(items, percent_off=0, free_delivery=False):
    """Canonical totals from cart/order items (unit_price_pence x qty), as the tax service computes them."""
    sub = sum(int(i["unit_price_pence"]) * int(i["qty"]) for i in items)
    if percent_off and float(percent_off) == int(percent_off):
        disc = round_half_up_div(sub * int(percent_off), 100)
    elif percent_off:
        disc = int(math.floor(sub * float(percent_off) / 100 + 0.5))
    else:
        disc = 0
    deliv = 0 if free_delivery or sub - disc >= 4000 else 399
    return {"subtotal_pence": sub, "discount_pence": disc, "delivery_fee_pence": deliv,
            "total_pence": sub - disc + deliv}


def qty_for(price_pence, lo=1000, hi=3999, max_qty=24):
    """Smallest qty with lo <= qty*price <= hi."""
    for q in range(1, max_qty + 1):
        if lo <= q * price_pence <= hi:
            return q
    raise AssertionError(f"no qty puts {price_pence}p between {lo} and {hi}")


def regular_lines(catalog, n=1, lo=1000, hi=3999, vat_band=None, min_stock=200):
    """n unused products (not 3for2) with quantities (<= 24) so that total goods are within [lo, hi]."""
    per_lo, per_hi = -(-lo // n), hi // n
    prods, lines = [], []
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < min_stock:
            continue
        if vat_band and p["vat_band"] != vat_band:
            continue
        q = next((q for q in range(1, 25) if per_lo <= q * p["price_pence"] <= per_hi), None)
        if q is None:
            continue
        if len(prods) == n - 1:  # last line: the whole basket must not be an SF-1 pricing case
            from larder_support import engines_agree
            pq = [(x["price_pence"], qq) for x, (_, qq) in zip(prods, lines)] + [(p["price_pence"], q)]
            if not engines_agree(pq):
                continue
        prods.append(p)
        lines.append((p["sku"], q))
        if len(prods) == n:
            break
    assert len(prods) == n, "not enough matching seed products"
    catalog._used.update(p["sku"] for p in prods)
    return lines, prods


def exact_goods_line(catalog, total, min_stock=200):
    """(sku, qty) for one unused non-3for2 product whose goods are exactly `total` pence (qty <= 24)."""
    from larder_support import engines_agree
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < min_stock:
            continue
        q, rem = divmod(total, p["price_pence"])
        if rem == 0 and 1 <= q <= 24 and engines_agree([(p["price_pence"], q)]):
            catalog._used.add(p["sku"])
            return p["sku"], q
    raise AssertionError(f"no seed product makes exactly {total}p of goods")


def pick_range(catalog, n, min_price, max_price, min_stock=200, **kw):
    """n unused non-3for2 products priced within [min_price, max_price]."""
    out = []
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < min_stock:
            continue
        if not min_price <= p["price_pence"] <= max_price:
            continue
        if any(p.get(k) != v for k, v in kw.items()):
            continue
        out.append(p)
        if len(out) == n:
            break
    assert len(out) == n, "not enough matching seed products"
    catalog._used.update(p["sku"] for p in out)
    return out


# ------------------------------------------------------------------ carts / orders

def new_cart(edge, user_id, lines=(), coupon=None):
    r = edge.post("/api/carts", json={"user_id": user_id})
    assert r.status_code in (200, 201), r.text
    cid = r.json()["cart_id"]
    for sku, qty in lines:
        r = edge.post(f"/api/carts/{cid}/items", json={"sku": sku, "qty": qty})
        assert r.status_code == 200, f"add {sku} x{qty}: {r.status_code} {r.text}"
    if coupon:
        r = edge.post(f"/api/carts/{cid}/coupon", json={"code": coupon})
        assert r.status_code == 200, f"coupon {coupon}: {r.status_code} {r.text}"
    return cid


def get_cart(edge, cid):
    r = edge.get(f"/api/carts/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def list_slots(edge, postcode):
    r = edge.get("/api/delivery/slots", params={"postcode": postcode})
    assert r.status_code == 200, r.text
    return r.json()


def hold(edge, cid, postcode):
    """Returns (hold_id, slot) where slot is the listing entry {slot_id,date,window,...}."""
    for s in list_slots(edge, postcode)["slots"]:
        if s.get("available"):
            r = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": s["slot_id"]})
            if r.status_code == 201:
                return r.json()["hold_id"], s
    raise AssertionError("no slot could be held")


def checkout(edge, cid, hold_id, token="tok_ok", headers=None, **extra):
    body = {"cart_id": cid, "hold_id": hold_id, "payment_token": token, **extra}
    return edge.post("/api/checkout", json=body, headers=headers or {})


def place(edge, user, lines, coupon=None, **extra):
    """Full order. Returns dict(order=<201 json>, cart=<cart before checkout>, cart_id, hold_id, slot)."""
    cid = new_cart(edge, user["user_id"], lines, coupon)
    cart = get_cart(edge, cid)
    hid, slot = hold(edge, cid, user["postcode"])
    r = checkout(edge, cid, hid, **extra)
    assert r.status_code == 201, f"checkout: {r.status_code} {r.text}"
    return {"order": r.json(), "cart": cart, "cart_id": cid, "hold_id": hid, "slot": slot}


def consistent_total(cart, percent_off, free_delivery=False):
    return canonical(cart["items"], percent_off, free_delivery)["total_pence"] == cart["total_pence"]


def coupon_cart(edge, user_id, prods, coupon, percent_off, lo=1000, hi=3999, extra_lines=()):
    """Cart with `prods` (one line each) + coupon whose total agrees with integer maths.

    Tries a few quantity combinations (keeps goods within [lo, hi])."""
    tried = []
    n = len(prods)
    for bump in range(0, 12):
        lines = []
        for i, p in enumerate(prods):
            q = qty_for(p["price_pence"], -(-lo // n), hi // n)
            if i == 0:
                q += bump
            lines.append((p["sku"], q))
        goods = sum(q * p["price_pence"] for (_, q), p in zip(lines, prods))
        if goods > hi or any(q > 24 for _, q in lines):
            break
        cid = new_cart(edge, user_id, list(lines) + list(extra_lines), coupon)
        cart = get_cart(edge, cid)
        if consistent_total(cart, percent_off):
            return cid, cart, lines
        tried.append(cart["total_pence"])
    raise AssertionError(f"could not build a consistent {coupon} cart (tried totals {tried})")


def order_view(edge, ref):
    r = edge.get(f"/api/orders/{ref}")
    assert r.status_code == 200, r.text
    return r.json()


def internal_order(mesh, ref):
    r = mesh("orders").get(f"/internal/orders/{ref}")
    assert r.status_code == 200, r.text
    return r.json()


def dispatch(mesh, ref):
    r = mesh("warehouse").post(f"/internal/dispatch/{ref}", json={})
    assert r.status_code == 200, f"dispatch {ref}: {r.status_code} {r.text}"
    return r.json()


def feature_present(r):
    return r.status_code not in FEATURE_MISSING


# ------------------------------------------------------------------ ops state

def available(mesh, sku):
    r = mesh("inventory").get(f"/internal/inventory/{sku}")
    assert r.status_code == 200, r.text
    return r.json()["available"]


@contextlib.contextmanager
def drained(mesh, sku, leave=0):
    """Temporarily reserve stock of `sku` so that exactly `leave` units are available."""
    tag = "ACC-DRAIN-" + uuid.uuid4().hex[:8]
    avail = available(mesh, sku)
    take = avail - leave
    assert take >= 0, f"{sku} has only {avail} available"
    if take:
        r = mesh("inventory").post("/internal/inventory/reservations", json={"sku": sku, "qty": take, "order_ref": tag})
        assert r.status_code in (200, 201), f"reserve {sku} x{take}: {r.status_code} {r.text}"
    try:
        yield take
    finally:
        if take:
            mesh("inventory").post("/internal/inventory/releases",
                                   json={"order_ref": tag, "items": [{"sku": sku, "qty": take}]})


def zone_slots(mesh, zone):
    r = mesh("slots").get("/internal/slots", params={"zone": zone})
    assert r.status_code == 200, r.text
    return {s["slot_id"]: s for s in r.json()["slots"]}


def zone_of(mesh, postcode):
    r = mesh("slots").get("/internal/zones/lookup", params={"postcode": postcode})
    assert r.status_code == 200, r.text
    return r.json()["zone"]


def psp_ledger(psp):
    return psp.get("/admin/ledger")


def psp_charges(psp, ref):
    return [c for c in psp_ledger(psp)["charges"] if str(c.get("order_ref")) == str(ref)]


def psp_refunds(psp, ref):
    return [r for r in psp_ledger(psp)["refunds"] if str(r.get("order_ref")) == str(ref)]


def user_orders_total(mesh, user_id):
    r = mesh("orders").get("/internal/orders", params={"user_id": user_id, "page": 1, "per_page": 100})
    assert r.status_code == 200, r.text
    return [o for o in r.json()["orders"] if o.get("user_id") == user_id]


# ------------------------------------------------------------------ mesh ingress log

class Ingress:
    """Mark the mesh ingress log now; later read the records written since."""

    def __init__(self):
        self.path = ARTIFACTS / "ingress.jsonl"
        time.sleep(0.3)  # let earlier calls' records land before the mark
        self.t0 = time.time()
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def since(self, settle_s=1.2):
        time.sleep(settle_s)  # mesh flushes every 0.5s
        assert self.path.exists(), f"no ingress log at {self.path} (set LARDER_EVAL_ARTIFACTS)"
        out = []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            for line in fh.read().decode("utf-8", "replace").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("ts_epoch", 0) >= self.t0:
                    out.append(rec)
        return out


# ------------------------------------------------------------------ FEAT-040 expiry probe

def iso_epoch(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def start_expiry_probe(base_url, mesh_slots_url, hold):
    """Background thread: just after the hold's expires_at (and before the slots sweeper,
    which runs once a minute, gets to it) try to check out with it. Result in SHARED."""
    import threading

    def run():
        try:
            time.sleep(max(0.0, iso_epoch(hold["expires_at"]) + 0.5 - time.time()))
            with httpx.Client(base_url=base_url, timeout=60) as c:
                st = httpx.get(f"{mesh_slots_url}/internal/slot-holds/{hold['hold_id']}", timeout=10)
                status_before = st.json().get("status") if st.status_code == 200 else None
                r = c.post("/api/checkout", json={"cart_id": hold["cart_id"], "hold_id": hold["hold_id"],
                                                  "payment_token": "tok_ok"})
            SHARED["early_result"] = {"status": r.status_code, "body": jbody(r), "hold_status_before": status_before}
        except Exception as e:  # noqa
            SHARED["early_result"] = {"status": None, "body": {"exception": repr(e)}}

    t = threading.Thread(target=run, name="feat040-expiry-probe", daemon=True)
    t.start()
    SHARED["early_thread"] = t
    return t


# ------------------------------------------------------------------ dates

def today_utc():
    return datetime.datetime.now(datetime.timezone.utc).date()


def parse_seed_expiry(raw):
    """Seed coupon expiry text to a date (None = never)."""
    if raw in (None, "", "never"):
        return None
    raw = str(raw)
    if "T" in raw:
        return datetime.date.fromisoformat(raw[:10])
    if "/" in raw:
        d, m, y = raw.split("/")
        return datetime.date(int(y), int(m), int(d))
    return datetime.date.fromisoformat(raw)
