"""
Saved addresses (SB 2022-02, "multiple addresses", paused at the API stage).

Not mounted anywhere. The routes below were registered on a blueprint that
was never added to the app (see app.py). Kept because the mobile team have a
branch that uses it.
"""
from flask import Blueprint

from larder_common.errors import BadRequest
from larder_common.web import body, ok
from services.accounts import postcodes
from services.accounts.models import Address

bp = Blueprint("accounts_addresses", __name__)

MAX_ADDRESSES = 5  # product said 5. (SB)


@bp.route("/internal/users/<user_id>/addresses")
def list_addresses(user_id):
    rows = Address.filter(user_id=user_id, order_by="-is_default")
    return ok({"addresses": [a.to_dict() for a in rows]})


@bp.route("/internal/users/<user_id>/addresses", methods=["POST"])
def add_address(user_id):
    data = body()
    if not data.get("line1") or not data.get("postcode"):
        raise BadRequest("address_incomplete")
    if Address.count(user_id=user_id) >= MAX_ADDRESSES:
        raise BadRequest("too_many_addresses")
    a = Address.create(
        user_id=user_id,
        label=data.get("label") or "Home",
        line1=data["line1"],
        line2=data.get("line2"),
        town=data.get("town"),
        postcode=postcodes.normalise(data["postcode"]),
        is_default=0,
    )
    return ok(a.to_dict(), 201)
