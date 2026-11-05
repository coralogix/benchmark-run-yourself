"""
checkout routes (internal API, called by edge-api).

    POST /internal/checkout {cart_id,hold_id,payment_token}
        201 {order_ref,status:"confirmed",total_pence,total}
        400/404/409/402/502 {error}
    GET  /internal/checkouts/<order_ref>    local attempt record (debug)
    GET  /internal/checkouts/_inflight      orders calls in progress (debug)
"""
from flask import Blueprint

from larder_common.errors import NotFound
from larder_common.web import body, ok
from services.checkout import checkout

bp = Blueprint("checkout", __name__)


@bp.route("/internal/checkout", methods=["POST"])
def do_checkout():
    data = body()
    result = checkout.checkout(data.get("cart_id"), data.get("hold_id"), data.get("payment_token"))
    return ok(result, 201)


@bp.route("/internal/checkouts/_inflight")
def inflight():
    return ok({"inflight": checkout.inflight()})


@bp.route("/internal/checkouts/<order_ref>")
def get_checkout(order_ref):
    rec = checkout.get_checkout(order_ref)
    if rec is None:
        raise NotFound("checkout_not_found")
    return ok(rec.to_api())
