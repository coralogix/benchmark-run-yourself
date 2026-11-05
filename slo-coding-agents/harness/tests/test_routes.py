from harness.runtime.mesh import error_reason, listeners
from harness.runtime.routes import normalise


def test_edge_templates():
    assert normalise("edge-api", "GET", "/api/products/SKU-0001") == "GET /api/products/:sku"
    assert normalise("edge-api", "GET", "/api/products") == "GET /api/products"
    assert normalise("edge-api", "POST", "/api/carts/c-123/items") == "POST /api/carts/:id/items"
    assert normalise("edge-api", "GET", "/api/orders/LD-77/receipt") == "GET /api/orders/:ref/receipt"
    assert normalise("edge-api", "GET", "/api/delivery/slots/next?postcode=x") == "GET /api/delivery/slots/next"
    assert normalise("edge-api", "POST", "/api/checkout/") == "POST /api/checkout"
    assert normalise("edge-api", "GET", "/api/account/u-0001/orders?page=1&per_page=10") == "GET /api/account/:user_id/orders"


def test_internal_routes_generic():
    cases = {
        ("orders", "POST", "/internal/orders"): "POST /internal/orders",
        ("orders", "GET", "/internal/orders/LD-000123"): "GET /internal/orders/:id",
        ("orders", "GET", "/internal/orders?user_id=u-0001&page=2"): "GET /internal/orders",
        ("inventory", "GET", "/internal/inventory/SKU-0042"): "GET /internal/inventory/:id",
        ("inventory", "POST", "/internal/inventory/availability"): "POST /internal/inventory/availability",
        ("slots", "GET", "/internal/zones/lookup?postcode=LD1+2BB"): "GET /internal/zones/lookup",
        ("slots", "POST", "/internal/slot-holds"): "POST /internal/slot-holds",
        ("slots", "DELETE", "/internal/slot-holds/h-9f3a"): "DELETE /internal/slot-holds/:id",
        ("slots", "POST", "/internal/slot-holds/3f2a1c9e-1111-2222-3333-444455556666/confirm"):
            "POST /internal/slot-holds/:id/confirm",
        ("slots", "GET", "/internal/slots/LD1-2026-10-20-am"): "GET /internal/slots/:id",
        ("loyalty", "GET", "/internal/loyalty/u-0615"): "GET /internal/loyalty/:id",
        ("warehouse", "POST", "/internal/dispatch/LD-77"): "POST /internal/dispatch/:id",
        ("cart", "GET", "/internal/carts/9f8e7d6c5b4a3f2e/items/SKU-1"): "GET /internal/carts/:id/items/:id",
        ("cart", "GET", "/v1/carts/c-abcd"): "GET /v1/carts/:id",
        ("catalog", "GET", "/internal/products/12345"): "GET /internal/products/:id",
        ("bus", "POST", "/publish"): "POST /publish",
        ("tax", "POST", "/internal/tax/totals"): "POST /internal/tax/totals",
        ("fraud", "GET", "/healthz"): "GET /healthz",
    }
    for (svc, m, path), want in cases.items():
        assert normalise(svc, m, path) == want, (svc, path)


def test_any_merges_edge_and_internal():
    assert normalise("any", "POST", "/internal/dispatch/LD-9") == "POST /internal/dispatch/:id"
    assert normalise("any", "GET", "/api/products/SKU-1") == "GET /api/products/:sku"
    assert normalise("any", "GET", "/wp-login.php") == "GET other"


def test_unknown_is_bounded():
    assert normalise("edge-api", "GET", "/wp-admin/x.php") == "GET other"
    assert normalise("edge-api", "BREW", "/api/products") == "OTHER /api/products"
    assert normalise("edge-api", "GET", "/api/" + "x" * 1000) == "GET other"
    # the public table does not contain internal routes
    assert normalise("edge-api", "GET", "/internal/health") == "GET other"
    assert normalise("orders", "GET", "/a/b/c/d/e/f/g/h/i") == "GET other"
    assert normalise("orders", "GET", "/internal/" + "x" * 300) == "GET other"
    # random ids never become literal labels
    seen = {normalise("orders", "GET", f"/internal/orders/LD-{i:06d}") for i in range(500)}
    seen |= {normalise("cart", "GET", f"/carts/{i:016x}") for i in range(500)}
    assert seen == {"GET /internal/orders/:id", "GET /carts/:id"}


def test_mesh_listeners():
    ls = listeners(800)
    assert len(ls) == 22
    by_port = {lp: (svc, up) for lp, svc, up in ls}
    assert by_port[8800] == ("edge-api", 8901)
    assert by_port[9001] == ("edge-api", 8901)
    assert by_port[9019] == ("warehouse", 8919)
    assert by_port[9021] == ("bus", 8921)
    assert ls[-1][0] == 8800  # edge opened last (supervisor readiness probe)


def test_error_reason():
    assert error_reason(200, b'{"error":"x"}') == ""
    assert error_reason(409, b'{"error":"price_mismatch"}') == "price_mismatch"
    assert error_reason(500, b'{"error":"Traceback: Boom!"}') == "other"
    assert error_reason(500, b"<html>oops</html>") == ""
    assert error_reason(400, b'{"message":"x"}') == ""
    assert error_reason(400, b'{"error":"' + b"a" * 9000 + b'"}') == ""
