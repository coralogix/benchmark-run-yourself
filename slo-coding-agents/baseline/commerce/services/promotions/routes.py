"""
promotions routes (internal).

    GET  /internal/coupons                       {coupons:[...]}   (hidden codes excluded)
    GET  /internal/coupons/<code>                coupon dict / 404 invalid_coupon
    POST /internal/coupons/validate {code, subtotal_pence}
                                                 200 {code,percent_off,free_delivery}
                                                 400 invalid_coupon | expired_coupon | min_spend

Called by pricing (cart -> pricing -> promotions) and by edge-api's /v1 API.
"""
from flask import Blueprint

from larder_common.errors import NotFound
from larder_common.utils import safe_int
from larder_common.web import body, ok
from services.promotions import coupons

bp = Blueprint("promotions", __name__)


@bp.route("/internal/coupons")
def list_coupons():
    return ok({"coupons": [c.as_dict() for c in coupons.list_visible()]})


@bp.route("/internal/coupons/validate", methods=["POST"])
def validate():
    data = body()
    coupon = coupons.check_code(data.get("code"), safe_int(data.get("subtotal_pence"), 0))
    return ok(coupon.as_payload())


@bp.route("/internal/coupons/<code>")
def get_coupon(code):
    c = coupons.lookup(code)
    if c is None:
        raise NotFound("invalid_coupon")
    return ok(c.as_dict())


@bp.route("/internal/coupons/<code>/describe")
def describe(code):
    # marketing banner text (2019). Parses expiry, so same caveats as validate.
    c = coupons.lookup(code)
    if c is None:
        raise NotFound("invalid_coupon")
    return ok({"code": c.code, "text": coupons.describe(c), "expires": coupons.expiry_display(c)})


@bp.route("/internal/coupons/_export")
def export_csv():
    from flask import Response

    from services.promotions import export

    return Response(export.as_csv(), mimetype="text/csv")
