"""
Product badges (DP 2022). "New", "Featured" and "3 for 2" badges computed
from tags + created_at. The apps compute these themselves now; edge-api never
called this. FEAT-11 ("only N left") would go here, except stock lives in ops.
"""
import datetime

NEW_DAYS = 30
BADGE_TAGS = {"featured": "Featured", "3for2": "3 for 2"}


def badges(product, now=None):
    out = []
    for tag in product.get("tags") or []:
        if tag in BADGE_TAGS:
            out.append(BADGE_TAGS[tag])
    created = product.get("created_at")
    if created:
        try:
            dt = datetime.datetime.strptime(created[:10], "%Y-%m-%d")
        except ValueError:
            dt = None
        now = now or datetime.datetime.utcnow()
        if dt and (now - dt).days <= NEW_DAYS:
            out.append("New")
    return out
