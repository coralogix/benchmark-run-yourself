"""Acceptance tests FEAT-051..FEAT-060 (loyalty redemption/tiers, emails, webhooks, warehouse)."""
from __future__ import annotations

import datetime as dt
import time

import pytest

from _feat_helpers_b import (cancel_order, dispatch, emails_for, emails_to, error_of, order_internal, partner_events, pick_skus,
                             place_order, pooled_user, qty_for, require_cancel, settle)
from conftest import wait_until


def account_points(edge, user_id) -> int:
    r = edge.get(f"/api/account/{user_id}")
    assert r.status_code == 200, r.text
    return int(r.json().get("loyalty_points") or 0)


def expected_tier(points: int) -> str:
    return "bronze" if points < 500 else ("silver" if points < 2000 else "gold")


# ---------------------------------------------------------------- FEAT-051

@pytest.mark.item("FEAT-051")
def test_feat051_redeem_points_end_to_end(edge, shop, users, catalog):
    """Redeem 100 points on a cart, check out, balance drops by 100.

    Depends on FEAT-005: the user must first earn points by ordering; if FEAT-005 points
    don't exist, nothing can be redeemed and the test fails.
    """
    u = users.fresh()
    earn, buy = pick_skus(catalog, 2, min_price=900)
    place_order(shop, u, [(earn["sku"], qty_for(earn, 22000))])
    bal = wait_until(lambda: account_points(edge, u["user_id"]) >= 200, 30)
    assert bal, "no loyalty points after a >£200 order: FEAT-051 requires FEAT-005 points"
    time.sleep(1)
    bal = account_points(edge, u["user_id"])

    last = None
    for attempt in range(3):
        cid = shop.cart(u["user_id"])
        assert shop.add(cid, buy["sku"], qty_for(buy, 5000) + attempt).status_code == 200
        before = edge.get(f"/api/carts/{cid}").json()
        r = edge.post(f"/api/carts/{cid}/loyalty", json={"points": 100})
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        cart = edge.get(f"/api/carts/{cid}").json()
        assert cart.get("loyalty_discount_pence") == 100, cart
        assert cart["total_pence"] == before["total_pence"] - 100, (before, cart)
        hid = shop.hold(cid, u["postcode"])
        last = shop.checkout(cid, hid)
        if last.status_code == 409 and error_of(last) == "price_mismatch":
            continue
        break
    assert last.status_code == 201, f"{last.status_code} {last.text}"
    ref = last.json()["order_ref"]
    assert last.json()["total_pence"] == cart["total_pence"]
    order = edge.get(f"/api/orders/{ref}").json()
    assert order["total_pence"] == cart["total_pence"], order

    goods = cart["subtotal_pence"] - (cart.get("discount_pence") or 0)
    earned = {goods // 100, (goods - 100) // 100}
    if isinstance(order.get("loyalty_points_earned"), int):
        earned.add(order["loyalty_points_earned"])
    ok = wait_until(lambda: account_points(edge, u["user_id"]) in {bal - 100 + e for e in earned}, 30)
    assert ok, (f"balance {account_points(edge, u['user_id'])} after redeeming 100 of {bal} "
                f"(this order earns one of {sorted(earned)})")


@pytest.mark.item("FEAT-051")
def test_feat051_insufficient_points(edge, shop, users, catalog):
    """More points than the balance returns 400 insufficient_points; not a multiple of 100 returns 400.
    Depends on FEAT-005 (a user with a known balance)."""
    u = users.fresh()
    [p] = catalog.pick(1)
    place_order(shop, u, [(p["sku"], qty_for(p, 1500))])
    time.sleep(2)
    bal = account_points(edge, u["user_id"])
    cid = shop.cart(u["user_id"])
    assert shop.add(cid, p["sku"], qty_for(p, 5000)).status_code == 200
    too_many = (bal // 100 + 1) * 100
    r = edge.post(f"/api/carts/{cid}/loyalty", json={"points": too_many})
    assert r.status_code == 400 and error_of(r) == "insufficient_points", f"{r.status_code} {r.text}"
    r = edge.post(f"/api/carts/{cid}/loyalty", json={"points": 150})
    assert r.status_code == 400, f"points=150 (not a multiple of 100): {r.status_code} {r.text}"
    cart = edge.get(f"/api/carts/{cid}").json()
    assert not cart.get("loyalty_discount_pence"), cart


# ---------------------------------------------------------------- FEAT-052

@pytest.mark.item("FEAT-052")
def test_feat052_new_user_is_bronze(edge, users):
    u = users.fresh()
    r = edge.get(f"/api/account/{u['user_id']}")
    assert r.status_code == 200
    assert r.json().get("tier") == "bronze", r.json()


@pytest.mark.item("FEAT-052")
def test_feat052_tier_follows_points(edge, shop, users, catalog):
    """Tier must match the account's loyalty points: bronze <500, silver 500-1999 (incl. 1500-1999,
    where the old marketing deck said gold), gold 2000+. Points are earned through orders (FEAT-005);
    if points never move, the account must stay bronze."""
    u = users.fresh()
    pool = pick_skus(catalog, 10, min_price=1100, expensive_first=True, min_stock=300)

    def check():
        acc = edge.get(f"/api/account/{u['user_id']}").json()
        pts = int(acc.get("loyalty_points") or 0)
        assert acc.get("tier") == expected_tier(pts), acc
        return pts

    def lines_worth(goods_pence):
        """Lines (<= 24 units each) whose goods total is goods_pence rounded up to one unit."""
        lines, left = [], goods_pence
        while left > 0:
            p = pool.pop(0)
            q = min(24, -(-left // p["price_pence"]))
            lines.append((p["sku"], q))
            left -= q * p["price_pence"]
        return lines

    def order_and_wait(goods_pence, at_least):
        place_order(shop, u, lines_worth(goods_pence))
        ok = wait_until(lambda: account_points(edge, u["user_id"]) >= at_least, 20)
        time.sleep(1)
        return ok

    if not order_and_wait(55000, 500):          # about 550 points: silver
        check()                                  # no points earned (no FEAT-005): must say bronze
        return
    pts = check()
    assert 500 <= pts < 2000
    order_and_wait((1750 - pts) * 100, 1500)     # 1500-1999: still silver
    pts = check()
    order_and_wait((2100 - pts) * 100, 2000)     # 2000+: gold
    assert check() >= 2000


# ---------------------------------------------------------------- FEAT-053

@pytest.mark.item("FEAT-053")
def test_feat053_cancellation_email(edge, mesh, shop, users, catalog, email):
    """Depends on FEAT-002 (POST /api/orders/<ref>/cancel); without it the test fails."""
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    require_cancel(edge, ref)
    assert wait_until(lambda: emails_for(email, ref, "order_cancelled"), 30), "no order_cancelled email"
    settle(mesh)
    msgs = emails_for(email, ref, "order_cancelled")
    assert len(msgs) == 1, msgs
    assert msgs[0]["data"]["order_ref"] == ref


# ---------------------------------------------------------------- FEAT-054

@pytest.mark.item("FEAT-054")
def test_feat054_tracking_url_in_dispatch_email(mesh, shop, users, catalog, email):
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    r = dispatch(mesh, ref)
    assert r.status_code == 200, r.text
    msgs = wait_until(lambda: emails_for(email, ref, "order_dispatched"), 30)
    assert msgs, "no order_dispatched email"
    assert msgs[-1]["data"].get("tracking_url") == f"https://track.larder.test/{ref}", msgs[-1]


# ---------------------------------------------------------------- FEAT-055

@pytest.mark.item("FEAT-055")
def test_feat055_no_email_to_larder_mail_domain(mesh, shop, users, catalog, email):
    """A larder-mail.test customer gets no confirmation/dispatch email; a normal customer whose
    events come after it on the bus still does. ('logged and counted' has no specified
    observable interface, so it is not checked.)"""
    started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))
    blocked = [x for x in users.legacy if x["email"].lower().endswith("@larder-mail.test")][-1]
    normal = pooled_user(users)
    p, q = catalog.pick(2)
    a = place_order(shop, blocked, [(p["sku"], qty_for(p))])["order_ref"]
    b = place_order(shop, normal, [(q["sku"], qty_for(q))])["order_ref"]
    assert dispatch(mesh, a).status_code == 200
    assert dispatch(mesh, b).status_code == 200
    # bus delivery is ordered per topic: once b's emails exist, a's events were handled
    assert wait_until(lambda: emails_for(email, b, "order_dispatched"), 30), "normal customer got no dispatch email"
    assert emails_for(email, b, "order_confirmation"), "normal customer got no confirmation email"
    settle(mesh)
    assert emails_for(email, a) == [], emails_for(email, a)
    leaked = [m for m in emails_to(email, blocked["email"]) if m["ts"] >= started]
    assert leaked == [], leaked


# ---------------------------------------------------------------- FEAT-056

@pytest.mark.item("FEAT-056")
def test_feat056_order_cancelled_webhook(edge, mesh, shop, users, catalog, partner):
    """Depends on FEAT-002 (POST /api/orders/<ref>/cancel); without it the test fails."""
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    require_cancel(edge, ref)
    assert wait_until(lambda: partner_events(partner, ref, "order.cancelled"), 30), "partner got no order.cancelled"
    settle(mesh, 4)
    assert len(partner_events(partner, ref, "order.cancelled")) == 1


# ---------------------------------------------------------------- FEAT-057

@pytest.mark.item("FEAT-057")
def test_feat057_open_orders_on_account(edge, mesh, shop, users, catalog):
    """Confirmed and not yet dispatched counts as open. If FEAT-002 cancellation exists, a
    cancelled order must not count either."""
    u = users.fresh()

    def open_orders():
        acc = edge.get(f"/api/account/{u['user_id']}").json()
        assert isinstance(acc.get("open_orders"), int), acc
        return acc["open_orders"]

    assert open_orders() == 0
    p, q, r = catalog.pick(3)
    refs = [place_order(shop, u, [(x["sku"], qty_for(x))])["order_ref"] for x in (p, q, r)]
    assert open_orders() == 3
    assert dispatch(mesh, refs[0]).status_code == 200
    assert open_orders() == 2
    if cancel_order(edge, refs[1]).status_code == 200:
        assert open_orders() == 1


# ---------------------------------------------------------------- FEAT-058

@pytest.mark.item("FEAT-058")
def test_feat058_webhook_stats(mesh, shop, users, catalog, partner):
    def stats():
        r = mesh("webhooks").get("/internal/webhooks/stats")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        s = r.json()
        for k in ("queue_depth", "delivered_total", "failed_attempts_total"):
            assert isinstance(s.get(k), int) and not isinstance(s.get(k), bool) and s[k] >= 0, s
        return s

    before = stats()
    u = pooled_user(users)
    [p] = catalog.pick(1)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    assert wait_until(lambda: partner_events(partner, ref, "order.confirmed"), 30)
    assert wait_until(lambda: stats()["delivered_total"] >= before["delivered_total"] + 1, 10), (before, stats())


# ---------------------------------------------------------------- FEAT-059

@pytest.mark.item("FEAT-059")
def test_feat059_picklists_by_date(mesh, shop, users, catalog):
    u = pooled_user(users)
    x, y = catalog.pick(2)
    mine = (x["sku"], y["sku"])

    def lines_for(date):
        r = mesh("warehouse").get("/internal/picklists", params={"date": date})
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        b = r.json()
        assert b.get("date") == date, b
        out = {}
        for ln in b["lines"]:
            out[ln["sku"]] = out.get(ln["sku"], 0) + ln["qty"]
        return {k: v for k, v in out.items() if k in mine and v}

    today = dt.date.today()
    before = {d: lines_for(d) for d in ((today + dt.timedelta(days=i)).isoformat() for i in range(-1, 10))}

    def expected(orders):
        exp = {d: dict(v) for d, v in before.items()}   # anything already open for these SKUs
        for date, lines in orders:
            exp.setdefault(date, {})
            for sku, q in lines.items():
                exp[date][sku] = exp[date].get(sku, 0) + q
        return exp

    o1 = place_order(shop, u, [(x["sku"], 2), (y["sku"], qty_for(y))])
    o2 = place_order(shop, u, [(x["sku"], qty_for(x))])
    d1 = order_internal(mesh, o1["order_ref"])["slot"]["date"]
    d2 = order_internal(mesh, o2["order_ref"])["slot"]["date"]
    l1, l2 = dict(o1["lines"]), dict(o2["lines"])

    exp = expected([(d1, l1), (d2, l2)])
    assert wait_until(lambda: all(lines_for(d) == e for d, e in exp.items()), 20), {d: lines_for(d) for d in exp}
    # dispatched orders drop off the list
    assert dispatch(mesh, o1["order_ref"]).status_code == 200
    exp = expected([(d2, l2)])
    assert wait_until(lambda: all(lines_for(d) == e for d, e in exp.items()), 20), {d: lines_for(d) for d in exp}
    # a date nobody delivers on
    assert lines_for("2001-01-01") == {}


# ---------------------------------------------------------------- FEAT-060

@pytest.mark.item("FEAT-060")
def test_feat060_cancelled_orders_are_not_dispatched(edge, mesh, shop, users, catalog, partner, email):
    """Depends on FEAT-002 (POST /api/orders/<ref>/cancel); without it the test fails."""
    u = pooled_user(users)
    p, q = catalog.pick(2)
    ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
    require_cancel(edge, ref)
    r = dispatch(mesh, ref)
    assert r.status_code == 409 and error_of(r) == "order_cancelled", f"{r.status_code} {r.text}"
    # barrier: a later dispatch's events arrive, so ours would have too
    other = place_order(shop, pooled_user(users), [(q["sku"], qty_for(q))])["order_ref"]
    assert dispatch(mesh, other).status_code == 200
    assert wait_until(lambda: partner_events(partner, other, "order.dispatched"), 30)
    assert wait_until(lambda: emails_for(email, other, "order_dispatched"), 30)
    settle(mesh)
    assert partner_events(partner, ref, "order.dispatched") == []
    assert emails_for(email, ref, "order_dispatched") == []
    assert edge.get(f"/api/orders/{ref}").json().get("status") == "cancelled"
