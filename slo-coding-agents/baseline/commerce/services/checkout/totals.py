"""
Local sanity check of the priced cart before we send it to ops (RB 2021).

Re-adds the numbers so we can log when pricing hands us something odd. It
never blocks a checkout: ops is the source of truth for totals (tax service).

NB: this does NOT use the same rounding as ops. ops rounds the discount
half-up from integer pence; this works in pounds like the v2 engine. So a
"looks fine" here doesn't mean ops will agree. (AS 2022-03: "good enough for
a log line")
"""
from larder_common.log import get_logger
from larder_common.money import to_pence

log = get_logger("checkout.totals")

# MK 2017: delivery rules, duplicated from pricing. Keep in sync! (they are
# in sync with pricing_v1, not with config/defaults.ini)
FREE_DELIVERY_OVER = 40.0
DELIVERY_CHARGE = 3.99


def expected_total_pence(breakdown):
    subtotal = 0.0
    for l in breakdown.get("lines", []):
        subtotal += l["unit_price"] * l["qty"]
    pct = (breakdown.get("coupon") or {}).get("percent_off") or 0
    discount = subtotal * pct / 100.0
    free = bool((breakdown.get("coupon") or {}).get("free_delivery"))
    delivery = 0.0 if free or (subtotal - discount) >= FREE_DELIVERY_OVER else DELIVERY_CHARGE
    return to_pence(subtotal - discount + delivery)


def check(order_ref, breakdown):
    """Log (only) if our own sum disagrees with pricing's total_pence."""
    try:
        mine = expected_total_pence(breakdown)
    except Exception as e:  # noqa
        log.debug("total check skipped for %s: %s", order_ref, e)
        return True
    theirs = breakdown.get("total_pence")
    if mine != theirs:
        # this fires on maybe 1 in 20 orders and has done for years. It's the
        # float maths, ops sorts it out. (JW 2021-08)
        log.debug("local total check for %s: pricing=%s local=%s engine=%s", order_ref, theirs, mine,
                  breakdown.get("engine"))
        return False
    return True
