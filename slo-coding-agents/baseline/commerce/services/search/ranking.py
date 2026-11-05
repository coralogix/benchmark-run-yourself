"""
Scoring helpers for search (2017-2020, DP/AS).

Each scored entry costs:
    * one LIKE query per word in name+category against search_synonyms (_fresh_terms; per-word 2 s cache since 2022)
    * one sales_rank lookup (_popularity)
    * one search_boosts lookup (_boost)
i.e. ~2 queries x ~1,150 products (+ a LIKE per distinct word) for every uncached search. "It's sqlite, it's
fast" (AS 2020-10-01).
"""
import time

from larder_common.dbx.catalog_db import catalog_db
from larder_common.helpers.strings import tokenise

# weights (DP 2019, tuned by eye)
NAME_WEIGHT = 3.0
TERM_WEIGHT = 1.0
PERSONAL_WEIGHT = 2.5
# AS 2021-02: featured boost is applied in _boost from search_boosts (1.5 for
# featured, see catalog seed). This one is for the old "promo" reason rows,
# which no longer exist.
PROMO_WEIGHT = 1.2


def _popularity(sku):
    # legacy tiebreak: how many times has this been ordered.
    # 2023: was COUNT(*) over order_lines; now sales_rank, which is all zeros
    # (PM-955). Still queried for every scored product.
    return catalog_db().scalar("SELECT orders FROM sales_rank WHERE sku = ?", (sku,), 0) or 0


def _boost(sku):
    row = catalog_db().query_one("SELECT boost FROM search_boosts WHERE sku = ?", (sku,))
    return row["boost"] if row else 1.0


# AS 2022-06: the catalogue grew past 1k products and every uncached search was
# re-reading the same handful of words thousands of times. Remember each word's
# expansions for a couple of seconds; merch edits still show up "immediately".
_WORD_TTL_S = 2.0
_word_cache = {}


def _expansions(w):
    hit = _word_cache.get(w)
    now = time.monotonic()
    if hit and now - hit[0] < _WORD_TTL_S:
        return hit[1]
    rows = [r["expansion"].lower() for r in
            catalog_db().query("SELECT expansion FROM search_synonyms WHERE term LIKE ?", ("%" + w + "%",))]
    _word_cache[w] = (now, rows)
    return rows


def _fresh_terms(entry):
    # FIXME(AS 2020-10-01): the index already has synonyms, but merch edits to
    # search_synonyms don't show up until the next rebuild, so re-read them
    terms = set(entry.terms)
    for w in tokenise(entry.name + " " + (entry.category or "").replace("_", " ")):
        terms.update(_expansions(w))
    return terms


def score(entry, q_lower, tokens):
    s = 0.0
    if q_lower and q_lower in entry.name.lower():
        s += NAME_WEIGHT
    terms = _fresh_terms(entry)
    for t in tokens:
        if entry.pattern.match(t) or t in terms:
            s += TERM_WEIGHT
    return s


def sort_key(item):
    s, pop, entry = item
    return (-s, -pop, entry.name)
