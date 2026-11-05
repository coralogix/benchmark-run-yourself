"""Acceptance tests FEAT-077..FEAT-090 (carts, search, edge HTTP behaviour, checkout extras, GDPR export).

FEAT-076 (24h cart expiry) can't be exercised in real time; its grey-box test lives in
test_zz_feat_b_faults.py.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal

import pytest

from _feat_helpers_b import (dispatch, error_of, find_list, place_order, pooled_user, psp_ledger, qty_for, rhu)
from conftest import SEED_COMMERCE

ORIGIN = "https://www.larder.test"


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def cart_lines(edge, cid) -> dict:
    r = edge.get(f"/api/carts/{cid}")
    assert r.status_code == 200, r.text
    return {i["sku"]: i["qty"] for i in r.json()["items"]}


# ---------------------------------------------------------------- FEAT-077

@pytest.mark.item("FEAT-077")
def test_feat077_merge_carts(edge, shop, users, catalog):
    u = pooled_user(users)
    a, b, c = catalog.pick(3)
    target, source = shop.cart(u["user_id"]), shop.cart(u["user_id"])
    for cid, sku, q in ((target, a, 2), (target, b, 1), (source, a, 3), (source, c, 4)):
        assert shop.add(cid, sku["sku"], q).status_code == 200
    r = edge.post(f"/api/carts/{target}/merge", json={"from_cart_id": source})
    assert 200 <= r.status_code < 300, f"{r.status_code} {r.text[:200]}"
    assert cart_lines(edge, target) == {a["sku"]: 5, b["sku"]: 1, c["sku"]: 4}
    assert edge.get(f"/api/carts/{source}").status_code == 404, "source cart still exists"


@pytest.mark.item("FEAT-077")
def test_feat077_merge_respects_line_limit(edge, shop, users, catalog):
    u = pooled_user(users)
    [x] = catalog.pick(1, min_stock=500)
    probe = shop.cart(u["user_id"])
    limit = None
    for q in (99, 50, 24):
        if shop.add(probe, x["sku"], q).status_code == 200:
            limit = cart_lines(edge, probe)[x["sku"]]
            break
    assert limit, "could not determine the per-line limit"
    target, source = shop.cart(u["user_id"]), shop.cart(u["user_id"])
    assert shop.add(target, x["sku"], limit - 1).status_code == 200
    assert shop.add(source, x["sku"], 2).status_code == 200
    r = edge.post(f"/api/carts/{target}/merge", json={"from_cart_id": source})
    if 200 <= r.status_code < 300:
        assert cart_lines(edge, target)[x["sku"]] <= limit, "merge exceeded the per-line limit"
    else:
        assert r.status_code in (400, 409, 422), f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- FEAT-078

def _edit1(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def _name_words(catalog) -> set:
    return {w for p in catalog.products for w in re.findall(r"[a-z0-9]+", p["name"].lower())}


@pytest.mark.item("FEAT-078")
def test_feat078_did_you_mean(edge, catalog):
    words = _name_words(catalog)
    typos = ["chiken"] + [w[:2] + w[3:] for w in sorted(words) if len(w) >= 7 and w[:2] + w[3:] not in words][:6]
    for typo in typos:
        r = edge.get("/api/search", params={"q": typo})
        assert r.status_code == 200
        b = r.json()
        if b["results"] == []:
            break
    else:
        raise AssertionError(f"precondition: one of {typos} should find nothing")
    s = b.get("suggestion")
    assert isinstance(s, str) and s.lower() in words and _edit1(s.lower(), typo), (typo, b)
    r = edge.get("/api/search", params={"q": "qqxxzzvv"})
    assert r.status_code == 200 and r.json()["results"] == []
    assert "suggestion" in r.json() and r.json()["suggestion"] is None, r.json()


# ---------------------------------------------------------------- FEAT-079

@pytest.mark.item("FEAT-079")
def test_feat079_cors(edge, shop, users):
    r = edge.get("/api/products", params={"per_page": 1}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == ORIGIN, dict(r.headers)
    cid = shop.cart(pooled_user(users)["user_id"])
    r = edge.get(f"/api/carts/{cid}", headers={"Origin": ORIGIN})
    assert r.headers.get("access-control-allow-origin") == ORIGIN, dict(r.headers)
    for path, method in (("/api/products", "GET"), ("/api/carts", "POST"), (f"/api/carts/{cid}/items", "POST")):
        r = edge.options(path, headers={"Origin": ORIGIN, "Access-Control-Request-Method": method,
                                        "Access-Control-Request-Headers": "content-type"})
        assert r.status_code == 204, f"OPTIONS {path}: {r.status_code}"
        allowed = r.headers.get("access-control-allow-methods", "")
        assert method in [m.strip().upper() for m in allowed.split(",")], (path, dict(r.headers))


# ---------------------------------------------------------------- FEAT-080

@pytest.mark.item("FEAT-080")
def test_feat080_api_version(edge):
    r = edge.get("/api/version")
    assert r.status_code == 200, r.status_code
    assert r.json().get("api_version") == "2026.1"


# ---------------------------------------------------------------- FEAT-081

@pytest.mark.item("FEAT-081")
def test_feat081_gzip(edge):
    r = edge.get("/api/products", params={"per_page": 50}, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and len(r.content) > 1024
    assert r.headers.get("content-encoding") == "gzip", dict(r.headers)
    assert len(r.json()["items"]) == 50
    r = edge.get("/api/products", params={"per_page": 50}, headers={"Accept-Encoding": "identity"})
    assert r.headers.get("content-encoding") in (None, "identity"), dict(r.headers)


# ---------------------------------------------------------------- FEAT-082

@pytest.mark.item("FEAT-082")
def test_feat082_etag(edge, catalog):
    sku = catalog.pick(1, reuse=True)[0]["sku"]
    r = edge.get(f"/api/products/{sku}")
    etag = r.headers.get("etag")
    assert r.status_code == 200 and etag, dict(r.headers)
    r2 = edge.get(f"/api/products/{sku}", headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.content == b"", (r2.status_code, r2.content[:100])
    r3 = edge.get(f"/api/products/{sku}", headers={"If-None-Match": '"not-the-etag"'})
    assert r3.status_code == 200 and r3.json()["sku"] == sku


# ---------------------------------------------------------------- FEAT-083

SERVER_TIMING = re.compile(r"^\s*app;\s*dur=\d+(\.\d+)?\s*$")


@pytest.mark.item("FEAT-083")
def test_feat083_server_timing(edge, shop, users, catalog):
    u = pooled_user(users)
    sku = catalog.pick(1, reuse=True)[0]["sku"]
    cid = shop.cart(u["user_id"])
    responses = {
        "GET /api/products": edge.get("/api/products", params={"per_page": 2}),
        "GET /api/products/<sku>": edge.get(f"/api/products/{sku}"),
        "GET /api/search": edge.get("/api/search", params={"q": "apple"}),
        "POST /api/carts/<id>/items": shop.add(cid, sku, 1),
        "GET /api/account/<id>": edge.get(f"/api/account/{u['user_id']}"),
        "POST /api/checkout (400)": edge.post("/api/checkout", json={}),
        "GET /api/products/<unknown> (404)": edge.get("/api/products/SKU-99999"),
    }
    for name, resp in responses.items():
        assert SERVER_TIMING.match(resp.headers.get("server-timing", "")), (name, resp.status_code,
                                                                            resp.headers.get("server-timing"))


# ---------------------------------------------------------------- FEAT-084

def _breakdown(edge, cid, cart):
    r = edge.get(f"/api/carts/{cid}/price-breakdown")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    b = r.json()
    lines = find_list(b, "lines", "items")
    assert lines is not None, b
    items = {i["sku"]: i for i in cart["items"]}
    assert sorted(ln["sku"] for ln in lines) == sorted(items)
    for ln in lines:
        it = items[ln["sku"]]
        assert ln["qty"] == it["qty"] and ln["unit_price_pence"] == it["unit_price_pence"], (ln, it)
        assert ln["line_total_pence"] == it["line_total_pence"], (ln, it)
        assert is_int(ln.get("discount_allocated_pence")) and ln["discount_allocated_pence"] >= 0, ln
    tot = b if "discount_pence" in b else (b.get("totals") or {})
    for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        assert tot.get(k) == cart[k], (k, tot, cart)
    assert sum(ln["discount_allocated_pence"] for ln in lines) == cart["discount_pence"]
    return lines


@pytest.mark.item("FEAT-084")
def test_feat084_price_breakdown(edge, shop, users, catalog):
    u = pooled_user(users)
    ps = catalog.pick(3)
    cid = shop.cart(u["user_id"])
    for p, q in zip(ps, (1, 2, 3)):
        assert shop.add(cid, p["sku"], q).status_code == 200
    _breakdown(edge, cid, edge.get(f"/api/carts/{cid}").json())
    r = edge.post(f"/api/carts/{cid}/coupon", json={"code": "VIP7"})   # 7.5% off: allocations don't divide evenly
    assert r.status_code == 200, r.text
    cart = edge.get(f"/api/carts/{cid}").json()
    assert cart["discount_pence"] > 0
    _breakdown(edge, cid, cart)


# ---------------------------------------------------------------- FEAT-085

@pytest.mark.item("FEAT-085")
def test_feat085_euro_total(edge, shop, users, catalog):
    u = pooled_user(users)
    choice = None
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < 200:
            continue
        for q in range(1, 11):
            goods = p["price_pence"] * q
            total = goods + (399 if goods < 4000 else 0)
            if total % 100 == 50 and goods >= 1000:      # x.5 cents: round-half-up matters
                choice = (p, q)
                break
        if choice:
            break
    assert choice
    p, q = choice
    catalog._used.add(p["sku"])
    cid = shop.cart(u["user_id"])
    assert shop.add(cid, p["sku"], q).status_code == 200
    r = edge.get(f"/api/carts/{cid}", params={"currency": "EUR"})
    assert r.status_code == 200
    b = r.json()
    assert b.get("total_eur_cents") == rhu(Decimal(b["total_pence"]) * Decimal("1.17")), b
    r = edge.get(f"/api/carts/{cid}", params={"currency": "USD"})
    assert r.status_code == 400 and error_of(r) == "unsupported_currency", f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- FEAT-086

def _seed_coupons():
    return json.loads((SEED_COMMERCE / "coupons.json").read_text())


def _expiry(raw):
    if raw in (None, "", "never"):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return dt.datetime.strptime(raw, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=dt.timezone.utc)
    if "T" in raw:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt.datetime.strptime(raw, "%d/%m/%Y").replace(hour=23, minute=59, second=59, tzinfo=dt.timezone.utc)


def _cart_with_subtotal(edge, shop, user, catalog, lo, hi):
    for p in catalog.products:
        if p["sku"] in catalog._used or "3for2" in p["tags"] or catalog.stock.get(p["sku"], 0) < 200:
            continue
        for q in range(1, 25):
            if lo <= p["price_pence"] * q <= hi:
                catalog._used.add(p["sku"])
                cid = shop.cart(user["user_id"])
                assert shop.add(cid, p["sku"], q).status_code == 200
                cart = edge.get(f"/api/carts/{cid}").json()
                assert lo <= cart["subtotal_pence"] <= hi, cart
                return cid, cart["subtotal_pence"]
    raise AssertionError("no product fits")


@pytest.mark.item("FEAT-086")
@pytest.mark.parametrize("lo,hi", [(1600, 1900), (2600, 2900)])
def test_feat086_eligible_coupons(edge, shop, users, catalog, lo, hi):
    u = users.fresh()   # never used a coupon (FEAT-012 may make used one-per-customer codes ineligible)
    cid, subtotal = _cart_with_subtotal(edge, shop, u, catalog, lo, hi)
    now = dt.datetime.now(dt.timezone.utc)
    seed = _seed_coupons()
    unexpired = {c["code"]: c for c in seed if (_expiry(c.get("expires")) is None or _expiry(c["expires"]) >= now)}
    eligible = {code for code, c in unexpired.items() if (c.get("min_spend_pence") or 0) <= subtotal}
    ineligible = {c["code"] for c in seed} - eligible | {"FIVER"}   # FIVER (FEAT-050) needs 3000
    r = edge.get(f"/api/carts/{cid}/eligible-coupons")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    got = set(r.json()["coupons"])
    assert eligible <= got, f"missing {sorted(eligible - got)} for subtotal {subtotal}"
    assert not (got & ineligible), f"not eligible: {sorted(got & ineligible)} for subtotal {subtotal}"


# ---------------------------------------------------------------- FEAT-087

def _await_expiry_probe(margin_s: float = 90):
    """The FEAT-040 expiry probe (see test_feat_001_012.py) checks out an expired hold in the
    background; on a build without FEAT-040 that creates a confirmed order. If it is due while
    this test measures global order counts, let it finish first."""
    import time

    import _feat_helpers_a as HA
    early, th = HA.SHARED.get("early_hold"), HA.SHARED.get("early_thread")
    if early and th is not None and th.is_alive():
        wait = HA.iso_epoch(early["expires_at"]) - time.time()
        if wait < margin_s:
            th.join(timeout=max(0.0, wait) + 60)


@pytest.mark.item("FEAT-087")
def test_feat087_order_counts_by_status(mesh, shop, users, catalog):
    def stats():
        r = mesh("orders").get("/internal/orders/stats")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        s = r.json()
        assert all(isinstance(k, str) and is_int(v) and v > 0 for k, v in s.items()), s
        return s

    def delta(a, b):
        return {k: b.get(k, 0) - a.get(k, 0) for k in set(a) | set(b) if b.get(k, 0) != a.get(k, 0)}

    _await_expiry_probe()
    s0 = stats()
    u = pooled_user(users)
    p, q = catalog.pick(2)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    s1 = stats()
    assert delta(s0, s1) == {"confirmed": 1}, delta(s0, s1)
    assert dispatch(mesh, ref).status_code == 200
    s2 = stats()
    assert delta(s1, s2) == {"confirmed": -1, "dispatched": 1}, delta(s1, s2)
    cid = shop.cart(u["user_id"])
    assert shop.add(cid, q["sku"], qty_for(q)).status_code == 200
    r = shop.checkout(cid, shop.hold(cid, u["postcode"]), token="tok_decline")
    assert r.status_code == 402
    s3 = stats()
    d = delta(s2, s3)
    assert d == {} or (len(d) == 1 and list(d.values()) == [1]), d
    dbg = mesh("orders").get("/internal/debug/orders/stats")
    if dbg.status_code == 200:
        assert s3 == dbg.json()["by_status"], (s3, dbg.json()["by_status"])


# ---------------------------------------------------------------- FEAT-088

@pytest.mark.item("FEAT-088")
def test_feat088_delivery_notes(edge, shop, users, catalog):
    u = pooled_user(users)
    p, q = catalog.pick(2)
    note = "Leave with neighbour at no. 12 - ring twice"
    ref = place_order(shop, u, [(p["sku"], qty_for(p))], delivery_note=note)["order_ref"]
    assert edge.get(f"/api/orders/{ref}").json().get("delivery_note") == note
    longest = "x" * 200
    ref = place_order(shop, u, [(q["sku"], qty_for(q))], delivery_note=longest)["order_ref"]
    assert edge.get(f"/api/orders/{ref}").json().get("delivery_note") == longest
    cid = shop.cart(u["user_id"])
    assert shop.add(cid, q["sku"], qty_for(q)).status_code == 200
    r = shop.checkout(cid, shop.hold(cid, u["postcode"]), delivery_note="y" * 201)
    assert r.status_code == 400 and error_of(r) == "note_too_long", f"{r.status_code} {r.text[:200]}"


# ---------------------------------------------------------------- FEAT-089

def _psp_charged(psp, ref):
    return [c["amount_pence"] for c in psp_ledger(psp)["charges"] if str(c.get("order_ref")) == ref]


@pytest.mark.item("FEAT-089")
def test_feat089_tip_is_charged_and_shown(edge, shop, users, catalog, psp):
    u = pooled_user(users)
    p, q = catalog.pick(2)
    o = place_order(shop, u, [(p["sku"], qty_for(p))], tip_pence=250)
    cart_total = o["cart"]["total_pence"]
    assert o["total_pence"] == cart_total + 250, (o, cart_total)
    view = edge.get(f"/api/orders/{o['order_ref']}").json()
    assert view.get("tip_pence") == 250 and view["total_pence"] == cart_total + 250, view
    assert _psp_charged(psp, o["order_ref"]) == [cart_total + 250]
    # not discounted: a 10% coupon leaves the tip whole; boundary 1000 accepted
    # (a customer who never used WELCOME10: FEAT-012 allows it once per customer)
    o = place_order(shop, users.fresh(), [(q["sku"], qty_for(q, 2000))], coupon="WELCOME10", tip_pence=1000)
    assert o["cart"]["discount_pence"] > 0
    assert o["total_pence"] == o["cart"]["total_pence"] + 1000, o
    assert _psp_charged(psp, o["order_ref"]) == [o["cart"]["total_pence"] + 1000]


@pytest.mark.item("FEAT-089")
def test_feat089_invalid_tips_and_no_tip(edge, shop, users, catalog):
    u = pooled_user(users)
    p, q = catalog.pick(2)
    cid = shop.cart(u["user_id"])
    assert shop.add(cid, p["sku"], qty_for(p)).status_code == 200
    hid = shop.hold(cid, u["postcode"])
    for bad in (-1, 1001, 2.5, "lots"):
        r = shop.checkout(cid, hid, tip_pence=bad)
        assert r.status_code == 400 and error_of(r) == "invalid_tip", f"tip={bad!r}: {r.status_code} {r.text[:200]}"
    o = place_order(shop, u, [(q["sku"], qty_for(q))])
    view = edge.get(f"/api/orders/{o['order_ref']}").json()
    assert o["total_pence"] == o["cart"]["total_pence"] == view["total_pence"]
    assert view.get("tip_pence") in (None, 0), view
    o = place_order(shop, u, [(p["sku"], qty_for(p))], tip_pence=0)
    assert o["total_pence"] == o["cart"]["total_pence"]


# ---------------------------------------------------------------- FEAT-090

@pytest.mark.item("FEAT-090")
def test_feat090_personal_data_export(edge, shop, users, catalog):
    u = users.fresh()
    p, q = catalog.pick(2)
    refs = {place_order(shop, u, [(x["sku"], qty_for(x))])["order_ref"] for x in (p, q)}
    r = edge.get(f"/api/account/{u['user_id']}/export")
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    b = r.json()
    assert b["account"].get("user_id") == u["user_id"] and b["account"].get("email") == u["email"], b["account"]
    assert {o["order_ref"] for o in b["orders"]} == refs, b["orders"]
    assert all(o.get("user_id", u["user_id"]) == u["user_id"] for o in b["orders"])
    assert is_int(b["loyalty"]["points"]) and b["loyalty"]["points"] >= 0, b["loyalty"]
