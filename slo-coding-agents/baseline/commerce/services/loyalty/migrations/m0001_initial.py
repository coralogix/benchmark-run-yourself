# loyalty schema (2022, PM-1142 spike). Nothing writes to these yet.


def up(run):
    run(
        """CREATE TABLE points_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            order_ref TEXT,
            points INTEGER,
            reason TEXT,
            created_at TEXT
        )"""
    )
    run("CREATE TABLE balances (user_id TEXT PRIMARY KEY, points INTEGER DEFAULT 0, updated_at TEXT)")
    run("CREATE TABLE seen_deliveries (delivery_id TEXT PRIMARY KEY, topic TEXT, received_at TEXT)")
