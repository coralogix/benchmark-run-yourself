# saved delivery addresses (SB 2022-02, "multiple addresses" project, paused).
# Nothing writes here; the account page still uses users.postcode.


def up(run):
    run(
        """CREATE TABLE addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            label TEXT,
            line1 TEXT,
            line2 TEXT,
            town TEXT,
            postcode TEXT,
            is_default INTEGER DEFAULT 0,
            created_at TEXT
        )"""
    )
