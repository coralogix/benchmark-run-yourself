# Local record of every checkout attempt. Was the storefront `orders` table
# (m0002_orders); renamed in the 2023 split because ops owns orders now.
# Money columns kept as REAL pounds + total_pence, like before. (TD-1)


def up(run):
    run(
        """CREATE TABLE checkouts (
            order_ref TEXT PRIMARY KEY,
            cart_id TEXT,
            user_id TEXT,
            status TEXT,
            subtotal REAL,
            discount REAL,
            delivery_fee REAL,
            total REAL,
            total_pence INTEGER,
            coupon_code TEXT,
            hold_id TEXT,
            charge_id TEXT,
            engine TEXT,
            error TEXT,
            attempts INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    run("CREATE INDEX idx_checkouts_cart ON checkouts (cart_id)")
