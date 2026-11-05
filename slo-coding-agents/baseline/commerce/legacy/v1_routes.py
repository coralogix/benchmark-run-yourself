"""
/v1 API used by the 2016 mobile app (iOS 1.x / Android 1.x).

The apps were retired in 2020 but some partner integrations still call
/v1/products and /v1/price-check, so this blueprint stays registered (edge-api
mounts it at /v1, see services/edge_api/app.py). Prices here are pounds
(float) only.

Since the 2023 split these call the commerce services instead of the
database, except /v1/basket which never worked after 2018 anyway.
"""
import simplejson
from flask import Blueprint, Response, request

from larder_common.errors import ApiError
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("legacy.v1")

bp = Blueprint("v1", __name__)

# shown to v1 clients in the X-Deprecated header
SUNSET = "2020-12-31"


def _json(payload, status=200):
    # v1 clients expect Decimal-free, 2dp floats and a trailing newline
    body = simplejson.dumps(payload, use_decimal=True) + "\n"
    resp = Response(body, status=status, mimetype="application/json")
    resp.headers["X-Deprecated"] = "sunset=%s" % SUNSET
    return resp


@bp.route("/products")
def v1_products():
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    # catalog pages, v1 used offsets. Only works when offset is a multiple of limit.
    page = offset // limit + 1 if limit else 1
    res = client("catalog").get("/internal/products", params={"page": page, "per_page": min(limit, 100)})
    items = res.body.get("items", []) if res.ok else []
    return _json(
        {
            "products": [
                {"id": p.get("id"), "sku": p["sku"], "title": p["name"], "price": p["price"], "cat": p["category"]}
                for p in items
            ],
            "count": len(items),
        }
    )


@bp.route("/products/<int:product_id>")
def v1_product(product_id):
    res = client("catalog").get("/internal/products/by-id/%d" % product_id)
    if not res.ok:
        return _json({"error": "not found"}, 404)
    p = res.body
    return _json({"id": p.get("id"), "sku": p["sku"], "title": p["name"], "price": p["price"],
                  "desc": p.get("description")})


@bp.route("/price-check", methods=["POST"])
def v1_price_check():
    """[{"sku": ..., "qty": ...}] -> {"total": pounds}. Always uses the v1 engine."""
    data = request.get_json(silent=True) or {}
    res = client("pricing").post(
        "/internal/price-check", json={"lines": data.get("lines", []), "percent_off": data.get("percent_off", 0)}
    )
    if not res.ok:
        return _json({"error": "price check failed"}, res.status)
    return _json({"total": res.body.get("total"), "currency": "GBP"})


@bp.route("/basket/<basket_id>")
def v1_basket(basket_id):
    # baskets were replaced by carts in 2018 (now the cart service)
    from legacy import basket

    b = basket.load_basket(basket_id)
    return _json(b.to_v1())


@bp.route("/stock/<sku>")
def v1_stock(sku):
    # FIXME(JW 2019-03-07): fulfilment removed /stock in their v3, this 404s
    from legacy.stock_sync import fetch_stock

    return _json(fetch_stock(sku))


@bp.route("/coupons/<code>")
def v1_coupon(code):
    from legacy.promo_dates import parse_promo_date

    res = client("promotions").get("/internal/coupons/%s" % code.strip().upper())
    if not res.ok:
        raise ApiError("invalid_coupon")
    c = res.body
    raw = c.get("expires")
    # v1 only ever had spreadsheet dates; ISO ones are passed through as-is
    if raw and "-" in str(raw)[:5]:
        exp = raw
    else:
        d = parse_promo_date(raw)
        exp = d.isoformat() if d else None
    return _json({"code": c["code"], "percent": c.get("percent_off"), "expires": exp})


@bp.route("/me/<user_id>")
def v1_me(user_id):
    from larder_common.helpers.strings import mask_email

    res = client("accounts").get("/internal/users/%s" % user_id)
    if not res.ok:
        return _json({"error": "not found"}, 404)
    u = res.body
    return _json({"id": u["user_id"], "name": u.get("name"), "email": mask_email(u.get("email"))})
