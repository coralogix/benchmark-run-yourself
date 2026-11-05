"""
Abandoned checkout follow-up (KP 2021-08, "recover lost revenue").

Finds checkout attempts that failed with price_mismatch and were not retried
within an hour, so marketing could email a coupon. Ran from cron on the old
VMs; nothing runs it now. Interesting reading if you want to know how often
customers hit "prices changed" - see summary().
"""
import datetime

from larder_common import dbx
from larder_common.log import get_logger

log = get_logger("checkout.abandoned")

RETRY_WINDOW_MINUTES = 60
FOLLOW_UP_ERRORS = ("price_mismatch", "out_of_stock")


def _cutoff(minutes=RETRY_WINDOW_MINUTES):
    return (datetime.datetime.utcnow() - datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidates(limit=500):
    marks = ",".join("?" * len(FOLLOW_UP_ERRORS))
    rows = dbx.db("checkout").query(
        "SELECT * FROM checkouts WHERE status = 'failed' AND error IN (%s) AND updated_at < ? "
        "ORDER BY updated_at DESC LIMIT ?" % marks,
        list(FOLLOW_UP_ERRORS) + [_cutoff(), limit],
    )
    return [dict(r) for r in rows]


def summary():
    rows = dbx.db("checkout").query("SELECT status, error, COUNT(*) AS n FROM checkouts GROUP BY status, error")
    out = {}
    for r in rows:
        out["%s/%s" % (r["status"], r["error"] or "-")] = r["n"]
    return out
