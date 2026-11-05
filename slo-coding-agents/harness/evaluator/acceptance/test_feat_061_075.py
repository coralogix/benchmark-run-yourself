"""Acceptance tests FEAT-061..FEAT-075 (inventory, payments, fraud, ledger, tax, bus, slots, registration).

FEAT-072 (bus dead-letter queue) needs a failing subscriber and lives in test_zz_feat_b_faults.py.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from _feat_helpers_b import (body_of, cancel_order, error_of, order_internal, place_order, pooled_user, psp_ledger, qty_for, rhu,
                             today_utc, uid)
from conftest import mesh_url, wait_until


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_timestamp(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- FEAT-061

@pytest.mark.item("FEAT-061")
def test_feat061_restock_increases_on_hand(mesh, catalog):
    sku = catalog.pick(1)[0]["sku"]
    inv = mesh("inventory")
    before = inv.get(f"/internal/inventory/{sku}").json()
    r = inv.post(f"/internal/inventory/{sku}/restock", json={"qty": 7})
    assert 200 <= r.status_code < 300, f"{r.status_code} {r.text[:200]}"
    b = r.json()
    exp = {"sku": sku, "on_hand": before["on_hand"] + 7, "reserved": before["reserved"],
           "available": before["available"] + 7}
    assert {k: b.get(k) for k in exp} == exp, b
    assert {k: v for k, v in inv.get(f"/internal/inventory/{sku}").json().items() if k in exp} == exp
    for bad in (0, -3):
        r = inv.post(f"/internal/inventory/{sku}/restock", json={"qty": bad})
        assert 400 <= r.status_code < 500, f"qty={bad}: {r.status_code} {r.text[:200]}"
    assert inv.get(f"/internal/inventory/{sku}").json()["on_hand"] == exp["on_hand"]


@pytest.mark.item("FEAT-061")
def test_feat061_concurrent_restocks_and_reservations(mesh, catalog):
    sku = catalog.pick(1, min_stock=1000)[0]["sku"]
    base = mesh_url("inventory")
    before = mesh("inventory").get(f"/internal/inventory/{sku}").json()
    ref = uid("acc-feat061-")

    def restock(_):
        return httpx.post(f"{base}/internal/inventory/{sku}/restock", json={"qty": 5}, timeout=30).status_code

    def reserve(_):
        return httpx.post(f"{base}/internal/inventory/reservations", json={"sku": sku, "qty": 1, "order_ref": ref},
                          timeout=30).status_code

    jobs = [restock] * 20 + [reserve] * 20
    with ThreadPoolExecutor(max_workers=16) as ex:
        codes = list(ex.map(lambda f: f(None), jobs))
    try:
        assert all(200 <= c < 300 for c in codes[:20]), codes[:20]
        assert all(c == 201 for c in codes[20:]), codes[20:]
        after = mesh("inventory").get(f"/internal/inventory/{sku}").json()
        assert after["on_hand"] == before["on_hand"] + 100, (before, after)
        assert after["reserved"] == before["reserved"] + 20, (before, after)
        assert after["available"] == after["on_hand"] - after["reserved"]
    finally:
        mesh("inventory").post("/internal/inventory/releases", json={"order_ref": ref,
                                                                     "items": [{"sku": sku, "qty": 20}]})


# ---------------------------------------------------------------- FEAT-062

def _availability(mesh, catalog) -> dict:
    r = mesh("inventory").post("/internal/inventory/availability", json={"skus": sorted(catalog.stock)})
    return {k: v["available"] for k, v in r.json().items()}


@pytest.mark.item("FEAT-062")
def test_feat062_low_stock_report(mesh, catalog):
    inv = mesh("inventory")
    r = inv.get("/internal/inventory/low-stock", params={"threshold": 60})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    avail = _availability(mesh, catalog)
    got = [(i["sku"], i["available"]) for i in items]
    assert [a for _, a in got] == sorted(a for _, a in got), f"not lowest first: {got}"
    assert {s for s, _ in got} == {s for s, a in avail.items() if a <= 60}, got
    assert all(avail[s] == a for s, a in got), got
    assert len(got) == len({s for s, _ in got}), "duplicate SKUs"
    # default threshold is 10
    d = inv.get("/internal/inventory/low-stock").json()["items"]
    assert {i["sku"] for i in d} == {s for s, a in avail.items() if a <= 10}, d


# ---------------------------------------------------------------- FEAT-063

@pytest.mark.item("FEAT-063")
def test_feat063_public_stock(edge, mesh, catalog):
    sku = catalog.pick(1, reuse=True)[0]["sku"]
    r = edge.get(f"/api/products/{sku}/stock")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    inv = mesh("inventory").get(f"/internal/inventory/{sku}").json()
    assert r.json().get("sku") == sku and r.json().get("available") == inv["available"], r.json()
    assert edge.get("/api/products/SKU-99999/stock").status_code == 404


# ---------------------------------------------------------------- FEAT-064

@pytest.mark.item("FEAT-064")
def test_feat064_payments_by_order(mesh, shop, users, catalog):
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    o = order_internal(mesh, ref)
    r = mesh("payments").get(f"/internal/payments/orders/{ref}")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    b = r.json()
    assert b.get("order_ref") == ref
    assert isinstance(b.get("refunds"), list) and b["refunds"] == [], b
    assert len(b.get("charges") or []) == 1, b
    c = b["charges"][0]
    assert c.get("charge_id") == o["charge_id"] and c.get("amount_pence") == o["total_pence"], (c, o)
    assert is_timestamp(c.get("at")), c


# ---------------------------------------------------------------- FEAT-065

def _psp_refunds(psp, ref):
    return [x for x in psp_ledger(psp)["refunds"] if str(x.get("order_ref")) == ref]


@pytest.mark.item("FEAT-065")
def test_feat065_refund_api(mesh, shop, users, catalog, psp):
    u = pooled_user(users)
    [p] = catalog.pick(1)
    o = place_order(shop, u, [(p["sku"], qty_for(p, 2500))])
    ref, total = o["order_ref"], o["total_pence"]
    pay = mesh("payments")

    def refund(amount, key):
        return pay.post("/internal/payments/refunds", json={"order_ref": ref, "amount_pence": amount},
                        headers={"Idempotency-Key": key})

    k = uid("acc-rf-")
    r = refund(100, k)
    assert r.status_code == 201 and isinstance((body_of(r) or {}).get("refund_id"), str), f"{r.status_code} {r.text}"
    refund(100, k)  # client retry of the same request
    assert sum(x["amount_pence"] for x in _psp_refunds(psp, ref)) == 100, _psp_refunds(psp, ref)

    r = refund(total - 100 + 1, uid("acc-rf-"))
    assert r.status_code == 409 and error_of(r) == "exceeds_captured", f"{r.status_code} {r.text}"
    r = refund(total - 100, uid("acc-rf-"))
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    r = refund(1, uid("acc-rf-"))
    assert r.status_code == 409 and error_of(r) == "exceeds_captured", f"{r.status_code} {r.text}"
    refunds = _psp_refunds(psp, ref)
    assert sum(x["amount_pence"] for x in refunds) == total and len(refunds) == 2, refunds


# ---------------------------------------------------------------- FEAT-066

@pytest.mark.item("FEAT-066")
def test_feat066_fraud_rule_hits(mesh):
    fraud = mesh("fraud")

    def rules():
        r = fraud.get("/internal/fraud/rules")
        assert r.status_code == 200
        rs = r.json()["rules"]
        assert rs and all(isinstance(x.get("name"), str) and x["name"] and is_int(x.get("hits")) for x in rs), rs
        return {x["name"]: x["hits"] for x in rs}

    before = rules()
    s = fraud.post("/internal/fraud/score", json={"order_ref": uid("acc-fr-"), "email": f"{uid()}@example.test",
                                                  "user_id": "u-acc", "amount_pence": 30000,
                                                  "token": "tok_ok"}).json()
    assert s.get("reasons"), s
    after = rules()
    inc = {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}
    assert sum(inc.values()) == len(s["reasons"]) and all(v == 1 for v in inc.values()), (s["reasons"], inc)


# ---------------------------------------------------------------- FEAT-067

def _psp_day(psp, date):
    led = psp_ledger(psp)
    cap = sum(c["amount_pence"] for c in led["charges"] if str(c.get("created_at", ""))[:10] == date)
    ref = sum(x["amount_pence"] for x in led["refunds"] if str(x.get("created_at", ""))[:10] == date)
    return {"captured_pence": cap, "refunded_pence": ref, "net_pence": cap - ref}


@pytest.mark.item("FEAT-067")
def test_feat067_daily_ledger_agrees_with_psp(mesh, shop, users, catalog, psp):
    """The day's ledger totals move exactly as the PSP's records do while we charge (and, when the
    FEAT-065 refund API exists, refund). Compared as deltas so earlier history can't interfere."""
    day = today_utc()

    def ledger(date):
        r = mesh("ledger").get("/internal/ledger/daily", params={"date": date})
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        b = r.json()
        assert b.get("date") == date, b
        out = {k: b.get(k) for k in ("captured_pence", "refunded_pence", "net_pence")}
        assert all(is_int(v) for v in out.values()) and out["net_pence"] == out["captured_pence"] - out["refunded_pence"], b
        return out

    def delta(a, b):
        return {k: b[k] - a[k] for k in a}

    l0, p0 = ledger(day), _psp_day(psp, day)
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    mesh("payments").post("/internal/payments/refunds", json={"order_ref": ref, "amount_pence": 100},
                          headers={"Idempotency-Key": uid()})
    assert wait_until(lambda: delta(l0, ledger(day)) == delta(p0, _psp_day(psp, day)), 20), (
        delta(l0, ledger(day)), delta(p0, _psp_day(psp, day)))
    assert delta(p0, _psp_day(psp, day))["captured_pence"] > 0
    assert ledger("2001-01-01") == {"captured_pence": 0, "refunded_pence": 0, "net_pence": 0}


