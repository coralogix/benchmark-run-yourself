"""
Monthly VAT summary for finance (2018, DP). Finance gets this from ops' tax
service now (FEAT-7 will put VAT on receipts). Reads catalog.db for VAT bands
and an orders CSV export for amounts.

    python -m legacy.vat_report orders.csv
"""
import csv
import sys

from larder_common.dbx.catalog_db import catalog_db
from larder_common.money import VAT_RATES, money_fmt, vat_from_gross


def bands():
    return {r["sku"]: r["vat_band"] for r in catalog_db().query("SELECT sku, vat_band FROM products")}


def summarise(rows, sku_bands):
    out = {b: {"gross": 0.0, "vat": 0.0} for b in VAT_RATES}
    for r in rows:
        band = sku_bands.get(r["sku"], "standard")
        gross = float(r["unit_price"]) * int(r["qty"])
        out[band]["gross"] += gross
        out[band]["vat"] += vat_from_gross(gross, band)
    return out


def main(path):
    import services  # noqa: F401

    with open(path) as fh:
        rows = list(csv.DictReader(fh))
    for band, v in summarise(rows, bands()).items():
        print("%-8s gross %-12s vat %s" % (band, money_fmt(v["gross"]), money_fmt(v["vat"])))


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1])
