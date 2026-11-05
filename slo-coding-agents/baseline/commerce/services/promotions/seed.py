"""First-boot coupon load from seed/coupons.json (moved here from catalog's seed in 2023)."""
from larder_common import dbx
from larder_common.log import get_logger
from larder_common.seedfiles import load_json

log = get_logger("promotions.seed")


def is_empty():
    return (dbx.db("promotions").scalar("SELECT COUNT(*) FROM coupons", default=0) or 0) == 0


def load_if_empty():
    if not is_empty():
        return False
    d = dbx.db("promotions")
    n = 0
    with d.transaction():
        for c in load_json("coupons.json"):
            d.execute(
                "INSERT INTO coupons (code, percent_off, min_spend, expires, free_delivery, active)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                (
                    c["code"],
                    c.get("percent_off", 0),
                    c.get("min_spend_pence", 0) / 100.0,
                    c.get("expires"),
                    1 if c.get("free_delivery") else 0,
                ),
            )
            n += 1
    log.info("loaded %d coupons", n)
    return True
