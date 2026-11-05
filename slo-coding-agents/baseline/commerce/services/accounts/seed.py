"""
First-boot user load from seed/users.json (the 2021 PHP site export).

Emails are loaded exactly as exported. Some of the 2016 accounts have
very long, single-token local parts (bulk-created test/legacy accounts that
nobody cleaned up). We don't validate them here; see users.looks_like_email.
"""
from larder_common import dbx
from larder_common.log import get_logger
from larder_common.seedfiles import load_json

log = get_logger("accounts.seed")


def is_empty():
    return (dbx.db("accounts").scalar("SELECT COUNT(*) FROM users", default=0) or 0) == 0


def load_if_empty():
    if not is_empty():
        return False
    log.info("empty accounts database, loading seed users")
    d = dbx.db("accounts")
    n = 0
    with d.transaction():
        for u in load_json("users.json"):
            d.execute(
                "INSERT INTO users (user_id, name, email, postcode, created) VALUES (?, ?, ?, ?, ?)",
                (u["user_id"], u.get("name"), u.get("email"), u.get("postcode"), u.get("created")),
            )
            n += 1
    log.info("loaded %d users", n)
    return True
