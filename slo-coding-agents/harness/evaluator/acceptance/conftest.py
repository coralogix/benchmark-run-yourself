"""Shared fixtures for the hidden black-box acceptance suite (evaluator-only).

The suite runs against a fresh evaluation instance started by
`harness.evaluator.evaluate` (fresh seed data, quiet simulators, no telemetry).
Every test is tagged with the backlog item or seeded issue it checks:

    @pytest.mark.item("FEAT-017")      # feature acceptance
    @pytest.mark.item("SF-3")          # seeded issue fixed
    @pytest.mark.item("SMOKE")         # existing behaviour (regression check)

Results are written to $LARDER_EVAL_RESULTS as JSON for scoring.

Conventions for test authors:
  * black-box only: HTTP against edge (:8000) and mesh ports (:8200+i) plus simulator admin APIs
  * never assume a feature exists: a 404/405 on a new route simply fails the test
  * isolate state: use `users.fresh()` for a user nobody else touches, `catalog.pick(...)` for SKUs
  * credit every item independently: a test must pass whichever OTHER backlog items are implemented.
    For "just some basket" use `shop.safe_basket()` / `shop.safe_cart()` / `catalog.safe_lines()`
    (goods 1000..3999p, <= 24 per line, no 3for2 products, no SF-1 pricing case); never compare
    error bodies for equality (FEAT-023 adds request_id); use a fresh user for every confirmed
    order with WELCOME10/STUDENT20 (FEAT-012); take holds right before checkout (FEAT-040); ask
    for `include_full=true` when a full slot must be listed (FEAT-074).
  * tests that degrade the system (load, faults) live in files named test_zz_*.py, restore any
    simulator mode they change, and call `control.restart(...)` if they may leave a service wedged
"""
from __future__ import annotations

import itertools
import json
import math
import os
import time
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
OFFSET = int(os.environ.get("LARDER_EVAL_OFFSET", "0"))
ADMIN_TOKEN = os.environ.get("LARDER_ADMIN_TOKEN", "")
RESULTS = os.environ.get("LARDER_EVAL_RESULTS")
ARTIFACTS = Path(os.environ.get("LARDER_EVAL_ARTIFACTS", "/nonexistent"))  # ingress.jsonl, resources.jsonl, sim dumps
SEED_COMMERCE = ROOT / "baseline" / "commerce" / "seed"
SEED_OPS = ROOT / "baseline" / "ops" / "seed"
# per-entity seeded issues (HC-n): the SKUs / customers that trigger them. Shared fixtures never hand
# these out, so only the HC tests (and tests that ask for them explicitly) touch them.
HC_ENTITIES = json.loads((ROOT / "harness" / "evaluator" / "hc_entities.json").read_text())
HC_SKUS = frozenset(HC_ENTITIES["HC-1_skus"])
HC_USERS = frozenset(HC_ENTITIES["HC-2_users"]) | frozenset(HC_ENTITIES["HC-3_users"])
REGISTRY = yaml.safe_load((ROOT / "harness" / "runtime" / "services.yaml").read_text())
INDEX = {s["name"]: int(s["index"]) for s in REGISTRY["services"]}

_results: list[dict] = []


def pytest_configure(config):
    config.addinivalue_line("markers", "item(id): backlog item or seeded issue this test checks")


def pytest_collection_modifyitems(items):
    # degrading tests last, everything else in file order
    items.sort(key=lambda it: (Path(str(it.fspath)).name.startswith("test_zz_"),))


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        item = None
        for m in getattr(report, "_item_markers", []):
            item = m
        _results.append({"name": report.nodeid, "item": item, "outcome": report.outcome,
                         "duration_s": round(report.duration, 3),
                         "message": (str(report.longrepr)[-600:] if report.outcome != "passed" else None)})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    m = item.get_closest_marker("item")
    rep._item_markers = [m.args[0]] if m else []


def pytest_sessionfinish(session, exitstatus):
    if RESULTS:
        Path(RESULTS).write_text(json.dumps({"tests": _results}, indent=2))


# ------------------------------------------------------------------ endpoints

def edge_url() -> str:
    return f"http://127.0.0.1:{8000 + OFFSET}"


def mesh_url(service: str) -> str:
    return f"http://127.0.0.1:{8200 + INDEX[service] + OFFSET}"


def app_url(service: str) -> str:
    return f"http://127.0.0.1:{8100 + INDEX[service] + OFFSET}"


@pytest.fixture(scope="session")
def edge():
    with httpx.Client(base_url=edge_url(), timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def mesh():
    clients: dict[str, httpx.Client] = {}

    def get(service: str) -> httpx.Client:
        if service not in clients:
            clients[service] = httpx.Client(base_url=mesh_url(service), timeout=30)
        return clients[service]

    yield get
    for c in clients.values():
        c.close()


class Admin:
    def __init__(self, port: int):
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{port + OFFSET}", timeout=30,
                              headers={"X-Admin-Token": ADMIN_TOKEN})

    def get(self, path, **kw):
        r = self.c.get(path, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path, body=None):
        r = self.c.post(path, json=body or {})
        r.raise_for_status()
        return r.json() if r.content else None

    def mode(self, **kw):
        return self.post("/admin/mode", kw)


