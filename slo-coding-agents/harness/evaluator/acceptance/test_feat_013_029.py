"""Acceptance tests for FEAT-013..FEAT-029 (evaluator-only, black-box)."""
from __future__ import annotations

import concurrent.futures as cf
import time
import uuid

import pytest

import _feat_helpers_a as H
from conftest import wait_until

PRODUCT_FIELDS = ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock")


def _all_products(edge, **params):
    out, page = [], 1
    while True:
        r = edge.get("/api/products", params={"page": page, "per_page": 100, **params})
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        b = r.json()
        out += b["items"]
        if not b["items"] or len(out) >= b["total"]:
            return out, b
        page += 1


# =========================================================================== FEAT-013

@pytest.mark.item("FEAT-013")
def test_feat_013_sort_listing_across_pages(edge, catalog):
    for sort, key, rev in (("price_asc", lambda p: p["price_pence"], False),
                           ("price_desc", lambda p: p["price_pence"], True)):
        items, b = _all_products(edge, sort=sort)
        assert len(items) == b["total"] == len(catalog.products), (sort, len(items), b["total"])
        vals = [key(p) for p in items]
        assert vals == sorted(vals, reverse=rev), sort
        assert len({p["sku"] for p in items}) == len(items)
    items, _ = _all_products(edge, sort="name")
    names = [p["name"] for p in items]
    assert names == sorted(names) or names == sorted(names, key=str.casefold)
    # small pages within a category: ordering spans pages
    seen = []
    for page in (1, 2, 3, 4):
        r = edge.get("/api/products", params={"category": "dairy", "sort": "price_asc", "page": page, "per_page": 10})
        assert r.status_code == 200
        seen += [p["price_pence"] for p in r.json()["items"]]
    dairy = sorted(p["price_pence"] for p in catalog.products if p["category"] == "dairy")
    assert seen == dairy, seen


@pytest.mark.item("FEAT-013")
def test_feat_013_invalid_sort_and_default_order(edge, catalog):
    r = edge.get("/api/products", params={"sort": "popularity"})
    assert r.status_code == 400 and H.err(r) == "invalid_sort", f"{r.status_code} {r.text}"
    items, b = _all_products(edge)
    assert [p["sku"] for p in items] == [p["sku"] for p in catalog.products]
    for p in items[:5]:
        assert all(k in p for k in PRODUCT_FIELDS), p


# =========================================================================== FEAT-014

@pytest.mark.item("FEAT-014")
def test_feat_014_diet_filter_on_listing(edge, catalog):
    for diet in ("vegan", "vegetarian", "gluten_free"):
        items, b = _all_products(edge, diet=diet)
        expected = [p["sku"] for p in catalog.products if diet in p["diet"]]
        assert b["total"] == len(expected), (diet, b["total"], len(expected))
        assert sorted(p["sku"] for p in items) == sorted(expected), diet
        assert all(diet in p["diet"] for p in items)
    r = edge.get("/api/products", params={"diet": "vegan", "category": "dairy", "per_page": 100})
    assert r.status_code == 200
    exp = [p["sku"] for p in catalog.products if "vegan" in p["diet"] and p["category"] == "dairy"]
    assert sorted(p["sku"] for p in r.json()["items"]) == sorted(exp) and r.json()["total"] == len(exp)
    r = edge.get("/api/products", params={"diet": "paleo"})
    assert r.status_code == 400 and H.err(r) == "invalid_diet", f"{r.status_code} {r.text}"
    assert edge.get("/api/products").json()["total"] == len(catalog.products)


# =========================================================================== FEAT-015

@pytest.mark.item("FEAT-015")
def test_feat_015_categories_with_counts(edge, catalog):
    r = edge.get("/api/categories")
    assert r.status_code == 200
    cats = r.json().get("categories")
    assert isinstance(cats, list) and cats and all(isinstance(c, dict) for c in cats), cats
    counts = {}
    for p in catalog.products:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    assert [c.get("category") for c in cats] == sorted(counts), cats
    assert {c["category"]: c.get("product_count") for c in cats} == counts


# =========================================================================== FEAT-016

@pytest.mark.item("FEAT-016")
def test_feat_016_vat_rate_on_product_detail(edge, catalog):
    for band, rate in (("zero", 0), ("reduced", 5), ("standard", 20)):
        p = catalog.pick(1, vat_band=band, reuse=True, min_stock=0)[0]
        r = edge.get(f"/api/products/{p['sku']}")
        assert r.status_code == 200
        b = r.json()
        assert b.get("vat_band") == band
        v = b.get("vat_rate")
        assert isinstance(v, (int, float)) and not isinstance(v, bool) and v == rate, (band, v)


