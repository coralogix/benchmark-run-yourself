# PROMO-61 (KP 2020-06): one-use-per-customer coupons. Table created, feature
# never built; nothing inserts here. (FEAT-12 on the backlog is the same idea.)


def up(run):
    run(
        """CREATE TABLE redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            user_id TEXT,
            order_ref TEXT,
            created_at TEXT
        )"""
    )
