"""
recommendations routes.

    GET /internal/recommendations/<sku>?limit=   {sku, also_bought:[P]}
                                                 404 {"error":"not_found"}

Errors from catalog/search bubble up as 500s; edge-api treats any failure as
"no recommendations" and omits the field.
"""
from flask import Blueprint, request

from larder_common.errors import NotFound
from larder_common.utils import parse_int
from larder_common.web import ok
from services.recommendations import engine

bp = Blueprint("recommendations", __name__)

# hard ceiling on ?limit= (TH 2023-04). More than RECS_CANDIDATES is pointless.
MAX_LIMIT = 12


@bp.route("/internal/recommendations/<sku>")
def recommendations(sku):
    limit = parse_int(request.args.get("limit"), None, lo=1, hi=MAX_LIMIT)
    try:
        _, items = engine.also_bought(sku, limit=limit)
    except engine.NotFound:
        raise NotFound("not_found")
    return ok({"sku": sku, "also_bought": items})
