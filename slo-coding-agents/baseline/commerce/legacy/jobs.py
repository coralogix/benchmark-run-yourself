"""
Old cron jobs (2017-2020). The crontab that ran them was deleted with the VMs.

    python -m legacy.jobs abandoned_carts     # "email" users about abandoned carts
    python -m legacy.jobs reindex             # rebuild search index (no-op now)

abandoned_carts used to delete carts older than 14 days as a side effect,
which is how cart storage stayed bounded on the old boxes. Nobody runs it now.
"""
import datetime
import sys

from larder_common.log import legacy_log

ABANDONED_AFTER_DAYS = 14


def abandoned_carts(dry_run=True):
    import services  # noqa: F401
    from larder_common import dbx

    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=ABANDONED_AFTER_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cart_db = dbx.db("cart")
    rows = cart_db.query("SELECT cart_id, user_id FROM carts WHERE status = 'open' AND updated_at < ?", (cutoff,))
    legacy_log("notice", "%d abandoned carts older than %s", len(rows), cutoff)
    if not dry_run:
        for r in rows:
            cart_db.execute("DELETE FROM cart_items WHERE cart_id = ?", (r["cart_id"],))
            cart_db.execute("DELETE FROM carts WHERE cart_id = ?", (r["cart_id"],))
    return len(rows)


def reindex():
    legacy_log("notice", "reindex is a no-op since search builds its own index (2017)")
    return 0


JOBS = {"abandoned_carts": abandoned_carts, "reindex": reindex}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in JOBS:
        print("usage: python -m legacy.jobs [%s]" % "|".join(sorted(JOBS)))
        return 2
    print(JOBS[argv[0]]())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
