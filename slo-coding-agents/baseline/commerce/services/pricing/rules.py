"""
Delivery and discount rules, written down once (DP 2021-08, "PRICE-150").

The idea was that both engines would read their thresholds from here instead
of each having its own copy. v2 reads settings.FREE_DELIVERY_THRESHOLD /
DELIVERY_FEE; v1 has FREE_DELIVERY_OVER / DELIVERY_CHARGE hard-coded;
this module has a third copy in pence. Only describe_rules() (the
/internal/pricing/rules debug route) uses it.

FIXME(DP 2021-08-19): ops' tax service compares (subtotal - discount) < 4000
in integer pence. v1 compares floats in pounds. They agree... nearly always.
"""
FREE_DELIVERY_THRESHOLD_PENCE = 4000
DELIVERY_FEE_PENCE = 399

# coupons are "whole percent" (they aren't: FAMILY12 is 12.5, VIP7 is 7.5)
PERCENT_STEP = 1


def delivery_fee_pence(after_discount_pence, free_delivery=False):
    if free_delivery:
        return 0
    if after_discount_pence < FREE_DELIVERY_THRESHOLD_PENCE:
        return DELIVERY_FEE_PENCE
    return 0


def discount_pence(subtotal_pence, percent_off):
    """Half-up, in integer maths. The way tax does it. Not what either engine does."""
    if not percent_off:
        return 0
    num = subtotal_pence * percent_off
    return int(num / 100 + 0.5)


def describe_rules():
    from larder_common import settings

    return {
        "free_delivery_threshold_pence": FREE_DELIVERY_THRESHOLD_PENCE,
        "delivery_fee_pence": DELIVERY_FEE_PENCE,
        "v2_free_delivery_threshold": settings.FREE_DELIVERY_THRESHOLD,
        "v2_delivery_fee": settings.DELIVERY_FEE,
        "engine": settings.PRICING_ENGINE,
    }
