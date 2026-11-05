"""
Loyalty tiers (marketing deck, 2022-11). Not used: there are no points yet.

Thresholds are points in the trailing 12 months. Benefits are what the deck
promised; FREESHIP-for-gold was going to be a per-customer coupon (FEAT-12
would be needed for that).
"""

TIERS = [
    # (name, min_points, perks)
    ("bronze", 0, ()),
    ("silver", 500, ("birthday_coupon",)),
    ("gold", 1500, ("birthday_coupon", "free_delivery")),
    ("platinum", 5000, ("birthday_coupon", "free_delivery", "priority_slots")),
]


def tier_for(points):
    name = TIERS[0][0]
    for tier, minimum, _ in TIERS:
        if points >= minimum:
            name = tier
    return name


def perks_for(points):
    for tier, minimum, perks in reversed(TIERS):
        if points >= minimum:
            return list(perks)
    return []


def next_tier(points):
    for tier, minimum, _ in TIERS:
        if points < minimum:
            return {"tier": tier, "points_needed": minimum - points}
    return None
