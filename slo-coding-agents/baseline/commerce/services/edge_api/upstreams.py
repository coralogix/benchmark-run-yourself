"""
Everything edge-api calls, in one place.

Each function returns the upstream body (dict) or raises:
    PassThrough      - the upstream answered 4xx/5xx; the customer gets the same
    UpstreamError    - we couldn't get an answer at all (after the shared
                       client's retries), 502 {"error":"<target>_unavailable"}
                       or 504 {"error":"upstream_timeout"} if the gateway timed out

All calls use the shared client (larder_common.http): no timeout, retried on
transport errors. That includes the POSTs (cart writes, checkout).
"""
from larder_common import discovery
from larder_common.errors import PassThrough, UpstreamError
from larder_common.http import GatewayError, client
from larder_common.log import get_logger

log = get_logger("edge.upstreams")

CATALOG = "catalog"
SEARCH = "search"
CART = "cart"
CHECKOUT = "checkout"
ACCOUNTS = "accounts"
RECOMMENDATIONS = "recommendations"
INVENTORY = "inventory"
SLOTS = "slots"
ORDERS = "orders"

ALL = (CATALOG, SEARCH, CART, CHECKOUT, ACCOUNTS, RECOMMENDATIONS, INVENTORY, SLOTS, ORDERS)


def describe():
    return {t: discovery.peer_url(t) for t in ALL}


def call(target, method, path, params=None, json=None, expect=(200, 201), raise_errors=True):
    """Make the call; return the ServiceResponse, mapping failures to errors."""
    try:
        res = client(target).request(method, path, params=params, json=json)
    except GatewayError as e:
        log.error("%s %s%s: gateway %s after retries", method, target, path, e.status)
        if e.status == 504:
            raise UpstreamError("upstream_timeout", status=504)
        raise UpstreamError("%s_unavailable" % target)
    except Exception as e:
        log.error("%s %s%s failed after retries: %s", method, target, path, e)
        raise UpstreamError("%s_unavailable" % target)
    if raise_errors and res.status not in expect:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res


def get(target, path, params=None, **kw):
    return call(target, "GET", path, params=params, **kw).body


def post(target, path, json=None, **kw):
    return call(target, "POST", path, json=json, **kw).body


# --- catalog ----------------------------------------------------------------


def list_products(page, per_page, category=None):
    params = {"page": page, "per_page": per_page}
    if category:
        params["category"] = category
    return get(CATALOG, "/internal/products", params=params)


def product(sku):
    return get(CATALOG, "/internal/products/%s" % sku)


def categories():
    return get(CATALOG, "/internal/categories").get("categories", [])


# --- search / recommendations ---------------------------------------------------


def search(q, uid=None):
    params = {"q": q}
    if uid:
        params["uid"] = uid
    return get(SEARCH, "/internal/search", params=params)


def also_bought(sku):
    """[P] or None. NB: no timeout (shared client), so a slow recommendations
    service makes the product page slow. Failures just mean "no field"."""
    try:
        res = client(RECOMMENDATIONS).get("/internal/recommendations/%s" % sku)
    except Exception as e:  # noqa
        log.info("recommendations failed for %s: %s", sku, e)
        return None
    if not res.ok:
        return None
    return res.body.get("also_bought")


# --- inventory (ops) ----------------------------------------------------------


def availability(skus):
    """{sku: available_int}. Raises on any failure (callers degrade to None)."""
    if not skus:
        return {}
    res = client(INVENTORY).post("/internal/inventory/availability", json={"skus": list(skus)})
    if not res.ok:
        raise UpstreamError("inventory_unavailable")
    out = {}
    for sku, info in (res.body or {}).items():
        if isinstance(info, dict):
            out[sku] = info.get("available")
        else:
            out[sku] = info
    return out


# --- cart / checkout / accounts ---------------------------------------------------


def create_cart(user_id):
    return call(CART, "POST", "/internal/carts", json={"user_id": user_id}, expect=(201,))


def cart(cart_id):
    return get(CART, "/internal/carts/%s" % cart_id)


def cart_lines(cart_id):
    return get(CART, "/internal/carts/%s/lines" % cart_id)


def add_item(cart_id, sku, qty):
    return post(CART, "/internal/carts/%s/items" % cart_id, json={"sku": sku, "qty": qty})


def remove_item(cart_id, sku):
    return call(CART, "DELETE", "/internal/carts/%s/items/%s" % (cart_id, sku)).body


def apply_coupon(cart_id, code):
    return post(CART, "/internal/carts/%s/coupon" % cart_id, json={"code": code})


def remove_coupon(cart_id):
    return call(CART, "DELETE", "/internal/carts/%s/coupon" % cart_id).body


def set_hold(cart_id, hold_id, slot_id, expires_at):
    return call(
        CART,
        "PUT",
        "/internal/carts/%s/hold" % cart_id,
        json={"hold_id": hold_id, "slot_id": slot_id, "expires_at": expires_at},
    ).body


def checkout(cart_id, hold_id, payment_token):
    # whatever checkout answers (201 / 4xx / 5xx), the customer gets
    return call(
        CHECKOUT,
        "POST",
        "/internal/checkout",
        json={"cart_id": cart_id, "hold_id": hold_id, "payment_token": payment_token},
        raise_errors=False,
    )


def account(user_id):
    return get(ACCOUNTS, "/internal/accounts/%s" % user_id)


# --- slots / orders (ops) ---------------------------------------------------------


def zone_lookup(postcode):
    """zone for a postcode. Raises UpstreamError if slots can't answer.

    delivery.resolve_zone only asks about postcodes in our zone table (PM-1712),
    and slots has every one of those, so there's no 404 case any more.
    """
    res = call(SLOTS, "GET", "/internal/zones/lookup", params={"postcode": postcode}, raise_errors=False)
    if res.status >= 500:
        raise UpstreamError("slots_unavailable")
    return res.body["zone"]


def slots(zone):
    return get(SLOTS, "/internal/slots", params={"zone": zone}).get("slots", [])


def hold_slot(slot_id, cart_ref):
    return call(SLOTS, "POST", "/internal/slot-holds", json={"slot_id": slot_id, "cart_ref": cart_ref},
                raise_errors=False)


def order(order_ref):
    return call(ORDERS, "GET", "/internal/orders/%s" % order_ref, raise_errors=False)
