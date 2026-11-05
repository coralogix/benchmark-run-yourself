"""
Query normalisation (DP 2018, "search v1.5").

Was applied to every query before scoring until 2020, when AS moved scoring
onto larder_common.helpers.strings.tokenise. Still used by the admin reindex
report and by spellfix. NB: this tokeniser keeps stopwords and splits on
hyphens differently from helpers.strings.tokenise ("semi-skimmed" -> one
token here, two there), so queries that look the same in the query_log can
score differently. Don't "unify" without re-tuning NAME_WEIGHT (DP).
"""
import re

_SPLIT = re.compile(r"[\s,;/]+")
_STRIP = re.compile(r"[^a-z0-9\-]")

# british -> american (and back). Overlaps with catalog's LEGACY_SYNONYMS.
SPELLINGS = {
    "yoghurt": "yogurt",
    "colour": "color",
    "flavour": "flavor",
    "savoury": "savory",
    "aluminium": "aluminum",
    "nappies": "diapers",
    "courgette": "zucchini",
    "aubergine": "eggplant",
}

# words we drop from the *front* of a query only (mobile app autocomplete
# used to prefix "buy")
LEADING_NOISE = ("buy", "cheap", "best", "larder")

MAX_QUERY_LEN = 120


def normalise_query(q):
    q = (q or "").strip().lower()[:MAX_QUERY_LEN]
    words = [_STRIP.sub("", w) for w in _SPLIT.split(q)]
    words = [w for w in words if w]
    while words and words[0] in LEADING_NOISE:
        words = words[1:]
    return " ".join(words)


def tokens(q):
    return normalise_query(q).split()


def americanise(q):
    return " ".join(SPELLINGS.get(w, w) for w in tokens(q))


def britishise(q):
    rev = {v: k for k, v in SPELLINGS.items()}
    return " ".join(rev.get(w, w) for w in tokens(q))


def is_sku_query(q):
    # "SKU-0001" style lookups go to legacy.search_v1 in the old app
    return bool(re.match(r"^sku-\d{3,6}$", (q or "").strip().lower()))
