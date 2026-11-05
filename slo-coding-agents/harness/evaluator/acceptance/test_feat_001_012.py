"""Acceptance tests for FEAT-001..FEAT-012 (evaluator-only, black-box)."""
from __future__ import annotations

import concurrent.futures as cf
import math
import time

import pytest

import _feat_helpers_a as H
from conftest import edge_url, mesh_url, wait_until


@pytest.fixture(scope="session", autouse=True)
def _early_slot_hold(edge, users, catalog):
    """FEAT-040 needs to check out with a hold just after it expires: take one as early as
    possible in the run and let a background thread try it at expiry (asserted in
    test_zz_feat_001_050.py)."""
    try:
        user = users.fresh()
        lines, _ = H.regular_lines(catalog, 1)
        cid = H.new_cart(edge, user["user_id"], lines)
        for s in H.list_slots(edge, user["postcode"])["slots"]:
            if s.get("available"):
                r = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": s["slot_id"]})
                if r.status_code == 201 and r.json().get("expires_at"):
                    hold = {"user": user, "cart_id": cid, "hold_id": r.json()["hold_id"],
                            "expires_at": r.json()["expires_at"]}
                    H.SHARED["early_hold"] = hold
                    H.start_expiry_probe(edge_url(), mesh_url("slots"), hold)
                    break
    except Exception:  # noqa - best effort; the FEAT-040 test reports what is missing
        pass
    yield


def _items(cart):
    return {i["sku"]: i for i in cart["items"]}


# =========================================================================== FEAT-001

@pytest.mark.item("FEAT-001")
def test_feat_001_multibuy_line_pricing_and_cart_fields(edge, users, catalog):
    user = users.fresh()
    mb = catalog.pick(1, tag="3for2")[0]
    other = catalog.pick(1, not_tag="3for2", max_price=1500)[0]

    # cart without any 3for2 product: discount field present and 0
    cid = H.new_cart(edge, user["user_id"], [(other["sku"], 2)])
    cart = H.get_cart(edge, cid)
    assert cart.get("multibuy_discount_pence") == 0, cart
    assert all("multibuy_free_units" in i for i in cart["items"]), cart["items"]
    assert cart["subtotal_pence"] == 2 * other["price_pence"]

    for q in (2, 3, 7):
        cid = H.new_cart(edge, user["user_id"], [(mb["sku"], q), (other["sku"], 2)])
        cart = H.get_cart(edge, cid)
        items = _items(cart)
        p = items[mb["sku"]]["unit_price_pence"]
        free = q // 3
        assert items[mb["sku"]].get("multibuy_free_units") == free, (q, items[mb["sku"]])
        assert items[mb["sku"]]["line_total_pence"] in ((q - free) * p, q * p), items[mb["sku"]]
        assert cart.get("multibuy_discount_pence") == free * p, (q, cart)
        # other products are unaffected
        o = items[other["sku"]]
        assert o.get("multibuy_free_units", 0) in (0, None), o
        assert o["line_total_pence"] == 2 * o["unit_price_pence"]
        # subtotal is after multi-buy savings
        assert cart["subtotal_pence"] == (q - free) * p + 2 * o["unit_price_pence"], (q, cart)
        assert cart["total_pence"] == cart["subtotal_pence"] - cart["discount_pence"] + cart["delivery_fee_pence"]


