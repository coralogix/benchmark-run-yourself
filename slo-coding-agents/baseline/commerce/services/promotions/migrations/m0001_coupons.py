# coupons (2016 schema). `expires` is free text: three generations of
# marketing tooling wrote three formats into it. See coupons.parse_expiry.


def up(run):
    run(
        """CREATE TABLE coupons (
            code TEXT PRIMARY KEY,
            percent_off REAL DEFAULT 0,
            min_spend REAL DEFAULT 0,
            expires TEXT,
            free_delivery INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )"""
    )
