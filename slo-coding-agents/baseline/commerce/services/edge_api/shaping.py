"""
Response shaping for the public API (see the API doc in the api-docs repo).

Product = {sku,name,category,price,price_pence,vat_band,diet,tags,in_stock}
(+ description and meta_description on detail). Internal fields from catalog
(id, ...) are dropped.

NB: price_pence is recomputed here from price, with round(); catalog already
sends price_pence. They agree for every seeded product. (DP 2023-03: "belt
and braces")
"""
from larder_common.helpers.strings import humanise_category, truncate

PRODUCT_FIELDS = ("sku", "name", "category", "price", "price_pence", "vat_band", "diet", "tags", "in_stock")
DETAIL_FIELDS = PRODUCT_FIELDS + ("description",)


def product(p, detail=False):
    fields = DETAIL_FIELDS if detail else PRODUCT_FIELDS
    out = {k: p.get(k) for k in fields}
    if out.get("price") is not None:
        out["price_pence"] = int(round(out["price"] * 100))
    if out.get("diet") is None:
        out["diet"] = []
    if out.get("tags") is None:
        out["tags"] = []
    if detail:
        out["meta_description"] = meta_description(out)
    return out


def products(items, detail=False):
    return [product(p, detail=detail) for p in items or []]


def pence_to_pounds(pence):
    # (third copy of this in the repo. see larder_common.money.from_pence and utils.pence_to_pounds)
    if pence is None:
        return None
    return round(pence / 100.0, 2)


# SEO (DP 2019): the storefront puts this in <meta name="description"> on the
# product page. Google shows ~155 characters, and marketing wanted the category
# on the end of every one ("... | Health Beauty | Larder").
META_DESCRIPTION_MAX = 155
META_SUFFIX = " | %s | Larder"


def meta_description(p):
    text = (p.get("description") or p.get("name") or "").strip()
    room = META_DESCRIPTION_MAX - len(META_SUFFIX) - 16  # 16 ~ longest category name
    return (truncate(text, room) + META_SUFFIX) % humanise_category(p.get("category"))
