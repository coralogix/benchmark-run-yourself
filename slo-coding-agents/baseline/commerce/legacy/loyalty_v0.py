"""
"Larder Points" v0 (2017, RB). Scrapped before launch; the loyalty service
(services/loyalty, PM-1142) is the second attempt.

Rules as signed off by marketing in 2017, in case anyone wants them back:
    * 1 point per £1 spent on food, 2 per £1 on own-brand
    * no points on delivery charges or on discounted amounts
    * points expire after 12 months

Prices are pounds (float), like everything from 2017.
"""
import datetime

OWN_BRAND_MARKER = "Larder own-brand"
EXPIRY_DAYS = 365
FOOD_CATEGORIES = ("produce", "pantry", "dairy", "bakery", "meat", "frozen", "drinks")


def points_for_line(line):
    if line.get("category") not in FOOD_CATEGORIES:
        return 0
    per_pound = 2 if OWN_BRAND_MARKER in (line.get("description") or "") else 1
    return int(line["unit_price"] * line["qty"]) * per_pound


def points_for_basket(lines, discount=0.0):
    pts = sum(points_for_line(l) for l in lines)
    # "no points on discounted amounts": knock off a point per £1 of discount
    return max(0, pts - int(discount))


def expiry_for(earned_at=None):
    earned_at = earned_at or datetime.datetime.utcnow()
    return earned_at + datetime.timedelta(days=EXPIRY_DAYS)


def balance(entries, now=None):
    now = now or datetime.datetime.utcnow()
    return sum(e["points"] for e in entries if expiry_for(e["earned_at"]) > now)
