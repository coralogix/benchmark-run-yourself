"""
Engine selection.

    engine = get_engine()
    breakdown = engine.price_cart(lines, coupon)

Both engines take lines [{sku, name, unit_price (pounds), qty, ...}] and a
coupon (dict / None) and return the same breakdown dict shape.

settings.PRICING_ENGINE: "v2" (default in config/defaults.ini and in the
test suite) or "v1" (legacy/pricing_v1.py). See larder_common/settings.py for
where the value can come from - it is not only the ini file.
"""
import sys

from larder_common.log import get_logger

log = get_logger("pricing.engines")

# name -> module path. "v3" was the Decimal engine spike (TH 2022), deleted.
ENGINES = {
    "v1": "legacy.pricing_v1",
    "v2": "services.pricing.engine_v2",
}

_warned = set()


def get_engine():
    """Return the module implementing price_cart() for the configured engine."""
    from larder_common import settings

    name = (settings.PRICING_ENGINE or "v2").lower()
    if name not in ENGINES:
        if name not in _warned:
            _warned.add(name)
            log.warning("unknown PRICING_ENGINE %r, using v2", name)
        name = "v2"
    path = ENGINES[name]
    mod = sys.modules.get(path)
    if mod is None:
        import importlib

        mod = importlib.import_module(path)
    return mod


def engine_name():
    return get_engine().ENGINE_NAME


def v1():
    from legacy import pricing_v1

    return pricing_v1
