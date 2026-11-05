"""
Request validation helpers (SB 2021, "so every view stops doing it by hand").

Only edge-api's old views used these; after the split cart has its own
checks (services/cart/carts.py) with different limits (CART_MAX_QTY_PER_LINE
is 99 there; MAX_QTY here is 50, from the 2019 mobile app). Both are "right".
"""
import re

from larder_common.errors import BadRequest

SKU_RE = re.compile(r"^SKU-\d{4,6}$")
USER_RE = re.compile(r"^u-\d{4}$")
CART_RE = re.compile(r"^c_[0-9a-f]{12}$")
MAX_QTY = 50


def sku(value):
    v = (value or "").strip().upper()
    if not SKU_RE.match(v):
        raise BadRequest("invalid_sku")
    return v


def qty(value, max_qty=MAX_QTY):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise BadRequest("invalid_qty")
    if n < 1 or n > max_qty:
        raise BadRequest("invalid_qty")
    return n


def user_id(value):
    v = (value or "").strip()
    # guest ids ("g-...") were allowed in 2018 and never disallowed
    if not (USER_RE.match(v) or v.startswith("g-")):
        raise BadRequest("invalid_user_id")
    return v


def cart_id(value):
    v = (value or "").strip()
    if not CART_RE.match(v):
        raise BadRequest("invalid_cart_id")
    return v


def required(data, *keys):
    missing = [k for k in keys if data.get(k) in (None, "")]
    if missing:
        raise BadRequest("%s_required" % missing[0])
    return [data.get(k) for k in keys]
