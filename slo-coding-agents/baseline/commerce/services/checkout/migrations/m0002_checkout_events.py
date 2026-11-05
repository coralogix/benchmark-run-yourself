# was order_events (storefront m0006)


def up(run):
    run(
        """CREATE TABLE checkout_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_ref TEXT,
            event TEXT,
            payload TEXT,
            created_at TEXT
        )"""
    )
