"""
"Download my data" export (SB 2022-03, for the GDPR SAR backlog).

Never wired to a route: legal wanted it behind a support tool that didn't
get built. It fetches the user's orders the same way the account page does
(see summary.py) and returns everything as one dict.
"""
from larder_common.helpers.strings import mask_email
from larder_common.http import client
from larder_common.log import get_logger
from services.accounts import users

log = get_logger("accounts.gdpr")

EXPORT_VERSION = 2
# fields legal said we must NOT include (payment tokens live in ops anyway)
REDACT = ("payment_token", "charge_id", "hold_id")


def _orders(user_id):
    res = client("orders").get("/internal/orders", params={"user_id": user_id})
    if not res.ok:
        return []
    out = []
    for o in res.body.get("orders") or []:
        if o.get("user_id") != user_id:
            continue
        out.append({k: v for k, v in o.items() if k not in REDACT})
    return out


def export(user_id, mask=False):
    user = users.get_user(user_id).to_dict()
    if mask:
        user = dict(user, email=mask_email(user.get("email")))
    orders = _orders(user_id)
    log.info("gdpr export for %s: %d orders", user_id, len(orders))
    return {"version": EXPORT_VERSION, "user": user, "orders": orders, "loyalty": {"points": 0}}
