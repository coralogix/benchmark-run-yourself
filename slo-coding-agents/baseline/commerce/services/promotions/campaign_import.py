"""
Campaign API importer (AS 2021-02, "PROMO-40").

The marketing campaign tool used to POST new coupon batches here as CSV.
The tool was replaced by "just ask the web team to add a row" in 2022, so
the route that called this was removed; the importer is still here and is
the only thing that knows about the third expiry format (ISO timestamps with
a time component, e.g. SPRING15).

    python -m services.promotions.campaign_import batch.csv

CSV columns: code,percent_off,min_spend,expires,free_delivery
min_spend is POUNDS in the CSV (marketing's choice), unlike seed/coupons.json.
"""
import csv
import sys

from larder_common import dbx
from larder_common.log import get_logger

log = get_logger("promotions.import")

# AS 2021-02: the campaign tool caps discounts at 50%; anything more is a typo
MAX_PERCENT = 50
REQUIRED = ("code", "percent_off", "expires")


class ImportError_(Exception):
    pass


def _row_to_coupon(row):
    for k in REQUIRED:
        if not row.get(k):
            raise ImportError_("missing %s in %r" % (k, row))
    pct = float(row["percent_off"])
    if pct > MAX_PERCENT:
        raise ImportError_("percent_off %s > %s for %s" % (pct, MAX_PERCENT, row["code"]))
    return {
        "code": row["code"].strip().upper(),
        "percent_off": pct,
        "min_spend": float(row.get("min_spend") or 0),
        # stored as-is: whatever format the tool exported. parse_expiry copes
        # with the three we know about (see coupons.py)
        "expires": row["expires"].strip(),
        "free_delivery": 1 if (row.get("free_delivery") or "").lower() in ("1", "y", "yes", "true") else 0,
    }


def import_rows(rows, replace=False):
    d = dbx.db("promotions")
    n = 0
    with d.transaction():
        for row in rows:
            c = _row_to_coupon(row)
            existing = d.query_one("SELECT code FROM coupons WHERE code = ?", (c["code"],))
            if existing and not replace:
                log.info("skipping existing coupon %s", c["code"])
                continue
            d.execute("DELETE FROM coupons WHERE code = ?", (c["code"],))
            d.execute(
                "INSERT INTO coupons (code, percent_off, min_spend, expires, free_delivery, active)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                (c["code"], c["percent_off"], c["min_spend"], c["expires"], c["free_delivery"]),
            )
            n += 1
    return n


def import_csv(path, replace=False):
    with open(path, newline="") as fh:
        return import_rows(csv.DictReader(fh), replace=replace)


def main(argv=None):  # pragma: no cover
    import services  # noqa: F401

    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m services.promotions.campaign_import batch.csv [--replace]")
        return 2
    from services.promotions.app import create_app

    create_app()
    print(import_csv(argv[0], replace="--replace" in argv))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
