"""
First-boot catalog load. If the products table is empty we load
seed/products.json and generate the search synonyms.

Prices arrive as integer pence (price_pence) from the merch export and are
stored as REAL pounds, because that's what the products table has always
held. API responses derive price_pence again on the way out.

(2023 split: users, coupons and zones used to be loaded here too; they moved
to accounts, promotions and ops respectively.)
"""
import re

from larder_common.dbx.catalog_db import catalog_db
from larder_common.log import get_logger
from larder_common.seedfiles import load_json

log = get_logger("catalog.seed")

# hand-maintained synonyms from the 2017 search project ("make search
# understand british english"). The rest are generated from product names.
LEGACY_SYNONYMS = {
    "aubergine": ["eggplant"],
    "courgette": ["zucchini"],
    "crisps": ["chips"],
    "chips": ["fries", "crisps"],
    "coriander": ["cilantro"],
    "mince": ["ground", "minced"],
    "nappy": ["nappies", "diaper", "diapers"],
    "yoghurt": ["yogurt", "yoghurts"],
    "biscuits": ["cookies"],
    "prawns": ["shrimp"],
    "lager": ["beer"],
    "beer": ["ale", "lager"],
    "fizzy": ["soda", "sparkling"],
    "sparkling": ["fizzy"],
    "cola": ["coke", "soda"],
    "toothpaste": ["dental"],
    "wipes": ["baby wipes"],
    "loo": ["toilet"],
    "plasters": ["band aid", "bandages"],
    "sausages": ["bangers"],
    "potatoes": ["spuds", "potato"],
    "tomatoes": ["tomato"],
    "peppers": ["capsicum"],
    "rocket": ["arugula"],
    "porridge": ["oats", "oatmeal"],
    "oats": ["porridge"],
    "cheddar": ["cheese"],
    "halloumi": ["cheese"],
    "feta": ["cheese"],
    "salmon": ["fish"],
    "cod": ["fish"],
    "washing": ["laundry"],
    "laundry": ["washing"],
    "sponges": ["scourers"],
    "kombucha": ["fermented"],
    "wine": ["vino"],
}


def is_empty():
    return (catalog_db().scalar("SELECT COUNT(*) FROM products", default=0) or 0) == 0


def load_if_empty():
    if not is_empty():
        return False
    log.info("empty catalog database, loading seed data")
    db = catalog_db()
    with db.transaction():
        _load_products(db)
        _load_synonyms(db)
        _load_sales_rank(db)
    log.info("catalog seed load complete")
    return True


def _load_products(db):
    for p in load_json("products.json"):
        pid = db.execute(
            "INSERT INTO products (sku, name, category, price, vat_band, description, active, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))",
            (
                p["sku"],
                p["name"],
                p.get("category"),
                p["price_pence"] / 100.0,
                p.get("vat_band", "standard"),
                p.get("description", ""),
            ),
        )
        for tag in p.get("tags", []):
            db.execute("INSERT INTO product_tags (product_id, tag) VALUES (?, ?)", (pid, tag))
            if tag == "featured":
                db.execute("INSERT INTO search_boosts (sku, boost, reason) VALUES (?, 1.5, 'featured')", (p["sku"],))
        for d in p.get("diet", []):
            db.execute("INSERT INTO product_diet (product_id, diet) VALUES (?, ?)", (pid, d))


def _load_synonyms(db):
    for term, expansions in LEGACY_SYNONYMS.items():
        for e in expansions:
            db.execute(
                "INSERT INTO search_synonyms (term, expansion, source) VALUES (?, ?, 'legacy')", (term, e)
            )
    # generated: every word in a product name maps to its category, plus
    # crude singular/plural pairs. (once per pair: the 2021 range extension
    # made the table 15x bigger and every search LIKE-scans it)
    seen = set()
    for row in db.query("SELECT name, category FROM products"):
        for word in re.findall(r"[a-z]+", row["name"].lower()):
            if len(word) < 3:
                continue
            alt = word[:-1] if word.endswith("s") else word + "s"
            for expansion in (row["category"], alt):
                if (word, expansion) in seen:
                    continue
                seen.add((word, expansion))
                db.execute(
                    "INSERT INTO search_synonyms (term, expansion, source) VALUES (?, ?, 'gen')", (word, expansion)
                )


def _load_sales_rank(db):
    # PM-955 placeholder rows so search's tiebreak query finds *something*
    for row in db.query("SELECT sku FROM products"):
        db.execute("INSERT INTO sales_rank (sku, orders, updated_at) VALUES (?, 0, datetime('now'))", (row["sku"],))