@pytest.fixture(scope="session")
def psp():
    return Admin(9090)


@pytest.fixture(scope="session")
def partner():
    return Admin(9091)


@pytest.fixture(scope="session")
def email():
    return Admin(9092)


class Control:
    def __init__(self):
        self.c = httpx.Client(base_url=f"http://127.0.0.1:{7070 + OFFSET}", timeout=180)

    def restart(self, target: str, wait_s: int = 90):
        self.c.post(f"/services/{target}/restart")
        end = time.time() + wait_s
        while time.time() < end:
            try:
                if httpx.get(edge_url() + "/healthz", timeout=3).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)


@pytest.fixture(scope="session")
def control():
    return Control()


# ------------------------------------------------------------------ data

class Users:
    """Hands out seeded users so that no two tests share one."""

    def __init__(self):
        users = json.loads((SEED_COMMERCE / "users.json").read_text())
        self.by_id = {u["user_id"]: u for u in users}
        users = [u for u in users if u["user_id"] not in HC_USERS]
        # u-0400..u-1400 are reserved for the acceptance suite (the workload is not running in eval mode)
        self._it = iter([u for u in users if 400 <= int(u["user_id"][2:]) <= 1400])
        self.legacy = [u for u in users if int(u["user_id"][2:]) > 1400]

    def fresh(self) -> dict:
        return next(self._it)


@pytest.fixture(scope="session")
def users():
    return Users()


MULTIBUY_TAG = "3for2"   # FEAT-001: every third unit of these products may be free
MAX_LINE_QTY = 24        # FEAT-019: per-line quantity cap
MIN_GOODS_PENCE = 1000   # FEAT-039: minimum goods value (after discounts) at checkout
FREE_DELIVERY_PENCE = 4000


