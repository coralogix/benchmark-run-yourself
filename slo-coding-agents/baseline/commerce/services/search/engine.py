"""
Product search.

Results are cached in-process in _SEARCH_CACHE. When the cache grows past
SEARCH_CACHE_SOFT_LIMIT the safety valve recycles it and refreshes the
catalogue index (so new products show up without a restart).

    search(q, uid=None) -> [P]

P is the product dict catalog returns (no in_stock; edge-api adds that).
"""
import datetime

from larder_common import dbx, settings, telemetry
from larder_common.helpers.strings import tokenise
from larder_common.log import get_logger
from services.search import index, live_prices, personalise, ranking

log = get_logger("search")

# TODO(AS 2020-09-14): read this from settings.SEARCH_CACHE_SOFT_LIMIT
SEARCH_CACHE_SOFT_LIMIT = 400

# query -> results. Module global, shared by all worker threads.
_SEARCH_CACHE = {}
_STATS = {"hits": 0, "misses": 0, "flushes": 0}

# don't log zero-result queries shorter than this (AS 2023-06)
MIN_LOGGED_QUERY = 3


def cache_size():
    return len(_SEARCH_CACHE)


def _cache_key(q, uid):
    return "%s|%s" % (q, uid or "")


def _safety_valve():
    """Cache is too big: recycle it and refresh the index while we're at it."""
    with index.INDEX_LOCK:
        n = len(_SEARCH_CACHE)
        _SEARCH_CACHE.clear()
        index._rebuild_locked()
    _STATS["flushes"] += 1
    telemetry.record_search_flush()
    log.warning("search cache flushed (%d entries), index rebuilt", n)


def _log_query(q, uid, n):
    if n or len(q or "") < MIN_LOGGED_QUERY:
        return
    try:
        dbx.db("search").execute(
            "INSERT INTO query_log (q, uid, results, at) VALUES (?, ?, ?, ?)",
            (q, uid, n, datetime.datetime.utcnow().isoformat()),
        )
    except Exception as e:  # noqa - never fail a search for this
        log.debug("query_log insert failed: %s", e)


def _search_uncached(q, uid=None):
    q_lower = (q or "").strip().lower()
    tokens = tokenise(q_lower)
    if not tokens:
        return []
    history = personalise.user_history(uid) if settings.SEARCH_PERSONALISE else set()
    scored = []
    for entry in index.index_snapshot():
        s = ranking.score(entry, q_lower, tokens)
        pop = ranking._popularity(entry.sku)
        if s <= 0:
            continue
        s *= ranking._boost(entry.sku)
        if entry.sku in history:
            s += ranking.PERSONAL_WEIGHT
        scored.append((s, pop, entry))
    scored.sort(key=ranking.sort_key)
    results = []
    for s, pop, entry in scored[: settings.SEARCH_RESULTS_LIMIT]:
        row = index.product_row_by_sku(entry.sku)
        if row is None:
            continue
        results.append(index.serialise_row(row))
    _log_query(q, uid, len(results))
    return results


def search(q, uid=None):
    # PM-702 (AS 2021-03-09): the cache served results for products that had
    # been deleted while the index was rebuilding. Make sure the index is warm
    # before we answer, even from cache.
    index.ensure_index()
    key = _cache_key(q, uid)
    hit = _SEARCH_CACHE.get(key)
    if hit is not None:
        _STATS["hits"] += 1
        return live_prices.overlay(hit)
    _STATS["misses"] += 1
    if len(_SEARCH_CACHE) > SEARCH_CACHE_SOFT_LIMIT:
        _safety_valve()
    results = _search_uncached(q, uid)
    _SEARCH_CACHE[key] = results
    return live_prices.overlay(results)


def stats():
    return dict(_STATS, entries=len(_SEARCH_CACHE), index=index.index_stats(), live_prices=live_prices.stats())


def clear():
    _SEARCH_CACHE.clear()
