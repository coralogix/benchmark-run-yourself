"""
Personalisation (2.7.1, 2019-08-30): returning customers get their previously
bought SKUs boosted.

Pre-split this joined orders/order_lines. In the 2023 split those moved to
ops, so it reads search.db's user_affinity instead, which is meant to be
filled from the order.confirmed bus topic (PM-981, never done). So this is
always empty. It's also turned off in tests (settings.SEARCH_PERSONALISE).
Note the cache key includes uid regardless, so personalised or not, every
(query, user) pair is a separate cache entry.
"""
from larder_common import dbx


def user_history(uid):
    if not uid:
        return set()
    rows = dbx.db("search").query("SELECT DISTINCT sku FROM user_affinity WHERE uid = ?", (uid,))
    return {r["sku"] for r in rows}


def user_categories(uid):
    # TODO(SB 2023-08-14): category affinity, for recommendations. unused
    if not uid:
        return {}
    rows = dbx.db("search").query("SELECT category, SUM(weight) AS w FROM user_affinity WHERE uid = ? GROUP BY category",
                                  (uid,))
    return {r["category"]: r["w"] for r in rows}
