"""
GET /api/orders/<ref>: ops' order, reshaped into the 2019 storefront order
format the apps already understand.

ops only stores {sku, qty, unit_price_pence} per item; the order page wants
names, so we look each one up in catalog (one call per item - orders are
small, "usually"). If catalog can't find it (delisted), name is null.
"""
from larder_common.errors import NotFound, UpstreamError
from larder_common.http import client
from larder_common.log import get_logger
from services.edge_api import upstreams
from services.edge_api.shaping import pence_to_pounds

log = get_logger("edge.orders")


def _name_for(sku):
    try:
        res = client(upstreams.CATALOG).get("/internal/products/%s" % sku)
    except Exception as e:  # noqa
        log.info("name lookup for %s failed: %s", sku, e)
        return None
    if not res.ok:
        return None
    return res.body.get("name")


def get_order(order_ref):
    res = upstreams.order(order_ref)
    if res.status == 404:
        raise NotFound("order_not_found")
    if not res.ok:
        raise UpstreamError("orders_unavailable")
    o = res.body
    items = []
    for it in o.get("items") or []:
        upp = it.get("unit_price_pence")
        items.append(
            {
                "sku": it.get("sku"),
                "name": _name_for(it.get("sku")),
                "qty": it.get("qty"),
                "unit_price": pence_to_pounds(upp),
                "unit_price_pence": upp,
            }
        )
    total_pence = o.get("total_pence")
    return {
        "order_ref": o.get("order_ref", order_ref),
        "user_id": o.get("user_id"),
        "status": o.get("status"),
        "items": items,
        "subtotal_pence": o.get("subtotal_pence"),
        "discount_pence": o.get("discount_pence"),
        "delivery_fee_pence": o.get("delivery_fee_pence"),
        "total_pence": total_pence,
        "total": pence_to_pounds(total_pence),
        "coupon": (o.get("coupon") or {}).get("code") if isinstance(o.get("coupon"), dict) else o.get("coupon"),
        "hold_id": o.get("hold_id"),
        "charge_id": o.get("charge_id"),
        "created_at": o.get("created_at"),
    }
