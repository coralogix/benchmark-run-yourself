import pytest


@pytest.fixture()
def cart(cart_svc):
    return cart_svc.post("/internal/carts", json={"user_id": "u-0001"}).get_json()["cart_id"]


def _add(cart_svc, cart, sku="SKU-0050", qty=1):
    return cart_svc.post("/internal/carts/%s/items" % cart, json={"sku": sku, "qty": qty})


def test_create_cart(cart_svc):
    r = cart_svc.post("/internal/carts", json={"user_id": "u-0001"})
    assert r.status_code == 201
    assert r.get_json()["cart_id"].startswith("c_")


def test_add_item(cart_svc, cart):
    data = _add(cart_svc, cart, qty=2).get_json()
    assert data["items"][0]["sku"] == "SKU-0050"
    assert data["items"][0]["qty"] == 2
    assert data["items"][0]["unit_price_pence"] == 249


def test_add_same_item_twice_merges(cart_svc, cart):
    _add(cart_svc, cart, qty=1)
    data = _add(cart_svc, cart, qty=2).get_json()
    assert len(data["items"]) == 1
    assert data["items"][0]["qty"] == 3


def test_add_unknown_sku(cart_svc, cart):
    r = _add(cart_svc, cart, sku="NOPE")
    assert r.status_code == 404
    assert r.get_json()["error"] == "unknown_sku"


def test_cart_totals_small_basket_pays_delivery(cart_svc, cart):
    data = _add(cart_svc, cart, qty=2).get_json()
    assert data["subtotal_pence"] == 498
    assert data["delivery_fee_pence"] == 399
    assert data["total_pence"] == 897
    assert data["total"] == 8.97


def test_get_cart_shape(cart_svc, cart):
    r = cart_svc.get("/internal/carts/%s" % cart)
    assert r.status_code == 200
    for key in ("cart_id", "user_id", "items", "subtotal_pence", "discount_pence", "delivery_fee_pence",
                "total_pence", "total", "coupon"):
        assert key in r.get_json()


def test_apply_coupon(cart_svc, cart, mesh):
    _add(cart_svc, cart, qty=20)
    r = cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "welcome10"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["coupon"] == "WELCOME10"
    assert data["discount_pence"] == 498
    # cart never talks to promotions itself
    assert not [c for c in mesh.calls if c[0] == "promotions" and c[2].startswith("/internal/carts")]


def test_coupon_refusals_pass_through(cart_svc, cart):
    _add(cart_svc, cart, qty=1)
    r = cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "NOPE"})
    assert (r.status_code, r.get_json()["error"]) == (400, "invalid_coupon")
    r = cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "FAMILY12"})
    assert r.get_json()["error"] == "min_spend"


def test_hold_and_lines(cart_svc, cart):
    _add(cart_svc, cart, qty=3)
    r = cart_svc.put("/internal/carts/%s/hold" % cart, json={"hold_id": "H-1", "slot_id": "SL-Z1-2"})
    assert r.get_json()["hold_id"] == "H-1"
    data = cart_svc.get("/internal/carts/%s/lines" % cart).get_json()
    assert data["lines"] == [{"sku": "SKU-0050", "qty": 3}]
    assert data["hold_id"] == "H-1"


def test_closed_cart_rejects_changes(cart_svc, cart):
    cart_svc.post("/internal/carts/%s/close" % cart, json={"order_ref": "LD-1"})
    assert _add(cart_svc, cart).status_code == 409
    assert cart_svc.get("/internal/carts/%s" % cart).get_json()["status"] == "checked_out"


def test_cart_reloads_from_db(cart_svc, cart):
    from services.cart import state

    _add(cart_svc, cart, qty=2)
    state._CARTS.pop(cart)
    data = cart_svc.get("/internal/carts/%s" % cart).get_json()
    assert data["items"][0]["qty"] == 2


@pytest.mark.xfail(reason="coupon stacking not supported yet (PROMO-88)")
def test_coupon_stacking(cart_svc, cart):
    _add(cart_svc, cart, qty=20)
    cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "WELCOME10"})
    data = cart_svc.post("/internal/carts/%s/coupon" % cart, json={"code": "LARDER5"}).get_json()
    assert data["coupon"] == ["WELCOME10", "LARDER5"]