@pytest.mark.item("FEAT-001")
def test_feat_001_multibuy_with_coupon_and_delivery_threshold(edge, users, catalog):
    user = users.fresh()
    mb = catalog.pick(1, tag="3for2")[0]
    # a second 3for2 product for which some quantity crosses the delivery threshold only before multi-buy
    mb2 = next(x for x in catalog.products if "3for2" in x["tags"] and x["sku"] not in catalog._used
               and catalog.stock.get(x["sku"], 0) >= 200
               and any(q * x["price_pence"] >= 4000 and (q - q // 3) * x["price_pence"] < 4000 for q in range(3, 25)))
    catalog._used.add(mb2["sku"])

    # coupon percentage applies to the subtotal after multi-buy
    cid = H.new_cart(edge, user["user_id"], [(mb["sku"], 6)], coupon="WELCOME10")
    cart = H.get_cart(edge, cid)
    p = _items(cart)[mb["sku"]]["unit_price_pence"]
    assert cart.get("multibuy_discount_pence") == 2 * p, cart
    assert cart["subtotal_pence"] == 4 * p, cart
    # 10% of the post-multi-buy subtotal (penny rounding is REL-1's business, not this ticket's)
    assert abs(cart["discount_pence"] - 4 * p * 10 / 100) < 1, cart

    # delivery threshold uses the subtotal after multi-buy (and coupon): pick q so that the
    # undiscounted basket is >= 4000 but the charged one is < 4000
    p2 = mb2["price_pence"]
    q = next(q for q in range(3, 25) if q * p2 >= 4000 and (q - q // 3) * p2 < 4000)
    cid = H.new_cart(edge, user["user_id"], [(mb2["sku"], q)])
    cart = H.get_cart(edge, cid)
    charged = (q - q // 3) * _items(cart)[mb2["sku"]]["unit_price_pence"]
    assert cart["subtotal_pence"] == charged, cart
    assert cart["delivery_fee_pence"] == 399, cart
    assert cart["total_pence"] == charged + 399, cart


@pytest.mark.item("FEAT-001")
def test_feat_001_multibuy_checkout_total_matches_cart(edge, users, catalog, psp):
    user = users.fresh()
    mb = catalog.pick(1, tag="3for2")[0]
    other = H.pick_range(catalog, 1, 100, 900)[0]
    lines = [(mb["sku"], 3), (other["sku"], H.qty_for(other["price_pence"], 900, 1800))]
    res = H.place(edge, user, lines)
    cart = res["cart"]
    assert cart.get("multibuy_discount_pence", 0) > 0, cart
    order = res["order"]
    assert order["total_pence"] == cart["total_pence"], (order, cart)
    view = H.order_view(edge, order["order_ref"])
    assert view["total_pence"] == cart["total_pence"], view
    charges = H.psp_charges(psp, order["order_ref"])
    assert [c["amount_pence"] for c in charges] == [cart["total_pence"]], charges


# =========================================================================== FEAT-002

def _cancel(edge, ref):
    return edge.post(f"/api/orders/{ref}/cancel", json={})


@pytest.mark.item("FEAT-002")
def test_feat_002_cancel_refunds_exactly_once(edge, users, catalog, psp):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    order = H.place(edge, user, lines)["order"]
    ref, total = order["order_ref"], order["total_pence"]

    r = _cancel(edge, ref)
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    b = r.json()
    assert b.get("order_ref") == ref and b.get("status") == "cancelled", b
    assert b.get("refund_pence") == total, b
    assert H.order_view(edge, ref)["status"] == "cancelled"

    # second click: same result or 409 already_cancelled, never a second refund
    r2 = _cancel(edge, ref)
    assert r2.status_code in (200, 409), r2.text
    if r2.status_code == 200:
        assert r2.json().get("status") == "cancelled" and r2.json().get("refund_pence") == total, r2.text
    else:
        assert H.err(r2) == "already_cancelled", r2.text
    time.sleep(1.0)
    refunds = H.psp_refunds(psp, ref)
    assert len(refunds) == 1 and refunds[0]["amount_pence"] == total, refunds

    # double click at the same time on another order
    order = H.place(edge, user, lines)["order"]
    ref, total = order["order_ref"], order["total_pence"]
    with cf.ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda _: _cancel(edge, ref), range(2)))
    codes = sorted(r.status_code for r in rs)
    assert codes[0] == 200 and codes[1] in (200, 409), [(r.status_code, r.text) for r in rs]
    for r in rs:
        if r.status_code == 409:
            assert H.err(r) == "already_cancelled", r.text
    time.sleep(1.0)
    refunds = H.psp_refunds(psp, ref)
    assert len(refunds) == 1 and refunds[0]["amount_pence"] == total, refunds


@pytest.mark.item("FEAT-002")
def test_feat_002_cancel_returns_stock_and_slot_capacity(edge, mesh, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    sku, qty = lines[0]
    before = H.available(mesh, sku)
    res = H.place(edge, user, lines)
    ref = res["order"]["order_ref"]
    assert H.available(mesh, sku) == before - qty
    zone = H.zone_of(mesh, user["postcode"])
    booked = H.zone_slots(mesh, zone)[res["slot"]["slot_id"]]["booked"]

    r = _cancel(edge, ref)
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    assert wait_until(lambda: H.available(mesh, sku) == before, 10), (H.available(mesh, sku), before)
    assert wait_until(lambda: H.zone_slots(mesh, zone)[res["slot"]["slot_id"]]["booked"] == booked - 1, 10)


@pytest.mark.item("FEAT-002")
def test_feat_002_dispatched_or_unknown_orders(edge, mesh, users, catalog, psp):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    ref = H.place(edge, user, lines)["order"]["order_ref"]
    H.dispatch(mesh, ref)
    r = _cancel(edge, ref)
    assert r.status_code == 409 and H.err(r) == "already_dispatched", f"{r.status_code} {r.text}"
    assert H.order_view(edge, ref)["status"] == "dispatched"
    assert H.psp_refunds(psp, ref) == []
    r = _cancel(edge, "LD-NOSUCHORDER1")
    assert r.status_code == 404, r.text


# =========================================================================== FEAT-003

def _expected_next(mesh, zone):
    slots = [s for s in H.zone_slots(mesh, zone).values() if s["capacity"] - s["booked"] > 0]
    slots.sort(key=lambda s: (s["date"], s["window"].split("-")[0]))
    return slots[0] if slots else None


@pytest.mark.item("FEAT-003")
def test_feat_003_next_slot_is_earliest_with_capacity(edge, mesh, catalog):
    for z in catalog.zones:
        pc = z["postcodes"][0]
        zone = H.zone_of(mesh, pc)
        exp = _expected_next(mesh, zone)
        r = edge.get("/api/delivery/slots/next", params={"postcode": pc})
        if exp is None:
            assert r.status_code == 404 and H.err(r) == "no_slots", r.text
            continue
        assert r.status_code == 200, f"{pc}: {r.status_code} {r.text}"
        b = r.json()
        assert b.get("zone") == zone, b
        s = b.get("slot") or {}
        assert s.get("slot_id") == exp["slot_id"], (b, exp)
        assert s.get("date") == exp["date"] and s.get("window") == exp["window"], (b, exp)
        avail = s.get("available")
        assert avail is True or (isinstance(avail, int) and not isinstance(avail, bool)
                                 and avail == exp["capacity"] - exp["booked"]), b


@pytest.mark.item("FEAT-003")
def test_feat_003_unknown_postcode_and_latency(edge, catalog):
    r = edge.get("/api/delivery/slots/next", params={"postcode": "ZZ9 9ZZ"})
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    pc = catalog.postcode("Z2")
    assert edge.get("/api/delivery/slots/next", params={"postcode": pc}).status_code == 200

    def call():
        assert edge.get("/api/delivery/slots/next", params={"postcode": pc}).status_code == 200

    lat = H.timed(call, 40)
    assert H.p95(lat) < 300, f"p95 {H.p95(lat):.0f}ms"
    # existing slot listing unchanged
    assert H.list_slots(edge, pc)["slots"]


# =========================================================================== FEAT-004

def _search(edge, q, **params):
    r = edge.get("/api/search", params={"q": q, **params})
    return r


def _skus(r):
    return [x["sku"] for x in r.json()["results"]]


DIET_QUERIES = ("cheese", "oat")


@pytest.mark.item("FEAT-004")
def test_feat_004_filters_honoured_and_complete(edge):
    for q in DIET_QUERIES:
        base = _search(edge, q)
        assert base.status_code == 200
        base_res = base.json()["results"]
        assert 0 < len(base_res) < 20, (q, len(base_res))
        cases = [({"diet": d}, lambda p, d=d: d in p["diet"]) for d in ("vegan", "vegetarian", "gluten_free")]
        cases += [({"max_price_pence": 300}, lambda p: p["price_pence"] <= 300),
                  ({"diet": "vegetarian", "max_price_pence": 500},
                   lambda p: "vegetarian" in p["diet"] and p["price_pence"] <= 500)]
        for params, pred in cases:
            r = _search(edge, q, **params)
            assert r.status_code == 200, (q, params, r.text)
            res = r.json()["results"]
            assert all(pred(p) for p in res), (q, params, res)
            assert {p["sku"] for p in res} == {p["sku"] for p in base_res if pred(p)}, (q, params)


@pytest.mark.item("FEAT-004")
def test_feat_004_sorting_over_full_filtered_set(edge, catalog):
    base = _search(edge, "cheese").json()["results"]
    for sort, key, rev in (("price_asc", lambda p: p["price_pence"], False),
                           ("price_desc", lambda p: p["price_pence"], True)):
        r = _search(edge, "cheese", sort=sort)
        assert r.status_code == 200, r.text
        res = r.json()["results"]
        assert {p["sku"] for p in res} == {p["sku"] for p in base}
        assert [key(p) for p in res] == sorted((key(p) for p in res), reverse=rev), (sort, res)
    r = _search(edge, "cheese", sort="name")
    assert r.status_code == 200, r.text
    names = [p["name"] for p in r.json()["results"]]
    assert names == sorted(names) or names == sorted(names, key=str.casefold), names

    # "dairy" matches every dairy product (category term) and more than fit in one response;
    # sorting must consider all of them, not just the most relevant ones
    dairy = [p for p in catalog.products if p["category"] == "dairy"]
    r = _search(edge, "dairy", sort="price_asc")
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert res and res[0]["price_pence"] <= min(p["price_pence"] for p in dairy), res[:3]
    r = _search(edge, "dairy", sort="price_desc")
    res = r.json()["results"]
    assert res and res[0]["price_pence"] >= max(p["price_pence"] for p in dairy), res[:3]
    r = _search(edge, "dairy", sort="name")
    res = r.json()["results"]
    assert res and res[0]["name"].casefold() <= min(p["name"].casefold() for p in dairy), res[:3]
    r = _search(edge, "dairy", sort="price_asc", diet="vegan")
    res = r.json()["results"]
    vegan_dairy = [p for p in dairy if "vegan" in p["diet"]]
    assert all("vegan" in p["diet"] for p in res)
    if vegan_dairy:
        assert res and res[0]["price_pence"] <= min(p["price_pence"] for p in vegan_dairy), res[:3]


@pytest.mark.item("FEAT-004")
def test_feat_004_same_query_different_filters(edge):
    q = "milk"
    base = _search(edge, q).json()["results"]
    assert 0 < len(base) < 20
    seq = [({"diet": "vegetarian"}, lambda p: "vegetarian" in p["diet"]),
           ({"max_price_pence": 150}, lambda p: p["price_pence"] <= 150),
           ({}, lambda p: True),
           ({"diet": "gluten_free"}, lambda p: "gluten_free" in p["diet"]),
           ({"diet": "vegan"}, lambda p: "vegan" in p["diet"]),
           ({"diet": "vegetarian", "max_price_pence": 300}, lambda p: "vegetarian" in p["diet"] and p["price_pence"] <= 300)]
    for params, pred in seq + seq:
        r = _search(edge, q, **params)
        assert r.status_code == 200, r.text
        res = r.json()["results"]
        assert {p["sku"] for p in res} == {p["sku"] for p in base if pred(p)}, params


# baseline results (as a set) for queries without the new parameters
BASELINE_SEARCH = {
    "juice": 5,
    "chicken": 3,
}


@pytest.mark.item("FEAT-004")
def test_feat_004_validation_and_unchanged_default(edge):
    r = _search(edge, "milk", diet="keto")
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    r = _search(edge, "milk", sort="cheapest")
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    for q, n in BASELINE_SEARCH.items():
        r = _search(edge, q)
        assert r.status_code == 200
        b = r.json()
        assert b["count"] == len(b["results"]) == n, b
        assert all(k in p for p in b["results"] for k in ("sku", "name", "price_pence", "diet", "in_stock"))


# =========================================================================== FEAT-005

def _points(order_like):
    return (order_like["subtotal_pence"] - order_like["discount_pence"]) // 100


@pytest.mark.item("FEAT-005")
def test_feat_005_points_per_order_and_account_total(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1, lo=1250, hi=3999)
    o1 = H.place(edge, user, lines)["order"]
    prods = H.pick_range(catalog, 1, 200, 1500)
    cid, cart, _ = H.coupon_cart(edge, user["user_id"], prods, "WELCOME10", 10, lo=1500)
    hid, _slot = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    o2 = r.json()

    expected = {}
    for o in (o1, o2):
        v = H.order_view(edge, o["order_ref"])
        expected[o["order_ref"]] = _points(v)
    assert expected[o2["order_ref"]] == (cart["subtotal_pence"] - cart["discount_pence"]) // 100

    for ref, pts in expected.items():
        got = wait_until(lambda ref=ref: H.order_view(edge, ref).get("loyalty_points_earned"), 30)
        assert got == pts, (ref, got, pts)
    total = sum(expected.values())
    acc = wait_until(lambda: edge.get(f"/api/account/{user['user_id']}").json().get("loyalty_points") == total, 30)
    assert acc, edge.get(f"/api/account/{user['user_id']}").json()


@pytest.mark.item("FEAT-005")
def test_feat_005_cancelled_orders_do_not_count(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1, lo=1250, hi=3999)
    keep = H.place(edge, user, lines)["order"]
    drop = H.place(edge, user, lines)["order"]
    pts = _points(H.order_view(edge, keep["order_ref"]))
    both = 2 * pts
    assert wait_until(lambda: edge.get(f"/api/account/{user['user_id']}").json().get("loyalty_points") == both, 30), \
        edge.get(f"/api/account/{user['user_id']}").json()
    r = edge.post(f"/api/orders/{drop['order_ref']}/cancel", json={})
    if not H.feature_present(r):
        return  # no cancellation (FEAT-002): nothing more to check
    assert r.status_code == 200, r.text
    assert wait_until(lambda: edge.get(f"/api/account/{user['user_id']}").json().get("loyalty_points") == pts, 30), \
        edge.get(f"/api/account/{user['user_id']}").json()


# =========================================================================== FEAT-006

@pytest.mark.item("FEAT-006")
def test_feat_006_signed_v2_webhooks_for_every_event(edge, mesh, partner, users, catalog):
    user = users.fresh()
    lines, prods = H.regular_lines(catalog, 2)
    ref = H.place(edge, user, lines)["order"]["order_ref"]
    H.dispatch(mesh, ref)

    def mine():
        d = partner.get("/admin/deliveries", params={"limit": 500})["deliveries"]
        return [x for x in d if x.get("order_ref") == ref]

    got = wait_until(lambda: {x["event"] for x in mine()} >= {"order.confirmed", "order.dispatched"} and mine(), 60)
    assert got, f"partner did not receive both events for {ref}: {mine()}"
    for d in got:
        assert d.get("schema_version") == 2, d
        assert d.get("signature") == "valid", d

    # data.items on order.dispatched: the partner simulator does not record bodies, so look at
    # the webhook queue on the webhooks service (if it still exposes one)
    r = mesh("webhooks").get("/internal/webhooks/queue/items", params={"limit": 500})
    if r.status_code == 200:
        payloads = [i.get("payload") or {} for i in r.json().get("items", [])]
        disp = [p for p in payloads if p.get("order_ref") == ref and p.get("event") == "order.dispatched"]
        if disp:
            items = (disp[-1].get("data") or {}).get("items")
            assert isinstance(items, list), disp[-1]
            assert sorted((i["sku"], i["qty"]) for i in items) == sorted(lines), items


# =========================================================================== FEAT-007

RATES = {"zero": 0, "reduced": 5, "standard": 20}


def _alloc(discount, totals):
    """Proportional split, floored, leftover to the largest line."""
    sub = sum(totals)
    shares = [discount * t // sub for t in totals]
    shares[totals.index(max(totals))] += discount - sum(shares)
    return shares


def _receipt_basket(prods):
    """Quantities (goods 1000..3999, WELCOME10) where floor and round allocations agree and
    line totals are distinct, so every reasonable reading of the allocation rule agrees."""
    import itertools
    for qs in itertools.product(range(1, 9), repeat=len(prods)):
        totals = [q * p["price_pence"] for q, p in zip(qs, prods)]
        sub = sum(totals)
        if not 1500 <= sub <= 3999 or len(set(totals)) != len(totals):
            continue
        disc = H.round_half_up_div(sub * 10, 100)
        exact = [disc * t / sub for t in totals]
        if any(e - math.floor(e) >= 0.5 for e in exact):
            continue
        yield list(qs)


@pytest.mark.item("FEAT-007")
def test_feat_007_vat_receipt(edge, users, catalog):
    user = users.fresh()
    prods = [H.pick_range(catalog, 1, 100, 900, vat_band=b)[0] for b in ("zero", "reduced", "standard")]
    cart = cid = None
    for qs in _receipt_basket(prods):
        cid = H.new_cart(edge, user["user_id"], [(p["sku"], q) for p, q in zip(prods, qs)], coupon="WELCOME10")
        cart = H.get_cart(edge, cid)
        if H.consistent_total(cart, 10):
            break
        cart = None
    assert cart is not None, "no usable basket"
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    ref = r.json()["order_ref"]
    order = H.order_view(edge, ref)

    rr = edge.get(f"/api/orders/{ref}/receipt")
    assert rr.status_code == 200, f"{rr.status_code} {rr.text}"
    rec = rr.json()
    assert rec.get("order_ref") == ref
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        assert rec.get(k) == order[k], (k, rec.get(k), order[k])

    items = order["items"]
    totals = [i["unit_price_pence"] * i["qty"] for i in items]
    shares = _alloc(order["discount_pence"], totals)
    exp_lines = {}
    breakdown = {"zero": 0, "reduced": 0, "standard": 0}
    band_of = {p["sku"]: p["vat_band"] for p in prods}
    for i, t, s in zip(items, totals, shares):
        band = band_of[i["sku"]]
        rate = RATES[band]
        vat = H.round_half_up_div((t - s) * rate, 100 + rate)
        exp_lines[i["sku"]] = (t, t - s, band, rate, vat)
        breakdown[band] += vat
    deliv_vat = H.round_half_up_div(order["delivery_fee_pence"] * 20, 120)
    breakdown["standard"] += deliv_vat

    lines = rec.get("lines") or []
    assert sorted(l["sku"] for l in lines) == sorted(exp_lines), lines
    names = {p["sku"]: p["name"] for p in prods}
    for l in lines:
        t, disc_t, band, rate, vat = exp_lines[l["sku"]]
        assert l.get("name") == names[l["sku"]], l
        assert l.get("qty") == next(i["qty"] for i in items if i["sku"] == l["sku"])
        assert l.get("unit_price_pence") == next(i["unit_price_pence"] for i in items if i["sku"] == l["sku"])
        assert l.get("vat_band") == band and l.get("vat_rate") == rate, l
        assert l.get("line_total_pence") in (t, disc_t), (l, t, disc_t)
        assert l.get("vat_pence") == vat, (l, vat)
        for k in ("unit_price_pence", "line_total_pence", "vat_pence", "qty"):
            assert isinstance(l[k], int) and not isinstance(l[k], bool), (k, l)
    assert rec.get("vat_breakdown") == breakdown, (rec.get("vat_breakdown"), breakdown)
    assert rec.get("vat_total_pence") == sum(breakdown.values()), rec
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence", "vat_total_pence"):
        assert isinstance(rec[k], int) and not isinstance(rec[k], bool), k
    assert sum(v[1] for v in exp_lines.values()) + rec["delivery_fee_pence"] == rec["total_pence"]

    assert edge.get("/api/orders/LD-NOSUCHORDER7/receipt").status_code == 404


# =========================================================================== FEAT-008

@pytest.fixture(scope="module")
def history(edge, users, catalog):
    a, b = users.fresh(), users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    refs = []
    for _ in range(11):
        refs.append(H.place(edge, a, lines)["order"]["order_ref"])
        time.sleep(0.01)
    other = H.place(edge, b, lines)["order"]["order_ref"]
    return {"user": a, "refs": refs, "other": other}


@pytest.mark.item("FEAT-008")
def test_feat_008_order_history_paginated(edge, history):
    uid = history["user"]["user_id"]
    r = edge.get(f"/api/account/{uid}/orders", params={"page": 1, "per_page": 10})
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    b = r.json()
    assert b.get("page") == 1 and b.get("per_page") == 10 and b.get("total") == 11, b
    got = [o["order_ref"] for o in b["orders"]]
    assert got == list(reversed(history["refs"]))[:10], got
    for o in b["orders"]:
        assert o.get("status") == "confirmed" and isinstance(o.get("total_pence"), int) and o.get("created_at"), o
    stamps = [o["created_at"] for o in b["orders"]]
    assert stamps == sorted(stamps, reverse=True)

    r = edge.get(f"/api/account/{uid}/orders", params={"page": 2, "per_page": 10})
    assert [o["order_ref"] for o in r.json()["orders"]] == [history["refs"][0]]

    # per_page defaults to 10
    b = edge.get(f"/api/account/{uid}/orders").json()
    assert b.get("per_page") == 10 and len(b["orders"]) == 10 and b.get("page") == 1, b
    # other users' orders never appear
    b = edge.get(f"/api/account/{uid}/orders", params={"page": 1, "per_page": 50}).json()
    refs = [o["order_ref"] for o in b["orders"]]
    assert sorted(refs) == sorted(history["refs"]) and history["other"] not in refs
    # maximum 50
    r = edge.get(f"/api/account/{uid}/orders", params={"page": 1, "per_page": 500})
    assert r.status_code == 400 or (r.status_code == 200 and r.json().get("per_page", 0) <= 50), r.text


@pytest.mark.item("FEAT-008")
def test_feat_008_unknown_user_and_latency(edge, history):
    r = edge.get("/api/account/u-9999/orders")
    assert r.status_code == 404, f"{r.status_code} {r.text}"
    uid = history["user"]["user_id"]

    def call():
        assert edge.get(f"/api/account/{uid}/orders", params={"page": 1, "per_page": 10}).status_code == 200

    lat = H.timed(call, 40)
    assert H.p95(lat) < 300, f"p95 {H.p95(lat):.0f}ms"


# =========================================================================== FEAT-009

def _substitution_case(catalog, mesh):
    """(original, [acceptable cheapest substitutes]) using seed data + live availability."""
    prods = catalog.products
    for x in prods:
        if x["sku"] in catalog._used or "3for2" in x["tags"] or catalog.stock.get(x["sku"], 0) < 200:
            continue
        cands = [y for y in prods if y["sku"] != x["sku"] and y["category"] == x["category"]
                 and set(x["diet"]) <= set(y["diet"]) and y["price_pence"] * 10 <= x["price_pence"] * 11]
        cands = [y for y in cands if y["price_pence"] != x["price_pence"] and "3for2" not in y["tags"]]
        if not cands:
            continue
        r = mesh("inventory").post("/internal/inventory/availability", json={"skus": [y["sku"] for y in cands]})
        avail = {k: v["available"] for k, v in r.json().items()}
        cands = [y for y in cands if avail.get(y["sku"], 0) > 10]
        if not cands:
            continue
        best = min(y["price_pence"] for y in cands)
        catalog._used.add(x["sku"])
        return x, [y for y in cands if y["price_pence"] == best]
    raise AssertionError("no substitution candidate in the seed")


@pytest.mark.item("FEAT-009")
def test_feat_009_out_of_stock_item_is_substituted(edge, mesh, users, catalog):
    user = users.fresh()
    x, best = _substitution_case(catalog, mesh)
    extra, _ = H.regular_lines(catalog, 1)
    lines = [(x["sku"], 2)] + extra
    with H.drained(mesh, x["sku"]):
        cid = H.new_cart(edge, user["user_id"], lines)
        hid, _ = H.hold(edge, cid, user["postcode"])
        r = H.checkout(edge, cid, hid, allow_substitutions=True)
        assert r.status_code == 201, f"{r.status_code} {r.text}"
        b = r.json()
    subs = b.get("substitutions")
    assert isinstance(subs, list) and len(subs) == 1, b
    s = subs[0]
    assert s.get("sku") == x["sku"] and s.get("qty") == 2, s
    assert s.get("substitute_sku") in {y["sku"] for y in best}, (s, [y["sku"] for y in best])
    sub = next(y for y in best if y["sku"] == s["substitute_sku"])
    order = H.order_view(edge, b["order_ref"])
    skus = {i["sku"]: i for i in order["items"]}
    assert x["sku"] not in skus and sub["sku"] in skus, order["items"]
    assert skus[sub["sku"]]["qty"] == 2 and skus[sub["sku"]]["unit_price_pence"] == sub["price_pence"]
    exp = H.canonical([{"unit_price_pence": sub["price_pence"], "qty": 2}] +
                      [{"unit_price_pence": i["unit_price_pence"], "qty": i["qty"]} for i in order["items"] if i["sku"] != sub["sku"]])
    assert b["total_pence"] == order["total_pence"] == exp["total_pence"], (b, order, exp)


@pytest.mark.item("FEAT-009")
def test_feat_009_no_substitution_cases(edge, mesh, users, catalog):
    user = users.fresh()
    extra, _ = H.regular_lines(catalog, 1)
    # nothing out of stock: empty list
    cid = H.new_cart(edge, user["user_id"], extra)
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid, allow_substitutions=True)
    assert r.status_code == 201, r.text
    assert r.json().get("substitutions") == [], r.json()

    # without the flag: unchanged (409 out_of_stock)
    x, _best = _substitution_case(catalog, mesh)
    with H.drained(mesh, x["sku"]):
        cid = H.new_cart(edge, user["user_id"], [(x["sku"], 1)] + extra)
        hid, _ = H.hold(edge, cid, user["postcode"])
        r = H.checkout(edge, cid, hid)
        assert r.status_code == 409 and H.err(r) == "out_of_stock", r.text

    # no acceptable substitute (nothing in the category is cheap enough / has the diet tags)
    lonely = None
    for p in catalog.products:
        if p["sku"] in catalog._used or catalog.stock.get(p["sku"], 0) < 50:
            continue
        ok = [y for y in catalog.products if y["sku"] != p["sku"] and y["category"] == p["category"]
              and set(p["diet"]) <= set(y["diet"]) and y["price_pence"] * 10 <= p["price_pence"] * 11]
        if not ok:
            lonely = p
            break
    assert lonely is not None
    catalog._used.add(lonely["sku"])
    with H.drained(mesh, lonely["sku"]):
        cid = H.new_cart(edge, user["user_id"], [(lonely["sku"], 1)] + extra)
        hid, _ = H.hold(edge, cid, user["postcode"])
        r = H.checkout(edge, cid, hid, allow_substitutions=True)
        assert r.status_code == 409 and H.err(r) == "out_of_stock", r.text


# =========================================================================== FEAT-010

@pytest.mark.item("FEAT-010")
def test_feat_010_confirmation_email_v2(edge, email, users, catalog):
    user = users.fresh()
    lines, prods = H.regular_lines(catalog, 2)
    res = H.place(edge, user, lines)
    ref = res["order"]["order_ref"]

    def v2():
        m = email.get("/admin/messages", params={"to": user["email"], "template": "order_confirmation_v2", "limit": 500})
        return [x for x in m["messages"] if (x.get("data") or {}).get("order_ref") == ref]

    msgs = wait_until(v2, 30)
    assert msgs, f"no order_confirmation_v2 email for {ref}"
    time.sleep(3)
    allm = email.get("/admin/messages", params={"to": user["email"], "limit": 500})["messages"]
    conf = [m for m in allm if (m.get("data") or {}).get("order_ref") == ref and m["template"].startswith("order_confirmation")]
    assert len(conf) == 1, [m["template"] for m in conf]
    d = conf[0]["data"]
    assert conf[0]["template"] == "order_confirmation_v2" and conf[0]["to"] == user["email"]
    names = {p["sku"]: p["name"] for p in prods}
    assert sorted((i["sku"], i["name"], i["qty"]) for i in d.get("items") or []) == \
        sorted((s, names[s], q) for s, q in lines), d
    assert d.get("total_pence") == res["order"]["total_pence"], d
    deliv = d.get("delivery") or {}
    assert (deliv.get("date"), deliv.get("window")) == (res["slot"]["date"], res["slot"]["window"]), d


# =========================================================================== FEAT-011

@pytest.mark.item("FEAT-011")
def test_feat_011_only_n_left_badge(edge, mesh, catalog):
    sku = catalog.pick(1, not_tag="3for2")[0]["sku"]

    def detail():
        r = edge.get(f"/api/products/{sku}")
        assert r.status_code == 200
        return r.json()

    d = detail()
    assert d.get("low_stock") is False and "only_n_left" in d and d["only_n_left"] is None, d
    for leave, low in ((7, True), (10, True), (11, False), (1, True)):
        with H.drained(mesh, sku, leave=leave):
            d = detail()
            assert d.get("low_stock") is low, (leave, d)
            assert d.get("only_n_left") == (leave if low else None), (leave, d)
    d = detail()
    assert d.get("low_stock") is False and d.get("only_n_left") is None


@pytest.mark.item("FEAT-011")
def test_feat_011_listing_unchanged_and_detail_fast(edge, catalog):
    sku = catalog.pick(1, not_tag="3for2", reuse=True)[0]["sku"]
    mark = H.Ingress()
    r = edge.get("/api/products", params={"page": 1, "per_page": 50})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 50
    for p in items:
        for k in ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock"):
            assert k in p, (k, p)
    recs = [x for x in mark.since() if x["service"] in ("inventory", "catalog")]
    assert len(recs) <= 4, f"listing made {len(recs)} catalog/inventory calls"

    def call():
        rr = edge.get(f"/api/products/{sku}")
        assert rr.status_code == 200 and "low_stock" in rr.json()

    lat = H.timed(call, 30)
    assert H.p95(lat) < 250, f"detail p95 {H.p95(lat):.0f}ms"


# =========================================================================== FEAT-012

def _welcome_cart(edge, user, catalog):
    prods = H.pick_range(catalog, 1, 200, 1500)
    return H.coupon_cart(edge, user["user_id"], prods, "WELCOME10", 10, lo=1200)


@pytest.mark.item("FEAT-012")
def test_feat_012_coupon_once_per_customer(edge, users, catalog):
    user = users.fresh()
    cid, _, _ = _welcome_cart(edge, user, catalog)
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    assert H.order_view(edge, r.json()["order_ref"])["discount_pence"] > 0

    lines, _ = H.regular_lines(catalog, 1)
    cid2 = H.new_cart(edge, user["user_id"], lines)
    r = edge.post(f"/api/carts/{cid2}/coupon", json={"code": "WELCOME10"})
    assert r.status_code == 400 and H.err(r) == "coupon_already_used", f"{r.status_code} {r.text}"
    # another customer can still use it
    other = users.fresh()
    cid3 = H.new_cart(edge, other["user_id"], lines)
    assert edge.post(f"/api/carts/{cid3}/coupon", json={"code": "WELCOME10"}).status_code == 200

    # STUDENT20 too, when it is usable at all in this build (its seed expiry is a legacy format)
    lines20, prods20 = H.regular_lines(catalog, 1, lo=1600)
    probe = H.new_cart(edge, user["user_id"], lines20)
    pr = edge.post(f"/api/carts/{probe}/coupon", json={"code": "STUDENT20"})
    if pr.status_code == 200:
        cid4, _, _ = H.coupon_cart(edge, user["user_id"], prods20, "STUDENT20", 20, lo=1600)
        hid, _ = H.hold(edge, cid4, user["postcode"])
        r = H.checkout(edge, cid4, hid)
        assert r.status_code == 201, r.text
        cid5 = H.new_cart(edge, user["user_id"], lines20)
        r = edge.post(f"/api/carts/{cid5}/coupon", json={"code": "STUDENT20"})
        assert r.status_code == 400 and H.err(r) == "coupon_already_used", r.text


@pytest.mark.item("FEAT-012")
def test_feat_012_concurrent_checkouts_one_discount(edge, users, catalog):
    user = users.fresh()
    carts = []
    for _ in range(2):
        cid, _, _ = _welcome_cart(edge, user, catalog)
        hid, _ = H.hold(edge, cid, user["postcode"])
        carts.append((cid, hid))
    with cf.ThreadPoolExecutor(2) as ex:
        rs = list(ex.map(lambda c: H.checkout(edge, c[0], c[1]), carts))
    ok = [r.json()["order_ref"] for r in rs if r.status_code == 201]
    assert ok, [(r.status_code, r.text) for r in rs]
    discounted = [ref for ref in ok if H.order_view(edge, ref)["discount_pence"] > 0]
    assert len(discounted) <= 1, discounted
    # and the coupon is now used up for this customer
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "WELCOME10"})
    if discounted:
        assert r.status_code == 400 and H.err(r) == "coupon_already_used", r.text


@pytest.mark.item("FEAT-012")
def test_feat_012_cancellation_frees_coupon(edge, users, catalog):
    user = users.fresh()
    cid, _, _ = _welcome_cart(edge, user, catalog)
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 201, r.text
    ref = r.json()["order_ref"]
    lines, _ = H.regular_lines(catalog, 1)
    cid2 = H.new_cart(edge, user["user_id"], lines)
    r = edge.post(f"/api/carts/{cid2}/coupon", json={"code": "WELCOME10"})
    assert r.status_code == 400 and H.err(r) == "coupon_already_used", r.text
    rc = edge.post(f"/api/orders/{ref}/cancel", json={})
    if not H.feature_present(rc):
        return  # no cancellation (FEAT-002)
    assert rc.status_code == 200, rc.text
    r = edge.post(f"/api/carts/{cid2}/coupon", json={"code": "WELCOME10"})
    assert r.status_code == 200 and r.json().get("coupon") == "WELCOME10", r.text
