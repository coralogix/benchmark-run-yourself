# initial schema (2016), price columns are REAL pounds - matches the old MySQL
# DECIMAL(8,2) export. see TD-1 for the pence migration that never happened.
# (2023 split: users/coupons/carts tables removed from this file, they live in
# accounts/promotions/cart now)


def up(run):
    run(
        """CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            price REAL NOT NULL,
            vat_band TEXT DEFAULT 'standard',
            description TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )"""
    )
    run("CREATE TABLE product_tags (product_id INTEGER, tag TEXT)")
    run("CREATE TABLE product_diet (product_id INTEGER, diet TEXT)")
