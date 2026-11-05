from flask import Blueprint, request

from larder_common.web import body, ok
from services.edge_api import delivery

bp = Blueprint("delivery", __name__)


@bp.route("/api/delivery/slots")
def slots():
    return ok(delivery.list_slots(request.args.get("postcode", "")))


@bp.route("/api/delivery/holds", methods=["POST"])
def hold():
    data = body()
    return ok(delivery.hold(data.get("cart_id"), data.get("slot_id")), 201)