# =========================================================================== FEAT-017

@pytest.mark.item("FEAT-017")
def test_feat_017_remove_item(edge, users, catalog):
    user = users.fresh()
    a, b = catalog.pick(2, not_tag="3for2", max_price=1500)
    cid = H.new_cart(edge, user["user_id"], [(a["sku"], 2), (b["sku"], 1)])
    r = edge.delete(f"/api/carts/{cid}/items/{b['sku']}")
    assert r.status_code == 200, r.text
    cart = r.json()
    assert [i["sku"] for i in cart["items"]] == [a["sku"]]
    assert cart["subtotal_pence"] == 2 * a["price_pence"]
    assert cart["total_pence"] == H.canonical(cart["items"])["total_pence"]
    assert H.get_cart(edge, cid)["subtotal_pence"] == 2 * a["price_pence"]

    for sku in (b["sku"], "SKU-9999"):
        r = edge.delete(f"/api/carts/{cid}/items/{sku}")
        assert r.status_code == 404 and H.err(r) == "item_not_in_cart", f"{sku}: {r.status_code} {r.text}"
    assert [i["sku"] for i in H.get_cart(edge, cid)["items"]] == [a["sku"]]


# =========================================================================== FEAT-018

def _cart_updated_count(mesh):
    r = mesh("bus").get("/internal/bus/topics")
    assert r.status_code == 200, r.text
    for t in r.json().get("topics", []):
        if t.get("topic") == "cart.updated":
            return int(t.get("delivered_total") or 0) + int(t.get("depth") or 0)
    return 0


@pytest.mark.item("FEAT-018")
def test_feat_018_cart_changes_published(edge, users, catalog, mesh):
    """Every change to a cart's contents (add, quantity change, removal) publishes cart.updated."""
    user = users.fresh()
    a, b = catalog.pick(2, not_tag="3for2", max_price=1000)
    before = _cart_updated_count(mesh)
    cid = H.new_cart(edge, user["user_id"], [(a["sku"], 1), (b["sku"], 2)])
    r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": 3})
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    r = edge.patch(f"/api/carts/{cid}/items/{b['sku']}", json={"qty": 0})
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    # two adds, one quantity change, one removal
    assert wait_until(lambda: _cart_updated_count(mesh) - before >= 4, timeout_s=20), \
        f"cart.updated published {_cart_updated_count(mesh) - before} times for 4 content changes"


@pytest.mark.item("FEAT-018")
def test_feat_018_change_quantity(edge, users, catalog):
    user = users.fresh()
    a, b = catalog.pick(2, not_tag="3for2", max_price=1000)
    cid = H.new_cart(edge, user["user_id"], [(a["sku"], 1), (b["sku"], 2)])
    r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": 3})
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    items = {i["sku"]: i for i in r.json()["items"]}
    assert items[a["sku"]]["qty"] == 3 and items[a["sku"]]["line_total_pence"] == 3 * a["price_pence"]
    assert r.json()["subtotal_pence"] == 3 * a["price_pence"] + 2 * b["price_pence"]
    r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": 1})
    assert {i["sku"]: i["qty"] for i in r.json()["items"]}[a["sku"]] == 1

    r = edge.patch(f"/api/carts/{cid}/items/{b['sku']}", json={"qty": 0})
    assert r.status_code == 200, r.text
    assert [i["sku"] for i in r.json()["items"]] == [a["sku"]]
    assert r.json()["subtotal_pence"] == a["price_pence"]

    for bad in (-1, 2.5, "two", None):
        r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": bad})
        assert r.status_code == 400 and H.err(r) == "invalid_qty", f"qty={bad!r}: {r.status_code} {r.text}"
    cart = H.get_cart(edge, cid)
    assert [(i["sku"], i["qty"]) for i in cart["items"]] == [(a["sku"], 1)]


# =========================================================================== FEAT-019