# ---------------------------------------------------------------- FEAT-068

@pytest.mark.item("FEAT-068")
def test_feat068_refunds_reach_the_ledger(edge, mesh, shop, users, catalog):
    """Depends on a refund path: FEAT-065 (refund API) and/or FEAT-002 (cancellation refunds).
    Every one that exists must write a negative ledger entry for the order; if neither exists, fails."""
    u = pooled_user(users)
    p, q = catalog.pick(2)
    refs = []
    a = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    r = mesh("payments").post("/internal/payments/refunds", json={"order_ref": a, "amount_pence": 100},
                              headers={"Idempotency-Key": uid()})
    if r.status_code == 201:
        refs.append(a)
    b = place_order(shop, u, [(q["sku"], qty_for(q))])["order_ref"]
    if cancel_order(edge, b).status_code == 200:
        refs.append(b)
    assert refs, "no refund path exists (neither FEAT-065 refunds nor FEAT-002 cancellation)"

    def negative(ref):
        es = mesh("ledger").get("/internal/ledger/entries", params={"order_ref": ref}).json()["entries"]
        return [e for e in es if (e.get("amount_pence") or 0) < 0 and e.get("order_ref") == ref]

    for ref in refs:
        assert wait_until(lambda: negative(ref), 20), f"no negative ledger entry for refunded order {ref}"


