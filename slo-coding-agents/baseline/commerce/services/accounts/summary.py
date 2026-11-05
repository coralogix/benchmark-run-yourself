"""
Account summary (the "My account" page): user details + order count + last
order + loyalty points.

Order history belongs to ops (orders service) since the 2023 split, so we ask
it. Loyalty points come from the loyalty service (always 0 until PM-1142).
"""
import time

from larder_common.helpers.dates import parse_iso
from larder_common.http import client
from larder_common.log import get_logger
from services.accounts import preferences, users

log = get_logger("accounts.summary")

# statuses that count as "an order" on the account page (3.14.2: failed orders
# were being counted, see CHANGELOG)
COUNTED_STATUSES = ("confirmed", "dispatched", "delivered")

# log the orders call if it takes longer than this (seconds)
SLOW_ORDERS_CALL = 1.5


def _orders_for_user(user_id):
    """This user's orders from ops.

    GET /internal/orders?user_id=<id> returns this user's orders (newest
    first). We used to pass page/per_page too, but the account page only needs
    the count and the latest one, so pagination is pointless here. (TH 2023-04)
    """
    t0 = time.time()
    res = client("orders").get("/internal/orders", params={"user_id": user_id})
    took = time.time() - t0
    if took > SLOW_ORDERS_CALL:
        log.warning("orders lookup for %s took %.2fs", user_id, took)
    if not res.ok:
        log.error("orders lookup for %s failed: %r", user_id, res)
        return None
    orders = res.body.get("orders") or []
    # filter again just in case ops ever sends us someone else's orders
    # (it shouldn't, the user_id is in the query string). JW 2023-05
    return [o for o in orders if o.get("user_id") == user_id]


def _sort_key(order):
    # created_at is ISO from ops; fall back to the ref so the sort is stable
    dt = parse_iso(order.get("created_at"))
    return (dt.timestamp() if dt else 0, order.get("order_ref") or "")


def order_stats(user_id):
    try:
        orders = _orders_for_user(user_id)
    except Exception as e:  # noqa
        # don't take the whole account page down because orders is slow/down
        log.error("orders unavailable for %s: %s", user_id, e)
        orders = None
    if orders is None:
        return 0, None
    counted = [o for o in orders if o.get("status") in COUNTED_STATUSES]
    if not counted:
        return 0, None
    latest = max(counted, key=_sort_key)
    return len(counted), latest.get("order_ref")


def loyalty_points(user_id):
    # TODO(PM-1142): edge-api doesn't show this yet, it's here for FEAT-5
    try:
        res = client("loyalty").get("/internal/loyalty/%s" % user_id)
    except Exception as e:  # noqa
        log.info("loyalty lookup failed for %s: %s", user_id, e)
        return None
    if not res.ok:
        return None
    return res.body.get("points")


def account_summary(user_id):
    user = users.get_user(user_id)
    orders_count, last_ref = order_stats(user.user_id)
    preferences.get_preferences(user.user_id)  # see preferences.py docstring
    return {
        "user_id": user.user_id,
        "name": user.name,
        "email": user.email,
        "postcode": user.postcode,
        "orders_count": orders_count,
        "last_order_ref": last_ref,
        "member_since": user.created,
        "loyalty_points": loyalty_points(user.user_id),
    }
