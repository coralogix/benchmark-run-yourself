"""
Price a basket with both engines and show the difference (KP 2020-02, for
the OPS-1182 finance sign-off that never happened).

    uv run python -m scripts.price_diff SKU-0001:2 SKU-0042:1 --coupon FAMILY12

Reads prices from catalog.db (needs LARDER_DATA_DIR pointing at a data dir
next to catalog's, e.g. var/pricing). Coupons are looked up in promotions.db
next to it, without any expiry checks.
"""
import argparse
import os
import sqlite3
import sys

import services  # noqa: F401
from larder_common.dbx.catalog_db import catalog_db
from legacy import pricing_v1
from services.pricing import engine_v2


def _lines(specs):
    out = []
    for spec in specs:
        sku, _, qty = spec.partition(":")
        row = catalog_db().query_one("SELECT sku, name, price FROM products WHERE sku = ?", (sku,))
        if row is None:
            print("unknown sku", sku)
            continue
        out.append({"sku": row["sku"], "name": row["name"], "unit_price": row["price"], "qty": int(qty or 1)})
    return out


def _coupon(code):
    if not code:
        return None
    from larder_common import settings

    path = os.path.join(os.path.abspath(settings.DATA_DIR), "..", "promotions", "promotions.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM coupons WHERE code = ?", (code.upper(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("lines", nargs="+")
    ap.add_argument("--coupon")
    args = ap.parse_args(argv)
    lines = _lines(args.lines)
    coupon = _coupon(args.coupon)
    a = pricing_v1.price_cart(lines, coupon)
    b = engine_v2.price_cart(lines, coupon)
    for key in ("subtotal_pence", "discount_pence", "delivery_fee_pence", "total_pence"):
        flag = "" if a[key] == b[key] else "   <-- differs"
        print("%-20s v1=%-8s v2=%-8s%s" % (key, a[key], b[key], flag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
