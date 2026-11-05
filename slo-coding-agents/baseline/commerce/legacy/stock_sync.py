"""
Nightly stock sync from the warehouse (2017-2019).

Before fulfilment had a real API we pulled stock levels into a local
`stock` table every night. The cron was removed when /internal/inventory
appeared; fetch_stock() is still used by /v1/stock.
"""
import requests

from larder_common import settings, discovery
from larder_common.log import legacy_log

STOCK_PATH = "/stock/%s"
LOW_STOCK = 5


def fetch_stock(sku):
    url = discovery.peer_url("inventory") + STOCK_PATH % sku
    resp = requests.get(url, timeout=3)
    if resp.status_code != 200:
        legacy_log("warn", "stock fetch %s -> %s", sku, resp.status_code)
        return {"sku": sku, "qty": None}
    data = resp.json()
    return {"sku": sku, "qty": data.get("qty"), "low": (data.get("qty") or 0) < LOW_STOCK}


def sync_all(skus):
    out = {}
    for sku in skus:
        try:
            out[sku] = fetch_stock(sku)
        except Exception as e:  # noqa
            legacy_log("err", "stock sync failed for %s: %s", sku, e)
    return out


if __name__ == "__main__":  # pragma: no cover
    import sys

    print(sync_all(sys.argv[1:]))
