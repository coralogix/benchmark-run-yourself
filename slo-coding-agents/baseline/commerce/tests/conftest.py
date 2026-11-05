"""
Test setup for the whole commerce repo.

All ten services run in ONE process here. Their outbound HTTP goes through
larder_common.http._session, which the ``mesh`` fixture replaces with a
FakeMesh that dispatches to the other apps' Flask test clients (commerce) or
to FakeOps (orders / inventory / slots stubs).

LARDER_DATA_DIR points at <tmp>/catalog so that catalog's own catalog.db and
the "../catalog/catalog.db" path search + pricing compute are the same file
(see larder_common/dbx/paths.py). Every other service's db lands next to it.
"""
import os
import shutil
import tempfile

# must happen before anything imports larder_common.settings
_TMP = tempfile.mkdtemp(prefix="larder-commerce-test-")
_DATA = os.path.join(_TMP, "catalog")
os.makedirs(_DATA)
os.environ["LARDER_ENV"] = "test"
os.environ["LARDER_DATA_DIR"] = _DATA
os.environ["LARDER_SERVICE"] = "test"
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
os.environ.pop("LARDER_PORT_OFFSET", None)

import services  # noqa: E402,F401  (sys.path + larder_common patches)
from larder_common import discovery  # noqa: E402

for _name in discovery.SERVICE_INDEX:
    os.environ[discovery.env_var_for(_name)] = "http://%s.test" % _name

import pytest  # noqa: E402

from tests.fakes import FakeMesh, FakeOps  # noqa: E402

# creation order matters: catalog seeds catalog.db, search/pricing wait for it
APP_FACTORIES = [
    ("catalog", "services.catalog.app"),
    ("promotions", "services.promotions.app"),
    ("pricing", "services.pricing.app"),
    ("search", "services.search.app"),
    ("cart", "services.cart.app"),
    ("accounts", "services.accounts.app"),
    ("loyalty", "services.loyalty.app"),
    ("checkout", "services.checkout.app"),
    ("recommendations", "services.recommendations.app"),
    ("edge-api", "services.edge_api.app"),
]

_APPS = {}


@pytest.fixture(scope="session")
def apps():
    import importlib
    import importlib.util

    for svc, mod in APP_FACTORIES:
        if svc in _APPS:
            continue
        if importlib.util.find_spec(mod) is None:
            continue  # service not built yet (only matters while developing)
        _APPS[svc] = importlib.import_module(mod).create_app()
    return _APPS


@pytest.fixture()
def ops():
    return FakeOps()


@pytest.fixture()
def mesh(monkeypatch, apps, ops):
    from larder_common import http

    fake = FakeMesh(apps, ops)
    monkeypatch.setattr(http, "_session", fake)
    # no sleeping between retries in tests
    monkeypatch.setattr("larder_common.retry.time.sleep", lambda s: None)
    return fake


def _client(name):
    return _APPS[name].test_client()


@pytest.fixture()
def edge(mesh):
    return _client("edge-api")


@pytest.fixture()
def catalog(mesh):
    return _client("catalog")


@pytest.fixture()
def search(mesh):
    return _client("search")


@pytest.fixture()
def pricing(mesh):
    return _client("pricing")


@pytest.fixture()
def promotions(mesh):
    return _client("promotions")


@pytest.fixture()
def cart_svc(mesh):
    return _client("cart")


@pytest.fixture()
def checkout_svc(mesh):
    return _client("checkout")


@pytest.fixture()
def accounts(mesh):
    return _client("accounts")


@pytest.fixture()
def loyalty(mesh):
    return _client("loyalty")


@pytest.fixture()
def recs(mesh):
    return _client("recommendations")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
