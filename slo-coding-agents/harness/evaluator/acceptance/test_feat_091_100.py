"""Acceptance tests FEAT-091..FEAT-100 (account deletion, notifications, dispatch, zones, listing filters,
search stats, service info, reviews, favourites)."""
from __future__ import annotations

import json
import time

import httpx
import pytest

from _feat_helpers_b import (emails_for, error_of, find_list, is_iso_ts, place_order, pooled_user, qty_for, uid)
from conftest import INDEX, REGISTRY, SEED_OPS, wait_until

PRODUCT_KEYS = {"sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock"}


def all_products(edge, per_page=50, **params):
    """Page through GET /api/products; returns (items, totals reported per page, page sizes)."""
    items, totals, sizes, page = [], set(), [], 1
    while True:
        r = edge.get("/api/products", params={"page": page, "per_page": per_page, **params})
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        b = r.json()
        totals.add(b["total"])
        sizes.append(len(b["items"]))
        items += b["items"]
        if not b["items"] or len(items) >= b["total"] or page > 50:
            return items, totals, sizes
        page += 1


# ---------------------------------------------------------------- FEAT-091

@pytest.mark.item("FEAT-091")
def test_feat091_account_deletion(edge, shop, users, catalog):
    u = users.fresh()
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    r = edge.delete(f"/api/account/{u['user_id']}")
    assert r.status_code == 204, f"{r.status_code} {r.text[:200]}"
    acc = edge.get(f"/api/account/{u['user_id']}")
    assert acc.status_code == 200, acc.text
    assert acc.json().get("name") == "deleted" and "email" in acc.json() and acc.json()["email"] is None, acc.json()
    o = edge.get(f"/api/orders/{ref}")
    assert o.status_code == 200 and o.json()["order_ref"] == ref, "order not retained"
    r = edge.post("/api/carts", json={"user_id": u["user_id"]})
    assert r.status_code == 403 and error_of(r) == "account_deleted", f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- FEAT-092

@pytest.mark.item("FEAT-092")
def test_feat092_notification_history(mesh, shop, users, catalog, email):
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    assert wait_until(lambda: emails_for(email, ref, "order_confirmation"), 30)

    def history():
        r = mesh("notifications").get("/internal/notifications", params={"order_ref": ref})
        assert r.status_code == 200, r.text
        return r.json().get("messages")

    msgs = wait_until(lambda: history(), 10)
    assert isinstance(msgs, list) and msgs, history()
    m = next((m for m in msgs if m.get("template") == "order_confirmation"), None)
    assert m, msgs
    assert m.get("to") == u["email"] and isinstance(m.get("status"), str) and m["status"], m
    assert is_iso_ts(m.get("sent_at")), m


# ---------------------------------------------------------------- FEAT-093

@pytest.mark.item("FEAT-093")
def test_feat093_driver_on_dispatched_orders(edge, mesh, shop, users, catalog):
    u = pooled_user(users)
    p, q = catalog.pick(2)
    with_driver = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    without = place_order(shop, u, [(q["sku"], qty_for(q))])["order_ref"]
    r = mesh("warehouse").post(f"/internal/dispatch/{with_driver}", json={"driver_id": "drv-0042"})
    assert r.status_code == 200, r.text
    r = mesh("warehouse").post(f"/internal/dispatch/{without}")      # no body at all
    assert r.status_code == 200, r.text
    o = edge.get(f"/api/orders/{with_driver}").json()
    assert o.get("driver_id") == "drv-0042", o
    o = edge.get(f"/api/orders/{without}").json()
    assert "driver_id" in o and o["driver_id"] is None, o


# ---------------------------------------------------------------- FEAT-094

@pytest.mark.item("FEAT-094")
def test_feat094_delivery_zones(edge, catalog):
    r = edge.get("/api/delivery/zones")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    got = {z["zone"]: (z["name"], sorted(z["postcodes"])) for z in r.json()["zones"]}
    # the zone map is ops' (slots); commerce's copy may hold postcodes ops doesn't have yet (HC-3),
    # and a fix may add those to ops, so they are allowed but not required
    ops_zones = json.loads((SEED_OPS / "zones.json").read_text())
    extra = {z["zone"]: set(z["postcodes"]) for z in catalog.zones}
    assert set(got) == {z["zone"] for z in ops_zones}, sorted(got)
    for z in ops_zones:
        name, pcs = got[z["zone"]]
        assert name == z["name"], (z["zone"], name)
        assert set(z["postcodes"]) <= set(pcs) <= set(z["postcodes"]) | extra[z["zone"]], z["zone"]


# ---------------------------------------------------------------- FEAT-095

@pytest.mark.item("FEAT-095")
@pytest.mark.parametrize("tag", ["3for2", "featured"])
def test_feat095_filter_by_tag(edge, tag):
    full, _, _ = all_products(edge, per_page=100)
    exp = sorted(p["sku"] for p in full if tag in p["tags"])
    items, totals, sizes = all_products(edge, per_page=10, tag=tag)
    assert all(tag in p["tags"] for p in items), [p["sku"] for p in items if tag not in p["tags"]]
    assert totals == {len(exp)}, (totals, len(exp))
    assert sorted(p["sku"] for p in items) == exp


# ---------------------------------------------------------------- FEAT-096