@pytest.mark.item("FEAT-019")
def test_feat_019_line_quantity_limit(edge, users, catalog):
    user = users.fresh()
    a = catalog.pick(1, not_tag="3for2", max_price=500)[0]
    cid = H.new_cart(edge, user["user_id"])
    r = edge.post(f"/api/carts/{cid}/items", json={"sku": a["sku"], "qty": 25})
    assert r.status_code == 400, f"{r.status_code} {r.text}"
    assert r.json() == {"error": "qty_limit", "max": 24} or (H.err(r) == "qty_limit" and r.json().get("max") == 24), r.text
    assert H.get_cart(edge, cid)["items"] == []

    assert edge.post(f"/api/carts/{cid}/items", json={"sku": a["sku"], "qty": 20}).status_code == 200
    r = edge.post(f"/api/carts/{cid}/items", json={"sku": a["sku"], "qty": 5})
    assert r.status_code == 400 and H.err(r) == "qty_limit" and r.json().get("max") == 24, r.text
    assert [(i["sku"], i["qty"]) for i in H.get_cart(edge, cid)["items"]] == [(a["sku"], 20)]
    r = edge.post(f"/api/carts/{cid}/items", json={"sku": a["sku"], "qty": 4})
    assert r.status_code == 200 and r.json()["items"][0]["qty"] == 24, r.text

    # updating a line (FEAT-018), if that exists
    r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": 10})
    if H.feature_present(r):
        assert r.status_code == 200, r.text
        r = edge.patch(f"/api/carts/{cid}/items/{a['sku']}", json={"qty": 25})
        assert r.status_code == 400 and H.err(r) == "qty_limit" and r.json().get("max") == 24, r.text
        assert [(i["sku"], i["qty"]) for i in H.get_cart(edge, cid)["items"]] == [(a["sku"], 10)]


# =========================================================================== FEAT-020

@pytest.mark.item("FEAT-020")
def test_feat_020_empty_the_cart(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 2)
    cid = H.new_cart(edge, user["user_id"], lines, coupon="WELCOME10")
    assert H.get_cart(edge, cid)["discount_pence"] > 0
    r = edge.delete(f"/api/carts/{cid}/items")
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    for cart in (r.json(), H.get_cart(edge, cid)):
        assert cart["items"] == [] and cart.get("coupon") is None, cart
        for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
            assert cart.get(k) == 0, (k, cart)
        if "total" in cart:
            assert cart["total"] == 0, cart
    # the emptied cart is still a normal cart
    r = edge.post(f"/api/carts/{cid}/items", json={"sku": lines[0][0], "qty": 1})
    assert r.status_code == 200 and len(r.json()["items"]) == 1, r.text


# =========================================================================== FEAT-021

@pytest.mark.item("FEAT-021")
def test_feat_021_item_count(edge, users, catalog):
    user = users.fresh()
    cid = H.new_cart(edge, user["user_id"])
    assert H.get_cart(edge, cid).get("item_count") == 0
    a, b = catalog.pick(2, not_tag="3for2", max_price=800)
    edge.post(f"/api/carts/{cid}/items", json={"sku": a["sku"], "qty": 2})
    r = edge.post(f"/api/carts/{cid}/items", json={"sku": b["sku"], "qty": 3})
    assert r.json().get("item_count") == 5, r.json()
    assert H.get_cart(edge, cid).get("item_count") == 5


# =========================================================================== FEAT-022

@pytest.mark.item("FEAT-022")
def test_feat_022_savings(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    assert H.get_cart(edge, cid).get("savings_pence") == 0
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "WELCOME10"})
    assert r.status_code == 200
    cart = H.get_cart(edge, cid)
    assert cart["discount_pence"] > 0 and cart.get("savings_pence") == cart["discount_pence"], cart

    mb = catalog.pick(1, tag="3for2")[0]
    cid = H.new_cart(edge, user["user_id"], [(mb["sku"], 3)] + lines, coupon="WELCOME10")
    cart = H.get_cart(edge, cid)
    assert cart.get("savings_pence") == cart["discount_pence"] + (cart.get("multibuy_discount_pence") or 0), cart


# =========================================================================== FEAT-023

