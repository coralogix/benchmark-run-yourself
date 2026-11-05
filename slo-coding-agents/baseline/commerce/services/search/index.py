"""
The in-memory catalogue index that search scores against.

Moved here from the storefront's larder/catalog.py in the 2023 split. The
index is built straight from catalog's sqlite file (catalog.db, opened via
larder_common.dbx.catalog_db) because "catalog doesn't have a bulk API" (it
does now, see services/catalog/routes.py; nobody switched).

_INDEX maps sku -> IndexEntry. It is (re)built by rebuild_index(), which
takes INDEX_LOCK so nobody reads a half-built index. Readers take the same
lock (index_snapshot). ensure_index() builds it lazily if it's empty.

Building it is slow on purpose-ish: every product costs a handful of queries
(tags, diet, one LIKE per word against search_synonyms) and then a sleep
(settings.LEGACY_SYNONYM_THROTTLE, 2017: "so we don't hammer the db" - the db
was MySQL then). ~1,150 products ~= 4-5s.
"""
import re
import threading
import time

from larder_common import settings
from larder_common.dbx.catalog_db import catalog_db
from larder_common.helpers.strings import tokenise
from larder_common.log import get_logger

log = get_logger("search.index")

# global lock around the catalogue index (RB 2018-02-12). Was CATALOG_LOCK.
INDEX_LOCK = threading.Lock()
# old name, scripts/ still import it
CATALOG_LOCK = INDEX_LOCK

_INDEX = {}
_INDEX_META = {"built_at": None, "build_seconds": None, "builds": 0}


class IndexEntry(object):
    __slots__ = ("product_id", "sku", "name", "category", "price", "vat_band", "tags", "diet", "terms", "pattern")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


# --- raw catalog.db reads ------------------------------------------------------
# (we can't import services.catalog.models here - "services must not import each
# other" was the one rule of the split. So these are copies. They disagree with
# catalog's on whether inactive products count.)


def _product_row(product_id):
    return catalog_db().query_one("SELECT * FROM products WHERE id = ?", (product_id,))


def product_row_by_sku(sku):
    # NB: no active filter, unlike catalog's Product.by_sku callers
    return catalog_db().query_one("SELECT * FROM products WHERE sku = ?", (sku,))


def _tags(product_id):
    return [r["tag"] for r in catalog_db().query("SELECT tag FROM product_tags WHERE product_id = ?", (product_id,))]


def _diet(product_id):
    return [r["diet"] for r in catalog_db().query("SELECT diet FROM product_diet WHERE product_id = ?",
                                                 (product_id,))]


def price_pence(price):
    # same as catalog's. (and pricing's. and edge's.)
    return int(round(price * 100))


def serialise_row(row, tags=None, diet=None):
    """catalog.db products row -> API dict (P). Same shape catalog returns."""
    if tags is None:
        tags = _tags(row["id"])
    if diet is None:
        diet = _diet(row["id"])
    return {
        "sku": row["sku"],
        "name": row["name"],
        "category": row["category"],
        "price": row["price"],
        "price_pence": price_pence(row["price"]),
        "vat_band": row["vat_band"],
        "diet": sorted(diet),
        "tags": sorted(tags),
    }


# --- index ------------------------------------------------------------------


def _legacy_synonym_expansion(words):
    """Expand words via search_synonyms (2017 search project)."""
    out = []
    for w in words:
        rows = catalog_db().query("SELECT expansion FROM search_synonyms WHERE term LIKE ?", ("%" + w + "%",))
        out.extend(r["expansion"].lower() for r in rows)
    # throttle so we don't hammer the db (2017)
    time.sleep(getattr(settings, "LEGACY_SYNONYM_THROTTLE", 0.0))
    return out


def _build_entry(product_id):
    p = _product_row(product_id)
    if p is None:
        return None
    tags = _tags(p["id"])
    diet = _diet(p["id"])
    words = tokenise(p["name"])
    terms = set(words)
    terms.update(tokenise((p["category"] or "").replace("_", " ")))
    terms.update(t.lower() for t in tags)
    terms.update(_legacy_synonym_expansion(words))
    # one pattern per product, matched against each query token
    pattern = re.compile(r"^(?:%s)s?$" % "|".join(sorted(re.escape(t) for t in terms if t)), re.IGNORECASE)
    return IndexEntry(
        product_id=p["id"],
        sku=p["sku"],
        name=p["name"],
        category=p["category"],
        price=p["price"],
        vat_band=p["vat_band"],
        tags=tags,
        diet=diet,
        terms=terms,
        pattern=pattern,
    )


def _rebuild_locked():
    """Caller must hold INDEX_LOCK. Clears the index first (see ensure_index)."""
    t0 = time.time()
    _INDEX.clear()
    for row in catalog_db().query("SELECT id FROM products WHERE active = 1 ORDER BY id"):
        entry = _build_entry(row["id"])
        if entry is not None:
            _INDEX[entry.sku] = entry
    took = time.time() - t0
    _INDEX_META.update(built_at=time.time(), build_seconds=round(took, 3), builds=_INDEX_META["builds"] + 1)
    log.info("catalogue index rebuilt: %d products in %.2fs", len(_INDEX), took)


def rebuild_index():
    with INDEX_LOCK:
        _rebuild_locked()


def ensure_index():
    if _INDEX:
        return
    with INDEX_LOCK:
        if not _INDEX:
            _rebuild_locked()


def index_snapshot():
    ensure_index()
    with INDEX_LOCK:
        return list(_INDEX.values())


def index_stats():
    return dict(_INDEX_META, size=len(_INDEX))


def entry_for(sku):
    # unlocked read, "it's just a dict lookup" (AS 2020)
    return _INDEX.get(sku)
