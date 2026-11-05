"""
Where database files live.

Every service has a data dir (LARDER_DATA_DIR, e.g. <repo>/var/cart) and keeps
``<name>.db`` in it.

catalog.db is special: catalog owns it, but search and pricing open it
directly (2023 split: "temporary, until catalog has a bulk API"). They find it
by going up one level from *their own* data dir and into catalog's:

    LARDER_DATA_DIR/../catalog/catalog.db

which only works because the supervisor lays out var/<service> side by side.
It also means catalog has to have created + seeded the file before search or
pricing can do anything; see wait_for_catalog_db().
"""
import os
import time

from larder_common.log import get_logger

log = get_logger("dbx.paths")

CATALOG_DB_FILENAME = "catalog.db"


def data_dir():
    from larder_common import settings

    d = os.path.abspath(settings.DATA_DIR)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def service_db_path(name):
    return os.path.join(data_dir(), "%s.db" % name)


def catalog_db_path():
    """catalog.db as seen from *any* service (see module docstring)."""
    from larder_common import settings

    here = os.path.abspath(settings.DATA_DIR)
    d = os.path.normpath(os.path.join(here, "..", "catalog"))
    # creating catalog's dir from search/pricing is harmless; catalog itself
    # needs it on first boot (TH 2023-04)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, CATALOG_DB_FILENAME)


def _catalog_ready(path):
    if not os.path.exists(path):
        return False
    import sqlite3

    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            n = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return n > 0


def wait_for_catalog_db(timeout=None, poll=0.5):
    """Block until catalog.db exists and has products. Returns True if it does.

    Called at startup by search and pricing. If catalog hasn't come up within
    the timeout we carry on anyway and the first queries will fail.
    """
    from larder_common import settings

    if timeout is None:
        timeout = settings.CATALOG_DB_WAIT_SECONDS
    path = catalog_db_path()
    deadline = time.time() + timeout
    waited = False
    while True:
        if _catalog_ready(path):
            if waited:
                log.info("catalog db ready at %s", path)
            return True
        if time.time() >= deadline:
            log.error("catalog db not ready at %s after %ss, continuing anyway", path, timeout)
            return False
        if not waited:
            log.warning("waiting for catalog db at %s (is catalog running?)", path)
            waited = True
        time.sleep(poll)
