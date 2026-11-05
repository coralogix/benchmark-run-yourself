# see larder_common/dbx/migrate.py - modules here are listed explicitly.
# Numbers 2, 3, 5, 6 belonged to tables that moved to other services in the
# 2023 split (orders -> ops, zones -> ops, carts -> cart). Don't reuse them.
MIGRATIONS = [
    (1, "m0001_initial"),
    (4, "m0004_search_synonyms"),
    # 7 was the MySQL->sqlite price column fix, folded into 0001 (MK 2017)
    (8, "m0008_search_boosts"),
    (9, "m0009_sales_rank"),
    (10, "m0010_product_indexes"),
]
