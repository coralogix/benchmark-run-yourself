"""SMOKE: existing behaviour (public API and cross-repo contracts) that must keep working.

Every test here passes on the untouched baseline. Carts are chosen so that no total
depends on a seeded defect (no SF-1 rounding case, no SF-2 coupon, no full slot).
"""
from __future__ import annotations

import pytest

from conftest import INDEX, mesh_url
from larder_support import canonical, engines_agree

import httpx

pytestmark = pytest.mark.item("SMOKE")

PRODUCT_KEYS = {"sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock"}
CART_KEYS = {"cart_id", "user_id", "items", "subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence",
             "total", "coupon"}
LINE_KEYS = {"sku", "name", "qty", "unit_price", "unit_price_pence", "line_total_pence"}
ACCOUNT_KEYS = {"user_id", "name", "email", "postcode", "orders_count", "last_order_ref"}
ORDER_KEYS = {"order_ref", "user_id", "status", "items", "subtotal_pence", "discount_pence", "delivery_fee_pence",
              "total_pence", "charge_id", "hold_id", "created_at"}


def _product_ok(p: dict):
    missing = PRODUCT_KEYS - set(p)
    assert not missing, f"product missing {missing}: {p}"
    assert isinstance(p["price_pence"], int) and p["price_pence"] > 0
    assert abs(p["price"] * 100 - p["price_pence"]) < 1
    assert isinstance(p["diet"], list) and isinstance(p["tags"], list)
    assert isinstance(p["in_stock"], bool)


def _small_cart_lines(catalog, n_skus=1, qty=1):
    """A basket that existing behaviour must always accept, whatever backlog features ship:
    goods total between 1000 and 3999 pence (above any minimum order value, below free delivery),
    quantities well under any per-line limit, no multi-buy product, and never an SF-1
    (pricing-engine) disagreement. See Catalog.safe_lines."""
    return catalog.safe_lines(n_skus, qty=qty, min_price=300, max_price=900)


@pytest.fixture(scope="module")
def placed_order(shop, users, catalog):
    """One confirmed order through the public API, shared by the order/account smoke tests."""
    user = users.fresh()
    lines = _small_cart_lines(catalog, 2, 2)
    body = shop.order(user, [(s, q) for s, q, _ in lines])
    expected = canonical([(pp, q) for _, q, pp in lines])
    return {"user": user, "lines": lines, "body": body, "expected": expected}


# ------------------------------------------------------------------ health

def test_healthz_edge_and_all_21_services(edge):
    r = edge.get("/healthz")
    assert r.status_code == 200, r.text
    bad = {}
    for name in INDEX:
        try:
            rr = httpx.get(mesh_url(name) + "/healthz", timeout=10)
        except httpx.HTTPError as e:  # pragma: no cover - reported below
            bad[name] = repr(e)
            continue
        if rr.status_code != 200:
            bad[name] = f"{rr.status_code} {rr.text[:100]}"
            continue
        body = rr.json()
        if body.get("ok") is not True or body.get("service") != name:
            bad[name] = body
    assert len(INDEX) == 21
    assert not bad, f"unhealthy services: {bad}"


# ------------------------------------------------------------------ catalogue

