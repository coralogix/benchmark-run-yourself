def test_get_user(accounts):
    r = accounts.get("/internal/users/u-0001")
    assert r.status_code == 200
    data = r.get_json()
    assert data["user_id"] == "u-0001"
    assert data["email"] == "quinn.murphy1@example.test"


def test_unknown_user_404(accounts):
    r = accounts.get("/internal/users/u-9999")
    assert r.status_code == 404
    assert r.get_json()["error"] == "user_not_found"


def test_account_summary_no_orders(accounts):
    data = accounts.get("/internal/accounts/u-0002").get_json()
    assert data["user_id"] == "u-0002"
    assert data["orders_count"] == 0
    assert data["last_order_ref"] is None
    assert data["loyalty_points"] == 0


def test_account_summary_counts_confirmed_orders(accounts, ops):
    ops.orders.append({"order_ref": "LD-A1", "user_id": "u-0004", "status": "confirmed",
                       "created_at": "2026-10-20T09:00:00Z"})
    ops.orders.append({"order_ref": "LD-A2", "user_id": "u-0004", "status": "dispatched",
                       "created_at": "2026-10-20T11:00:00Z"})
    ops.orders.append({"order_ref": "LD-A3", "user_id": "u-0004", "status": "failed",
                       "created_at": "2026-10-20T12:00:00Z"})
    data = accounts.get("/internal/accounts/u-0004").get_json()
    assert data["orders_count"] == 2
    assert data["last_order_ref"] == "LD-A2"


def test_account_summary_survives_orders_down(accounts, mesh):
    mesh.down.add("orders")
    r = accounts.get("/internal/accounts/u-0001")
    assert r.status_code == 200
    assert r.get_json()["orders_count"] == 0
