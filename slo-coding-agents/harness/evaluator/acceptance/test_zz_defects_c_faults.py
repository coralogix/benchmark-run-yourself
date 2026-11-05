"""Seeded defects that need an injected fault: FF-3, FF-2, SF-4, FF-4, FF-5.

Every test restores what it changed (simulator modes, frozen/wedged services) in a
`finally`, so later tests start from a healthy system whether or not it passed.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import Counter

import httpx
import pytest

from conftest import edge_url, mesh_url, wait_until
from larder_support import freeze, thaw, wait_healthy


def _orders_of(mesh, user_id):
    r = mesh("orders").get("/internal/orders", params={"user_id": user_id, "page": 1, "per_page": 100})
    assert r.status_code == 200, r.text
    return r.json()["orders"]


def _captures(psp, refs):
    charges = psp.get("/admin/ledger")["charges"]
    return Counter(str(c["order_ref"]) for c in charges if str(c["order_ref"]) in refs)


def _fill_cart(shop, user, catalog, max_price=None):
    """A cart every backlog feature lets through checkout (goods 1000..3999p, no 3for2, <= 24/line)."""
    return shop.safe_cart(user["user_id"], 1, max_price=max_price)


# ------------------------------------------------------------------ FF-3

@pytest.mark.item("FF-3")
def test_ff3_slot_full_rejections_do_not_wedge_holds_or_checkout(shop, mesh, users, catalog, control):
    """45 hold attempts against a full slot (more than the slots service's 36-connection pool),
    then an ordinary hold and checkout. Baseline leaks a pool connection on every slot_full
    answer, so once the pool is gone holds and order confirmations fail after a 5s wait."""
    slots = mesh("slots").get("/internal/slots", params={"zone": "Z1"}).json()["slots"]
    full = next(s for s in slots if s["booked"] >= s["capacity"])
    user = users.fresh()
    try:
        url = mesh_url("slots") + "/internal/slot-holds"
        codes = []

        def attempt(i):
            try:
                with httpx.Client(timeout=15) as c:
                    r = c.post(url, json={"slot_id": full["slot_id"], "cart_ref": f"acc-ff3-{i}"})
            except httpx.HTTPError as e:
                codes.append((0, type(e).__name__))
                return
            try:
                err = r.json().get("error")
            except ValueError:
                err = r.text[:40]
            codes.append((r.status_code, err))

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(attempt, range(45)))
        bad = [c for c in codes if c != (409, "slot_full")]
        assert not bad, f"{len(bad)}/45 holds on a full slot did not answer 409 slot_full: {Counter(bad)}"

        cid = _fill_cart(shop, user, catalog)
        t = time.time()
        with httpx.Client(base_url=edge_url(), timeout=10) as c:
            free = next(s for s in c.get("/api/delivery/slots", params={"postcode": user["postcode"]}).json()["slots"]
                        if s["available"])
            try:
                r = c.post("/api/delivery/holds", json={"cart_id": cid, "slot_id": free["slot_id"]})
            except httpx.TimeoutException:
                pytest.fail("ordinary hold timed out (>10s) after the slot_full burst")
            took = time.time() - t
            assert r.status_code == 201 and took < 2.0, f"ordinary hold: {r.status_code} {r.text[:100]} in {took:.1f}s"
            t = time.time()
            r = c.post("/api/checkout", json={"cart_id": cid, "hold_id": r.json()["hold_id"], "payment_token": "tok_ok"})
            took = time.time() - t
            assert r.status_code == 201 and took < 2.0, f"checkout: {r.status_code} {r.text[:100]} in {took:.1f}s"
    finally:
        control.restart("slots")
        wait_healthy(mesh_url("slots"), 60)


# ------------------------------------------------------------------ FF-2

@pytest.mark.item("FF-2")
def test_ff2_slow_psp_success_captures_at_most_once(shop, mesh, psp, users, catalog):
    """Every PSP charge succeeds but only after 9s, longer than payments' PSP timeout.
    Baseline payments retries the charge without an Idempotency-Key: 4 captures per order."""
    user = users.fresh()
    cid = _fill_cart(shop, user, catalog)
    hid = shop.hold(cid, user["postcode"])
    psp.mode(slow_success_rate=1.0, slow_success_ms=9000)
    t0 = time.time()
    try:
        with httpx.Client(base_url=edge_url(), timeout=90) as c:
            r = c.post("/api/checkout", json={"cart_id": cid, "hold_id": hid, "payment_token": "tok_ok"})
        assert r.status_code in (201, 402, 502, 503, 504), r.text
    finally:
        psp.mode(preset="normal")
    refs = {o["order_ref"] for o in _orders_of(mesh, user["user_id"])}
    assert refs, "the checkout left no order record at all"
    # charges complete in the PSP even if the caller gave up; wait for any stragglers
    caps = Counter()
    while time.time() < t0 + 22:
        caps = _captures(psp, refs)
        if any(n > 1 for n in caps.values()):
            break
        time.sleep(1)
    dup = {ref: n for ref, n in caps.items() if n > 1}
    assert not dup, f"duplicate captures per order_ref: {dup} (checkout answered {r.status_code} {r.text[:80]})"


# ------------------------------------------------------------------ SF-4

@pytest.mark.item("SF-4")
def test_sf4_checkout_timing_out_on_orders_does_not_duplicate_orders(shop, mesh, psp, users, catalog):
    """POST /internal/orders is made to take ~34s while every single downstream call stays inside
    its own 30s timeout: inventory hangs for 25s (frozen), then slots hangs for 9s, after which the
    order completes normally. The mesh answers checkout 504 at 30s, while the first attempt is
    still in flight. Baseline checkout's shared client retries the POST with the same order_ref,
    so both attempts complete: two orders and two captures for one order_ref.

    checkout is called on its mesh port (the call edge makes) so that edge's own retry of the
    checkout POST (a second order_ref competing for the same slot hold) cannot race the result.
    """
    user = users.fresh()
    cid = _fill_cart(shop, user, catalog)
    hid = shop.hold(cid, user["postcode"])
    result = {}

    def do_checkout():
        try:
            with httpx.Client(base_url=mesh_url("checkout"), timeout=150) as c:
                r = c.post("/internal/checkout", json={"cart_id": cid, "hold_id": hid, "payment_token": "tok_ok"})
            result["r"] = (r.status_code, r.text[:200])
        except httpx.HTTPError as e:
            result["r"] = (0, repr(e))

    th = threading.Thread(target=do_checkout)
    frozen = ["inventory"]
    freeze("inventory")
    try:
        th.start()
        time.sleep(25)
        freeze("slots")
        frozen.append("slots")
        thaw("inventory")
        frozen.remove("inventory")
        time.sleep(9)
    finally:
        for svc in frozen:
            thaw(svc)
    th.join(120)
    for svc in ("inventory", "slots", "orders"):
        wait_healthy(mesh_url(svc), 30)
    time.sleep(3)  # let anything still in flight in orders finish
    orders = _orders_of(mesh, user["user_id"])
    per_ref = Counter(o["order_ref"] for o in orders)
    caps = _captures(psp, set(per_ref))
    dup_orders = {k: v for k, v in per_ref.items() if v > 1}
    dup_caps = {k: v for k, v in caps.items() if v > 1}
    assert not dup_orders and not dup_caps, (f"duplicates for one order_ref: orders {dup_orders}, captures "
                                             f"{dup_caps} (all orders {dict(per_ref)}; checkout {result.get('r')})")


# ------------------------------------------------------------------ FF-4

@pytest.mark.item("FF-4")
def test_ff4_partner_outage_retries_back_off_and_all_events_arrive(shop, partner, users, catalog):
    """Partner fails every webhook for 60s. Baseline retries every failed event on every 2s tick
    (~30 attempts per event per minute, each copied into the persisted queue). Fixed: bounded
    attempts (backoff), webhooks stays responsive, and every event is delivered after recovery."""
    buyers = [users.fresh(), users.fresh()]
    before = partner.get("/admin/stats")["received_total"]
    partner.mode(failure_rate=1.0)
    lat = []
    refs = []
    try:
        t0 = time.time()
        for i in range(8):
            u = buyers[i % 2]
            refs.append(shop.order(u, shop.safe_basket(1))["order_ref"])
        with httpx.Client(timeout=10) as c:
            while time.time() < t0 + 60:
                t = time.perf_counter()
                try:
                    ok = c.get(mesh_url("webhooks") + "/healthz").status_code == 200
                except httpx.HTTPError:
                    ok = False
                lat.append(time.perf_counter() - t if ok else 10.0)
                time.sleep(1)
        during = partner.get("/admin/stats")["received_total"] - before
    finally:
        partner.mode(preset="normal")

    def delivered():
        got = {d["order_ref"] for d in partner.get("/admin/deliveries", params={"limit": 500})["deliveries"]
               if d.get("event") == "order.confirmed"}
        return set(refs) <= got

    arrived = wait_until(delivered, timeout_s=75, interval_s=1)
    per_event = during / len(refs)
    assert per_event <= 15, f"{during} partner attempts for {len(refs)} events in a 60s outage ({per_event:.1f}/event)"
    assert max(lat) < 1.0, f"webhooks /healthz slow during the outage: max {max(lat):.2f}s"
    assert arrived, "not every order.confirmed event reached the partner within 75s of recovery"


# ------------------------------------------------------------------ FF-5

@pytest.mark.item("FF-5")
def test_ff5_slow_email_does_not_delay_other_subscribers(shop, partner, email, users, catalog):
    """The email provider takes 8s per message. Baseline bus delivers each order.confirmed to its
    subscribers one at a time and message by message, so notifications (waiting on email) holds
    up webhooks/warehouse/ledger/loyalty for every later order. Fixed: other subscribers get the
    event within a few seconds of confirmation."""
    user = users.fresh()
    email.mode(latency_p50_ms=8000, latency_p99_ms=8000)
    confirmed, arrived = {}, {}
    try:
        for _ in range(4):
            ref = shop.order(user, shop.safe_basket(1))["order_ref"]
            confirmed[ref] = time.time()
        deadline = max(confirmed.values()) + 15
        while time.time() < deadline and len(arrived) < len(confirmed):
            now = time.time()
            for d in partner.get("/admin/deliveries", params={"limit": 500})["deliveries"]:
                ref = d.get("order_ref")
                if d.get("event") == "order.confirmed" and ref in confirmed and ref not in arrived:
                    arrived[ref] = now
            time.sleep(0.25)
    finally:
        email.mode(preset="normal")
    lag = {ref: round(arrived[ref] - t, 1) if ref in arrived else None for ref, t in confirmed.items()}
    late = {ref: v for ref, v in lag.items() if v is None or v > 6.0}
    assert not late, f"order.confirmed reached the partner late (s after confirmation; None = >15s): {lag}"
