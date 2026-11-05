"""
Search admin endpoints (2021, INC-512 follow-up). Not linked from anywhere.

    POST /internal/search/_reindex     rebuild the index now (blocks all searches while it runs)
    GET  /internal/search/_cache       cache keys (first 200)
    GET  /internal/search/_zero        recent zero-result queries from query_log

_reindex does NOT clear the result cache, so cached results keep their old
prices/names until the safety valve fires. (live_prices papers over prices.)
"""
from flask import Blueprint, request

from larder_common import dbx
from larder_common.internal_auth import internal_only
from larder_common.log import get_logger
from larder_common.utils import parse_int
from larder_common.web import ok
from services.search import engine, index, normalise

log = get_logger("search.admin")

bp = Blueprint("search_admin", __name__)

# AS 2021: don't dump more than this many keys, the response got huge
MAX_KEYS = 200


@bp.route("/internal/search/_reindex", methods=["POST"])
@internal_only
def reindex():
    index.rebuild_index()
    log.warning("index rebuilt by %s", request.headers.get("X-Larder-Caller", "?"))
    return ok({"index": index.index_stats()})


@bp.route("/internal/search/_cache")
@internal_only
def cache_keys():
    keys = list(engine._SEARCH_CACHE.keys())[:MAX_KEYS]
    return ok({"entries": engine.cache_size(), "keys": keys, "soft_limit": engine.SEARCH_CACHE_SOFT_LIMIT})


@bp.route("/internal/search/_zero")
@internal_only
def zero_results():
    limit = parse_int(request.args.get("limit"), 50, lo=1, hi=500)
    rows = dbx.db("search").query("SELECT q, uid, at FROM query_log ORDER BY id DESC LIMIT ?", (limit,))
    return ok({"queries": [dict(r, normalised=normalise.normalise_query(r["q"])) for r in rows]})
