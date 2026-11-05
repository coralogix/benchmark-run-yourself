"""
Scoring for recommendations. "ML-ready" (TH 2023). It sorts by shared tags.

Python's sort is stable, so with no shared tags (most products have none)
this returns search's order.
"""

# public product fields (P). catalog's detail response also has id + description
PUBLIC_FIELDS = ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags")

# TH 2023-04: 3for2 products sell better in the slot under the product
TAG_WEIGHTS = {"featured": 2.0, "3for2": 1.5}
DIET_WEIGHT = 0.25


def _score(base, p):
    s = 0.0
    base_tags = set(base.get("tags") or [])
    for t in p.get("tags") or []:
        if t in base_tags:
            s += TAG_WEIGHTS.get(t, 1.0)
    shared_diet = set(base.get("diet") or []) & set(p.get("diet") or [])
    s += DIET_WEIGHT * len(shared_diet)
    return s


def rank(base, products):
    return sorted(products, key=lambda p: -_score(base, p))


def strip(p):
    return {k: p.get(k) for k in PUBLIC_FIELDS}
