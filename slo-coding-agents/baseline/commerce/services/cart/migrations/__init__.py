# see larder_common/dbx/migrate.py. 1 and 5 are the storefront's carts tables
# (m0001_initial + m0005_cart_holds) copied over in the 2023 split.
MIGRATIONS = [
    (1, "m0001_carts"),
    (5, "m0005_cart_holds"),
    (6, "m0006_cart_snapshots"),
]
