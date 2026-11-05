"""
accounts routes (internal API).

    GET /internal/users/<id>       {user_id,name,email,postcode,created}   (checkout, edge /v1/me)
    GET /internal/accounts/<id>    account summary                        (edge /api/account)
    GET /internal/users/<id>/masked  email masked, for the support tool (2020)
"""
from flask import Blueprint

from larder_common.helpers.strings import mask_email
from larder_common.web import ok
from services.accounts import summary, users

bp = Blueprint("accounts", __name__)


@bp.route("/internal/users/<user_id>")
def get_user(user_id):
    return ok(users.get_user(user_id).to_api())


@bp.route("/internal/users/<user_id>/masked")
def get_user_masked(user_id):
    u = users.get_user(user_id).to_api()
    u["email"] = mask_email(u["email"])
    return ok(u)


@bp.route("/internal/accounts/<user_id>")
def account(user_id):
    return ok(summary.account_summary(user_id))
