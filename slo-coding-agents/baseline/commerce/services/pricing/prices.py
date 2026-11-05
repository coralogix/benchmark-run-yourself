"""
Unit prices.

Prices come straight out of catalog.db (shared database, see
larder_common/dbx/paths.py) - one query per sku. If a sku isn't in our view
of the file we ask catalog over HTTP instead ("new products since the last
sync" - TH 2023-03. There is no sync; it's the same file. The fallback only
fires for unknown skus, i.e. it costs an extra HTTP call per bad sku).

Prices are REAL pounds (TD-1). Don't convert to pence here; the engines do
their own conversion, and they do it differently on purpose (see v1).
"""
import time

from larder_common.dbx.catalog_db import catalog_db
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("pricing.prices")

# AS 2023-06: price lookups that take longer than this get logged
SLOW_LOOKUP = 0.2

_STATS = {"db": 0, "http": 0, "missing": 0}


def _from_db(sku):
    row = catalog_db().query_one(
        "SELECT sku, name, price, vat_band, active FROM products WHERE sku = ?", (sku,)
    )
    if row is None:
        return None
    return {
        "sku": row["sku"],
        "name": row["name"],
        "unit_price": row["price"],
        "vat_band": row["vat_band"],
        "active": bool(row["active"]),
    }


def _from_catalog_http(sku):
    try:
        res = client("catalog").get("/internal/products/%s" % sku)
    except Exception as e:  # noqa
        log.warning("catalog lookup for %s failed: %s", sku, e)
        return None
    if not res.ok:
        return None
    p = res.body
    return {
        "sku": p["sku"],
        "name": p["name"],
        "unit_price": p["price"],
        "vat_band": p.get("vat_band", "standard"),
        "active": True,
    }


def lookup(sku):
    """sku -> {sku, name, unit_price (pounds), vat_band, active} or None."""
    if not sku:
        return None
    t0 = time.time()
    try:
        p = _from_db(sku)
    except Exception as e:
        # catalog.db not there yet (catalog still starting) - ask over http
        log.warning("catalog.db lookup failed for %s: %s", sku, e)
        p = None
    if p is not None:
        _STATS["db"] += 1
    else:
        p = _from_catalog_http(sku)
        _STATS["http" if p else "missing"] += 1
    took = time.time() - t0
    if took > SLOW_LOOKUP:
        log.info("price lookup for %s took %.3fs", sku, took)
    return p


def unit_price(sku):
    p = lookup(sku)
    if p is None or not p["active"]:
        return None
    return p["unit_price"]


def build_lines(raw_lines):
    """[{sku, qty}] -> engine lines [{sku, name, unit_price, qty, vat_band}].

    Unknown and inactive skus are dropped silently (the cart still shows
    them... no it doesn't, cart only shows what we return).
    """
    lines = []
    for raw in raw_lines or []:
        sku = raw.get("sku")
        try:
            qty = int(raw.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty < 1:
            continue
        p = lookup(sku)
        if p is None or not p["active"]:
            continue
        lines.append(
            {
                "sku": p["sku"],
                "name": p["name"],
                "qty": qty,
                "unit_price": p["unit_price"],
                "vat_band": p["vat_band"],
            }
        )
    return lines


def stats():
    return dict(_STATS)
