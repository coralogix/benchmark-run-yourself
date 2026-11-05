def up(run):
    run(
        """CREATE TABLE carts (
            cart_id TEXT PRIMARY KEY,
            user_id TEXT,
            coupon_code TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    run(
        """CREATE TABLE cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT,
            sku TEXT,
            qty INTEGER,
            added_at TEXT
        )"""
    )
    run("CREATE INDEX idx_cart_items_cart ON cart_items (cart_id)")
