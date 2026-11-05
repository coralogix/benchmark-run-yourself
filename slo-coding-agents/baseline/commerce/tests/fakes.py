"""
Fakes for the test suite.

FakeMesh stands in for the requests.Session in larder_common.http. URLs look
like http://<service>.test/... (see conftest) or http://127.0.0.1:<port>/...
(code that bypasses discovery). Commerce services are dispatched to their
Flask test clients; ops services to FakeOps.
"""
import json
import re
import uuid
from urllib.parse import urlsplit

from larder_common import discovery

OPS = ("orders", "inventory", "slots", "payments", "tax", "bus", "warehouse")


class FakeResponse(object):
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)
        self.headers = headers or {"Content-Type": "application/json"}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code))


def _service_for(netloc):
    host, _, port = netloc.partition(":")
    if host.endswith(".test"):
        return host[: -len(".test")]
    if port:
        p = int(port)
        for name in discovery.SERVICE_INDEX:
            if p in (discovery.app_port(name), discovery.mesh_port(name)):
                return name
    return None


class FakeMesh(object):
    def __init__(self, apps, ops):
        self.apps = apps
        self.ops = ops
        self.calls = []
        self.down = set()
        self.headers = {}

    def count(self, service, method=None, path_prefix=None):
        return len(
            [
                c
                for c in self.calls
                if c[0] == service
                and (method is None or c[1] == method)
                and (path_prefix is None or c[2].startswith(path_prefix))
            ]
        )

    def request(self, method, url, params=None, json=None, headers=None, timeout=None, **kw):
        import requests

        parts = urlsplit(url)
        service = _service_for(parts.netloc)
        path = parts.path
        self.calls.append((service, method, path, params, json, parts.netloc))
        if service in self.down:
            raise requests.ConnectionError("%s down (fake mesh)" % service)
        if service in self.apps:
            c = self.apps[service].test_client()
            resp = c.open(path, method=method, query_string=params, json=json, headers=headers or {})
            try:
                body = resp.get_json()
            except Exception:
                body = None
            return FakeResponse(resp.status_code, body)
        if service in OPS:
            return FakeResponse(*self.ops.handle(service, method, path, params or {}, json))
        return FakeResponse(502, {"error": "upstream_unavailable"})

    # requests.Session-ish conveniences
    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


class FakeOps(object):
    """Just enough of orders / inventory / slots (the ops internal API contract)."""

    ZONES = {
        "LD1 1AA": "Z1", "LD1 2BB": "Z1", "LD1 3CC": "Z1", "LD2 1AA": "Z2", "LD3 2HH": "Z3", "LD3 9JJ": "Z3",
        "LD4 3KK": "Z4", "LD5 6LL": "Z5",
    }

    def __init__(self):
        self.stock = {}  # sku -> available; default 10
        self.full_slots = set()
        self.order_response = None  # (status, body) override for POST /internal/orders
        self.orders = []  # in creation order, duplicates and all
        self.slow_orders = False

    def handle(self, service, method, path, params, body):
        fn = getattr(self, "_" + service, None)
        if fn is None:
            return 404, {"error": "not_found"}
        return fn(method, path, params, body)

    # --- inventory ---------------------------------------------------------

    def _inventory(self, method, path, params, body):
        if method == "POST" and path == "/internal/inventory/availability":
            return 200, {s: {"available": self.stock.get(s, 10)} for s in body.get("skus", [])}
        m = re.match(r"^/internal/inventory/([^/]+)$", path)
        if method == "GET" and m:
            a = self.stock.get(m.group(1), 10)
            return 200, {"sku": m.group(1), "on_hand": a, "reserved": 0, "available": a}
        return 404, {"error": "not_found"}

    # --- slots -------------------------------------------------------------

    def _slots(self, method, path, params, body):
        if method == "GET" and path == "/internal/zones/lookup":
            z = self.ZONES.get((params.get("postcode") or "").upper())
            if z is None:
                return 404, {"error": "unknown_postcode"}
            return 200, {"zone": z}
        if method == "GET" and path == "/internal/slots":
            zone = params.get("zone")
            return 200, {
                "slots": [
                    {"slot_id": "SL-%s-1" % zone, "zone": zone, "date": "2026-10-21", "window": "08:00-10:00",
                     "capacity": 10, "booked": 10},
                    {"slot_id": "SL-%s-2" % zone, "zone": zone, "date": "2026-10-21", "window": "10:00-12:00",
                     "capacity": 10, "booked": 3},
                ]
            }
        if method == "POST" and path == "/internal/slot-holds":
            sid = body.get("slot_id")
            if sid in self.full_slots:
                return 409, {"error": "slot_full"}
            if not sid or not sid.startswith("SL-"):
                return 404, {"error": "unknown_slot"}
            return 201, {"hold_id": "H-" + uuid.uuid4().hex[:8], "expires_at": "2026-10-21T08:20:00Z"}
        return 404, {"error": "not_found"}

    # --- orders ------------------------------------------------------------

    def _orders(self, method, path, params, body):
        if method == "POST" and path == "/internal/orders":
            if self.order_response:
                return self.order_response
            order = dict(body, status="confirmed", charge_id="ch_test", created_at="2026-10-20T10:00:%02dZ"
                         % (len(self.orders) % 60))
            self.orders.append(order)
            return 201, {"order_ref": body["order_ref"], "status": "confirmed", "charge_id": "ch_test",
                         "total_pence": body["total_pence"]}
        m = re.match(r"^/internal/orders/([^/]+)$", path)
        if method == "GET" and m:
            for o in reversed(self.orders):
                if o["order_ref"] == m.group(1):
                    return 200, o
            return 404, {"error": "order_not_found"}
        if method == "GET" and path == "/internal/orders":
            if "page" not in params:
                # legacy behaviour (see ops): no page => everything
                return 200, {"orders": list(self.orders), "page": 1, "per_page": len(self.orders),
                             "total": len(self.orders)}
            page = int(params.get("page") or 1)
            per_page = int(params.get("per_page") or 20)
            mine = [o for o in self.orders if o.get("user_id") == params.get("user_id")]
            return 200, {"orders": mine[(page - 1) * per_page: page * per_page], "page": page,
                         "per_page": per_page, "total": len(mine)}
        return 404, {"error": "not_found"}
