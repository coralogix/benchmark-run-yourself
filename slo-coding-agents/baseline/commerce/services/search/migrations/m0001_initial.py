# search.db (2023 split). user_affinity replaces the old "what has this user
# ordered" join over orders/order_lines, which moved to ops. It was meant to be
# filled from the order.confirmed bus topic (PM-981). It never was.


def up(run):
    run(
        """CREATE TABLE user_affinity (
            uid TEXT,
            category TEXT,
            sku TEXT,
            weight REAL DEFAULT 1.0,
            updated_at TEXT
        )"""
    )
    run("CREATE INDEX idx_user_affinity_uid ON user_affinity (uid)")
