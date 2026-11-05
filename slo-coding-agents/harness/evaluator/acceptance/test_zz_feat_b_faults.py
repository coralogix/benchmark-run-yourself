"""Acceptance tests (FEAT-051..100 range) that need faults or service restarts. They run last.

  FEAT-071  bus lag: notifications (an order.confirmed/order.dispatched subscriber) is stopped so an
            event stays pending, then started again.
  FEAT-058  failure counters, with partner-sim failing. Partner mode is restored.
  FEAT-072  dead letters: loyalty (the last order.confirmed subscriber) is stopped so its deliveries
            fail, then started again.
  FEAT-076  24h cart expiry: GREY-BOX. There is no clock control in eval mode and waiting 24h is
            impossible, so the test stops the cart service, moves one cart's timestamps 25h into the
            past in <workspace>/commerce/var/cart/cart.db (every *_at/created/updated column of every
            table keyed by cart_id) and starts cart again. Needs $LARDER_EVAL_WORKSPACE (set by
            harness.evaluator.evaluate).
"""
from __future__ import annotations

import time

import httpx
import pytest

from _feat_helpers_b import (age_cart_in_db, emails_for, error_of, find_list, mesh_url, partner_events,
                             place_order, pooled_user, qty_for, start_service, stop_service, wait_healthy, workspace)
from conftest import wait_until


def _topics(mesh):
    r = mesh("bus").get("/internal/bus/topics")
    assert r.status_code == 200
    return {t["topic"]: t for t in r.json()["topics"]}


# ---------------------------------------------------------------- FEAT-071

@pytest.mark.item("FEAT-071")
def test_feat071_bus_lag_per_topic(mesh, shop, users, catalog):
    def age(t):
        v = t.get("oldest_pending_age_s")
        assert isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0, t
        return v

    assert wait_until(lambda: all(t.get("depth") == 0 for t in _topics(mesh).values()), 30)
    assert all(age(t) == 0 for t in _topics(mesh).values()), _topics(mesh)
    u = pooled_user(users)
    [p] = catalog.pick(1)
    stop_service("notifications")
    try:
        t0 = time.time()
        place_order(shop, u, [(p["sku"], qty_for(p))])
        # the event waits at notifications; its age grows (checked before any dead-lettering could kick in)
        seen = wait_until(lambda: (lambda a: a if a >= 1.5 else None)(age(_topics(mesh)["order.confirmed"])), 4, 0.2)
        assert seen, _topics(mesh)["order.confirmed"]
        assert seen <= time.time() - t0 + 1, (seen, time.time() - t0)
    finally:
        start_service("notifications")
    assert wait_until(lambda: _topics(mesh)["order.confirmed"].get("depth") == 0, 60)
    assert age(_topics(mesh)["order.confirmed"]) == 0


# ---------------------------------------------------------------- FEAT-058

@pytest.mark.item("FEAT-058")
def test_feat058_webhook_stats_count_failures(mesh, shop, users, catalog, partner):
    def stats():
        r = mesh("webhooks").get("/internal/webhooks/stats")
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        return r.json()

    u = pooled_user(users)
    [p] = catalog.pick(1)
    before = stats()
    partner.mode(failure_rate=1.0)
    try:
        ref = place_order(shop, u, [(p["sku"], qty_for(p))])["order_ref"]
        assert wait_until(lambda: stats()["failed_attempts_total"] > before["failed_attempts_total"], 20), stats()
        assert stats()["queue_depth"] >= 1, stats()
    finally:
        partner.mode(preset="normal")
    assert wait_until(lambda: partner_events(partner, ref, "order.confirmed"), 30)
    assert wait_until(lambda: stats()["delivered_total"] > before["delivered_total"], 10), stats()


# ---------------------------------------------------------------- FEAT-072

def _dead_letters(mesh):
    r = mesh("bus").get("/internal/bus/dead-letters")
    if r.status_code != 200:
        return None
    return find_list(r.json(), "dead_letters", "dead-letters", "deadLetters", "items", "messages") or []


def _confirmed_depth(mesh):
    topics = mesh("bus").get("/internal/bus/topics").json()["topics"]
    return next(t["depth"] for t in topics if t["topic"] == "order.confirmed")


@pytest.mark.item("FEAT-072")
def test_feat072_dead_letter_queue(mesh, shop, users, catalog, email):
    u = pooled_user(users)
    a_sku, b_sku, c_sku = catalog.pick(3)
    stop_service("loyalty")
    try:
        a = place_order(shop, u, [(a_sku["sku"], qty_for(a_sku))])["order_ref"]
        entry = wait_until(lambda: [d for d in (_dead_letters(mesh) or [])
                                    if d.get("key") == a and d.get("subscriber") == "loyalty"], 45)
        assert entry, f"no dead letter for {a}/loyalty: {_dead_letters(mesh)}"
        d = entry[0]
        assert d.get("topic") == "order.confirmed" and d.get("attempts") == 5 and d.get("last_error"), d
        # the topic is no longer wedged behind it
        b = place_order(shop, u, [(b_sku["sku"], qty_for(b_sku))])["order_ref"]
        assert wait_until(lambda: emails_for(email, b, "order_confirmation"), 30), "later event still blocked"
    finally:
        start_service("loyalty")
    c = place_order(shop, u, [(c_sku["sku"], qty_for(c_sku))])["order_ref"]
    assert wait_until(lambda: emails_for(email, c, "order_confirmation") and _confirmed_depth(mesh) == 0, 45), \
        "subscriber still blocked after it recovered"
    assert not [x for x in (_dead_letters(mesh) or []) if x.get("key") == c], "healthy delivery was dead-lettered"


# ---------------------------------------------------------------- FEAT-076

@pytest.mark.item("FEAT-076")
def test_feat076_abandoned_cart_expiry(edge, shop, users, catalog):
    ws = workspace()
    assert ws, "LARDER_EVAL_WORKSPACE is not set: cannot age a cart"
    db = ws / "commerce" / "var" / "cart" / "cart.db"
    assert db.exists(), db
    u = pooled_user(users)
    [p] = catalog.pick(1)
    carts = {}
    for name in ("read", "modify", "day_old", "active"):
        carts[name] = shop.cart(u["user_id"])
        assert shop.add(carts[name], p["sku"], 1).status_code == 200
    stop_service("cart")

    def down():
        try:
            return httpx.get(mesh_url("cart") + "/healthz", timeout=2).status_code != 200
        except httpx.HTTPError:
            return True
    assert wait_until(down, 30), "cart did not stop"
    time.sleep(1)
    try:
        assert age_cart_in_db(db, carts["read"], 25) and age_cart_in_db(db, carts["modify"], 25)
        assert age_cart_in_db(db, carts["day_old"], 23)
    finally:
        start_service("cart")
    assert wait_healthy("cart")
    r = edge.get(f"/api/carts/{carts['read']}")
    assert r.status_code == 404 and error_of(r) == "cart_expired", f"read: {r.status_code} {r.text[:200]}"
    r = shop.add(carts["modify"], p["sku"], 1)
    assert r.status_code == 404 and error_of(r) == "cart_expired", f"modify: {r.status_code} {r.text[:200]}"
    for name in ("day_old", "active"):
        assert edge.get(f"/api/carts/{carts[name]}").status_code == 200, name
        assert shop.add(carts[name], p["sku"], 1).status_code == 200, name
