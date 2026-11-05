"""
GET /api/products, /api/products/<sku>, /api/categories, /api/search
"""
from flask import Blueprint, request

from larder_common import settings
from larder_common.errors import NotFound, PassThrough
from larder_common.log import get_logger
from larder_common.utils import parse_int
from larder_common.web import ok
from services.edge_api import shaping, stock, upstreams

log = get_logger("edge.catalogue")

bp = Blueprint("catalogue", __name__)

DEFAULT_PER_PAGE = settings.CATALOG_DEFAULT_PER_PAGE
MAX_PER_PAGE = settings.CATALOG_MAX_PER_PAGE

# DP 2023: search is capped at 50 by search itself (40 on prod, settings_local)
MAX_SEARCH_QUERY = 200


@bp.route("/api/products")
def products():
    page = parse_int(request.args.get("page"), 1, lo=1)
    per_page = parse_int(request.args.get("per_page"), DEFAULT_PER_PAGE, lo=1, hi=MAX_PER_PAGE)
    category = request.args.get("category") or None
    data = upstreams.list_products(page, per_page, category)
    items = shaping.products(data.get("items"))
    stock.attach_stock(items)
    return ok({"items": items, "page": data.get("page", page), "per_page": data.get("per_page", per_page),
               "total": data.get("total", 0)})


@bp.route("/api/products/<sku>")
def product_detail(sku):
    try:
        raw = upstreams.product(sku)
    except PassThrough as e:
        if e.status == 404:
            raise NotFound("not_found")
        raise
    item = shaping.product(raw, detail=True)
    # "customers also bought" (PM-990). Omitted if recommendations fails.
    also = upstreams.also_bought(sku)
    if also is not None:
        also = shaping.products(also)
        stock.attach_stock([item], also)
        item["also_bought"] = also
    else:
        stock.attach_stock([item])
    return ok(item)


@bp.route("/api/categories")
def categories():
    return ok({"categories": upstreams.categories()})


@bp.route("/api/search")
def do_search():
    q = (request.args.get("q", "") or "")[:MAX_SEARCH_QUERY]
    uid = request.args.get("uid") or None
    data = upstreams.search(q, uid=uid)
    results = shaping.products(data.get("results"))
    stock.attach_stock(results)
    return ok({"query": q, "results": results, "count": len(results)})
