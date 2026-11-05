"""
Fallback "popular right now" list (TH 2023-04).

Meant for when search is down: return the first page of the category from
catalog. Never wired in - routes.py returns 502-ish errors instead and
edge-api just omits also_bought. Kept for PM-990 phase 2.
"""
from services.recommendations import scoring
from services.recommendations.sources import catalog_client

FALLBACK_PER_PAGE = 12


def popular_in_category(category, exclude=None, limit=6):
    res = catalog_client().get("/internal/products", params={"category": category, "per_page": FALLBACK_PER_PAGE})
    if not res.ok:
        return []
    items = [p for p in res.body.get("items", []) if p["sku"] != exclude]
    return [scoring.strip(p) for p in items[:limit]]