@pytest.mark.item("FEAT-023")
def test_feat_023_request_id_in_error_bodies(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    calls = [("GET", "/api/products/SKU-9999", None),                      # 404 raised by edge
             ("GET", "/api/no-such-route", None),                         # 404 routing
             ("PUT", "/api/products", None),                              # 405
             ("POST", "/api/carts", {}),                                  # 400 validation
             ("POST", f"/api/carts/{cid}/coupon", {"code": "NOSUCHCODE"}),  # 400 passed through from promotions
             ("POST", "/api/checkout", {"cart_id": "c_nosuchcart", "hold_id": "H-x", "payment_token": "tok_ok"}),
             ("GET", "/api/delivery/slots?postcode=ZZ9%209ZZ", None)]
    for method, path, body in calls:
        for headers in ({}, {"X-Request-Id": "acc-" + uuid.uuid4().hex}):
            r = edge.request(method, path, json=body, headers=headers)
            assert 400 <= r.status_code < 600, (method, path, r.status_code)
            b = r.json()
            rid = r.headers.get("X-Request-Id")
            assert rid and isinstance(b, dict) and b.get("request_id") == rid, (method, path, r.status_code, rid, b)
            if headers:
                assert rid == headers["X-Request-Id"], (method, path, rid)
            assert b.get("error"), b  # the error code is still there


# =========================================================================== FEAT-024

@pytest.mark.item("FEAT-024")
def test_feat_024_json_405_with_allow(edge, catalog):
    sku = catalog.products[0]["sku"]
    for method, path, supported in (("PUT", "/api/products", {"GET"}), ("DELETE", f"/api/products/{sku}", {"GET"}),
                                    ("GET", "/api/checkout", {"POST"}), ("PUT", "/api/delivery/holds", {"POST"}),
                                    ("POST", "/api/categories", {"GET"})):
        r = edge.request(method, path)
        assert r.status_code == 405, (method, path, r.status_code)
        assert r.headers.get("Content-Type", "").startswith("application/json"), (method, path, r.headers)
        assert H.err(r) == "method_not_allowed", (method, path, r.text)
        allow = {m.strip().upper() for m in (r.headers.get("Allow") or "").split(",") if m.strip()}
        assert supported <= allow and method not in allow, (method, path, r.headers.get("Allow"))


# =========================================================================== FEAT-025

EDGE_DEPENDENCIES = ("catalog", "search", "cart", "checkout", "accounts", "recommendations", "inventory", "slots", "orders")


@pytest.mark.item("FEAT-025")
def test_feat_025_deep_health(edge):
    t0 = time.perf_counter()
    r = edge.get("/api/healthz/deep")
    took = time.perf_counter() - t0
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    b = r.json()
    svcs = b.get("services") or {}
    assert set(EDGE_DEPENDENCIES) <= set(svcs), sorted(svcs)
    assert all(v is True for v in svcs.values()), svcs
    assert b.get("ok") is True
    assert took < 2.0, took


# =========================================================================== FEAT-026

@pytest.mark.item("FEAT-026")
def test_feat_026_delivery_on_order_view(edge, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    res = H.place(edge, user, lines)
    view = H.order_view(edge, res["order"]["order_ref"])
    d = view.get("delivery") or {}
    assert (d.get("date"), d.get("window")) == (res["slot"]["date"], res["slot"]["window"]), view
    assert view["status"] == "confirmed" and view["total_pence"] == res["order"]["total_pence"]


# =========================================================================== FEAT-027

@pytest.mark.item("FEAT-027")
def test_feat_027_line_totals_and_item_count_on_orders(edge, users, catalog):
    user = users.fresh()
    lines, prods = H.regular_lines(catalog, 2)
    ref = H.place(edge, user, lines)["order"]["order_ref"]
    view = H.order_view(edge, ref)
    for i in view["items"]:
        assert i.get("line_total_pence") == i["unit_price_pence"] * i["qty"], i
    assert view.get("item_count") == sum(q for _, q in lines), view
    assert view["total_pence"] == H.canonical(view["items"])["total_pence"]


# =========================================================================== FEAT-028

@pytest.mark.item("FEAT-028")
def test_feat_028_change_postcode(edge, users, catalog):
    user = users.fresh()
    new = next(z["postcodes"][1] for z in catalog.zones if z["postcodes"][0].split()[0] != user["postcode"].split()[0])
    r = edge.patch(f"/api/account/{user['user_id']}", json={"postcode": new})
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    assert r.json().get("postcode") == new and r.json().get("user_id") == user["user_id"], r.json()
    assert edge.get(f"/api/account/{user['user_id']}").json()["postcode"] == new
    r = edge.patch(f"/api/account/{user['user_id']}", json={"postcode": "ZZ9 9ZZ"})
    assert r.status_code == 400 and H.err(r) == "invalid_postcode", r.text
    assert edge.get(f"/api/account/{user['user_id']}").json()["postcode"] == new


# =========================================================================== FEAT-029

@pytest.mark.item("FEAT-029")
def test_feat_029_marketing_opt_in(edge, users):
    user = users.fresh()
    url = f"/api/account/{user['user_id']}"
    assert edge.get(url).json().get("marketing_opt_in") is False, edge.get(url).json()
    for value in (True, False, True):
        r = edge.patch(url, json={"marketing_opt_in": value})
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        b = edge.get(url).json()
        assert b.get("marketing_opt_in") is value, b
        assert b["user_id"] == user["user_id"] and b["email"] == user["email"]
    for bad in ("yes", 1, None, "true"):
        r = edge.patch(url, json={"marketing_opt_in": bad})
        assert r.status_code == 400 and H.err(r) == "invalid_opt_in", f"{bad!r}: {r.status_code} {r.text}"
    assert edge.get(url).json().get("marketing_opt_in") is True
    # other accounts are not affected
    assert edge.get(f"/api/account/{users.legacy[1]['user_id']}").json().get("marketing_opt_in") is False
