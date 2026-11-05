"""
cart.db write-through.

Every mutation in carts.py is written here as well as to the in-memory
state. Reads only come here on a cache miss (state.get), e.g. after a
restart. The db is the "source of truth" (TH 2023), except nobody ever reads
from it while the process is up.
"""
import json

from larder_common import dbx
from larder_common.helpers.dates import iso_now
from larder_common.log import get_logger

log = get_logger("cart.store")


def _db():
    return dbx.db("cart")


def insert_cart(st):
    _db().execute(
        "INSERT INTO carts (cart_id, user_id, coupon_code, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (st.cart_id, st.user_id, st.coupon_code, st.status, st.created_at, st.updated_at),
    )


def save_cart(st):
    _db().execute(
        "UPDATE carts SET user_id = ?, coupon_code = ?, status = ?, hold_id = ?, slot_id = ?, hold_expires_at = ?,"
        " updated_at = ? WHERE cart_id = ?",
        (st.user_id, st.coupon_code, st.status, st.hold_id, st.slot_id, st.hold_expires_at, st.updated_at,
         st.cart_id),
    )


def save_line(cart_id, sku, qty, added_at):
    d = _db()
    with d.transaction():
        row = d.query_one("SELECT id FROM cart_items WHERE cart_id = ? AND sku = ?", (cart_id, sku))
        if row is None:
            d.execute(
                "INSERT INTO cart_items (cart_id, sku, qty, added_at) VALUES (?, ?, ?, ?)",
                (cart_id, sku, qty, added_at),
            )
        else:
            d.execute("UPDATE cart_items SET qty = ? WHERE id = ?", (qty, row["id"]))


def delete_line(cart_id, sku):
    _db().execute("DELETE FROM cart_items WHERE cart_id = ? AND sku = ?", (cart_id, sku))


def load(cart_id):
    """-> (cart row, [item rows]) or (None, [])"""
    d = _db()
    row = d.query_one("SELECT * FROM carts WHERE cart_id = ?", (cart_id,))
    if row is None:
        return None, []
    items = d.query("SELECT sku, qty, added_at FROM cart_items WHERE cart_id = ? ORDER BY id", (cart_id,))
    return row, items


def count_carts(status=None):
    if status:
        return _db().scalar("SELECT COUNT(*) FROM carts WHERE status = ?", (status,), 0)
    return _db().scalar("SELECT COUNT(*) FROM carts", default=0)


def save_snapshot(cart_id, breakdown):
    """PM-610 follow-up (SB 2024-05): write the price snapshot so the banner
    survives a restart. Every snapshot, oldest first, same as memory."""
    _db().execute(
        "INSERT INTO cart_snapshots (cart_id, total_pence, payload, created_at) VALUES (?, ?, ?, ?)",
        (cart_id, breakdown.get("total_pence"), json.dumps(breakdown), iso_now()),
    )


def load_everything():
    """-> (cart rows, {cart_id: [item rows]}, {cart_id: [snapshot dicts]}). Used by state.warm()."""
    d = _db()
    carts = d.query("SELECT * FROM carts ORDER BY created_at")
    items, snaps = {}, {}
    for it in d.query("SELECT cart_id, sku, qty, added_at FROM cart_items ORDER BY id"):
        items.setdefault(it["cart_id"], []).append(it)
    for sn in d.query("SELECT cart_id, payload FROM cart_snapshots ORDER BY id"):
        try:
            snaps.setdefault(sn["cart_id"], []).append(json.loads(sn["payload"]))
        except ValueError:
            pass
    return carts, items, snaps