@pytest.mark.item("FEAT-096")
def test_feat096_in_stock_filter(edge, mesh, catalog):
    # make one product unavailable: reserve all of a low-stock SKU's units (released afterwards)
    sku = next(p["sku"] for p in catalog.products
               if p["sku"] not in catalog._used and 0 < catalog.stock.get(p["sku"], 0) < 100)
    catalog._used.add(sku)
    inv = mesh("inventory")
    ref = uid("acc-feat096-")
    n = inv.get(f"/internal/inventory/{sku}").json()["available"]
    assert inv.post("/internal/inventory/reservations", json={"sku": sku, "qty": n, "order_ref": ref}).status_code == 201
    try:
        full, _, _ = all_products(edge, per_page=100)
        avail = {k: v["available"] for k, v in inv.post("/internal/inventory/availability",
                                                        json={"skus": [p["sku"] for p in full]}).json().items()}
        exp = sorted(p["sku"] for p in full if avail.get(p["sku"], 0) > 0)
        assert sku not in exp
        items, totals, sizes = all_products(edge, per_page=40, in_stock="true")
        assert totals == {len(exp)}, (totals, len(exp))
        assert sorted(p["sku"] for p in items) == exp
        assert sizes[:-1] == [40] * (len(sizes) - 1), sizes
        assert all(p.get("in_stock") is True for p in items)
    finally:
        inv.post("/internal/inventory/releases", json={"order_ref": ref, "items": [{"sku": sku, "qty": n}]})


# ---------------------------------------------------------------- FEAT-097

@pytest.mark.item("FEAT-097")
def test_feat097_top_queries(edge, mesh):
    w = uid("zq")
    a, b, c = f"{w}a", f"{w}b mix", f"{w}c"
    for q in (a, f"  {a.upper()} ", a.capitalize(), f"{w}B   MIX", f" {w}b mix ", c):
        assert edge.get("/api/search", params={"q": q}).status_code == 200
    r = mesh("search").get("/internal/search/top-queries", params={"limit": 100})
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    qs = r.json()["queries"]
    counts = [x["count"] for x in qs]
    assert counts == sorted(counts, reverse=True), qs
    mine = {x["query"]: x["count"] for x in qs if w in x["query"].lower()}
    assert mine == {a: 3, b: 2, c: 1}, mine
    r = mesh("search").get("/internal/search/top-queries", params={"limit": 2})
    assert len(r.json()["queries"]) == 2


# ---------------------------------------------------------------- FEAT-098

@pytest.mark.item("FEAT-098")
def test_feat098_info_on_every_service(mesh):
    bad = {}
    for s in REGISTRY["services"]:
        name = s["name"]
        assert name in INDEX
        try:
            r = mesh(name).get("/internal/info")
            b = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            bad[name] = repr(e)
            continue
        if not (isinstance(b, dict) and b.get("service") == name and b.get("repo") == s["repo"]
                and isinstance(b.get("uptime_s"), (int, float)) and not isinstance(b.get("uptime_s"), bool)
                and b["uptime_s"] >= 0):
            bad[name] = (r.status_code, r.text[:120])
    assert not bad, bad
    assert len(REGISTRY["services"]) == 21


# ---------------------------------------------------------------- FEAT-099

@pytest.mark.item("FEAT-099")
def test_feat099_reviews(edge, users, catalog):
    sku = catalog.pick(1)[0]["sku"]
    reviewers = [x["user_id"] for x in users.legacy[:4]]
    d = edge.get(f"/api/products/{sku}").json()
    assert "rating_avg" in d and d["rating_avg"] is None and d.get("rating_count") == 0, d
    for user, rating, text in zip(reviewers, (5, 4, 4), ("Great", "Good", "OK")):
        r = edge.post(f"/api/products/{sku}/reviews", json={"user_id": user, "rating": rating, "text": text})
        assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
        time.sleep(1.05)   # distinct timestamps even at 1s resolution
    r = edge.get(f"/api/products/{sku}/reviews")
    assert r.status_code == 200
    reviews = find_list(r.json(), "reviews", "items")
    assert [(x["rating"], x["text"]) for x in reviews] == [(4, "OK"), (4, "Good"), (5, "Great")], reviews
    d = edge.get(f"/api/products/{sku}").json()
    assert d.get("rating_avg") == 4.3 and d.get("rating_count") == 3, d
    for bad in ({"rating": 0, "text": "x"}, {"rating": 6, "text": "x"}, {"rating": 4.5, "text": "x"},
                {"rating": 3, "text": "x" * 501}):
        r = edge.post(f"/api/products/{sku}/reviews", json={"user_id": reviewers[3], **bad})
        assert r.status_code == 400, f"{bad}: {r.status_code} {r.text[:200]}"
    r = edge.post(f"/api/products/{sku}/reviews", json={"user_id": reviewers[3], "rating": 3, "text": "y" * 500})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    d = edge.get(f"/api/products/{sku}").json()
    assert d.get("rating_avg") == 4.0 and d.get("rating_count") == 4, d


# ---------------------------------------------------------------- FEAT-100

@pytest.mark.item("FEAT-100")
def test_feat100_favourites(edge, users, catalog):
    u = users.fresh()["user_id"]
    a, b = (p["sku"] for p in catalog.pick(2, reuse=True))
    base = f"/api/account/{u}/favourites"
    for sku in (a, b, a):
        r = edge.put(f"{base}/{sku}")
        assert 200 <= r.status_code < 300, f"PUT {sku}: {r.status_code} {r.text[:200]}"

    def favs():
        r = edge.get(base)
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        ps = r.json()["products"]
        assert all(PRODUCT_KEYS <= set(p) for p in ps), ps
        return [p["sku"] for p in ps]

    assert favs() == [a, b]
    r = edge.delete(f"{base}/{a}")
    assert 200 <= r.status_code < 300, f"{r.status_code} {r.text[:200]}"
    assert favs() == [b]
    assert 200 <= edge.put(f"{base}/{a}").status_code < 300
    assert favs() == [b, a]
