"""
larder_common.settings - central configuration for every commerce service.

Precedence (lowest -> highest), see also config.py:

    1. config/defaults.ini
    2. environment variables (LARDER_*, PORT, <NAME>_URL)
    3. constants in this module
    4. settings_local.py (per-box overrides, applied last)

Access settings as module attributes: ``from larder_common import settings``
then ``settings.PRICING_ENGINE``. Don't ``from larder_common.settings import X``,
the value may be reloaded (see reload()) or patched by tests.
"""
import os

from larder_common import config
from larder_common.config import env as _env
from larder_common.config import ini_get as _ini_get
from larder_common.config import to_bool as _to_bool
from larder_common.config import to_float_or_none as _float_or_none

BASE_DIR = config.REPO_ROOT

# --- core -------------------------------------------------------------------

ENV = _env("ENV") or _ini_get("env", "production")
DEBUG = ENV in ("dev", "development")
TESTING = ENV == "test"

SERVICE = os.environ.get("LARDER_SERVICE") or "unknown"
HOST = _env("HOST") or _ini_get("host", "127.0.0.1")
PORT = int(os.environ.get("PORT") or _ini_get("port", "8080"))
PORT_OFFSET = int(os.environ.get("LARDER_PORT_OFFSET") or 0)
# each service gets its own data dir from the supervisor: <repo>/var/<service>
DATA_DIR = _env("DATA_DIR") or _ini_get("data_dir", "./var")
SEED_DIR = _env("SEED_DIR") or os.path.join(BASE_DIR, "seed")
RUN_ID = _env("RUN_ID", "local")

INTERNAL_KEY = _env("INTERNAL_KEY") or _ini_get("internal_key", "sf-internal-2018")

LOG_LEVEL = _env("LOG_LEVEL") or _ini_get("log_level", "INFO")
LOG_FORMAT = _ini_get("log_format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# --- outbound http (larder_common/http.py) ------------------------------------

HTTP_CLIENT_TIMEOUT = _float_or_none(_env("HTTP_TIMEOUT") or _ini_get("timeout", section="http"))
HTTP_CLIENT_EXTRA_ATTEMPTS = int(_env("HTTP_EXTRA_ATTEMPTS") or _ini_get("extra_attempts", "2", section="http"))
HTTP_CLIENT_RETRY_WAIT = float(_ini_get("retry_wait", "0.5", section="http"))

# --- catalog ----------------------------------------------------------------

CATALOG_DEFAULT_PER_PAGE = int(_ini_get("default_per_page", "20", section="catalog"))
CATALOG_MAX_PER_PAGE = int(_ini_get("max_per_page", "100", section="catalog"))
# search + pricing open catalog's sqlite file directly. They wait this long
# for catalog to have created + seeded it on startup.
CATALOG_DB_WAIT_SECONDS = 90

# --- money ------------------------------------------------------------------

# "v2" = services/pricing/engine_v2.py, "v1" = legacy/pricing_v1.py
PRICING_ENGINE = _env("PRICING_ENGINE") or _ini_get("engine", "v2", section="pricing")
FREE_DELIVERY_THRESHOLD = float(_ini_get("free_delivery_threshold", "40.00", section="pricing"))
DELIVERY_FEE = float(_ini_get("delivery_fee", "3.99", section="pricing"))
CURRENCY = "GBP"
LEGACY_COUPON_ROUNDING = False

# --- search -----------------------------------------------------------------

SEARCH_CACHE_SOFT_LIMIT = int(_ini_get("cache_soft_limit", "2000", section="search"))
SEARCH_RESULTS_LIMIT = int(_ini_get("results_limit", "50", section="search"))
SEARCH_PERSONALISE = _to_bool(_ini_get("personalise", "true", section="search"))
SEARCH_LIVE_PRICES = _to_bool(_ini_get("live_prices", "true", section="search"))
SEARCH_LIVE_PRICE_TOP_N = int(_ini_get("live_price_top_n", "6", section="search"))
# seconds slept per catalogue row in legacy synonym expansion (see services/search/index.py)
LEGACY_SYNONYM_THROTTLE = 0.0025

# --- coupons ----------------------------------------------------------------

COUPON_LEGACY_DATE_FORMAT = _ini_get("legacy_date_format", "%d/%b/%Y", section="coupons")
COUPON_CASE_SENSITIVE = _to_bool(_ini_get("case_sensitive", "false", section="coupons"))

# --- cart -------------------------------------------------------------------

CART_MAX_LINES = int(_ini_get("max_lines", "60", section="cart"))
CART_MAX_QTY_PER_LINE = int(_ini_get("max_qty_per_line", "99", section="cart"))

# --- checkout ---------------------------------------------------------------

ORDER_REF_PREFIX = _ini_get("order_ref_prefix", "LD-", section="checkout")

# hooks run after an order is confirmed (services/checkout/hooks.py). Order matters.
CHECKOUT_HOOKS = [
    "services.checkout.order_hooks.record_checkout_event",
    "services.checkout.order_hooks.close_cart",
    "services.checkout.order_hooks.crm_sync",
]

# --- recommendations ----------------------------------------------------------

RECS_CANDIDATES = int(_ini_get("candidates", "12", section="recommendations"))
RECS_LIMIT = int(_ini_get("limit", "6", section="recommendations"))
# recommendations talks to catalog directly (not through the mesh) unless this
# is set. See services/recommendations/sources.py.
RECS_CATALOG_VIA_MESH = _to_bool(_env("RECS_CATALOG_VIA_MESH", "false"))

# --- integrations -----------------------------------------------------------

CRM_ENABLED = _to_bool(_env("CRM_ENABLED") or _ini_get("enabled", "false", section="crm"))
CRM_ENDPOINT = _ini_get("endpoint", "", section="crm")

# --- hard-coded overrides ---------------------------------------------------

# JW 2019-06-12 (INC-402): timeouts on the fulfilment client caused more failed
# orders than they ever saved; slow PSP calls *do* complete eventually. The ini
# value above is ignored on purpose. Do not "fix" without talking to payments.
HTTP_CLIENT_TIMEOUT = None

# --- test mode --------------------------------------------------------------

if TESTING:
    LEGACY_SYNONYM_THROTTLE = 0.0
    SEARCH_PERSONALISE = False
    HTTP_CLIENT_RETRY_WAIT = 0.0
    CATALOG_DB_WAIT_SECONDS = 5


def _apply_local_overrides():
    """Pull in settings_local.py for the box we're running on.

    settings_local is applied last so a box can pin behaviour regardless of
    what the defaults (or the environment) say. It's skipped for the test
    suite.
    """
    if ENV == "test":
        return
    try:
        from larder_common import settings_local  # noqa
    except ImportError:  # pragma: no cover - not every box has one
        return
    g = globals()
    for name in dir(settings_local):
        if name.isupper():
            g[name] = getattr(settings_local, name)


_apply_local_overrides()


def reload():
    """Re-read env + ini. Used by the old admin endpoint (removed in 2.x)."""
    import importlib
    import sys

    importlib.reload(sys.modules[__name__])


def as_dict():
    return {k: v for k, v in globals().items() if k.isupper() and "KEY" not in k}
