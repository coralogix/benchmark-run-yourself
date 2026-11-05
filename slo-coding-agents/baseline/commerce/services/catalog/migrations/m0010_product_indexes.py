# AS 2023-05-11: tags/diet lookups were the top two queries in the slow log


def up(run):
    run("CREATE INDEX IF NOT EXISTS idx_product_tags_pid ON product_tags (product_id)")
    run("CREATE INDEX IF NOT EXISTS idx_product_diet_pid ON product_diet (product_id)")
