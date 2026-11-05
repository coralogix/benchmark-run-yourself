"""
Talking to promotions.

resolve()   - code -> coupon dict for the engines, or None. No validation:
              a coupon on a cart was validated when it was applied (cart
              service), and we don't re-check expiry at checkout. (KP 2020:
              "a customer who applied it before midnight keeps it")
validate()  - code + subtotal -> promotions' verdict, raised as PassThrough.
"""
from larder_common.errors import PassThrough
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("pricing.coupons")


def resolve(code):
    if not code:
        return None
    res = client("promotions").get("/internal/coupons/%s" % code)
    if res.status == 404:
        log.info("coupon %s on cart no longer exists, pricing without it", code)
        return None
    if not res.ok:
        # promotions is broken; don't fail the whole cart for a coupon
        log.warning("coupon lookup %s -> %s", code, res.status)
        return None
    c = res.body
    return {
        "code": c.get("code"),
        "percent_off": c.get("percent_off") or 0,
        "free_delivery": bool(c.get("free_delivery")),
    }


def validate(code, subtotal_pence):
    res = client("promotions").post("/internal/coupons/validate", json={"code": code, "subtotal_pence": subtotal_pence})
    if not res.ok:
        # whatever promotions said (400 invalid/expired/min_spend, or a 500),
        # the caller gets exactly that
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body
