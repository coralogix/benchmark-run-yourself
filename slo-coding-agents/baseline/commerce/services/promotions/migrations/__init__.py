# see larder_common/dbx/migrate.py - modules here are listed explicitly.
# The coupons table is the storefront's m0001 coupons table, lifted as-is in
# the 2023 split (hence REAL pounds for min_spend).
MIGRATIONS = [
    (1, "m0001_coupons"),
    (2, "m0002_redemptions"),
]