class Catalog:
    def __init__(self):
        self.products = json.loads((SEED_COMMERCE / "products.json").read_text())
        self.stock = {i["sku"]: i["on_hand"] for i in json.loads((SEED_OPS / "inventory.json").read_text())}
        self.zones = json.loads((SEED_COMMERCE / "zones.json").read_text())
        # HC-1 SKUs count as used from the start, so no helper hands them out
        self._used: set[str] = set(HC_SKUS)

    def pick(self, n=1, *, tag=None, not_tag=None, category=None, diet=None, vat_band=None, min_stock=200,
             max_price=None, reuse=False, multibuy=False) -> list[dict]:
        """n seed products. Products tagged `3for2` (FEAT-001 multi-buy) are only returned when
        asked for (tag="3for2" or multibuy=True), so a test that orders 3+ units of a picked
        product never has its totals changed by the multi-buy feature."""
        allow_mb = multibuy or tag == MULTIBUY_TAG
        out = []
        for p in self.products:
            if p["sku"] in HC_SKUS or (not reuse and p["sku"] in self._used):
                continue
            if tag and tag not in p["tags"]:
                continue
            if not_tag and not_tag in p["tags"]:
                continue
            if not allow_mb and MULTIBUY_TAG in p["tags"]:
                continue
            if category and p["category"] != category:
                continue
            if diet and diet not in p["diet"]:
                continue
            if vat_band and p["vat_band"] != vat_band:
                continue
            if self.stock.get(p["sku"], 0) < min_stock:
                continue
            if max_price is not None and p["price_pence"] > max_price:
                continue
            out.append(p)
            if len(out) == n:
                break
        assert len(out) == n, "not enough matching seed products"
        if not reuse:
            self._used.update(p["sku"] for p in out)
        return out

    def safe_lines(self, n_lines=1, *, lo=MIN_GOODS_PENCE, hi=FREE_DELIVERY_PENCE - 1, percent_off=0, qty=None,
                   min_stock=200, min_price=None, max_price=None, vat_band=None, reuse=False) -> list[tuple[str, int, int]]:
        """"Just some basket" that every backlog feature leaves alone: [(sku, qty, unit_price_pence)].

        * n_lines distinct products, none tagged 3for2 (FEAT-001 multi-buy can't change the total);
        * every line <= 24 units (FEAT-019);
        * goods after `percent_off` within [lo, hi]: by default 1000..3999p, i.e. at least the
          FEAT-039 minimum order value and below the free-delivery threshold;
        * both baseline pricing engines agree with the canonical integer rule (no SF-1 dependence).
        `qty` fixes every line's quantity; otherwise the smallest quantity reaching the per-line share is used.
        """
        from larder_support import canonical, engines_agree

        keep = 1 - float(percent_off or 0) / 100   # goods before the coupon that leave [lo, hi] after it
        goods_lo, goods_hi = math.ceil(lo / keep), math.floor(hi / keep)
        per_lo, per_hi = -(-goods_lo // n_lines), goods_hi // n_lines

        def line_qty(price):
            if qty is not None:
                return qty
            return next((q for q in range(1, MAX_LINE_QTY + 1) if per_lo <= q * price <= per_hi), None)

        def ok(lines):
            c = canonical([(pp, q) for _, q, pp in lines], percent_off)
            goods = c["subtotal_pence"] - c["discount_pence"]
            return lo <= goods <= hi and engines_agree([(pp, q) for _, q, pp in lines], percent_off)

        for allow_used in ((False, True) if reuse else (False,)):
            chosen: list[tuple[str, int, int]] = []
            for p in self.products:
                if p["sku"] in HC_SKUS or (p["sku"] in self._used and not allow_used):
                    continue
                if MULTIBUY_TAG in p["tags"] or self.stock.get(p["sku"], 0) < min_stock:
                    continue
                if vat_band and p["vat_band"] != vat_band:
                    continue
                if (min_price is not None and p["price_pence"] < min_price) or \
                        (max_price is not None and p["price_pence"] > max_price):
                    continue
                q = line_qty(p["price_pence"])
                if q is None or not 1 <= q <= MAX_LINE_QTY:
                    continue
                trial = chosen + [(p["sku"], q, p["price_pence"])]
                if len(trial) < n_lines:
                    chosen = trial
                    continue
                if ok(trial):
                    self._used.update(s for s, _, _ in trial)
                    return trial
        raise AssertionError(f"no safe {n_lines}-line basket ({lo}..{hi}p, {percent_off}% off) in the seed catalogue")

    def postcode(self, zone="Z1") -> str:
        return next(z for z in self.zones if z["zone"] == zone)["postcodes"][0]


@pytest.fixture(scope="session")
def catalog():
    return Catalog()


# ------------------------------------------------------------------ flows

class Shop:
    """Customer flows through the public API. Methods return the httpx.Response unless noted."""

    def __init__(self, edge: httpx.Client, catalog: Catalog):
        self.edge, self.catalog = edge, catalog
        self._n = itertools.count()

    def cart(self, user_id: str) -> str:
        r = self.edge.post("/api/carts", json={"user_id": user_id})
        assert r.status_code in (200, 201), r.text
        return r.json()["cart_id"]

    def add(self, cart_id: str, sku: str, qty: int = 1):
        return self.edge.post(f"/api/carts/{cart_id}/items", json={"sku": sku, "qty": qty})

    def safe_basket(self, n_lines: int = 1, **kw) -> list[tuple[str, int]]:
        """[(sku, qty)] for a basket no backlog feature changes (see Catalog.safe_lines)."""
        return [(s, q) for s, q, _ in self.catalog.safe_lines(n_lines, **kw)]

    def safe_cart(self, user_id: str, n_lines: int = 1, **kw) -> str:
        """A new cart for user_id filled with a safe_basket(); returns the cart_id."""
        cid = self.cart(user_id)
        for sku, qty in self.safe_basket(n_lines, **kw):
            r = self.add(cid, sku, qty)
            assert r.status_code == 200, f"add {sku} x{qty}: {r.status_code} {r.text}"
        return cid

    def hold(self, cart_id: str, postcode: str) -> str:
        slots = self.edge.get("/api/delivery/slots", params={"postcode": postcode}).json()["slots"]
        for s in slots:
            if s.get("available", 0) and s["available"] > 0:
                r = self.edge.post("/api/delivery/holds", json={"cart_id": cart_id, "slot_id": s["slot_id"]})
                if r.status_code == 201:
                    return r.json()["hold_id"]
        raise AssertionError("no slot could be held")

    def checkout(self, cart_id: str, hold_id: str, token: str = "tok_ok", **extra):
        return self.edge.post("/api/checkout", json={"cart_id": cart_id, "hold_id": hold_id, "payment_token": token, **extra})

    def order(self, user: dict, lines: list[tuple[str, int]], coupon: str | None = None, **extra) -> dict:
        """Place a full order; returns the 201 JSON (asserts success)."""
        cid = self.cart(user["user_id"])
        for sku, qty in lines:
            r = self.add(cid, sku, qty)
            assert r.status_code == 200, r.text
        if coupon:
            r = self.edge.post(f"/api/carts/{cid}/coupon", json={"code": coupon})
            assert r.status_code == 200, r.text
        hid = self.hold(cid, user["postcode"])
        r = self.checkout(cid, hid, **extra)
        assert r.status_code == 201, f"{r.status_code} {r.text}"
        body = r.json()
        body["cart_id"] = cid
        return body


@pytest.fixture(scope="session")
def shop(edge, catalog):
    return Shop(edge, catalog)


def wait_until(fn, timeout_s: float = 20, interval_s: float = 0.5):
    """Poll fn() until it returns a truthy value; return it (or the last value)."""
    end = time.time() + timeout_s
    val = None
    while time.time() < end:
        val = fn()
        if val:
            return val
        time.sleep(interval_s)
    return val
