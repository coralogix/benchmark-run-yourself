from flask import Blueprint

from larder_common.web import ok
from services.edge_api import upstreams

bp = Blueprint("account", __name__)

# fields the apps show. accounts sends more (loyalty_points, member_since);
# TODO(TH 2023-04): strip loyalty_points until FEAT-5 ships? (we don't)
ACCOUNT_FIELDS = ("user_id", "name", "email", "postcode", "orders_count", "last_order_ref")


@bp.route("/api/account/<user_id>")
def account(user_id):
    return ok(upstreams.account(user_id))
