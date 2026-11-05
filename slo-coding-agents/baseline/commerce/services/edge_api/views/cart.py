"""
/api/carts/* - straight through to the cart service.

The cart service prices the cart itself (via pricing), so whatever it returns
is what the customer sees, including its errors (e.g. coupon errors come from
promotions, through pricing, through cart, through here).
"""
from flask import Blueprint

from larder_common.errors import BadRequest
from larder_common.web import body, ok
from services.edge_api import upstreams

bp = Blueprint("cart", __name__)


@bp.route("/api/carts", methods=["POST"])
def create_cart():
    data = body()
    if not data.get("user_id"):
        raise BadRequest("user_id_required")
    res = upstreams.create_cart(data.get("user_id"))
    return ok({"cart_id": res.body.get("cart_id")}, 201)


@bp.route("/api/carts/<cart_id>", methods=["GET"])
def get_cart(cart_id):
    return ok(upstreams.cart(cart_id))


@bp.route("/api/carts/<cart_id>/items", methods=["POST"])
def add_item(cart_id):
    data = body()
    return ok(upstreams.add_item(cart_id, data.get("sku"), data.get("qty", 1)))


@bp.route("/api/carts/<cart_id>/items/<sku>", methods=["DELETE"])
def remove_item(cart_id, sku):
    return ok(upstreams.remove_item(cart_id, sku))


@bp.route("/api/carts/<cart_id>/coupon", methods=["POST"])
def apply_coupon(cart_id):
    data = body()
    return ok(upstreams.apply_coupon(cart_id, data.get("code")))


@bp.route("/api/carts/<cart_id>/coupon", methods=["DELETE"])
def remove_coupon(cart_id):
    return ok(upstreams.remove_coupon(cart_id))
