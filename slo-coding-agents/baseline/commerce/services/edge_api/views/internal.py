"""
Debug endpoints for on-call (TH 2023-05). Not under /api, so the mesh/loadgen
never see them.

    GET /_debug/upstreams     where edge-api thinks everything is
    GET /_debug/ping          GET /healthz on every upstream, one after the
                              other (no timeout, so one dead upstream hangs it)
"""
import time

from flask import Blueprint

from larder_common.http import client
from larder_common.web import ok
from services.edge_api import upstreams

bp = Blueprint("edge_internal", __name__)


@bp.route("/_debug/upstreams")
def show_upstreams():
    return ok(upstreams.describe())


@bp.route("/_debug/ping")
def ping():
    out = {}
    for target in upstreams.ALL:
        t0 = time.time()
        try:
            res = client(target).get("/healthz", retry=False)
            status = res.status
        except Exception as e:  # noqa
            status = type(e).__name__
        out[target] = {"status": status, "ms": int((time.time() - t0) * 1000)}
    return ok(out)
