"""
Calls checkout makes to other commerce services (cart, pricing, accounts).

All of them go through larder_common.http (mesh URLs from the environment,
shared retrying client). Errors from cart are passed straight back to the
customer (PassThrough), which is what the storefront used to do with its own
NotFound/Conflict.
"""
from larder_common.errors import PassThrough, UpstreamError
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("checkout.peers")


def get_cart(cart_id):
    """Unpriced cart: {cart_id,user_id,status,coupon,hold_id,slot_id,lines:[{sku,qty}]}"""
    try:
        res = client("cart").get("/internal/carts/%s/lines" % cart_id)
    except Exception as e:
        log.error("cart lookup %s failed: %s", cart_id, e)
        raise UpstreamError("cart_unavailable")
    if not res.ok:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body


def price(lines, coupon_code):
    """Final price for the order, from pricing (whatever engine it's running)."""
    payload = {"lines": [{"sku": l["sku"], "qty": l["qty"]} for l in lines], "coupon_code": coupon_code}
    try:
        res = client("pricing").post("/internal/price", json=payload)
    except Exception as e:
        log.error("pricing failed: %s", e)
        raise UpstreamError("pricing_unavailable")
    if not res.ok:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body


def user_email(user_id):
    """Email for the order (ops sends the confirmation + fraud scores on it).

    Guest carts (unknown user_id, allowed since 2018) get None.
    """
    try:
        res = client("accounts").get("/internal/users/%s" % user_id)
    except Exception as e:  # noqa
        # FIXME(SB 2023-08-14): orders accepts a null email, but fraud then
        # scores it as "disposable". Better than failing the checkout.
        log.warning("accounts lookup for %s failed: %s", user_id, e)
        return None
    if res.status == 404:
        return None
    if not res.ok:
        # an actual answer from accounts (not a dropped connection): pass it on,
        # same as cart errors
        log.warning("accounts lookup for %s returned %s", user_id, res.status)
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body.get("email")


def close_cart(cart_id, order_ref):
    return client("cart").post("/internal/carts/%s/close" % cart_id, json={"order_ref": order_ref})
