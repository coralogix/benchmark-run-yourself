# see larder_common/dbx/migrate.py - modules here are listed explicitly.
# 2 was "orders_cache" (2023-04, TH), dropped before it shipped. Don't reuse.
MIGRATIONS = [
    (1, "m0001_users"),
    (3, "m0003_addresses"),
    (4, "m0004_preferences"),
]
