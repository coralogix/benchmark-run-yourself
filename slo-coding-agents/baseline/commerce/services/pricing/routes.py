"""
pricing routes (internal).

    POST /internal/price {lines:[{sku,qty}], coupon_code, validate_coupon}   breakdown
    GET  /internal/prices/<sku>                                             {sku, price, price_pence}
    POST /internal/price-check {lines, percent_off}                         {total}  (v1 API, always v1)
    GET  /internal/pricing/engine                                           which engine is live
"""
from flask import Blueprint

from larder_common import settings
from larder_common.errors import BadRequest, NotFound
from larder_common.web import body, ok
from services.pricing import engines, prices, quote

bp = Blueprint("pricing", __name__)


@bp.route("/internal/price", methods=["POST"])
def price():
    data = body()
    lines = data.get("lines")
    if lines is None:
        lines = []
    if not isinstance(lines, list):
        raise BadRequest("lines_required")
    return ok(quote.quote(lines, data.get("coupon_code"), bool(data.get("validate_coupon"))))


@bp.route("/internal/prices/<sku>")
def live_price(sku):
    p = quote.live_price(sku)
    if p is None:
        raise NotFound("not_found")
    return ok(p)


@bp.route("/internal/price-check", methods=["POST"])
def price_check():
    data = body()
    return ok({"total": quote.price_check_v1(data.get("lines", []), data.get("percent_off", 0))})


@bp.route("/internal/pricing/engine")
def engine():
    return ok({"engine": engines.engine_name(), "configured": settings.PRICING_ENGINE, "lookups": prices.stats()})


@bp.route("/internal/pricing/rules")
def rules():
    from services.pricing import rules as _rules

    return ok(_rules.describe_rules())
