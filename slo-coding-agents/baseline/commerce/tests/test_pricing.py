from services.pricing import engine_v2, engines


def test_engine_in_tests_is_v2():
    assert engines.get_engine() is engine_v2


def test_price_cart_simple():
    b = engine_v2.price_cart([{"sku": "A", "name": "A", "unit_price": 1.25, "qty": 2}])
    assert b["subtotal_pence"] == 250
    assert b["delivery_fee_pence"] == 399
    assert b["total_pence"] == 649


def test_price_cart_percent_coupon():
    b = engine_v2.price_cart([{"sku": "A", "name": "A", "unit_price": 10.0, "qty": 5}], {"percent_off": 10})
    assert b["discount_pence"] == 500
    assert b["delivery_fee_pence"] == 0
    assert b["total_pence"] == 4500


def test_price_endpoint(pricing):
    r = pricing.post("/internal/price", json={"lines": [{"sku": "SKU-0050", "qty": 2}]})
    assert r.status_code == 200
    data = r.get_json()
    assert data["engine"] == "v2"
    assert data["lines"][0]["unit_price_pence"] == 249
    assert data["lines"][0]["line_total_pence"] == 498
    assert data["total_pence"] == 897
    assert data["coupon"] is None


def test_price_with_coupon_code(pricing):
    data = pricing.post(
        "/internal/price", json={"lines": [{"sku": "SKU-0050", "qty": 20}], "coupon_code": "WELCOME10"}
    ).get_json()
    assert data["coupon"]["code"] == "WELCOME10"
    assert data["discount_pence"] == 498


def test_validate_coupon_passes_through_refusal(pricing):
    r = pricing.post(
        "/internal/price",
        json={"lines": [{"sku": "SKU-0050", "qty": 1}], "coupon_code": "FAMILY12", "validate_coupon": True},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "min_spend"


def test_live_price(pricing):
    assert pricing.get("/internal/prices/SKU-0050").get_json() == {"sku": "SKU-0050", "price": 2.49,
                                                                    "price_pence": 249}
    assert pricing.get("/internal/prices/SKU-9999").status_code == 404


def test_price_check_v1_endpoint(pricing):
    r = pricing.post("/internal/price-check", json={"lines": [{"sku": "SKU-0050", "qty": 20}]})
    assert r.get_json()["total"] == 49.8
