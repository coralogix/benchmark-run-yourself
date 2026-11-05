"""
"Customers also bought" (TH 2023-04, PM-990).

The plan was co-purchase data from the orders export. The export never
arrived (PM-955), so v1 is: other popular products in the same category,
where "popular" means "search ranks it highly for the category name".

    also_bought(sku, limit) -> (base_product, [P])

Per product view this costs:
    1 catalog call for the base product
    1 search call (category query; usually a search cache hit)
    RECS_CANDIDATES (12) catalog calls, one per candidate
and returns `limit` (6) of them. The other 6 are fetched "for the scoring",
which currently only looks at tags.

TODO(TH 2023-04-20): cache per sku. Catalog only changes when merch edits it.
TODO(SB 2023-09-02): use catalog's POST /internal/products/bulk instead of 12
calls - started in bulk_v2.py behind featureflags "recs_bulk_lookup", never
finished.
"""
from larder_common import settings
from larder_common.helpers.strings import category_terms
from larder_common.log import get_logger
from services.recommendations import scoring
from services.recommendations.sources import catalog_client, search_client

log = get_logger("recommendations")


class NotFound(Exception):
    pass


def _product(sku):
    res = catalog_client().get("/internal/products/%s" % sku)
    if res.status == 404:
        return None
    if not res.ok:
        raise RuntimeError("catalog answered %s for %s" % (res.status, sku))
    return res.body


def _candidates(base):
    q = category_terms(base.get("category"))
    if not q:
        return []
    res = search_client().get("/internal/search", params={"q": q})
    if not res.ok:
        log.warning("search answered %s for %r", res.status, q)
        return []
    skus = [r["sku"] for r in res.body.get("results", []) if r.get("sku") != base["sku"]]
    return skus[: settings.RECS_CANDIDATES]


def also_bought(sku, limit=None):
    limit = limit or settings.RECS_LIMIT
    base = _product(sku)
    if base is None:
        raise NotFound(sku)
    products = []
    for cand in _candidates(base):
        # one call each (see module docstring)
        p = _product(cand)
        if p is not None:
            products.append(p)
    ranked = scoring.rank(base, products)
    return base, [scoring.strip(p) for p in ranked[:limit]]
