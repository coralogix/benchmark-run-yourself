"""
Feature flags (2020, "until we buy LaunchDarkly").

Flags are read from LARDER_FLAG_<NAME> env vars, falling back to DEFAULTS.
Values are cached on first read for the life of the process, so flipping an
env var needs a restart. Most of these flags are dead; grep before trusting.
"""
import os

from larder_common.config import to_bool

DEFAULTS = {
    # PM-702: search answers from cache even while the index is being rebuilt.
    # Turned off after cached results showed deleted products. (dead: the code
    # path that read it was removed in 2022, search always waits now)
    "search_serve_stale": False,
    # checkout calls loyalty directly instead of waiting for the bus (FEAT-5 spike)
    "checkout_direct_loyalty": False,
    # recommendations v2 (bulk catalog lookups). Never finished.
    "recs_bulk_lookup": False,
    # new account page (orders paginated). Blocked on ops (orders list API)
    "account_paginated_orders": False,
    "edge_v1_routes": True,
    "cart_snapshot_history": True,
}

_cache = {}


def enabled(name):
    if name in _cache:
        return _cache[name]
    raw = os.environ.get("LARDER_FLAG_" + name.upper())
    val = to_bool(raw) if raw is not None else DEFAULTS.get(name, False)
    _cache[name] = val
    return val


def all_flags():
    return {k: enabled(k) for k in DEFAULTS}


def _reset_for_tests():
    _cache.clear()
