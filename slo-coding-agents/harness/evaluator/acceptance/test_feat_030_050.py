"""Acceptance tests for FEAT-030..FEAT-050 (evaluator-only, black-box).

FEAT-040's expired-hold check lives in test_zz_feat_a.py (it needs a hold that is
at least one hold-TTL old)."""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time
import uuid

import pytest

import _feat_helpers_a as H
from conftest import HC_SKUS

PRODUCT_FIELDS = ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock")


# =========================================================================== FEAT-030

def _suggest_matches(catalog, prefix):
    pf = prefix.casefold()
    return {p["name"] for p in catalog.products if any(w.casefold().startswith(pf) for w in p["name"].split())}


@pytest.mark.item("FEAT-030")
def test_feat_030_suggestions(edge, catalog):
    for prefix in ("ap", "Or", "WH", "fre", "milk", "zzq"):
        r = edge.get("/api/search/suggest", params={"q": prefix})
        assert r.status_code == 200, f"{prefix}: {r.status_code} {r.text}"
        sug = r.json().get("suggestions")
        assert isinstance(sug, list), r.json()
        matches = _suggest_matches(catalog, prefix)
        assert len(sug) == min(8, len(matches)), (prefix, sug, len(matches))
        assert len(set(sug)) == len(sug), sug
        assert set(sug) <= matches, (prefix, set(sug) - matches)
        assert sug == sorted(sug) or sug == sorted(sug, key=str.casefold), sug
    for short in ("a", "", "W"):
        r = edge.get("/api/search/suggest", params={"q": short})
        assert r.status_code == 200 and r.json().get("suggestions") == [], (short, r.text)


@pytest.mark.item("FEAT-030")
def test_feat_030_suggest_latency(edge):
    prefixes = ["ap", "or", "mi", "ch", "br", "fr", "va", "cl", "li", "or"]
    i = iter(range(10 ** 6))

    def call():
        r = edge.get("/api/search/suggest", params={"q": prefixes[next(i) % len(prefixes)]})
        assert r.status_code == 200 and "suggestions" in r.json()

    lat = H.timed(call, 40)
    assert H.p95(lat) < 150, f"p95 {H.p95(lat):.0f}ms"


# =========================================================================== FEAT-031

@pytest.mark.item("FEAT-031")
def test_feat_031_search_pagination(edge):
    q = "dairy"
    r = edge.get("/api/search", params={"q": q})
    assert r.status_code == 200
    b = r.json()
    assert b.get("page") == 1 and b.get("per_page") == 20, {k: v for k, v in b.items() if k != "results"}
    total = b.get("total")
    assert isinstance(total, int) and total > 20, total
    assert len(b["results"]) == 20
    seen = []
    page = 1
    while len(seen) < total and page < 20:
        r = edge.get("/api/search", params={"q": q, "page": page})
        rb = r.json()
        assert rb.get("page") == page and rb.get("total") == total
        seen += [x["sku"] for x in rb["results"]]
        page += 1
    assert len(seen) == total and len(set(seen)) == total, (len(seen), len(set(seen)), total)
    r = edge.get("/api/search", params={"q": q, "per_page": 7, "page": 2})
    rb = r.json()
    assert rb.get("per_page") == 7 and [x["sku"] for x in rb["results"]] == seen[7:14]
    r = edge.get("/api/search", params={"q": q, "per_page": 50})
    rb = r.json()
    assert rb.get("per_page") == 50 and len(rb["results"]) == min(50, total)
    r = edge.get("/api/search", params={"q": q, "per_page": 500})
    assert r.status_code == 400 or (r.status_code == 200 and r.json().get("per_page") == 50
                                    and len(r.json()["results"]) <= 50), r.text[:300]


# =========================================================================== FEAT-032

