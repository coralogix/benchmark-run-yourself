"""
search routes (internal API, called by edge-api and recommendations).

    GET /internal/search?q=&uid=    {query, results:[P], count}
    GET /internal/search/stats      cache + index stats

uid is optional; when present it's part of the cache key (see engine.py).
"""
from flask import Blueprint, request

from larder_common.web import ok
from services.search import engine

bp = Blueprint("search", __name__)


@bp.route("/internal/search")
def do_search():
    q = request.args.get("q", "")
    uid = request.args.get("uid") or None
    results = engine.search(q, uid=uid)
    return ok({"query": q, "results": results, "count": len(results)})


@bp.route("/internal/search/stats")
def stats():
    return ok(engine.stats())


@bp.route("/internal/search/_flush", methods=["POST"])
def flush():
    # ops lever from INC-512 (2021): drop the cache without rebuilding. Note
    # this does NOT rebuild the index, so prices stay stale. Unlinked.
    n = engine.cache_size()
    engine.clear()
    return ok({"flushed": n})
