"""
Homegrown migration runner.

Each service keeps its migrations in ``services/<svc>/migrations/`` as modules
with an ``up(run)`` function, where ``run(sql, params=())`` executes one
statement, and lists them in that package's ``MIGRATIONS``. Migrations are
applied in list order (NOT by filename) and recorded in the schema_version
table of that service's database. There is no down().

    from larder_common.dbx import migrate
    migrate.run(dbx.db("cart"), "services.cart.migrations", MIGRATIONS)

Runs automatically on app start (each service's create_app).
"""
import importlib
import time

from larder_common.log import get_logger

log = get_logger("dbx.migrate")


def _ensure_version_table(database):
    database.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, name TEXT, applied_at REAL)"
    )


def current_version(database):
    _ensure_version_table(database)
    return database.scalar("SELECT MAX(version) FROM schema_version", default=0) or 0


def applied(database):
    _ensure_version_table(database)
    return {r["version"] for r in database.query("SELECT version FROM schema_version")}


def run(database, package, migrations, verbose=True):
    """Apply any migrations that haven't been applied yet. Returns count."""
    done = applied(database)
    count = 0
    for version, name in migrations:
        if version in done:
            continue
        mod = importlib.import_module("%s.%s" % (package, name))
        t0 = time.time()
        try:
            with database.transaction():
                mod.up(lambda sql, params=(): database.execute(sql, params))
                database.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, time.time()),
                )
        except Exception as e:
            # two processes migrating the same file at once (search + pricing
            # used to do this to catalog.db). Whoever lost will see the table.
            if "already exists" in str(e) or "UNIQUE constraint" in str(e):
                log.warning("migration %s %s raced: %s", version, name, e)
                continue
            raise
        count += 1
        if verbose:
            log.info("applied migration %s %s to %s in %.2fs", version, name, database.name, time.time() - t0)
    return count