@pytest.mark.item("FEAT-032")
def test_feat_032_long_queries_rejected(edge):
    long_q = ("apples " * 20)[:101]
    assert len(long_q) == 101
    r = edge.get("/api/search", params={"q": long_q})
    assert r.status_code == 400 and H.err(r) == "query_too_long", f"{r.status_code} {r.text[:200]}"
    r = edge.get("/api/search", params={"q": "x" * 5000})
    assert r.status_code == 400 and H.err(r) == "query_too_long", f"{r.status_code} {r.text[:200]}"
    ok_q = long_q[:100]
    r = edge.get("/api/search", params={"q": ok_q})
    assert r.status_code == 200, r.text[:200]


# =========================================================================== FEAT-033

@pytest.mark.item("FEAT-033")
def test_feat_033_normalised_queries(edge):
    groups = [("soap 4", ["  SOAP   4 ", "Soap  4", "soap\t4"]),
              ("orange juice", ["Orange  Juice", "  ORANGE juice  "]),
              ("whole milk", ["WHOLE   MILK", " whole milk "]),
              ("apples", ["  APPLES ", "Apples"])]
    for canonical, variants in groups:
        base = edge.get("/api/search", params={"q": canonical})
        assert base.status_code == 200
        want = [x["sku"] for x in base.json()["results"]]
        assert want, canonical
        for v in variants:
            r = edge.get("/api/search", params={"q": v})
            assert r.status_code == 200
            assert [x["sku"] for x in r.json()["results"]] == want, (canonical, v)


# =========================================================================== FEAT-034

@pytest.mark.item("FEAT-034")
def test_feat_034_slots_by_date(edge, catalog):
    pc = catalog.postcode("Z3")
    allslots = H.list_slots(edge, pc)["slots"]
    dates = sorted({s["date"] for s in allslots})
    assert len(dates) >= 2
    for d in dates[:2]:
        r = edge.get("/api/delivery/slots", params={"postcode": pc, "date": d})
        assert r.status_code == 200, r.text
        got = r.json()["slots"]
        assert got and all(s["date"] == d for s in got), got
        assert sorted(s["slot_id"] for s in got) == sorted(s["slot_id"] for s in allslots if s["date"] == d)
    for bad in ("2026-02-30", "21/10/2026", "tomorrow", "20261021"):
        r = edge.get("/api/delivery/slots", params={"postcode": pc, "date": bad})
        assert r.status_code == 400 and H.err(r) == "invalid_date", f"{bad}: {r.status_code} {r.text}"
    assert len(H.list_slots(edge, pc)["slots"]) == len(allslots)


# =========================================================================== FEAT-035