# ---------------------------------------------------------------- FEAT-069

@pytest.mark.item("FEAT-069")
def test_feat069_public_vat_rates(edge, mesh):
    r = edge.get("/api/tax/rates")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    assert r.json() == {"zero": 0, "reduced": 5, "standard": 20}
    assert r.json() == mesh("tax").get("/internal/tax/rates").json()


# ---------------------------------------------------------------- FEAT-070

@pytest.mark.item("FEAT-070")
@pytest.mark.parametrize("unit,coupon", [(1000, None), (5000, None),
                                         (1000, {"code": "FREESHIP", "percent_off": 0, "free_delivery": True})])
def test_feat070_delivery_vat(mesh, unit, coupon):
    r = mesh("tax").post("/internal/tax/totals", json={
        "items": [{"sku": "SKU-0001", "qty": 1, "unit_price_pence": unit}], "coupon": coupon})
    assert r.status_code == 200
    b = r.json()
    fee = b["delivery_fee_pence"]
    assert fee == (399 if unit < 4000 and not coupon else 0)
    assert b.get("delivery_vat_pence") == rhu(Decimal(fee) * 20 / 120), b   # 399 gives 66.5, rounded to 67


# FEAT-071 (event bus lag per topic) needs a stalled subscriber: see test_zz_feat_b_faults.py


# ---------------------------------------------------------------- FEAT-073

