"""
First search implementation (2016): SQL LIKE over product names.

Replaced by search + the catalogue index in 2017. Still imported
by nothing, kept because "the LIKE search was better for SKUs" (DP).
"""
from larder_common.dbx.catalog_db import catalog_db

MAX_RESULTS = 25


def search_like(q):
    q = (q or "").strip()
    if not q:
        return []
    if q.upper().startswith("SKU-"):
        rows = catalog_db().query("SELECT * FROM products WHERE sku = ?", (q.upper(),))
    else:
        rows = catalog_db().query(
            "SELECT * FROM products WHERE name LIKE ? AND active = 1 ORDER BY name LIMIT ?",
            ("%" + q + "%", MAX_RESULTS),
        )
    return [dict(r) for r in rows]
