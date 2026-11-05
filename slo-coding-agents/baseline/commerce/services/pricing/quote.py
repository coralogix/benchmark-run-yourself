"""
Pricing a cart: lines + coupon -> breakdown (the /internal/price endpoint).

    quote([{"sku": "SKU-0001", "qty": 2}], coupon_code="WELCOME10")

The engine does the maths; this module fetches prices, resolves the coupon,
and normalises the output so v1 and v2 look the same to callers.
"""
from larder_common.log import get_logger
from services.pricing import coupons_client, engines, prices

log = get_logger("pricing.quote")


def _public_line(line):
    return {
        "sku": line["sku"],
        "name": line.get("name"),
        "qty": line["qty"],
        "unit_price": line["unit_price"],
        "unit_price_pence": int(round(line["unit_price"] * 100)),
        "line_total_pence": line["line_total_pence"],
        "vat_band": line.get("vat_band", "standard"),
    }


def _shape(breakdown, coupon):
    out = {
        "engine": breakdown.get("engine"),
        "lines": [_public_line(l) for l in breakdown["lines"]],
        "subtotal": breakdown["subtotal"],
        "discount": breakdown["discount"],
        "delivery_fee": breakdown["delivery_fee"],
        "total": breakdown["total"],
        "subtotal_pence": breakdown["subtotal_pence"],
        "discount_pence": breakdown["discount_pence"],
        "delivery_fee_pence": breakdown["delivery_fee_pence"],
        "total_pence": breakdown["total_pence"],
        "coupon": coupon,
    }
    return out


def price_lines(lines, coupon=None):
    engine = engines.get_engine()
    return _shape(engine.price_cart(lines, coupon), coupon)


def quote(raw_lines, coupon_code=None, validate_coupon=False):
    lines = prices.build_lines(raw_lines)
    if coupon_code and validate_coupon:
        # price once without the coupon to get the subtotal promotions wants
        bare = price_lines(lines, None)
        coupons_client.validate(coupon_code, bare["subtotal_pence"])
    coupon = coupons_client.resolve(coupon_code) if coupon_code else None
    return price_lines(lines, coupon)


def live_price(sku):
    p = prices.lookup(sku)
    if p is None or not p["active"]:
        return None
    return {"sku": p["sku"], "price": p["unit_price"], "price_pence": int(round(p["unit_price"] * 100))}


def price_check_v1(raw_lines, percent_off=0):
    """/v1/price-check: always the v1 engine, regardless of settings."""
    pairs = []
    for raw in raw_lines or []:
        p = prices.lookup(raw.get("sku"))
        if p is None:
            continue
        pairs.append((p["unit_price"], int(raw.get("qty", 1))))
    return engines.v1().quick_total(pairs, percent_off or 0)
