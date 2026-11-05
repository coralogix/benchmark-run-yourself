"""
Carts: create, add/remove items, coupons, holds, close.

A cart stores skus and quantities only. Prices are looked up by pricing
every time the cart is priced (i.e. on every read), so the cart always shows
today's price. The previous prices live in CartState.snapshots.
"""
from larder_common import settings, utils
from larder_common.errors import BadRequest, Conflict, NotFound
from larder_common.helpers.dates import iso_now
from larder_common.log import get_logger
from services.cart import pricing_client, state, store

log = get_logger("cart")

MAX_QTY_PER_LINE = settings.CART_MAX_QTY_PER_LINE
MAX_LINES = settings.CART_MAX_LINES


def new_cart_id():
    return utils.gen_id("c_")


def create_cart(user_id):
    if not user_id:
        raise BadRequest("user_id_required")
    # 2018: guest carts allowed, so we don't check the user exists. (the
    # storefront used to warn; there's no users table here since the split)
    st = state.CartState(new_cart_id(), str(user_id))
    store.insert_cart(st)
    state.put(st)
    return st


def get_cart(cart_id):
    st = state.get(cart_id) if cart_id else None
    if st is None:
        raise NotFound("cart_not_found")
    return st


def get_open_cart(cart_id):
    st = get_cart(cart_id)
    if not st.is_open:
        raise Conflict("cart_closed")
    return st


def add_item(st, sku, qty):
    qty = utils.parse_int(qty, default=None)
    if not sku or qty is None or qty < 1:
        raise BadRequest("invalid_item")
    if qty > MAX_QTY_PER_LINE:
        raise BadRequest("qty_too_large")
    pricing_client.check_sku(sku)
    if sku in st.lines:
        st.lines[sku] = min(MAX_QTY_PER_LINE, st.lines[sku] + qty)
    else:
        if len(st.lines) >= MAX_LINES:
            raise BadRequest("too_many_lines")
        st.lines[sku] = qty
        st.added_at[sku] = iso_now()
    st.touch()
    store.save_line(st.cart_id, sku, st.lines[sku], st.added_at.get(sku))
    store.save_cart(st)
    return st


def remove_item(st, sku):
    if sku in st.lines:
        del st.lines[sku]
        st.added_at.pop(sku, None)
        store.delete_line(st.cart_id, sku)
    st.touch()
    store.save_cart(st)
    return st


def apply_coupon(st, code):
    code = (code or "").strip()
    if not code:
        raise BadRequest("code_required")
    # pricing asks promotions whether the code is OK for this basket and
    # passes any refusal straight back to us (and we pass it to edge-api)
    breakdown = pricing_client.price(st.raw_lines(), code, validate_coupon=True)
    coupon = breakdown.get("coupon") or {}
    st.coupon_code = coupon.get("code") or code.upper()
    st.touch()
    store.save_cart(st)
    st.remember(breakdown)
    log.info("coupon %s applied to cart %s", st.coupon_code, st.cart_id)
    return st, breakdown


def remove_coupon(st):
    st.coupon_code = None
    st.touch()
    store.save_cart(st)
    return st


def set_hold(st, hold_id, slot_id=None, expires_at=None):
    if not hold_id:
        raise BadRequest("hold_id_required")
    st.hold_id = hold_id
    st.slot_id = slot_id
    st.hold_expires_at = expires_at
    st.touch()
    store.save_cart(st)
    return st


def close(st, order_ref=None):
    """Mark checked out. NB: stays in state._CARTS (see state.py docstring)."""
    st.status = state.CHECKED_OUT
    st.order_ref = order_ref
    st.touch()
    store.save_cart(st)
    return st


def price(st):
    breakdown = pricing_client.price(st.raw_lines(), st.coupon_code)
    st.remember(breakdown)
    return breakdown


def cart_subtotal(st, breakdown=None):
    """Subtotal in POUNDS, float, unrounded (storefront carts.cart_subtotal).

    Kept for the price-changed banner. Will not always agree with pricing's
    subtotal_pence / 100 (that's rounded; this isn't).
    """
    breakdown = breakdown or st.first_snapshot()
    if not breakdown:
        return 0.0
    total = 0.0
    for line in breakdown["lines"]:
        total += line["unit_price"] * line["qty"]
    return total


def prices_changed(st):
    """True if the first and latest snapshot disagree on any unit price (PM-610)."""
    if len(st.snapshots) < 2:
        return False
    first = {l["sku"]: l["unit_price"] for l in st.snapshots[0]["lines"]}
    last = {l["sku"]: l["unit_price"] for l in st.snapshots[-1]["lines"]}
    return any(sku in first and first[sku] != p for sku, p in last.items())


def serialise(st, breakdown=None):
    if breakdown is None:
        breakdown = price(st)
    items = []
    for line in breakdown["lines"]:
        items.append(
            {
                "sku": line["sku"],
                "name": line["name"],
                "qty": line["qty"],
                "unit_price": line["unit_price"],
                "unit_price_pence": int(round(line["unit_price"] * 100)),
                "line_total_pence": line["line_total_pence"],
            }
        )
    return {
        "cart_id": st.cart_id,
        "user_id": st.user_id,
        "status": st.status,
        "items": items,
        "subtotal_pence": breakdown["subtotal_pence"],
        "discount_pence": breakdown["discount_pence"],
        "delivery_fee_pence": breakdown["delivery_fee_pence"],
        "total_pence": breakdown["total_pence"],
        "total": breakdown["total"],
        "coupon": st.coupon_code,
        "hold_id": st.hold_id,
        "prices_changed": prices_changed(st),
    }


def lines_view(st):
    return {
        "cart_id": st.cart_id,
        "user_id": st.user_id,
        "status": st.status,
        "coupon": st.coupon_code,
        "hold_id": st.hold_id,
        "slot_id": st.slot_id,
        "lines": st.raw_lines(),
    }
