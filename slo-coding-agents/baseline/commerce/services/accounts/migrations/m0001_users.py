# users table, copied from the storefront's m0001_initial in the 2023 split.
# `created` is a free-text date from the PHP site export (YYYY-MM-DD mostly).


def up(run):
    run(
        """CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            postcode TEXT,
            created TEXT
        )"""
    )
