import pytest


def _ready_cart(cart_svc, user_id="u-0003", sku="SKU-0050", qty=3, hold="H-test1"):
    cart = cart_svc.post("/internal/carts", json={"user_id": user_id}).get_json()["cart_id"]
    cart_svc.post("/internal/carts/%s/items" % cart, json={"sku": sku, "qty": qty})
    return cart, hold


def _checkout(checkout_svc, cart, hold, token="tok_visa"):
    return checkout_svc.post("/internal/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": token})


def _sent_bodies(mesh):
    return [c[4] for c in mesh.calls if c[0] == "orders" and c[1] == "POST" and c[2] == "/internal/orders"]


def test_checkout_happy_path(checkout_svc, cart_svc, mesh):
    cart, hold = _ready_cart(cart_svc)
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 201
    data = r.get_json()
    assert data["status"] == "confirmed"
    assert data["order_ref"].startswith("LD-")
    sent = _sent_bodies(mesh)[-1]
    assert sent["items"] == [{"sku": "SKU-0050", "qty": 3, "unit_price_pence": 249}]
    assert sent["email"] == "yusuf.evans3@example.test"
    assert sent["total_pence"] == data["total_pence"]
    assert sent["subtotal_pence"] == 747
    assert sent["delivery_fee_pence"] == 399
    assert sent["hold_id"] == hold


def test_checkout_with_coupon(checkout_svc, cart_svc, mesh):
    cart, hold = _ready_cart(cart_svc, qty=20)
    assert cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "WELCOME10"}).status_code == 200
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 201
    sent = _sent_bodies(mesh)[-1]
    assert sent["coupon"]["code"] == "WELCOME10"
    assert sent["discount_pence"] == 498


def test_checkout_declined(checkout_svc, cart_svc, ops):
    cart, hold = _ready_cart(cart_svc)
    ops.order_response = (402, {"error": "payment_declined"})
    r = _checkout(checkout_svc, cart, hold, token="tok_decline")
    assert r.status_code == 402
    assert r.get_json()["error"] == "payment_declined"


def test_checkout_out_of_stock(checkout_svc, cart_svc, ops):
    cart, hold = _ready_cart(cart_svc)
    ops.order_response = (409, {"error": "out_of_stock", "sku": "SKU-0050"})
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 409
    assert r.get_json() == {"error": "out_of_stock", "sku": "SKU-0050"}


def test_checkout_price_mismatch(checkout_svc, cart_svc, ops):
    cart, hold = _ready_cart(cart_svc)
    ops.order_response = (409, {"error": "price_mismatch", "expected_total_pence": 1})
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 409
    assert r.get_json()["error"] == "price_mismatch"


def test_checkout_payment_error(checkout_svc, cart_svc, ops):
    cart, hold = _ready_cart(cart_svc)
    ops.order_response = (502, {"error": "payment_error"})
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 502
    assert r.get_json()["error"] == "payment_error"


def test_checkout_empty_cart(checkout_svc, cart_svc):
    cart = cart_svc.post("/internal/carts", json={"user_id": "u-0001"}).get_json()["cart_id"]
    r = _checkout(checkout_svc, cart, "H-1")
    assert r.status_code == 400
    assert r.get_json()["error"] == "empty_cart"


def test_checked_out_cart_is_closed(checkout_svc, cart_svc):
    cart, hold = _ready_cart(cart_svc)
    assert _checkout(checkout_svc, cart, hold).status_code == 201
    assert _checkout(checkout_svc, cart, hold).status_code == 409


def test_guest_cart_checks_out_without_email(checkout_svc, cart_svc, mesh):
    cart, hold = _ready_cart(cart_svc, user_id="guest-123")
    assert _checkout(checkout_svc, cart, hold).status_code == 201
    assert _sent_bodies(mesh)[-1]["email"] is None


def test_account_after_checkout(checkout_svc, cart_svc, accounts):
    cart, hold = _ready_cart(cart_svc, user_id="u-0005")
    ref = _checkout(checkout_svc, cart, hold).get_json()["order_ref"]
    data = accounts.get("/internal/accounts/u-0005").get_json()
    assert data["orders_count"] >= 1
    assert data["last_order_ref"] == ref


@pytest.mark.skip(reason="flaky on CI - JW 2021")
def test_checkout_orders_timeout(checkout_svc, cart_svc, mesh):
    cart, hold = _ready_cart(cart_svc)
    mesh.down.add("orders")
    r = _checkout(checkout_svc, cart, hold)
    assert r.status_code == 504
