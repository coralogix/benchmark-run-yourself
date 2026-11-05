"""Calls to pricing (and catalog, for sku checks) on behalf of carts."""
from larder_common.errors import NotFound, PassThrough, UpstreamError
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("cart.pricing")


def price(raw_lines, coupon_code=None, validate_coupon=False):
    """-> pricing breakdown. Non-2xx answers are passed straight through."""
    payload = {"lines": raw_lines, "coupon_code": coupon_code, "validate_coupon": bool(validate_coupon)}
    try:
        res = client("pricing").post("/internal/price", json=payload)
    except Exception as e:
        log.error("pricing unavailable: %s", e)
        raise UpstreamError("pricing_unavailable")
    if not res.ok:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body


def check_sku(sku):
    """Raise NotFound('unknown_sku') unless catalog knows the sku. Returns the product."""
    try:
        res = client("catalog").get("/internal/products/%s" % sku)
    except Exception as e:
        log.error("catalog unavailable checking %s: %s", sku, e)
        raise UpstreamError("catalog_unavailable")
    if res.status == 404:
        raise NotFound("unknown_sku")
    if not res.ok:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res.body
