"""
Deep health checks (JW 2020). /healthz is deliberately shallow (the
supervisor restarts things that fail it, and we don't want a slow peer to get
*us* restarted). This was for a /healthz/deep endpoint that never got mounted.

    from larder_common.healthcheck import deep_check
    deep_check(["catalog", "pricing"], databases=["cart"])
"""
import time

from larder_common import dbx
from larder_common.http import client
from larder_common.log import get_logger

log = get_logger("healthcheck")


def check_peer(name):
    t0 = time.time()
    try:
        res = client(name).get("/healthz", retry=False)
        ok = res.ok
    except Exception as e:  # noqa
        log.info("peer %s unhealthy: %s", name, e)
        ok = False
    return {"ok": ok, "ms": int((time.time() - t0) * 1000)}


def check_db(name):
    t0 = time.time()
    try:
        dbx.db(name).scalar("SELECT 1")
        ok = True
    except Exception as e:  # noqa
        log.info("db %s unhealthy: %s", name, e)
        ok = False
    return {"ok": ok, "ms": int((time.time() - t0) * 1000)}


def deep_check(peers=(), databases=()):
    out = {"peers": {p: check_peer(p) for p in peers}, "databases": {d: check_db(d) for d in databases}}
    out["ok"] = all(v["ok"] for v in out["peers"].values()) and all(v["ok"] for v in out["databases"].values())
    return out
