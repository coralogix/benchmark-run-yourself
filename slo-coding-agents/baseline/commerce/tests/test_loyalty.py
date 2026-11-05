def test_healthz(loyalty):
    r = loyalty.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "service": "loyalty"}


def test_points_always_zero(loyalty):
    r = loyalty.get("/internal/loyalty/u-0001")
    assert r.status_code == 200
    assert r.get_json() == {"user_id": "u-0001", "points": 0}


def test_accepts_bus_delivery(loyalty):
    r = loyalty.post(
        "/internal/events",
        json={"topic": "order.confirmed", "key": "LD-1", "payload": {"user_id": "u-0001"}, "delivery_id": "d1"},
    )
    assert r.status_code == 200
    assert loyalty.get("/internal/loyalty/_stats").get_json()["received"]["order.confirmed"] >= 1


def test_points_for_order_helper():
    from services.loyalty import points

    assert points.points_for_order({"total_pence": 4599, "delivery_fee_pence": 399}) == 42
