"""Seeded defects with functional (non-latency) outcomes: SF-1, SF-2, SF-6, FF-1, and the
per-entity defects HC-1..HC-3 (a handful of SKUs / customers, listed in harness/evaluator/hc_entities.json).

Each test fails on the untouched baseline because the defect is present, and passes on
any genuine fix. Outcomes only: HTTP status codes, totals, order/inventory state.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import math
import re
import uuid

import httpx
import pytest

from conftest import HC_ENTITIES, MAX_LINE_QTY, MIN_GOODS_PENCE, MULTIBUY_TAG, SEED_COMMERCE, mesh_url
from larder_support import canonical, direct_order_body, v1_total, v2_total


# ------------------------------------------------------------------ SF-1

def _sf1_carts(catalog) -> list[dict]:
    """Single-SKU carts on which the float pricing engines disagree with the canonical total.

    Groups (2 carts each): v1 wrong with no coupon over GBP 40 (truncation), v1 wrong with
    WELCOME10 under / over GBP 40, and v2 wrong with WELCOME10 (half-penny discounts), so that
    neither "the v1 engine" nor "switch to v2" satisfies the canonical rule.

    Robust to other backlog items: no 3for2 products (FEAT-001 multi-buy), goods after the coupon
    >= 1000p (FEAT-039 minimum order), at most 20 units (FEAT-019 caps lines at 24).
    """
    want = {"v1_nocoupon": 2, "v1_coupon_small": 2, "v1_coupon_big": 2, "v2_coupon": 2}
    got: dict[str, list] = {k: [] for k in want}

    def group_for(lines):
        c0 = canonical(lines)["total_pence"]
        c10 = canonical(lines, 10)
        if c0 >= 4000 and v1_total(lines) != c0:
            return "v1_nocoupon", None, c0
        if c10["subtotal_pence"] - c10["discount_pence"] < MIN_GOODS_PENCE:
            return None, None, None
        if v1_total(lines, 10) != c10["total_pence"]:
            small = c10["subtotal_pence"] - c10["discount_pence"] < 4000
            return ("v1_coupon_small" if small else "v1_coupon_big"), "WELCOME10", c10["total_pence"]
        if v2_total(lines, 10) != c10["total_pence"]:
            return "v2_coupon", "WELCOME10", c10["total_pence"]
        return None, None, None

    taken: set[str] = set()
    # unused products first; products other tests already used only if the seed runs short
    for p in [x for x in catalog.products if x["sku"] not in catalog._used] + \
             [x for x in catalog.products if x["sku"] in catalog._used]:
        if p["sku"] in taken or MULTIBUY_TAG in p["tags"] or catalog.stock.get(p["sku"], 0) < 200:
            continue
        for qty in range(1, 21):
            g, coupon, expected = group_for([(p["price_pence"], qty)])
            if g and len(got[g]) < want[g]:
                got[g].append((p, qty, coupon, expected))
                taken.add(p["sku"])
                break  # one cart per SKU
        if all(len(got[k]) >= want[k] for k in want):
            break
    carts = [dict(group=k, product=p, qty=q, coupon=c, expected=e) for k, v in got.items() for p, q, c, e in v[:want[k]]]
    assert len(carts) == sum(want.values()), "not enough SF-1 candidate carts in the seed data"
    for c in carts:
        catalog._used.add(c["product"]["sku"])
    return carts


@pytest.mark.item("SF-1")
def test_sf1_checkout_totals_follow_canonical_integer_rule(shop, edge, users, catalog):
    carts = _sf1_carts(catalog)
    buyers = [users.fresh(), users.fresh()]
    problems = []
    for i, c in enumerate(carts):
        # a coupon order gets a customer who never used it (FEAT-012: one use per customer)
        user = users.fresh() if c["coupon"] else buyers[i % 2]
        sku = c["product"]["sku"]
        cid = shop.cart(user["user_id"])
        r = shop.add(cid, sku, c["qty"])
        assert r.status_code == 200, r.text
        if c["coupon"]:
            r = edge.post(f"/api/carts/{cid}/coupon", json={"code": c["coupon"]})
            assert r.status_code == 200, r.text
        cart = edge.get(f"/api/carts/{cid}").json()
        label = f"{c['group']} {sku} x{c['qty']} coupon={c['coupon']} expected={c['expected']}"
        if cart.get("total_pence") != c["expected"]:
            problems.append(f"cart total {cart.get('total_pence')} != canonical ({label})")
        hid = shop.hold(cid, user["postcode"])
        r = shop.checkout(cid, hid)
        if r.status_code != 201:
            problems.append(f"checkout {r.status_code} {r.text[:120]} ({label})")
        elif r.json().get("total_pence") != c["expected"]:
            problems.append(f"order total {r.json().get('total_pence')} != canonical ({label})")
    assert not problems, f"{len(problems)} pricing disagreements:\n" + "\n".join(problems)


# ------------------------------------------------------------------ SF-2

def _expiry(raw):
    """Seed `expires` to an aware UTC datetime (end of day for date-only forms) or None (never)."""
    s = str(raw).strip()
    if s.lower() in ("never", "none", "n/a", "-", ""):
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = dt.datetime.strptime(s, "%Y-%m-%d")
    elif "T" in s:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", s):
        d = dt.datetime.strptime(s, "%d/%m/%Y")
    else:  # pragma: no cover - not in the seed
        raise AssertionError(f"unknown expiry format {s!r}")
    return d.replace(hour=23, minute=59, second=59, tzinfo=dt.timezone.utc)


@pytest.mark.item("SF-2")
def test_sf2_every_seeded_coupon_answers_without_server_error(shop, edge, catalog):
    coupons = json.loads((SEED_COMMERCE / "coupons.json").read_text())
    now = dt.datetime.now(dt.timezone.utc)
    # over every min_spend (max 5000p) while staying within any per-line limit (FEAT-019 caps lines at 24)
    # (no 3for2 product: FEAT-001 multi-buy would take the basket back under some min_spend)
    p = next(x for x in catalog.products if x["sku"] not in catalog._used and catalog.stock.get(x["sku"], 0) >= 600
             and 400 <= x["price_pence"] <= 1500 and MULTIBUY_TAG not in x["tags"])
    catalog._used.add(p["sku"])
    qty = max(1, math.ceil(5200 / p["price_pence"]))
    assert qty <= 13
    results, problems = {}, []
    for c in coupons:
        code = c["code"]
        exp = _expiry(c["expires"])
        expired = exp is not None and exp < now
        cid = shop.cart("acc-sf2-guest")
        assert shop.add(cid, p["sku"], qty).status_code == 200
        r = edge.post(f"/api/carts/{cid}/coupon", json={"code": code})
        try:
            err = r.json().get("error")
        except ValueError:
            err = None
        results[code] = (r.status_code, err)
        if r.status_code >= 500:
            problems.append(f"{code} (expires {c['expires']!r}): {r.status_code} {r.text[:100]}")
        elif expired and (r.status_code, err) != (400, "expired_coupon"):
            problems.append(f"{code} expired {c['expires']!r} but got {r.status_code} {err}")
        elif not expired and r.status_code != 200:
            problems.append(f"{code} valid until {c['expires']!r} but got {r.status_code} {err}")
        elif not expired:
            cart = edge.get(f"/api/carts/{cid}").json()
            if cart.get("coupon") != code:
                problems.append(f"{code} accepted but cart shows coupon={cart.get('coupon')!r}")
    assert not problems, f"coupon results {results}\n" + "\n".join(problems)


# ------------------------------------------------------------------ SF-6

@pytest.mark.item("SF-6")
def test_sf6_orders_list_without_page_is_scoped_to_user(shop, mesh, edge, users, catalog):
    a, b = users.fresh(), users.fresh()
    o_a1 = shop.order(a, shop.safe_basket(1))
    o_b = shop.order(b, shop.safe_basket(1))
    o_a2 = shop.order(a, shop.safe_basket(2))

    r = mesh("orders").get("/internal/orders", params={"user_id": a["user_id"]})
    assert r.status_code == 200, r.text
    orders = r.json()["orders"]
    others = sorted({o.get("user_id") for o in orders} - {a["user_id"]}, key=str)
    assert not others, (f"GET /internal/orders?user_id={a['user_id']} (no page) returned {len(orders)} orders "
                        f"including other users' orders {others[:5]}")
    refs = {o["order_ref"] for o in orders}
    assert {o_a1["order_ref"], o_a2["order_ref"]} <= refs and o_b["order_ref"] not in refs

    acc = edge.get(f"/api/account/{a['user_id']}").json()
    assert acc["orders_count"] == 2 and acc["last_order_ref"] == o_a2["order_ref"], acc
    acc_b = edge.get(f"/api/account/{b['user_id']}").json()
    assert acc_b["orders_count"] == 1 and acc_b["last_order_ref"] == o_b["order_ref"], acc_b


# ------------------------------------------------------------------ FF-1

def _ff1_qty(available: int) -> int:
    return min(MAX_LINE_QTY, max(1, math.ceil(available / 8)))


def _low_stock_sku(catalog, mesh) -> tuple[dict, int]:
    """An unused low-stock SKU. Prefer one whose per-order goods stay >= 1000p even with every
    third unit free (every low-stock seed SKU is tagged 3for2), so neither FEAT-001 multi-buy
    nor a FEAT-039 minimum order value placed in orders can reject the burst for pricing reasons."""
    fallback = None
    for p in catalog.products:
        if p["sku"] in catalog._used or not (30 <= catalog.stock.get(p["sku"], 0) < 200):
            continue
        r = mesh("inventory").get(f"/internal/inventory/{p['sku']}")
        if r.status_code != 200 or r.json()["available"] < 20:
            continue
        avail = r.json()["available"]
        q = _ff1_qty(avail)
        if (q - q // 3) * p["price_pence"] >= MIN_GOODS_PENCE:
            catalog._used.add(p["sku"])
            return p, avail
        fallback = fallback or (p, avail)
    if fallback:
        catalog._used.add(fallback[0]["sku"])
        return fallback
    pytest.fail("no low-stock seed SKU left for FF-1")


def _ff1_totals(p, qty, user_id) -> dict:
    """Order totals the build's orders service accepts for (sku x qty): canonical unless the build
    prices this SKU differently (FEAT-001 multi-buy on 3for2 products). A probe with a total that is
    wrong under any rule is answered 409 price_mismatch + expected_total_pence before any stock is
    reserved; nothing is placed."""
    body = direct_order_body([(p["sku"], qty, p["price_pence"])], user_id=user_id)
    totals = {k: body[k] for k in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence")}
    probe = dict(body, total_pence=body["total_pence"] + 1, order_ref=body["order_ref"] + "P")
    with httpx.Client(timeout=60) as c:
        r = c.post(mesh_url("orders") + "/internal/orders", json=probe)
    try:
        expected = r.json().get("expected_total_pence")
    except ValueError:
        expected = None
    if r.status_code == 409 and isinstance(expected, int) and expected != totals["total_pence"]:
        mb = canonical([(p["price_pence"], qty - qty // 3)])  # every third unit free
        totals = {k: mb[k] for k in totals} if mb["total_pence"] == expected else dict(totals, total_pence=expected)
    return {"totals": totals, "probe_status": r.status_code}


@pytest.mark.item("FF-1")
def test_ff1_concurrent_orders_never_oversell_low_stock_sku(mesh, catalog):
    p, available = _low_stock_sku(catalog, mesh)
    qty = _ff1_qty(available)
    priced = _ff1_totals(p, qty, "acc-ff1-probe")
    if priced["probe_status"] == 201:  # this build places orders whatever the total: the probe took stock
        available = mesh("inventory").get(f"/internal/inventory/{p['sku']}").json()["available"]
    n = 24  # demand ~3x the available stock
    url = mesh_url("orders") + "/internal/orders"

    def place(i):
        body = direct_order_body([(p["sku"], qty, p["price_pence"])], user_id=f"acc-ff1-{i}")
        body.update(priced["totals"])
        with httpx.Client(timeout=60) as c:
            r = c.post(url, json=body)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"raw": r.text[:120]}

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(place, range(n)))
    codes = [c for c, _ in results]
    ok = codes.count(201)
    server_errors = [(c, b) for c, b in results if c >= 500]
    unexpected = [(c, b) for c, b in results if c not in (201, 409) and c < 500]
    sold = ok * qty

    after = mesh("inventory").get(f"/internal/inventory/{p['sku']}")
    assert after.status_code == 200, f"inventory lookup after the burst: {after.status_code} {after.text[:120]}"
    inv = after.json()
    assert sold <= available, f"oversold {p['sku']}: {ok} orders x {qty} = {sold} > available {available} ({inv})"
    assert not server_errors, f"{len(server_errors)} server errors during the burst: {server_errors[:3]}"
    assert not unexpected, unexpected[:3]
    assert inv["available"] >= 0 and inv["available"] == available - sold, (inv, available, sold)

    # still orderable afterwards (or a clean out_of_stock if it is genuinely gone)
    code, body = place("after")
    if inv["available"] >= qty:
        assert code == 201, (code, body)
    else:
        assert code == 409 and body.get("error") == "out_of_stock", (code, body)


# ------------------------------------------------------------------ HC-1 / HC-2 / HC-3 (per-entity)

@pytest.mark.item("HC-1")
def test_hc1_product_page_for_every_affected_sku(edge, catalog):
    """Product detail works for the SKUs whose description contains a '%' (and shows the seed data)."""
    seed = {p["sku"]: p for p in catalog.products}
    bad = []
    for sku in HC_ENTITIES["HC-1_skus"]:
        p = seed[sku]
        r = edge.get(f"/api/products/{sku}")
        if r.status_code != 200:
            bad.append((sku, r.status_code, r.text[:120]))
            continue
        b = r.json()
        got = (b.get("sku"), b.get("name"), b.get("price_pence"), b.get("category"), b.get("description"))
        if got != (sku, p["name"], p["price_pence"], p["category"], p["description"]):
            bad.append((sku, "wrong data", got))
    assert not bad, bad


@pytest.mark.item("HC-2")
def test_hc2_affected_customers_can_view_their_account(edge, users):
    """Account page for every customer whose name contains an apostrophe."""
    bad = []
    for uid in HC_ENTITIES["HC-2_users"]:
        u = users.by_id[uid]
        r = edge.get(f"/api/account/{uid}")
        if r.status_code != 200:
            bad.append((uid, r.status_code, r.text[:120]))
            continue
        b = r.json()
        if (b.get("user_id"), b.get("name"), b.get("email"), b.get("postcode")) != (uid, u["name"], u["email"], u["postcode"]):
            bad.append((uid, "wrong data", b))
    assert not bad, bad


@pytest.mark.item("HC-2")
def test_hc2_affected_customers_can_check_out(edge, shop, users):
    """A checkout (safe basket, no coupon) for several of those customers is confirmed."""
    ids = HC_ENTITIES["HC-2_users"]
    bad = []
    for uid in ids[::4]:
        u = users.by_id[uid]
        cid = shop.safe_cart(uid)
        hold = None
        r = edge.get("/api/delivery/slots", params={"postcode": u["postcode"]})
        assert r.status_code == 200, f"{uid}: slots {r.status_code} {r.text[:200]}"
        for s in r.json()["slots"]:
            if s.get("available"):
                h = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": s["slot_id"]})
                if h.status_code == 201:
                    hold = h.json()["hold_id"]
                    break
        assert hold, f"{uid}: no slot could be held"
        r = shop.checkout(cid, hold)
        if r.status_code != 201:
            bad.append((uid, r.status_code, r.text[:120]))
    assert not bad, bad


@pytest.mark.item("HC-3")
def test_hc3_affected_customers_can_list_and_hold_slots(edge, shop, users, catalog):
    """Customers at the newest postcodes (known to commerce's zone table) can list delivery slots
    and hold one. The zone must be the one commerce's table gives."""
    zone_of = {pc: z["zone"] for z in catalog.zones for pc in z["postcodes"]}
    bad = []
    for pc in HC_ENTITIES["HC-3_postcodes"]:
        r = edge.get("/api/delivery/slots", params={"postcode": pc})
        if r.status_code != 200 or r.json().get("zone") != zone_of[pc] or not r.json().get("slots"):
            bad.append((pc, r.status_code, r.text[:120]))
    for uid in HC_ENTITIES["HC-3_users"]:
        u = users.by_id[uid]
        r = edge.get("/api/delivery/slots", params={"postcode": u["postcode"]})
        if r.status_code != 200:
            bad.append((uid, u["postcode"], r.status_code, r.text[:120]))
            continue
        cid = shop.cart(uid)
        held = None
        for s in r.json().get("slots", []):
            if s.get("available"):
                h = edge.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": s["slot_id"]})
                if h.status_code == 201:
                    held = h.json().get("hold_id")
                    break
        if not held:
            bad.append((uid, u["postcode"], "no hold"))
    assert not bad, bad
