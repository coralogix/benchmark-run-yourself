# PM-610 (SB 2023-07): persist price snapshots so the "prices changed since
# you added this" banner survives a restart. The table was created; the code
# that writes to it was never merged (snapshots only live in memory, see
# state.py). Nothing reads it either.


def up(run):
    run(
        """CREATE TABLE cart_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT,
            total_pence INTEGER,
            payload TEXT,
            created_at TEXT
        )"""
    )
