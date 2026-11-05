"""
Cart pricing - "v2" engine (2019 rewrite).

All the maths is in pounds because that's what the products table stores.
Every function returns pounds AND pence so callers don't have to convert.

This is the engine everyone *thinks* production runs. Which engine is
actually used is decided by settings.PRICING_ENGINE, see engines.get_engine().
(moved from larder/pricing.py in the 2023 split)
"""
from larder_common.log import get_logger

log = get_logger("pricing.v2")

ENGINE_NAME = "v2"


def _coupon_fields(coupon):
    if coupon is None:
        return 0.0, False
    if isinstance(coupon, dict):
        return float(coupon.get("percent_off") or 0), bool(coupon.get("free_delivery"))
    return float(coupon.percent_off or 0), bool(coupon.free_delivery)


def line_total(unit_price, qty):
    return round(unit_price * qty, 2)


def delivery_fee_for(amount, free_delivery=False):
    from larder_common import settings

    if free_delivery:
        return 0.0
    if amount < settings.FREE_DELIVERY_THRESHOLD:
        return settings.DELIVERY_FEE
    return 0.0


def price_cart(lines, coupon=None):
    """
    lines: [{"sku", "name", "unit_price" (pounds), "qty"}]
    coupon: Coupon model / dict / None

    Returns a dict with pounds and pence for subtotal, discount, delivery and
    total, plus the priced lines.
    """
    pct, free_delivery = _coupon_fields(coupon)
    priced = []
    subtotal = 0.0
    for line in lines:
        lt = line_total(line["unit_price"], line["qty"])
        subtotal += lt
        priced.append(dict(line, line_total=lt, line_total_pence=int(round(lt * 100))))
    subtotal = round(subtotal, 2)

    discount = round(subtotal * pct / 100.0, 2) if pct else 0.0
    after_discount = subtotal - discount
    delivery = delivery_fee_for(after_discount, free_delivery)
    total = round(after_discount + delivery, 2)

    return {
        "engine": ENGINE_NAME,
        "lines": priced,
        "subtotal": subtotal,
        "discount": discount,
        "delivery_fee": delivery,
        "total": total,
        "subtotal_pence": int(round(subtotal * 100)),
        "discount_pence": int(round(discount * 100)),
        "delivery_fee_pence": int(round(delivery * 100)),
        "total_pence": int(round(total * 100)),
    }


def unit_price_pence(unit_price):
    return int(round(unit_price * 100))


def describe(breakdown):
    """Human readable one-liner, used in logs."""
    from larder_common.money import money_fmt

    return "sub=%s disc=%s del=%s total=%s (%s)" % (
        money_fmt(breakdown["subtotal"]),
        money_fmt(breakdown["discount"]),
        money_fmt(breakdown["delivery_fee"]),
        money_fmt(breakdown["total"]),
        breakdown.get("engine"),
    )