def test_products_list_shape_and_pagination(edge):
    r = edge.get("/api/products", params={"page": 1, "per_page": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page"] == 1 and body["per_page"] == 5
    assert body["total"] >= 299
    assert len(body["items"]) == 5
    for p in body["items"]:
        _product_ok(p)
    r2 = edge.get("/api/products", params={"page": 2, "per_page": 5}).json()
    assert {p["sku"] for p in r2["items"]}.isdisjoint({p["sku"] for p in body["items"]})


def test_products_list_category_filter(edge):
    r = edge.get("/api/products", params={"category": "dairy", "per_page": 50})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items and all(p["category"] == "dairy" for p in items)


def test_product_detail_shape(edge, catalog):
    p = catalog.pick(1, reuse=True)[0]
    r = edge.get(f"/api/products/{p['sku']}")
    assert r.status_code == 200, r.text
    body = r.json()
    _product_ok(body)
    assert body["sku"] == p["sku"] and body["price_pence"] == p["price_pence"] and body["name"] == p["name"]
    if "also_bought" in body:  # omitted only on recommendations failure
        assert isinstance(body["also_bought"], list)
        for q in body["also_bought"]:
            _product_ok(q)
            assert q["sku"] != p["sku"]


def test_product_detail_unknown_sku_404(edge):
    r = edge.get("/api/products/SKU-9999")
    assert r.status_code == 404


def test_search_shape(edge):
    r = edge.get("/api/search", params={"q": "milk"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "milk"
    assert body["count"] == len(body["results"]) > 0
    for p in body["results"]:
        _product_ok(p)
    assert any("milk" in p["name"].lower() for p in body["results"])


# ------------------------------------------------------------------ cart

def test_cart_create(edge, users):
    r = edge.post("/api/carts", json={"user_id": "smoke-guest-1"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["cart_id"]


def test_cart_add_and_view_small_basket(shop, edge, catalog):
    lines = _small_cart_lines(catalog, 2, 1)
    cid = shop.cart("smoke-guest-2")
    for sku, qty, _ in lines:
        r = shop.add(cid, sku, qty)
        assert r.status_code == 200, r.text
    r = edge.get(f"/api/carts/{cid}")
    assert r.status_code == 200, r.text
    cart = r.json()
    assert CART_KEYS <= set(cart), set(cart)
    assert cart["cart_id"] == cid and cart["user_id"] == "smoke-guest-2"
    by_sku = {i["sku"]: i for i in cart["items"]}
    assert set(by_sku) == {s for s, _, _ in lines}
    for sku, qty, pp in lines:
        it = by_sku[sku]
        assert LINE_KEYS <= set(it), set(it)
        assert it["qty"] == qty and it["unit_price_pence"] == pp and it["line_total_pence"] == pp * qty
    exp = canonical([(pp, q) for _, q, pp in lines])
    assert exp["delivery_fee_pence"] == 399
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        assert cart[k] == exp[k], (k, cart, exp)
    assert abs(cart["total"] * 100 - cart["total_pence"]) < 1
    assert cart["coupon"] in (None, "")


def test_cart_free_delivery_over_threshold(shop, edge, catalog):
    # a basket over GBP 40 whose total every pricing engine agrees on
    for p in catalog.products:
        if p["sku"] in catalog._used or catalog.stock.get(p["sku"], 0) < 200 or "3for2" in p["tags"]:
            continue  # (3for2 products: FEAT-001 multi-buy would change the total)
        qty = -(-4100 // p["price_pence"])
        if qty <= 20 and engines_agree([(p["price_pence"], qty)]):
            catalog._used.add(p["sku"])
            break
    else:  # pragma: no cover
        pytest.skip("no suitable seed product")
    cid = shop.cart("smoke-guest-3")
    assert shop.add(cid, p["sku"], qty).status_code == 200
    cart = edge.get(f"/api/carts/{cid}").json()
    exp = canonical([(p["price_pence"], qty)])
    assert cart["delivery_fee_pence"] == 0
    assert cart["subtotal_pence"] == exp["subtotal_pence"] and cart["total_pence"] == exp["total_pence"]


def test_cart_unknown_404(edge):
    assert edge.get("/api/carts/c_doesnotexist0").status_code == 404


# ------------------------------------------------------------------ coupons

def test_coupon_welcome10_applies(shop, edge, catalog):
    for p in catalog.products:
        if p["sku"] in catalog._used or catalog.stock.get(p["sku"], 0) < 200:
            continue
        qty = 2
        if engines_agree([(p["price_pence"], qty)], 10):
            catalog._used.add(p["sku"])
            break
    cid = shop.cart("smoke-guest-4")
    assert shop.add(cid, p["sku"], qty).status_code == 200
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "WELCOME10"})
    assert r.status_code == 200, r.text
    cart = r.json() if "total_pence" in r.json() else edge.get(f"/api/carts/{cid}").json()
    exp = canonical([(p["price_pence"], qty)], 10)
    assert cart["coupon"] == "WELCOME10"
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        assert cart[k] == exp[k], (k, cart, exp)
    view = edge.get(f"/api/carts/{cid}").json()
    assert view["coupon"] == "WELCOME10" and view["total_pence"] == exp["total_pence"]


def test_coupon_invalid_and_min_spend(shop, edge, catalog):
    p = catalog.pick(1, max_price=500)[0]
    cid = shop.cart("smoke-guest-5")
    assert shop.add(cid, p["sku"], 1).status_code == 200
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "NOSUCHCODE"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_coupon", r.text
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "SPRING15"})  # min spend 2000p
    assert r.status_code == 400 and r.json()["error"] == "min_spend", r.text


# ------------------------------------------------------------------ delivery

def test_delivery_slots_shape(edge, catalog):
    r = edge.get("/api/delivery/slots", params={"postcode": catalog.postcode("Z2")})
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots
    for s in slots:
        assert {"slot_id", "date", "window", "available"} <= set(s), s
    assert any(s["available"] for s in slots)


def test_delivery_hold(shop, edge, catalog):
    p = catalog.pick(1, max_price=900)[0]
    cid = shop.cart("smoke-guest-6")
    assert shop.add(cid, p["sku"], 1).status_code == 200
    slots = edge.get("/api/delivery/slots", params={"postcode": catalog.postcode("Z3")}).json()["slots"]
    slot = next(s for s in slots if s["available"])
    r = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": slot["slot_id"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["hold_id"] and body["expires_at"]


# ------------------------------------------------------------------ checkout / orders / account

def test_checkout_happy_path(placed_order):
    body, exp = placed_order["body"], placed_order["expected"]
    assert body["status"] == "confirmed"
    assert body["order_ref"]
    assert body["total_pence"] == exp["total_pence"]
    assert abs(body["total"] * 100 - body["total_pence"]) < 1


def test_order_view(edge, placed_order):
    ref = placed_order["body"]["order_ref"]
    r = edge.get(f"/api/orders/{ref}")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["order_ref"] == ref and o["status"] == "confirmed"
    assert o["user_id"] == placed_order["user"]["user_id"]
    assert o["total_pence"] == placed_order["expected"]["total_pence"]
    assert {(i["sku"], i["qty"]) for i in o["items"]} == {(s, q) for s, q, _ in placed_order["lines"]}


def test_account_view(edge, placed_order):
    u = placed_order["user"]
    r = edge.get(f"/api/account/{u['user_id']}")
    assert r.status_code == 200, r.text
    a = r.json()
    assert ACCOUNT_KEYS <= set(a), set(a)
    assert a["user_id"] == u["user_id"] and a["email"] == u["email"] and a["name"] == u["name"]
    assert a["postcode"] == u["postcode"]
    assert a["orders_count"] == 1
    assert a["last_order_ref"] == placed_order["body"]["order_ref"]


def test_checkout_declined_card(shop, edge, users, catalog):
    user = users.fresh()
    cid = shop.cart(user["user_id"])
    for sku, qty, _ in _small_cart_lines(catalog, 2, 2):
        assert shop.add(cid, sku, qty).status_code == 200
    hid = shop.hold(cid, user["postcode"])
    r = shop.checkout(cid, hid, token="tok_decline")
    assert r.status_code == 402, r.text
    assert r.json()["error"] == "payment_declined"


# ------------------------------------------------------------------ internal contracts (section 4)

def test_internal_inventory_availability_and_sku(mesh, catalog):
    p = catalog.pick(1, reuse=True)[0]
    r = mesh("inventory").post("/internal/inventory/availability", json={"skus": [p["sku"]]})
    assert r.status_code == 200, r.text
    avail = r.json()[p["sku"]]["available"]
    assert isinstance(avail, int) and avail > 0
    r = mesh("inventory").get(f"/internal/inventory/{p['sku']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sku"] == p["sku"]
    assert body["available"] == body["on_hand"] - body["reserved"]


def test_internal_slots_zone_lookup_and_list(mesh, catalog):
    r = mesh("slots").get("/internal/zones/lookup", params={"postcode": catalog.postcode("Z1")})
    assert r.status_code == 200 and r.json()["zone"] == "Z1", r.text
    assert mesh("slots").get("/internal/zones/lookup", params={"postcode": "ZZ9 9ZZ"}).status_code == 404
    r = mesh("slots").get("/internal/slots", params={"zone": "Z1"})
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots
    for s in slots:
        assert {"slot_id", "zone", "date", "window", "capacity", "booked"} <= set(s), s
        assert s["zone"] == "Z1"


def test_internal_orders_get(mesh, placed_order):
    ref = placed_order["body"]["order_ref"]
    r = mesh("orders").get(f"/internal/orders/{ref}")
    assert r.status_code == 200, r.text
    o = r.json()
    assert ORDER_KEYS <= set(o), set(o)
    assert o["order_ref"] == ref and o["status"] == "confirmed" and o["charge_id"]
    exp = placed_order["expected"]
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        assert o[k] == exp[k], (k, o, exp)


def test_internal_orders_list_paginated(mesh, placed_order):
    uid = placed_order["user"]["user_id"]
    r = mesh("orders").get("/internal/orders", params={"user_id": uid, "page": 1, "per_page": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"orders", "page", "per_page", "total"} <= set(body)
    assert body["page"] == 1 and body["total"] == 1
    assert [o["order_ref"] for o in body["orders"]] == [placed_order["body"]["order_ref"]]
    assert all(o["user_id"] == uid for o in body["orders"])
