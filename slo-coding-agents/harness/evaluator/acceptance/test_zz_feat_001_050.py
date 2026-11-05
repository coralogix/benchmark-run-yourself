"""FEAT-001..050 checks that stop a service or need a slot hold to have expired.

They run after everything else (conftest sorts test_zz_* last) and always start
again any service they stop."""
from __future__ import annotations

import time

import httpx
import pytest

import _feat_helpers_a as H
from conftest import edge_url, mesh_url, wait_until


def _healthy(service):
    try:
        return httpx.get(mesh_url(service) + "/healthz", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


class _Stopped:
    """Stop a service for the duration of a with-block; always start it again."""

    def __init__(self, control, service):
        self.control, self.service = control, service

    def __enter__(self):
        self.control.c.post(f"/services/{self.service}/stop")
        if not wait_until(lambda: not _healthy(self.service), 20, 0.25):
            self.__exit__()
            raise AssertionError(f"{self.service} did not stop")
        return self

    def __exit__(self, *exc):
        self.control.c.post(f"/services/{self.service}/start")
        if not wait_until(lambda: _healthy(self.service), 90, 0.5):
            self.control.restart(self.service)
        return False


# =========================================================================== FEAT-005

@pytest.mark.item("FEAT-005")
def test_feat_005_checkout_works_while_loyalty_is_down(edge, control, users, catalog):
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1, lo=1250, hi=3999)
    with _Stopped(control, "loyalty"):
        res = H.place(edge, user, lines)  # asserts 201
    ref = res["order"]["order_ref"]
    v = H.order_view(edge, ref)
    pts = (v["subtotal_pence"] - v["discount_pence"]) // 100
    # once loyalty is back the order still earns its points
    got = wait_until(lambda: H.order_view(edge, ref).get("loyalty_points_earned"), 60)
    assert got == pts, (got, pts)
    assert wait_until(lambda: edge.get(f"/api/account/{user['user_id']}").json().get("loyalty_points") == pts, 60)


# =========================================================================== FEAT-023

@pytest.mark.item("FEAT-023")
def test_feat_023_request_id_in_5xx_bodies(edge, control):
    with _Stopped(control, "search"):
        r = edge.get("/api/search", params={"q": "apples"}, timeout=60)
        assert 500 <= r.status_code < 600, f"{r.status_code} {r.text}"
        rid = r.headers.get("X-Request-Id")
        b = r.json()
        assert rid and isinstance(b, dict) and b.get("request_id") == rid, (r.status_code, rid, b)
    assert wait_until(lambda: edge.get("/api/search", params={"q": "apples"}).status_code == 200, 30)


# =========================================================================== FEAT-025

@pytest.mark.item("FEAT-025")
def test_feat_025_deep_health_with_a_dependency_down(edge, control):
    with _Stopped(control, "recommendations"):
        t0 = time.perf_counter()
        r = edge.get("/api/healthz/deep", timeout=15)
        took = time.perf_counter() - t0
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        b = r.json()
        assert took < 2.25, f"took {took:.2f}s"
        assert b.get("ok") is False, b
        svcs = b.get("services") or {}
        assert svcs.get("recommendations") is False, svcs
        for s in ("catalog", "search", "cart", "checkout", "accounts", "inventory", "slots", "orders"):
            assert svcs.get(s) is True, (s, svcs)
    assert wait_until(lambda: edge.get("/api/healthz/deep").json().get("ok") is True, 30)


# =========================================================================== FEAT-040

@pytest.mark.item("FEAT-040")
def test_feat_040_expired_or_released_holds_rejected(edge, mesh, users, catalog):
    # a released hold
    user = users.fresh()
    lines, _ = H.regular_lines(catalog, 1)
    cid = H.new_cart(edge, user["user_id"], lines)
    hid, _ = H.hold(edge, cid, user["postcode"])
    r = mesh("slots").delete(f"/internal/slot-holds/{hid}")
    assert r.status_code == 200, r.text
    r = H.checkout(edge, cid, hid)
    assert r.status_code == 409 and H.err(r) == "hold_expired", f"released: {r.status_code} {r.text}"
    assert H.user_orders_total(mesh, user["user_id"]) == [], "an order (and charge) was made"

    # an expired hold: taken at the start of the run, tried by a background thread right
    # after its expires_at (see _early_slot_hold in test_feat_001_012.py)
    early = H.SHARED.get("early_hold")
    if not early:
        u2 = users.fresh()
        cid2 = H.new_cart(edge, u2["user_id"], H.regular_lines(catalog, 1)[0])
        slot = next(s for s in H.list_slots(edge, u2["postcode"])["slots"] if s.get("available"))
        rh = edge.post("/api/delivery/holds", json={"cart_id": cid2, "slot_id": slot["slot_id"]})
        assert rh.status_code == 201, rh.text
        early = {"user": u2, "cart_id": cid2, "hold_id": rh.json()["hold_id"], "expires_at": rh.json()["expires_at"]}
        H.start_expiry_probe(edge_url(), mesh_url("slots"), early)
    wait = H.iso_epoch(early["expires_at"]) - time.time()
    assert wait < 20 * 60, f"hold lifetime too long to test ({wait:.0f}s)"
    H.SHARED["early_thread"].join(timeout=max(0, wait) + 120)
    res = H.SHARED.get("early_result")
    assert res is not None, "expiry probe did not finish"
    assert res["status"] == 409 and (res["body"] or {}).get("error") == "hold_expired", f"expired: {res}"
    assert H.user_orders_total(mesh, early["user"]["user_id"]) == [], "an order (and charge) was made"
