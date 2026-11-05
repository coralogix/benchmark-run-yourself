"""
Product listing / detail.

Before the split this module also owned the in-memory search index and its
CATALOG_LOCK; the index moved to the search service (which builds its own
from catalog.db). The lock stayed, because "listing reads a consistent
snapshot" (RB 2018-02-12). It's a process-wide lock around every listing and
detail read, i.e. catalog serves one product read at a time per process.
"""
import threading

from larder_common import settings
from larder_common.dbx.catalog_db import catalog_db
from larder_common.log import get_logger
from services.catalog.models import Product

log = get_logger("catalog")

# global lock around catalogue reads (RB 2018-02-12)
CATALOG_LOCK = threading.Lock()

DEFAULT_PER_PAGE = settings.CATALOG_DEFAULT_PER_PAGE
MAX_PER_PAGE = settings.CATALOG_MAX_PER_PAGE


# --- serialisation -----------------------------------------------------------


def price_pence(price):
    return int(round(price * 100))


def serialise(product, tags=None, diet=None, detail=False):
    """Product -> API dict (no in_stock; edge-api adds that). Pass tags/diet if you already have them."""
    if tags is None:
        tags = product.tags()
    if diet is None:
        diet = product.diet()
    out = {
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "price": product.price,
        "price_pence": price_pence(product.price),
        "vat_band": product.vat_band,
        "diet": sorted(diet),
        "tags": sorted(tags),
    }
    if detail:
        out["id"] = product.id
        out["description"] = product.description
    return out


# --- listing / detail -------------------------------------------------------


def list_products(page=1, per_page=DEFAULT_PER_PAGE, category=None):
    page = max(1, page)
    per_page = max(1, min(MAX_PER_PAGE, per_page))
    filters = {"active": 1}
    if category:
        filters["category"] = category
    with CATALOG_LOCK:
        total = Product.count(**filters)
        products = Product.filter(order_by="id", limit=per_page, offset=(page - 1) * per_page, **filters)
        items = [serialise(p) for p in products]
    return {"items": items, "page": page, "per_page": per_page, "total": total}


def get_product(sku):
    with CATALOG_LOCK:
        p = Product.by_sku(sku)
        if p is None or not p.active:
            return None
        return serialise(p, detail=True)


def get_product_by_id(product_id):
    with CATALOG_LOCK:
        p = Product.get(product_id)
        if p is None or not p.active:
            return None
        return serialise(p, detail=True)


def bulk(skus):
    """Several products in one go. Added 2023-04 for recommendations (TH).

    NB: still N queries underneath (tags + diet per product), but one HTTP call.
    """
    skus = [s for s in (skus or []) if s][:100]
    if not skus:
        return []
    with CATALOG_LOCK:
        products = Product.filter(sku__in=skus, active=1)
        by_sku = {p.sku: serialise(p) for p in products}
    return [by_sku[s] for s in skus if s in by_sku]


def categories():
    return [r["category"] for r in catalog_db().query("SELECT DISTINCT category FROM products ORDER BY category")]


def category_counts():
    rows = catalog_db().query(
        "SELECT category, COUNT(*) AS n FROM products WHERE active = 1 GROUP BY category ORDER BY category"
    )
    return {r["category"]: r["n"] for r in rows}
