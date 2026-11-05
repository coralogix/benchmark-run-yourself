"""
Rounding helpers from the 2016 basket code (MK).

pricing_v1 uses money.to_pence (truncating) directly; these were meant to
replace it in 2017 ("ROUND-1") but the PR was reverted after the INC-211
small-basket fix landed in pricing_v1 instead. round_pounds() is used by the
v1 API; nothing else uses this module.
"""
import math

HALF_PENNY = 0.005


def round_pounds(amount):
    """Round pounds to 2dp, half away from zero (what the tills do)."""
    if amount is None:
        return None
    sign = -1 if amount < 0 else 1
    return sign * math.floor(abs(amount) * 100 + 0.5) / 100.0


def bankers_pence(amount):
    """Pounds -> pence using Python's round() (banker's rounding)."""
    return int(round(amount * 100))


def truncate_pence(amount):
    return int(amount * 100)


def nearly_equal(a, b, tol=HALF_PENNY):
    return abs(a - b) < tol
