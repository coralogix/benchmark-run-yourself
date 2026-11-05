"""
POST /api/checkout -> checkout service (which talks to ops' orders).
GET  /api/orders/<ref> -> ops' orders, reshaped (orders_view.py).

Checkout's status + body are returned unchanged:
    201 {order_ref,status,total_pence,total}
    409 price_mismatch / out_of_stock, 402 payment_declined, 502 payment_error...
"""
from flask import Blueprint

from larder_common.errors import BadRequest
from larder_common.web import body, ok, passthrough
from services.edge_api import orders_view, upstreams

bp = Blueprint("checkout", __name__)


@bp.route("/api/checkout", methods=["POST"])
def do_checkout():
    data = body()
    if not data.get("cart_id"):
        raise BadRequest("cart_id_required")
    res = upstreams.checkout(data.get("cart_id"), data.get("hold_id"), data.get("payment_token"))
    return passthrough(res)


@bp.route("/api/orders/<order_ref>")
def get_order(order_ref):
    return ok(orders_view.get_order(order_ref))
