"""
Merch admin endpoints (2019, for the retired merch tool).

The merch tool called these to change prices and deactivate products. It was
switched off in 2022 but the endpoints are still mounted. Note that changing a
price here does NOT invalidate search's cache or index (search reads
catalog.db on its own schedule), so search keeps showing the old price until
its next safety-valve flush. Pricing reads catalog.db live, so carts get the
new price immediately. (PM-1020: "price on search page differs from basket")
"""
from flask import Blueprint

from larder_common.errors import BadRequest, NotFound
from larder_common.internal_auth import internal_only
from larder_common.log import get_logger
from larder_common.web import body, ok
from services.catalog import products
from services.catalog.models import Product

log = get_logger("catalog.admin")

bp = Blueprint("catalog_admin", __name__)


@bp.route("/internal/admin/products/<sku>/price", methods=["PUT"])
@internal_only
def set_price(sku):
    data = body()
    p = Product.by_sku(sku)
    if p is None:
        raise NotFound("not_found")
    pence = data.get("price_pence")
    if pence is None or int(pence) <= 0:
        raise BadRequest("price_pence_required")
    with products.CATALOG_LOCK:
        p.price = int(pence) / 100.0
        p.save()
    log.info("price for %s set to %s by %s", sku, p.price, data.get("who", "?"))
    return ok(products.serialise(p, detail=True))


@bp.route("/internal/admin/products/<sku>/active", methods=["PUT"])
@internal_only
def set_active(sku):
    data = body()
    p = Product.by_sku(sku)
    if p is None:
        raise NotFound("not_found")
    with products.CATALOG_LOCK:
        p.active = 1 if data.get("active", True) else 0
        p.save()
    return ok({"sku": sku, "active": bool(p.active)})
