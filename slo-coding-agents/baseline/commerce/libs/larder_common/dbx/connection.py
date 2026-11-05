"""
Per-thread sqlite3 connections.

sqlite3 connections can't be shared across threads safely, and waitress
gives us a pool of threads, so we keep one connection per (thread, database)
in a threading.local. Paths are resolved at *call* time so tests can point
them somewhere else.
"""
import contextlib
import sqlite3
import threading
import time

from larder_common.log import get_logger

log = get_logger("dbx")

_local = threading.local()

# MK 2017: sqlite busy timeout. 30s because the nightly export used to hold
# the write lock for ages. The export is gone, the timeout stays.
BUSY_TIMEOUT = 30

# slow query logging threshold (seconds)
SLOW_QUERY = 0.25

# total queries executed, per process. handy in the debugger
QUERY_COUNT = 0

_REGISTRY = {}
_REGISTRY_LOCK = threading.Lock()


def _connect(path):
    # NB: use sqlite3.connect (not `from sqlite3 import connect`) so the
    # platform team's instrumentation can wrap it.
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=OFF")  # FIXME(RB 2018-10-03): turn on once cart_items orphans are cleaned
    cur.close()
    return conn


def _conns():
    d = getattr(_local, "conns", None)
    if d is None:
        d = _local.conns = {}
    return d


class Database(object):
    """A named sqlite database. ``path_fn`` is called every time a thread
    needs a connection, so changing settings.DATA_DIR "just works"."""

    def __init__(self, name, path_fn):
        self.name = name
        self.path_fn = path_fn

    def path(self):
        return self.path_fn()

    def get_conn(self):
        path = self.path()
        conns = _conns()
        entry = conns.get(self.name)
        if entry is None or entry[1] != path:
            conn = _connect(path)
            entry = conns[self.name] = [conn, path, 0]
        return entry[0]

    def _depth(self):
        entry = _conns().get(self.name)
        return entry[2] if entry else 0

    def _set_depth(self, n):
        entry = _conns().get(self.name)
        if entry is not None:
            entry[2] = n

    def _run(self, sql, params=()):
        global QUERY_COUNT
        conn = self.get_conn()
        cur = conn.cursor()
        t0 = time.time()
        cur.execute(sql, params)
        QUERY_COUNT += 1
        took = time.time() - t0
        if took > SLOW_QUERY:
            log.warning("slow query on %s %.3fs: %s", self.name, took, sql[:200])
        return cur

    def query(self, sql, params=()):
        cur = self._run(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows

    def query_one(self, sql, params=()):
        cur = self._run(sql, params)
        row = cur.fetchone()
        cur.close()
        return row

    def scalar(self, sql, params=(), default=None):
        row = self.query_one(sql, params)
        if row is None:
            return default
        return row[0]

    def execute(self, sql, params=()):
        cur = self._run(sql, params)
        rowid = cur.lastrowid
        cur.close()
        return rowid

    def executescript(self, script):
        self.get_conn().executescript(script)

    def table_exists(self, table):
        return self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,), 0) > 0

    @contextlib.contextmanager
    def transaction(self):
        """BEGIN/COMMIT. Nested calls just join the outer transaction."""
        conn = self.get_conn()
        depth = self._depth()
        if depth == 0:
            self._run("BEGIN IMMEDIATE")
        self._set_depth(depth + 1)
        try:
            yield conn
        except Exception:
            self._set_depth(self._depth() - 1)
            if self._depth() == 0:
                self._run("ROLLBACK")
            raise
        else:
            self._set_depth(self._depth() - 1)
            if self._depth() == 0:
                self._run("COMMIT")

    def close(self):
        entry = _conns().pop(self.name, None)
        if entry is not None:
            try:
                entry[0].close()
            except Exception:
                pass

    def __repr__(self):
        return "<Database %s>" % self.name


def register(name, path_fn=None):
    """Register (or fetch) a database. Default path: <DATA_DIR>/<name>.db"""
    with _REGISTRY_LOCK:
        d = _REGISTRY.get(name)
        if d is None or path_fn is not None:
            if path_fn is None:
                from larder_common.dbx import paths

                def path_fn(_n=name):
                    return paths.service_db_path(_n)

            d = _REGISTRY[name] = Database(name, path_fn)
        return d


def db(name):
    d = _REGISTRY.get(name)
    if d is None:
        d = register(name)
    return d


def close_all():
    for name, entry in list(_conns().items()):
        try:
            entry[0].close()
        except Exception:
            pass
    _local.conns = {}
