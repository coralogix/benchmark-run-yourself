"""
in_stock for product lists: ONE bulk availability call to ops' inventory per
page (POST /internal/inventory/availability). Never one call per product -
see INC-517 (2022), when the per-product version took fulfilment down.

in_stock is true/false, or None ("unknown") when inventory is unreachable.
The page still renders without it.
"""
from larder_common.log import get_logger
from services.edge_api import upstreams

log = get_logger("edge.stock")

# FEAT-11 will want "only N left". Threshold agreed with merch, not used yet.
LOW_STOCK_THRESHOLD = 5


def stock_lookup(skus):
    """sku -> bool/None."""
    skus = [s for s in skus if s]
    if not skus:
        return {}
    try:
        avail = upstreams.availability(skus)
    except Exception as e:
        log.warning("availability lookup failed for %d skus: %s", len(skus), e)
        return {s: None for s in skus}
    out = {}
    for s in skus:
        a = avail.get(s)
        out[s] = None if a is None else a > 0
    return out


def attach_stock(*item_lists):
    """Set in_stock on every item of every list, with one inventory call total."""
    skus = []
    for items in item_lists:
        for i in items or []:
            if i.get("sku") not in skus:
                skus.append(i.get("sku"))
    stock = stock_lookup(skus)
    for items in item_lists:
        for i in items or []:
            i["in_stock"] = stock.get(i.get("sku"))
    return item_lists[0] if len(item_lists) == 1 else item_lists
