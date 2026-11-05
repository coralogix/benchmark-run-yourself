# 2023 split: search used to rank ties by COUNT(*) over order_lines, which
# moved to ops. This table was meant to be filled nightly from the orders
# export (PM-955). The export was never built, so every sku has rank 0.
# No index on sku - "it's tiny" (TH 2023-03)


def up(run):
    run("CREATE TABLE sales_rank (sku TEXT, orders INTEGER DEFAULT 0, updated_at TEXT)")
