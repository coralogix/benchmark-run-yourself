"""
Guest -> customer cart merge (RB 2018-05, from legacy/basket.merge_baskets).

When a guest logged in, the old storefront merged their guest basket into
their last open cart. The PHP login page that triggered it is gone (accounts
are read-only now), so nothing calls merge_into(). The route was removed in
the split; the function still works on CartStates.
"""
from larder_common import settings
from larder_common.log import get_logger
from services.cart import state, store

log = get_logger("cart.merge")


def merge_into(target, source):
    """Add source's lines to target (qty capped per line), close source."""
    for sku, qty in source.lines.items():
        new_qty = min(settings.CART_MAX_QTY_PER_LINE, target.lines.get(sku, 0) + qty)
        if sku not in target.lines:
            target.added_at[sku] = source.added_at.get(sku)
        target.lines[sku] = new_qty
        store.save_line(target.cart_id, sku, new_qty, target.added_at.get(sku))
    target.coupon_code = target.coupon_code or source.coupon_code
    target.touch()
    store.save_cart(target)
    source.status = "merged"
    source.touch()
    store.save_cart(source)
    log.info("merged cart %s into %s", source.cart_id, target.cart_id)
    return target


def latest_open_cart_for(user_id):
    """Linear scan over every cart in memory (see state.py: that's all of them)."""
    best = None
    for st in list(state._CARTS.values()):
        if st.user_id == user_id and st.is_open:
            if best is None or st.updated_at > best.updated_at:
                best = st
    return best