@pytest.mark.item("FEAT-073")
def test_feat073_adjust_slot_capacity(mesh):
    slots = mesh("slots")
    zone_slots = slots.get("/internal/slots", params={"zone": "Z2"}).json()["slots"]
    slot = [s for s in zone_slots if s["capacity"] > s["booked"]][-1]
    sid, original = slot["slot_id"], slot["capacity"]

    def current():
        return next(s for s in slots.get("/internal/slots", params={"zone": "Z2"}).json()["slots"]
                    if s["slot_id"] == sid)

    h = slots.post("/internal/slot-holds", json={"slot_id": sid, "cart_ref": uid("acc-feat073-")})
    assert h.status_code == 201
    try:
        booked = current()["booked"]
        r = slots.patch(f"/internal/slots/{sid}", json={"capacity": booked + 3})
        assert 200 <= r.status_code < 300, f"{r.status_code} {r.text[:200]}"
        assert current()["capacity"] == booked + 3
        r = slots.patch(f"/internal/slots/{sid}", json={"capacity": booked})
        assert 200 <= r.status_code < 300 and current()["capacity"] == booked, f"{r.status_code} {r.text[:200]}"
        r = slots.patch(f"/internal/slots/{sid}", json={"capacity": booked - 1})
        assert r.status_code == 409 and error_of(r) == "below_booked", f"{r.status_code} {r.text[:200]}"
        assert current()["capacity"] == booked
    finally:
        slots.patch(f"/internal/slots/{sid}", json={"capacity": original})
        slots.delete(f"/internal/slot-holds/{h.json()['hold_id']}")


# ---------------------------------------------------------------- FEAT-074

@pytest.mark.item("FEAT-074")
def test_feat074_full_slots_hidden_unless_asked(edge, mesh, catalog):
    """Today's first slot in every zone is full in the seed data, so each zone has full slots."""
    for z in catalog.zones:
        pc = z["postcodes"][0]
        raw = [s for s in mesh("slots").get("/internal/slots", params={"zone": z["zone"]}).json()["slots"]]
        full = {s["slot_id"] for s in raw if s["booked"] >= s["capacity"]}
        assert full, f"precondition: zone {z['zone']} has a full slot"
        r = edge.get("/api/delivery/slots", params={"postcode": pc})
        assert r.status_code == 200
        shown = {s["slot_id"] for s in r.json()["slots"]}
        assert shown == {s["slot_id"] for s in raw} - full, (z["zone"], sorted(shown & full))
        r = edge.get("/api/delivery/slots", params={"postcode": pc, "include_full": "true"})
        assert r.status_code == 200
        assert {s["slot_id"] for s in r.json()["slots"]} == {s["slot_id"] for s in raw}, z["zone"]


# ---------------------------------------------------------------- FEAT-075

@pytest.mark.item("FEAT-075")
def test_feat075_register_then_checkout(edge, shop, catalog):
    email = f"{uid('new.customer.')}@example.test"
    pc = catalog.postcode("Z1")
    r = edge.post("/api/account", json={"name": "New Customer", "email": email, "postcode": pc})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    user_id = r.json().get("user_id")
    assert isinstance(user_id, str) and user_id
    acc = edge.get(f"/api/account/{user_id}")
    assert acc.status_code == 200 and acc.json()["email"] == email and acc.json()["name"] == "New Customer", acc.text
    [p] = catalog.pick(1)
    o = place_order(shop, {"user_id": user_id, "postcode": pc}, [(p["sku"], qty_for(p))])
    assert edge.get(f"/api/orders/{o['order_ref']}").json()["user_id"] == user_id


@pytest.mark.item("FEAT-075")
def test_feat075_validation(edge, catalog, users):
    email = f"{uid('dupe.')}@example.test"
    pc = catalog.postcode("Z2")
    r = edge.post("/api/account", json={"name": "Dupe One", "email": email, "postcode": pc})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    for e in (email.upper(), email.capitalize()):
        r = edge.post("/api/account", json={"name": "Dupe Two", "email": e, "postcode": pc})
        assert r.status_code == 409 and error_of(r) == "email_taken", f"{e}: {r.status_code} {r.text[:200]}"
    seeded = users.legacy[0]["email"]
    r = edge.post("/api/account", json={"name": "Dupe Three", "email": seeded.upper(), "postcode": pc})
    assert r.status_code == 409 and error_of(r) == "email_taken", f"{r.status_code} {r.text[:200]}"
    for bad in ("not-an-email", "missing-at.example.test"):
        r = edge.post("/api/account", json={"name": "Bad", "email": bad, "postcode": pc})
        assert r.status_code == 400 and error_of(r) == "invalid_email", f"{bad!r}: {r.status_code} {r.text[:200]}"
    for bad in ("ZZ9 9ZZ", "not a postcode"):
        r = edge.post("/api/account", json={"name": "Bad", "email": f"{uid()}@example.test", "postcode": bad})
        assert r.status_code == 400 and error_of(r) == "invalid_postcode", f"{bad!r}: {r.status_code} {r.text[:200]}"
