"""
Google Shopping / price comparison feed export (2017, DP).

Used to be run by cron: python -m legacy.feeds > /var/www/feeds/products.json
The feed URL was turned off in 2021 but marketing want it "kept in case".
Reads catalog.db directly (it always did; the split just moved the file).
"""
import sys

import simplejson
import six

from larder_common.dbx.catalog_db import catalog_db
from larder_common.money import money_fmt

FEED_VERSION = 3
BRAND = "Larder"
# VAT band -> feed tax code
TAX_CODES = {"zero": "Z", "reduced": "R", "standard": "S"}


def feed_item(p):
    return {
        "id": p["sku"],
        "title": p["name"],
        "brand": BRAND,
        "price": money_fmt(p["price"], symbol=False) + " GBP",
        "availability": "in stock",
        "tax_code": TAX_CODES.get(p["vat_band"], "S"),
        "product_type": six.text_type(p["category"]).replace("_", " ").title(),
        "link": "https://www.larder.example/p/%s" % p["sku"].lower(),
    }


def build_feed():
    rows = catalog_db().query("SELECT * FROM products WHERE active = 1 ORDER BY id")
    items = [feed_item(r) for r in rows]
    return {"version": FEED_VERSION, "count": len(items), "items": items}


def main(out=sys.stdout):
    import services  # noqa: F401  (sys.path + patches)

    simplejson.dump(build_feed(), out, indent=1)


if __name__ == "__main__":  # pragma: no cover
    main()
