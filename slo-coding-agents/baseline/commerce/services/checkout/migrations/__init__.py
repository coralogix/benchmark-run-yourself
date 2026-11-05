# see larder_common/dbx/migrate.py - modules here are listed explicitly.
MIGRATIONS = [
    (1, "m0001_checkouts"),
    (2, "m0002_checkout_events"),
    # 3 was order_lines; ops owns order contents since 2023 (TH)
    (4, "m0004_saved_tokens"),
]
