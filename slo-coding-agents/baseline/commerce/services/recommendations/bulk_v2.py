"""
Recommendations v2: bulk catalog lookups (SB 2023-09, unfinished).

Behind featureflags "recs_bulk_lookup" (default off). Nothing calls
also_bought_v2(); routes.py still uses engine.also_bought. Last state of the
branch: "works locally, need to check catalog's bulk endpoint handles >100
skus" (it truncates at 100, which is more than 12, so it was fine).
"""
from larder_common import featureflags, settings
from larder_common.helpers.strings import category_terms
from services.recommendations import scoring
from services.recommendations.sources import catalog_client, search_client


def enabled():
    return featureflags.enabled("recs_bulk_lookup")


def also_bought_v2(sku, limit=None):
    limit = limit or settings.RECS_LIMIT
    base = catalog_client().get("/internal/products/%s" % sku)
    if not base.ok:
        return None, []
    base = base.body
    res = search_client().get("/internal/search", params={"q": category_terms(base["category"])})
    skus = [r["sku"] for r in res.body.get("results", []) if r["sku"] != sku][: settings.RECS_CANDIDATES]
    bulk = catalog_client().post("/internal/products/bulk", json={"skus": skus})
    # FIXME(SB 2023-09-12): bulk returns no description, scoring doesn't care
    products = bulk.body.get("items", []) if bulk.ok else []
    return base, [scoring.strip(p) for p in scoring.rank(base, products)[:limit]]
