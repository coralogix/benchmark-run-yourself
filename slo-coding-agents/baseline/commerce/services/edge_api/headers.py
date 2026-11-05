"""
Response headers for the public API (DP 2019, moved to edge-api in 2023).

    X-Request-Id    echoed from the request, or generated
    Cache-Control   "no-store" for /api/carts, /api/checkout, /api/account;
                    "public, max-age=60" for product pages (the CDN that
                    honoured this was removed in 2021; browsers still do)
    X-Larder-Edge   service version, for support tickets
"""
import uuid

from flask import g, request

import larder_common

NO_STORE_PREFIXES = ("/api/carts", "/api/checkout", "/api/account", "/api/delivery", "/api/orders")
CACHEABLE_PREFIXES = ("/api/products", "/api/categories")
PRODUCT_MAX_AGE = 60


def install(app):
    @app.before_request
    def _request_id():
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]

    @app.after_request
    def _headers(resp):
        resp.headers["X-Request-Id"] = getattr(g, "request_id", "-")
        resp.headers["X-Larder-Edge"] = larder_common.__version__
        path = request.path
        if path.startswith(NO_STORE_PREFIXES):
            resp.headers["Cache-Control"] = "no-store"
        elif path.startswith(CACHEABLE_PREFIXES) and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=%d" % PRODUCT_MAX_AGE
        return resp