@pytest.mark.item("FEAT-035")
def test_feat_035_release_hold(edge, mesh, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    zone = H.zone_of(mesh, user["postcode"])
    hid, slot = H.hold(edge, cid, user["postcode"])
    booked = H.zone_slots(mesh, zone)[slot["slot_id"]]["booked"]
    r = edge.delete(f"/api/delivery/holds/{hid}")
    assert r.status_code == 204, f"{r.status_code} {r.text}"
    assert H.zone_slots(mesh, zone)[slot["slot_id"]]["booked"] == booked - 1
    r = edge.delete(f"/api/delivery/holds/{hid}")
    assert r.status_code == 404, r.text
    r = edge.delete("/api/delivery/holds/H-NOSUCHHOLD")
    assert r.status_code == 404, r.text


# =========================================================================== FEAT-036

@pytest.mark.item("FEAT-036")
def test_feat_036_slot_details_on_hold(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    slot = next(s for s in H.list_slots(edge, user["postcode"])["slots"] if s.get("available"))
    r = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": slot["slot_id"]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b.get("hold_id") and b.get("expires_at")
    s = b.get("slot") or {}
    assert (s.get("slot_id"), s.get("date"), s.get("window")) == (slot["slot_id"], slot["date"], slot["window"]), b


# =========================================================================== FEAT-037

DOWNSTREAM = {"cart", "pricing", "accounts", "orders", "payments", "inventory", "slots", "tax", "fraud",
              "promotions", "catalog", "ledger"}


def _fraud_appends(mesh):
    r = mesh("fraud").get("/internal/fraud/history/stats")
    assert r.status_code == 200, r.text
    return int(r.json().get("appends") or 0)


@pytest.mark.item("FEAT-037")
def test_feat_037_fraud_prescreen(edge, users, catalog, mesh):
    """A checkout attempt is pre-screened by fraud before the order exists, as well as at charge time."""
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    hid, _ = H.hold(edge, cid, user["postcode"])
    before = _fraud_appends(mesh)
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    scored = _fraud_appends(mesh) - before
    assert scored >= 2, f"fraud scored this checkout {scored} time(s); expected a pre-screen and the charge-time check"


@pytest.mark.item("FEAT-037")
def test_feat_037_payment_token_required(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    hid, _ = H.hold(edge, cid, user["postcode"])
    mark = H.Ingress()
    r = edge.post("/api/checkout", json={"cart_id": cid, "hold_id": hid})
    assert r.status_code == 400 and H.err(r) == "missing_payment_token", f"{r.status_code} {r.text}"
    calls = [x for x in mark.since() if x["service"] in DOWNSTREAM]
    assert not calls, [(x["service"], x["route"]) for x in calls]
    # the cart is untouched and checks out normally with a token
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text


# =========================================================================== FEAT-038

@pytest.mark.item("FEAT-038")
def test_feat_038_idempotent_retry(edge, mesh, users, catalog, psp):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    hid, _ = H.hold(edge, cid, user["postcode"])
    key = "acc-" + uuid.uuid4().hex
    r1 = H.checkout(edge, cid, hid, headers={"Idempotency-Key": key})
    assert r1.status_code == 201, r1.text
    r2 = H.checkout(edge, cid, hid, headers={"Idempotency-Key": key})
    assert r2.status_code == 201, f"replay: {r2.status_code} {r2.text}"
    assert r2.json()["order_ref"] == r1.json()["order_ref"]
    assert r2.json() == r1.json() or r2.json()["total_pence"] == r1.json()["total_pence"], (r1.json(), r2.json())
    time.sleep(1)
    assert len(H.psp_charges(psp, r1.json()["order_ref"])) == 1
    assert len(H.user_orders_total(mesh, user["user_id"])) == 1
    # without a key, the closed cart is still refused as before
    r3 = H.checkout(edge, cid, hid)
    assert r3.status_code == 409, r3.text


@pytest.mark.item("FEAT-038")
def test_feat_038_concurrent_same_key(edge, mesh, users, catalog, psp):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    hid, _ = H.hold(edge, cid, user["postcode"])
    key = "acc-" + uuid.uuid4().hex
    with cf.ThreadPoolExecutor(4) as ex:
        rs = list(ex.map(lambda _: H.checkout(edge, cid, hid, headers={"Idempotency-Key": key}), range(4)))
    ok = [r.json()["order_ref"] for r in rs if r.status_code == 201]
    assert ok and len(set(ok)) == 1, [(r.status_code, r.text) for r in rs]
    time.sleep(1.5)
    orders = H.user_orders_total(mesh, user["user_id"])
    assert len(orders) == 1, [o["order_ref"] for o in orders]
    assert len(H.psp_charges(psp, ok[0])) == 1
    r = H.checkout(edge, cid, hid, headers={"Idempotency-Key": key})
    assert r.status_code == 201 and r.json()["order_ref"] == ok[0], r.text


# =========================================================================== FEAT-039

@pytest.mark.item("FEAT-039")
def test_feat_039_minimum_order_value(edge, mesh, users, catalog):
    user = users.fresh()
    small = catalog.pick(1, not_tag="3for2", max_price=450)[0]
    cid = H.new_cart(edge, user["user_id"], [(small["sku"], 2)])
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 400 and H.err(r) == "below_minimum" and r.json().get("minimum_pence") == 1000, \
        f"{r.status_code} {r.text}"
    sug = r.json().get("suggestions")
    assert isinstance(sug, list) and 1 <= len(sug) <= 3, f"suggestions: {sug!r}"
    assert all(isinstance(x, dict) and x.get("sku") and x["sku"] != small["sku"] for x in sug), sug

    # goods >= 1000 before the coupon but < 1000 after it
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < 200:
            continue
        q = next((q for q in range(1, 25) if 1000 <= q * p["price_pence"] <= 1105), None)
        if q is None:
            continue
        cid = H.new_cart(edge, user["user_id"], [(p["sku"], q)], coupon="WELCOME10")
        cart = H.get_cart(edge, cid)
        if not H.consistent_total(cart, 10) or cart["subtotal_pence"] - cart["discount_pence"] >= 1000:
            continue
        catalog._used.add(p["sku"])
        hid2, _ = H.hold(edge, cid, user["postcode"])
        r = H.checkout(edge, cid, hid2)
        assert r.status_code == 400 and H.err(r) == "below_minimum", f"{r.status_code} {r.text}"
        break
    else:
        raise AssertionError("no basket for the coupon case")
    assert H.user_orders_total(mesh, user["user_id"]) == []

    # exactly 1000 is fine
    cid = H.new_cart(edge, user["user_id"], [H.exact_goods_line(catalog, 1000)])
    assert H.get_cart(edge, cid)["subtotal_pence"] == 1000
    hid3, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid3)
    assert r.status_code == 201, r.text


# =========================================================================== FEAT-041

@pytest.mark.item("FEAT-041")
def test_feat_041_estimated_delivery(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    res = H.place(edge, user, lines)
    est = res["order"].get("estimated_delivery") or {}
    assert (est.get("date"), est.get("window")) == (res["slot"]["date"], res["slot"]["window"]), res["order"]
    assert res["order"]["status"] == "confirmed" and res["order"]["order_ref"]


# =========================================================================== FEAT-042

@pytest.mark.item("FEAT-042")
def test_feat_042_status_history(edge, mesh, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    ref = H.place(edge, user, lines)["order"]["order_ref"]
    r = edge.get(f"/api/orders/{ref}/status-history")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    hist = r.json().get("history")
    assert [h["status"] for h in hist] == ["confirmed"] and hist[0].get("at"), hist
    time.sleep(0.05)
    H.dispatch(mesh, ref)
    hist = edge.get(f"/api/orders/{ref}/status-history").json()["history"]
    assert [h["status"] for h in hist] == ["confirmed", "dispatched"], hist
    ats = [h["at"] for h in hist]
    assert ats == sorted(ats) and ats[0] != ats[1], ats

    ref2 = H.place(edge, user, lines)["order"]["order_ref"]
    rc = edge.post(f"/api/orders/{ref2}/cancel", json={})
    if H.feature_present(rc) and rc.status_code == 200:
        hist = edge.get(f"/api/orders/{ref2}/status-history").json()["history"]
        assert [h["status"] for h in hist] == ["confirmed", "cancelled"], hist


# =========================================================================== FEAT-043

@pytest.mark.item("FEAT-043")
def test_feat_043_reorder(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 2)
    res = H.place(edge, user, lines)
    r = edge.post(f"/api/orders/{res['order']['order_ref']}/reorder", json={})
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    cid = r.json().get("cart_id")
    assert cid and cid != res["cart_id"]
    cart = H.get_cart(edge, cid)
    assert cart["user_id"] == user["user_id"] and cart.get("status", "open") == "open"
    assert sorted((i["sku"], i["qty"]) for i in cart["items"]) == sorted(lines)
    # the new cart is a normal cart
    hid, _ = H.hold(edge, cid, user["postcode"])
    assert H.checkout(edge, cid, hid).status_code == 201


# =========================================================================== FEAT-044

@pytest.mark.item("FEAT-044")
def test_feat_044_also_bought_endpoint(edge, catalog):
    sku = catalog.products[10]["sku"]
    r = edge.get(f"/api/products/{sku}/also-bought")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    prods = r.json().get("products")
    assert isinstance(prods, list) and 0 < len(prods) <= 6, prods
    for p in prods:
        assert all(k in p for k in PRODUCT_FIELDS), p
    r = edge.get(f"/api/products/{sku}/also-bought", params={"limit": 2})
    assert r.status_code == 200 and 0 < len(r.json()["products"]) <= 2
    r = edge.get(f"/api/products/{sku}/also-bought", params={"limit": 12})
    assert r.status_code == 200 and len(prods) <= len(r.json()["products"]) <= 12
    r = edge.get(f"/api/products/{sku}/also-bought", params={"limit": 50})
    assert r.status_code == 400 or (r.status_code == 200 and len(r.json()["products"]) <= 12), r.text[:200]
    # product detail unchanged
    assert "also_bought" in edge.get(f"/api/products/{sku}").json()


# =========================================================================== FEAT-045

@pytest.mark.item("FEAT-045")
def test_feat_045_no_out_of_stock_recommendations(edge, mesh, catalog):
    base = catalog.products[20]["sku"]
    also = edge.get(f"/api/products/{base}").json().get("also_bought") or []
    assert also, "product has no recommendations"
    victim = also[0]["sku"]
    with H.drained(mesh, victim):
        d = edge.get(f"/api/products/{base}").json()
        recs = d.get("also_bought")
        assert recs, d
        skus = [p["sku"] for p in recs]
        assert victim not in skus, skus
        r = mesh("inventory").post("/internal/inventory/availability", json={"skus": skus})
        assert all(v["available"] > 0 for v in r.json().values()), r.json()
        assert all(p.get("in_stock") is True for p in recs), recs
        r = edge.get(f"/api/products/{base}/also-bought")
        if H.feature_present(r):
            assert r.status_code == 200
            skus = [p["sku"] for p in r.json()["products"]]
            assert victim not in skus, skus


# =========================================================================== FEAT-046

@pytest.mark.item("FEAT-046")
def test_feat_046_price_appropriate_recommendations(edge, catalog):
    """also_bought never exceeds 2x the viewed price, and recommendations are not simply removed."""
    with_recs = 0
    for p in [x for x in catalog.products[::20] if x["sku"] not in HC_SKUS]:
        d = edge.get(f"/api/products/{p['sku']}").json()
        recs = d.get("also_bought") or []
        too_dear = [(x["sku"], x["price_pence"]) for x in recs if x["price_pence"] > 2 * d["price_pence"]]
        assert not too_dear, (p["sku"], d["price_pence"], too_dear)
        with_recs += bool(recs)
    assert with_recs >= 20, f"only {with_recs} of the sampled products still had recommendations"


# =========================================================================== FEAT-047

def _fmt(pence):
    return "£%d.%02d" % (pence // 100, pence % 100)


@pytest.mark.item("FEAT-047")
def test_feat_047_price_display(edge, catalog):
    items = edge.get("/api/products", params={"per_page": 100}).json()["items"]
    for p in items:
        assert p.get("price_display") == _fmt(p["price_pence"]), p
    cheap = min(catalog.products, key=lambda p: p["price_pence"])
    for sku in (cheap["sku"], catalog.products[0]["sku"]):
        d = edge.get(f"/api/products/{sku}").json()
        assert d.get("price_display") == _fmt(d["price_pence"]), d


# =========================================================================== FEAT-048

@pytest.mark.item("FEAT-048")
def test_feat_048_active_promotions(edge):
    seed = json.loads((H.Path(__file__).resolve().parents[3] / "baseline" / "commerce" / "seed" / "coupons.json").read_text())
    r = edge.get("/api/promotions")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    promos = r.json().get("promotions")
    assert isinstance(promos, list), r.json()
    by_code = {p.get("code"): p for p in promos}
    today = H.today_utc()
    for c in seed:
        exp = H.parse_seed_expiry(c.get("expires"))
        live = exp is None or exp >= today
        if not live:
            assert c["code"] not in by_code, c["code"]
            continue
        assert c["code"] in by_code, c["code"]
        p = by_code[c["code"]]
        assert p.get("expires") == (exp.isoformat() if exp else None), (c, p)
        assert p.get("percent_off") == c.get("percent_off", 0), (c, p)
        assert p.get("min_spend_pence") == c.get("min_spend_pence", 0), (c, p)
    for p in promos:
        assert set(p) >= {"code", "percent_off", "min_spend_pence", "expires"}, p
        assert p["expires"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", p["expires"]), p
        if p["expires"]:
            assert p["expires"] >= today.isoformat(), p


# =========================================================================== FEAT-049

@pytest.mark.item("FEAT-049")
def test_feat_049_coupon_expiry_on_cart(edge, users, catalog):
    seed = json.loads((H.Path(__file__).resolve().parents[3] / "baseline" / "commerce" / "seed" / "coupons.json").read_text())
    today = H.today_utc()
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1, lo=2600, hi=3900)
    cid = H.new_cart(edge, user["user_id"], lines)
    assert H.get_cart(edge, cid).get("coupon_expires") is None
    checked = 0
    for c in seed:
        exp = H.parse_seed_expiry(c.get("expires"))
        if exp is not None and exp < today:
            continue
        r = edge.post(f"/api/carts/{cid}/coupon", json={"code": c["code"]})
        if c["code"] in ("WELCOME10", "SPRING15", "LARDER5", "FREESHIP"):
            assert r.status_code == 200, (c["code"], r.status_code, r.text)
        if r.status_code != 200:
            continue  # e.g. legacy-format expiries this build can't read, or min spend
        want = exp.isoformat() if exp else None
        assert r.json().get("coupon_expires") == want, (c["code"], r.json().get("coupon_expires"), want)
        assert H.get_cart(edge, cid).get("coupon_expires") == want
        checked += 1
    assert checked >= 4
    r = edge.delete(f"/api/carts/{cid}/coupon")
    assert r.status_code == 200 and r.json().get("coupon_expires") is None, r.text


# =========================================================================== FEAT-050

@pytest.mark.item("FEAT-050")
def test_feat_050_fiver(edge, users, catalog, psp):
    user = users.fresh()
    small, _ = H.regular_lines(catalog, 1, lo=1500, hi=2900)
    cid = H.new_cart(edge, user["user_id"], small)
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "FIVER"})
    assert r.status_code == 400 and H.err(r) == "min_spend", f"{r.status_code} {r.text}"

    big, _ = H.regular_lines(catalog, 1, lo=3100, hi=3900)
    cid = H.new_cart(edge, user["user_id"], big, coupon="FIVER")
    cart = H.get_cart(edge, cid)
    assert cart["discount_pence"] == 500 and cart["coupon"] == "FIVER", cart
    assert cart["total_pence"] == cart["subtotal_pence"] - 500 + cart["delivery_fee_pence"]
    assert cart["delivery_fee_pence"] == (399 if cart["subtotal_pence"] - 500 < 4000 else 0)
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    ref = r.json()["order_ref"]
    assert r.json()["total_pence"] == cart["total_pence"]
    assert H.order_view(edge, ref)["total_pence"] == cart["total_pence"]
    assert [c["amount_pence"] for c in H.psp_charges(psp, ref)] == [cart["total_pence"]]

    # boundary: exactly 3000 pence of goods qualifies
    cid = H.new_cart(edge, user["user_id"], [H.exact_goods_line(catalog, 3000)])
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "FIVER"})
    assert r.status_code == 200 and r.json()["discount_pence"] == 500, r.text
