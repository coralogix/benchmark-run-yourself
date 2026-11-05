"""
In-memory cart state.

_CARTS holds every cart this process has seen, keyed by cart_id. It's the
"hot" copy; cart.db is written through on every change (store.py) and only
read on a miss.

History: the storefront kept carts only in sqlite and re-read them on every
request. During the 2023 split the cart service got this dict "as a cache"
to avoid the reads (TH 2023-03). Eviction was going to come with the Redis
move (PM-1187), which was cancelled. The legacy.jobs.abandoned_carts cron
used to delete old carts from the db; it never touched memory, and nobody
runs it now anyway.

Each CartState also keeps `snapshots`: every priced breakdown we've ever
computed for it, oldest first, for the "prices changed since you added this"
banner (PM-610). The banner compares the first and last snapshot; the ones
in between are kept "for debugging".
"""
import collections
import threading

from larder_common.helpers.dates import iso_now
from larder_common.log import get_logger

log = get_logger("cart.state")

# cart_id -> CartState. Module global, shared by all worker threads.
_CARTS = {}
# guards inserts into _CARTS. Mutations of a CartState are not locked (a
# cart is only used by one customer at a time... in theory - JW 2023)
_CARTS_LOCK = threading.Lock()

OPEN = "open"
CHECKED_OUT = "checked_out"


class CartState(object):
    __slots__ = (
        "cart_id",
        "user_id",
        "status",
        "coupon_code",
        "hold_id",
        "slot_id",
        "hold_expires_at",
        "lines",
        "added_at",
        "created_at",
        "updated_at",
        "snapshots",
        "order_ref",
    )

    def __init__(self, cart_id, user_id, status=OPEN, coupon_code=None, created_at=None, updated_at=None):
        self.cart_id = cart_id
        self.user_id = user_id
        self.status = status
        self.coupon_code = coupon_code
        self.hold_id = None
        self.slot_id = None
        self.hold_expires_at = None
        self.lines = collections.OrderedDict()  # sku -> qty
        self.added_at = {}  # sku -> iso
        self.created_at = created_at or iso_now()
        self.updated_at = updated_at or self.created_at
        self.snapshots = []
        self.order_ref = None

    def touch(self):
        self.updated_at = iso_now()

    @property
    def is_open(self):
        return self.status == OPEN

    def raw_lines(self):
        return [{"sku": sku, "qty": qty} for sku, qty in self.lines.items()]

    def remember(self, breakdown):
        # PM-610: price-changed banner. keep them all, see module docstring.
        # Written through to cart_snapshots since SB 2024-05 so it survives a
        # restart (see warm()).
        self.snapshots.append(breakdown)
        from services.cart import store

        store.save_snapshot(self.cart_id, breakdown)

    def first_snapshot(self):
        return self.snapshots[0] if self.snapshots else None

    def __repr__(self):
        return "<CartState %s %s lines=%d snaps=%d>" % (self.cart_id, self.status, len(self.lines),
                                                         len(self.snapshots))


def put(st):
    with _CARTS_LOCK:
        _CARTS[st.cart_id] = st
    return st


def get(cart_id):
    """CartState or None. Falls back to cart.db (and caches what it finds)."""
    st = _CARTS.get(cart_id)
    if st is not None:
        return st
    from services.cart import store

    row, items = store.load(cart_id)
    if row is None:
        return None
    st = CartState(row["cart_id"], row["user_id"], row["status"] or OPEN, row["coupon_code"], row["created_at"],
                   row["updated_at"])
    st.hold_id = row["hold_id"]
    st.slot_id = row["slot_id"]
    st.hold_expires_at = row["hold_expires_at"]
    for it in items:
        st.lines[it["sku"]] = it["qty"]
        st.added_at[it["sku"]] = it["added_at"]
    log.info("cart %s loaded from cart.db", cart_id)
    return put(st)


def active_count():
    """commerce_active_carts. "Active" = in memory, which is every cart since start."""
    return len(_CARTS)


def snapshot_count():
    return sum(len(st.snapshots) for st in list(_CARTS.values()))


def stats():
    by_status = {}
    for st in list(_CARTS.values()):
        by_status[st.status] = by_status.get(st.status, 0) + 1
    return {"carts": len(_CARTS), "by_status": by_status, "snapshots": snapshot_count()}


def warm():
    """PM-1307 (TH 2024-06): the first requests after a deploy were slow while
    carts trickled in from cart.db one miss at a time (and the price-changed
    banner was empty for everyone). Load every cart, its lines and its price
    snapshots at boot instead. Returns the number of carts loaded."""
    from services.cart import store

    carts, items, snaps = store.load_everything()
    for row in carts:
        st = CartState(row["cart_id"], row["user_id"], row["status"] or OPEN, row["coupon_code"], row["created_at"],
                       row["updated_at"])
        st.hold_id = row["hold_id"]
        st.slot_id = row["slot_id"]
        st.hold_expires_at = row["hold_expires_at"]
        for it in items.get(row["cart_id"], []):
            st.lines[it["sku"]] = it["qty"]
            st.added_at[it["sku"]] = it["added_at"]
        st.snapshots = snaps.get(row["cart_id"], [])
        _CARTS[st.cart_id] = st
    if carts:
        log.info("warmed %d carts (%d snapshots) from cart.db", len(carts), sum(len(v) for v in snaps.values()))
    return len(carts)
