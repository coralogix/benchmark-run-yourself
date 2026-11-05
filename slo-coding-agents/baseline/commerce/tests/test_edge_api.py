import pytest


def _cart(edge, user="u-0003"):
    return edge.post("/api/carts", json={"user_id": user}).get_json()["cart_id"]


def _ready_cart(edge, sku="SKU-0050", qty=3, user="u-0003"):
    cart = _cart(edge, user)
    edge.post("/api/carts/%s/items" % cart, json={"sku": sku, "qty": qty})
    hold = edge.post("/api/delivery/holds", json={"cart_id": cart, "slot_id": "SL-Z1-2"}).get_json()
    return cart, hold["hold_id"]


def test_healthz(edge):
    r = edge.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "service": "edge-api"}


def test_products_first_page(edge):
    data = edge.get("/api/products").get_json()
    assert data["page"] == 1
    assert data["per_page"] == 20
    assert len(data["items"]) == 20
    assert data["total"] >= 200


def test_product_shape(edge):
    item = edge.get("/api/products?per_page=1").get_json()["items"][0]
    assert set(item) == {"sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock"}


def test_in_stock_uses_one_bulk_call(edge, mesh, ops):
    ops.stock["SKU-0001"] = 0
    data = edge.get("/api/products?per_page=10").get_json()
    assert mesh.count("inventory", "POST", "/internal/inventory/availability") == 1
    by_sku = {i["sku"]: i for i in data["items"]}
    assert by_sku["SKU-0001"]["in_stock"] is False
    assert by_sku["SKU-0002"]["in_stock"] is True


def test_in_stock_null_when_inventory_down(edge, mesh):
    mesh.down.add("inventory")
    r = edge.get("/api/products?per_page=3")
    assert r.status_code == 200
    assert all(i["in_stock"] is None for i in r.get_json()["items"])


def test_product_detail_has_also_bought(edge):
    data = edge.get("/api/products/SKU-0050").get_json()
    assert data["sku"] == "SKU-0050"
    assert "description" in data
    assert isinstance(data["also_bought"], list)
    assert all("in_stock" in p for p in data["also_bought"])
    assert "SKU-0050" not in [p["sku"] for p in data["also_bought"]]


def test_product_detail_without_recommendations(edge, mesh):
    mesh.down.add("recommendations")
    r = edge.get("/api/products/SKU-0002")
    assert r.status_code == 200
    assert "also_bought" not in r.get_json()


def test_product_detail_404(edge):
    r = edge.get("/api/products/SKU-9999")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_search(edge):
    data = edge.get("/api/search?q=apples").get_json()
    assert data["query"] == "apples"
    assert data["count"] == len(data["results"]) > 0
    assert all("in_stock" in p for p in data["results"])


def test_cart_flow(edge):
    cart = _cart(edge)
    data = edge.post("/api/carts/%s/items" % cart, json={"sku": "SKU-0050", "qty": 2}).get_json()
    assert data["subtotal_pence"] == 498
    assert data["delivery_fee_pence"] == 399
    assert data["total_pence"] == 897
    got = edge.get("/api/carts/%s" % cart).get_json()
    for key in ("cart_id", "user_id", "items", "subtotal_pence", "discount_pence", "delivery_fee_pence",
                "total_pence", "total", "coupon"):
        assert key in got


def test_coupon(edge):
    cart = _cart(edge)
    edge.post("/api/carts/%s/items" % cart, json={"sku": "SKU-0050", "qty": 20})
    data = edge.post("/api/carts/%s/coupon" % cart, json={"code": "WELCOME10"}).get_json()
    assert data["coupon"] == "WELCOME10"
    assert data["discount_pence"] == 498


def test_slots_for_postcode(edge):
    data = edge.get("/api/delivery/slots?postcode=LD1 1AA").get_json()
    assert data["zone"] == "Z1"
    assert [s["available"] for s in data["slots"]] == [False, True]


def test_slots_squashed_postcode(edge):
    assert edge.get("/api/delivery/slots?postcode=ld11aa").get_json()["zone"] == "Z1"


def test_hold_full_slot(edge, ops):
    ops.full_slots.add("SL-Z1-1")
    r = edge.post("/api/delivery/holds", json={"cart_id": _cart(edge), "slot_id": "SL-Z1-1"})
    assert r.status_code == 409
    assert r.get_json()["error"] == "slot_full"


def test_checkout_happy_path(edge, ops):
    cart, hold = _ready_cart(edge)
    r = edge.post("/api/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": "tok_visa"})
    assert r.status_code == 201
    data = r.get_json()
    assert data["status"] == "confirmed"
    assert data["total_pence"] == 1146
    sent = ops.orders[-1]
    assert sent["items"] == [{"sku": "SKU-0050", "qty": 3, "unit_price_pence": 249}]


def test_checkout_declined(edge, ops):
    cart, hold = _ready_cart(edge)
    ops.order_response = (402, {"error": "payment_declined"})
    r = edge.post("/api/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": "tok_decline"})
    assert r.status_code == 402
    assert r.get_json() == {"error": "payment_declined"}


def test_order_view(edge):
    cart, hold = _ready_cart(edge)
    ref = edge.post("/api/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": "tok_visa"})
    ref = ref.get_json()["order_ref"]
    data = edge.get("/api/orders/%s" % ref).get_json()
    assert data["order_ref"] == ref
    assert data["status"] == "confirmed"
    assert data["items"][0]["qty"] == 3
    assert data["items"][0]["name"]


def test_account(edge):
    cart, hold = _ready_cart(edge, user="u-0007")
    ref = edge.post("/api/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": "tok_visa"})
    data = edge.get("/api/account/u-0007").get_json()
    assert data["user_id"] == "u-0007"
    assert data["orders_count"] == 1
    assert data["last_order_ref"] == ref.get_json()["order_ref"]


def test_v1_products(edge):
    r = edge.get("/v1/products?limit=5")
    assert r.status_code == 200
    assert r.headers["X-Deprecated"].startswith("sunset=")
    assert len(r.get_json()["products"]) == 5


def test_v1_price_check(edge):
    r = edge.post("/v1/price-check", json={"lines": [{"sku": "SKU-0050", "qty": 2}]})
    assert r.status_code == 200
    assert r.get_json()["total"] == pytest.approx(8.97)


@pytest.mark.skip(reason="flaky on CI - JW 2021")
def test_checkout_orders_timeout(edge, mesh):
    cart, hold = _ready_cart(edge)
    mesh.down.add("orders")
    r = edge.post("/api/checkout", json={"cart_id": cart, "hold_id": hold, "payment_token": "tok_visa"})
    assert r.status_code == 504
