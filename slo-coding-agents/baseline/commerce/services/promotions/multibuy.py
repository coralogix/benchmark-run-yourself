"""
Multi-buy offers ("3 for 2"). FEAT-1.

TODO(KP 2020-11-19): products tagged "3for2" in the catalog are advertised on
the site but no discount is ever applied - the offer was "handled at the
till" in the 2016 stores. Online customers pay full price. This module was
started for the 2021 Christmas campaign and never wired in; nothing imports
it. Pricing (v1 and v2), tax (ops) and orders validation would all need to
agree on it before it can be turned on.
"""

MULTIBUY_TAG = "3for2"
BUY_N = 3
PAY_FOR = 2


def multibuy_discount_pence(lines, tagged_skus):
    """lines: [{sku, qty, unit_price_pence}] -> pence off. Cheapest-free per group of 3 (per sku)."""
    off = 0
    for line in lines:
        if line["sku"] not in tagged_skus:
            continue
        free = (int(line["qty"]) // BUY_N) * (BUY_N - PAY_FOR)
        off += free * int(line["unit_price_pence"])
    return off


def tagged_skus_from_catalog():
    # TODO(KP 2020-11-19): read product_tags from catalog.db like search does?
    # or ask catalog. Undecided. Returns nothing so nobody gets a discount.
    return set()
