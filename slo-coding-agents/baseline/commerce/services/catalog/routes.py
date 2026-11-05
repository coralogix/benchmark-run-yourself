"""
catalog routes (internal API, called by edge-api, recommendations, cart, pricing).

    GET  /internal/products?page&per_page&category   {items,page,per_page,total}
    GET  /internal/products/<sku>                   product + id + description
    GET  /internal/products/by-id/<id>              (v1 API)
    POST /internal/products/bulk {skus}             {items}
    GET  /internal/categories                       {categories}

Products here have no in_stock: stock is ops' business (edge-api asks inventory).
"""
from flask import Blueprint, request

from larder_common.errors import BadRequest, NotFound
from larder_common.internal_auth import internal_only
from larder_common.utils import parse_int
from larder_common.web import body, ok
from services.catalog import products

bp = Blueprint("catalog", __name__)


@bp.route("/internal/products")
def list_products():
    page = parse_int(request.args.get("page"), 1, lo=1)
    per_page = parse_int(request.args.get("per_page"), products.DEFAULT_PER_PAGE, lo=1, hi=products.MAX_PER_PAGE)
    category = request.args.get("category") or None
    return ok(products.list_products(page=page, per_page=per_page, category=category))


@bp.route("/internal/products/by-id/<int:product_id>")
def product_by_id(product_id):
    item = products.get_product_by_id(product_id)
    if item is None:
        raise NotFound("not_found")
    return ok(item)


@bp.route("/internal/products/bulk", methods=["POST"])
@internal_only
def bulk():
    data = body()
    skus = data.get("skus")
    if not isinstance(skus, list):
        raise BadRequest("skus_required")
    return ok({"items": products.bulk(skus)})


@bp.route("/internal/products/<sku>")
def product_detail(sku):
    item = products.get_product(sku)
    if item is None:
        raise NotFound("not_found")
    return ok(item)


@bp.route("/internal/categories")
def categories():
    return ok({"categories": products.categories()})


@bp.route("/internal/categories/counts")
def category_counts():
    return ok({"counts": products.category_counts()})
