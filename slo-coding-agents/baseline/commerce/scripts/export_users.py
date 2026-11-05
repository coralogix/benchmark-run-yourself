"""
Export users for the CRM (2019, DP). The CRM contract ended in 2021.

    uv run python -m scripts.export_users > users.csv

Used to call the storefront's /internal/users endpoint on the old ELB; now
reads accounts.db directly (var/accounts/accounts.db).
"""
import csv
import os
import sqlite3
import sys

import services  # noqa: F401
from larder_common import discovery
from larder_common.helpers.strings import mask_email

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the old endpoint, for reference
OLD_URL = "http://%s/internal/users" % discovery.LEGACY_HOSTS["fulfilment"]
FIELDS = ("user_id", "name", "email", "postcode", "created")


def main(out=sys.stdout, mask=True):
    path = os.path.join(ROOT, "var", "accounts", "accounts.db")
    if not os.path.exists(path):
        print("no accounts db at %s" % path, file=sys.stderr)
        return 1
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    w = csv.writer(out)
    w.writerow(FIELDS)
    for row in conn.execute("SELECT * FROM users ORDER BY user_id"):
        vals = [row[f] if f in row.keys() else "" for f in FIELDS]
        if mask:
            vals[2] = mask_email(vals[2])
        w.writerow(vals)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
