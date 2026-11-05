"""
Smoke test against a running edge-api (after a deploy, or with run_all).

    uv run python -m scripts.smoke http://127.0.0.1:8101

Walks the happy path: products -> detail -> search -> cart -> coupon ->
slots -> hold -> checkout -> order -> account. Exits non-zero on the first
failure. Needs ops (inventory, slots, orders) to be reachable from edge-api.
"""
import sys
import time

import requests

DEFAULT_BASE = "http://127.0.0.1:8000"
USER = "u-0001"
POSTCODE = "LD1 1AA"
# 20 x SKU-0001 so WELCOME10 doesn't trip any min spend
SKU, QTY = "SKU-0001", 20
COUPON = "WELCOME10"


def _check(label, resp, want=(200,)):
    ok = resp.status_code in want
    print("%-10s %s %s %.0fms" % (label, resp.status_code, "ok" if ok else "FAIL",
                                   resp.elapsed.total_seconds() * 1000))
    if not ok:
        print("  ", resp.text[:300])
        sys.exit(1)
    return resp.json()


def main(base=DEFAULT_BASE):
    s = requests.Session()
    t0 = time.time()
    _check("products", s.get(base + "/api/products"))
    _check("detail", s.get(base + "/api/products/" + SKU))
    _check("search", s.get(base + "/api/search", params={"q": "apples"}))
    cart = _check("cart", s.post(base + "/api/carts", json={"user_id": USER}), (201,))["cart_id"]
    _check("add", s.post(base + "/api/carts/%s/items" % cart, json={"sku": SKU, "qty": QTY}))
    _check("coupon", s.post(base + "/api/carts/%s/coupon" % cart, json={"code": COUPON}))
    slots = _check("slots", s.get(base + "/api/delivery/slots", params={"postcode": POSTCODE}))
    free = [x for x in slots["slots"] if x["available"]]
    if not free:
        print("no free slots for", POSTCODE)
        return 1
    hold = _check("hold", s.post(base + "/api/delivery/holds", json={"cart_id": cart, "slot_id": free[0]["slot_id"]}),
                  (201,))
    order = _check("checkout", s.post(base + "/api/checkout", json={"cart_id": cart, "hold_id": hold["hold_id"],
                                                                     "payment_token": "tok_visa"}), (201,))
    _check("order", s.get(base + "/api/orders/" + order["order_ref"]))
    _check("account", s.get(base + "/api/account/" + USER))
    print("smoke ok in %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE))
