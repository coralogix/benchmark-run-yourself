"""
Marketing export of live coupons (KP 2020-09).

Finance pulled this weekly as a spreadsheet to reconcile discount spend.
Since 2023 it's served at GET /internal/coupons/_export (routes.py), which
nobody calls. Note expiry_display() parses expiry, so this fails for the
whole export as soon as one coupon has a date it can't parse.
"""
import csv
import io

from services.promotions import coupons

COLUMNS = ("code", "description", "percent_off", "min_spend", "expires", "free_delivery")


def rows():
    out = []
    for c in coupons.list_visible():
        out.append(
            {
                "code": c.code,
                "description": coupons.describe(c),
                "percent_off": c.percent_off,
                # min_spend in pounds, two decimals, the way finance likes it
                "min_spend": "%.2f" % (c.min_spend or 0),
                "expires": coupons.expiry_display(c),
                "free_delivery": "Y" if c.free_delivery else "N",
            }
        )
    return out


def as_csv():
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows():
        w.writerow(r)
    return buf.getvalue()
