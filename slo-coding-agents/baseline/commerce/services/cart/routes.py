"""
cart routes (internal; edge-api proxies /api/carts/* here, checkout reads /lines).

    POST   /internal/carts {user_id}                 201 {cart_id}
    GET    /internal/carts/<id>                      Cart (priced)
    GET    /internal/carts/<id>/lines                unpriced lines (checkout)
    POST   /internal/carts/<id>/items {sku,qty}      Cart
    DELETE /internal/carts/<id>/items/<sku>          Cart
    POST   /internal/carts/<id>/coupon {code}        Cart
    DELETE /internal/carts/<id>/coupon               Cart
    PUT    /internal/carts/<id>/hold                 {hold_id, expires_at}
    POST   /internal/carts/<id>/close {order_ref}    {ok}
    GET    /internal/carts/_stats
"""
from flask import Blueprint

from larder_common.web import body, ok
from services.cart import carts, state

bp = Blueprint("cart", __name__)


@bp.route("/internal/carts", methods=["POST"])
def create_cart():
    data = body()
    st = carts.create_cart(data.get("user_id"))
    return ok({"cart_id": st.cart_id}, 201)


@bp.route("/internal/carts/_stats")
def cart_stats():
    return ok(state.stats())


@bp.route("/internal/carts/<cart_id>", methods=["GET"])
def get_cart(cart_id):
    st = carts.get_cart(cart_id)
    return ok(carts.serialise(st))


@bp.route("/internal/carts/<cart_id>/lines", methods=["GET"])
def get_lines(cart_id):
    return ok(carts.lines_view(carts.get_cart(cart_id)))


@bp.route("/internal/carts/<cart_id>/items", methods=["POST"])
def add_item(cart_id):
    data = body()
    st = carts.get_open_cart(cart_id)
    carts.add_item(st, data.get("sku"), data.get("qty", 1))
    return ok(carts.serialise(st))


@bp.route("/internal/carts/<cart_id>/items/<sku>", methods=["DELETE"])
def remove_item(cart_id, sku):
    st = carts.get_open_cart(cart_id)
    carts.remove_item(st, sku)
    return ok(carts.serialise(st))


@bp.route("/internal/carts/<cart_id>/coupon", methods=["POST"])
def apply_coupon(cart_id):
    data = body()
    st = carts.get_open_cart(cart_id)
    st, breakdown = carts.apply_coupon(st, data.get("code"))
    return ok(carts.serialise(st, breakdown))


@bp.route("/internal/carts/<cart_id>/coupon", methods=["DELETE"])
def remove_coupon(cart_id):
    st = carts.get_open_cart(cart_id)
    carts.remove_coupon(st)
    return ok(carts.serialise(st))


@bp.route("/internal/carts/<cart_id>/hold", methods=["PUT", "POST"])
def set_hold(cart_id):
    data = body()
    st = carts.get_open_cart(cart_id)
    carts.set_hold(st, data.get("hold_id"), data.get("slot_id"), data.get("expires_at"))
    return ok({"hold_id": st.hold_id, "expires_at": st.hold_expires_at})


@bp.route("/internal/carts/<cart_id>/close", methods=["POST"])
def close(cart_id):
    data = body()
    st = carts.get_cart(cart_id)
    carts.close(st, data.get("order_ref"))
    return ok({"ok": True})
