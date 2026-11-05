"""
Live prices for the top search results (MERCH-388, SB 2022-05-19).

Merch complained that search showed stale prices after a price change (the
index and cache only refresh on a safety-valve flush). Fix: ask pricing for
the current price of the top N results on every response, cached or not, and
write it into the result dicts. NB: the dicts are the cached ones, so this
also "refreshes" the cache in place.

One pricing call per result (GET /internal/prices/<sku>), through the mesh.
TODO(SB 2022-05-19): bulk endpoint on pricing. (PRICE-212)
"""
import threading

from larder_common import settings
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("search.live_prices")

_STATS = {"calls": 0, "failures": 0}
_STATS_LOCK = threading.Lock()


def _bump(key):
    with _STATS_LOCK:
        _STATS[key] += 1


def stats():
    return dict(_STATS)


def _fetch(sku):
    _bump("calls")
    try:
        res = client("pricing").get("/internal/prices/%s" % sku)
    except Exception as e:  # noqa
        _bump("failures")
        log.debug("live price for %s failed: %s", sku, e)
        return None
    if not res.ok:
        _bump("failures")
        return None
    return res.body


def overlay(results):
    if not settings.SEARCH_LIVE_PRICES or not results:
        return results
    for item in results[: settings.SEARCH_LIVE_PRICE_TOP_N]:
        live = _fetch(item["sku"])
        if not live:
            continue
        if live.get("price") is not None:
            item["price"] = live["price"]
        if live.get("price_pence") is not None:
            item["price_pence"] = live["price_pence"]
    return results
