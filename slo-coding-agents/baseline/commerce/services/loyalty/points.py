"""
Points engine. TODO(PM-1142): everything.

The plan (SB 2022-11-03, never started):
    * 1 point per whole pound of the order total (after discount, before delivery)
    * double points for featured SKUs in promo weeks
    * points credited on order.confirmed, debited on order.cancelled (FEAT-2)
    * dedupe bus deliveries on delivery_id (the bus redelivers)

Until then, balance() returns 0 for everyone and nothing is ever credited.
"""
from larder_common import dbx

POINTS_PER_POUND = 1
PROMO_MULTIPLIER = 2


def points_for_order(payload):
    # TODO(PM-1142): not called anywhere
    total_pence = int(payload.get("total_pence") or 0)
    delivery = int(payload.get("delivery_fee_pence") or 0)
    return max(0, (total_pence - delivery) // 100) * POINTS_PER_POUND


def balance(user_id):
    # deliberately not reading balances: the table is empty and the account
    # page called this on every view (accounts service). 0 is the right answer.
    return 0


def _credit(user_id, order_ref, points, reason="order"):  # pragma: no cover - unused
    d = dbx.db("loyalty")
    d.execute(
        "INSERT INTO points_ledger (user_id, order_ref, points, reason, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (user_id, order_ref, points, reason),
    )
