# merchandising boosts for search ranking. populated by the (retired)
# merch tool; seed.py fills in defaults for featured SKUs.


def up(run):
    run("CREATE TABLE search_boosts (sku TEXT, boost REAL DEFAULT 1.0, reason TEXT)")
