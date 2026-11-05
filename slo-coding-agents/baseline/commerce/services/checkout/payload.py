"""
Build the POST /internal/orders body for ops (the "orders" contract on the ops wiki).

ops re-prices every order from inventory's unit prices (tax service) and
answers 409 price_mismatch if our total_pence differs by even 1p. So the
numbers in here have to be the ones the customer saw.
"""
from larder_common.utils import pounds_to_pence


def unit_price_pence(line):
    # pricing already gives us unit_price_pence; recompute if an old pricing
    # build doesn't (pre-2023-05). NB rounds, unlike money.to_pence.
    if line.get("unit_price_pence") is not None:
        return int(line["unit_price_pence"])
    return pounds_to_pence(line["unit_price"])


def coupon_payload(breakdown, cart):
    c = breakdown.get("coupon")
    if c:
        return {
            "code": c.get("code"),
            "percent_off": c.get("percent_off") or 0,
            "free_delivery": bool(c.get("free_delivery")),
        }
    if cart.get("coupon"):
        # cart says there's a coupon but pricing didn't apply it (expired since
        # it was added, or promotions was down). Send nothing; ops would
        # reject a coupon it can't verify anyway. (KP 2020)
        return None
    return None


def build_order_body(order_ref, cart, breakdown, email, hold_id, payment_token):
    return {
        "order_ref": order_ref,
        "user_id": cart.get("user_id"),
        "email": email,
        "items": [
            {"sku": l["sku"], "qty": int(l["qty"]), "unit_price_pence": unit_price_pence(l)}
            for l in breakdown.get("lines", [])
        ],
        "coupon": coupon_payload(breakdown, cart),
        "subtotal_pence": breakdown["subtotal_pence"],
        "discount_pence": breakdown["discount_pence"],
        "delivery_fee_pence": breakdown["delivery_fee_pence"],
        "total_pence": breakdown["total_pence"],
        "hold_id": hold_id,
        "payment_token": payment_token,
    }
