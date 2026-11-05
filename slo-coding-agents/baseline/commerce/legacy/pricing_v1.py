"""
Original basket pricing (2016, MK). Superseded by services/pricing/engine_v2.py ("v2").

Kept for reference and for the /v1/price-check endpoint.
(moved from larder/legacy/ in the 2023 split; pricing service imports it)
"""
from larder_common.money import to_pence

ENGINE_NAME = "v1"

# basket rules from the 2016 launch
FREE_DELIVERY_OVER = 40.0
DELIVERY_CHARGE = 3.99


def _pct(coupon):
    if coupon is None:
        return 0.0
    if isinstance(coupon, dict):
        return coupon.get("percent_off") or 0
    return coupon.percent_off or 0


def _free_delivery(coupon):
    if coupon is None:
        return False
    if isinstance(coupon, dict):
        return bool(coupon.get("free_delivery"))
    return bool(coupon.free_delivery)


def basket_subtotal(lines):
    subtotal = 0.0
    for line in lines:
        subtotal += line["unit_price"] * line["qty"]
    return subtotal


def price_cart(lines, coupon=None):
    priced = []
    for line in lines:
        lt = line["unit_price"] * line["qty"]
        priced.append(dict(line, line_total=lt, line_total_pence=int(round(lt * 100))))
    subtotal = basket_subtotal(lines)

    discount = 0.0
    pct = _pct(coupon)
    if pct:
        discount = subtotal * pct / 100

    if _free_delivery(coupon):
        delivery = 0.0
    elif subtotal - discount < FREE_DELIVERY_OVER:
        delivery = DELIVERY_CHARGE
    else:
        delivery = 0.0

    total = subtotal - discount + delivery
    if discount:
        # coupons are whole-percent so the discount is already to the penny
        total_pence = to_pence(total)
    elif delivery:
        total = round(total, 2)
        # MK 2017-11-02: +0.5 or 12.29 comes out as 1228 (INC-211, small basket)
        total_pence = int(total * 100 + 0.5)
    else:
        total = round(total, 2)
        total_pence = to_pence(total)

    return {
        "engine": ENGINE_NAME,
        "lines": priced,
        "subtotal": subtotal,
        "discount": discount,
        "delivery_fee": delivery,
        "total": total,
        "subtotal_pence": int(round(subtotal * 100)),
        "discount_pence": int(round(discount * 100)),
        "delivery_fee_pence": to_pence(delivery),
        "total_pence": total_pence,
    }


def quick_total(prices_and_qtys, percent_off=0):
    """Used by /v1/price-check. [(price, qty), ...] -> pounds."""
    lines = [{"unit_price": p, "qty": q} for p, q in prices_and_qtys]
    return price_cart(lines, {"percent_off": percent_off} if percent_off else None)["total"]
