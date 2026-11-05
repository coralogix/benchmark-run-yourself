"""
Flask plumbing shared by every service.

    from larder_common.web import create_service_app, body, ok

    def create_app():
        app = create_service_app("catalog")
        app.register_blueprint(routes.bp)
        return app

create_service_app() does, in this order (order matters):
logging -> telemetry -> Flask app -> /healthz + /_status -> request hooks
(busy-worker gauge, slow request log) -> error handlers.

Telemetry has to come before the first sqlite connection (instrumentation
wraps sqlite3.connect), so services must run migrations AFTER calling this.
"""
import threading
import time

from flask import Blueprint, Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

import larder_common
from larder_common import log as larder_log
from larder_common import settings, telemetry
from larder_common.errors import BadRequest, LarderError, PassThrough

log = larder_log.get_logger("web")

# requests currently in flight across all waitress threads. One per process,
# which in production is one per service. (In tests it's shared by all apps.)
_BUSY = {"n": 0}
_BUSY_LOCK = threading.Lock()

# slow request logging (ms)
SLOW_REQUEST_MS = 2000


def busy_workers():
    return _BUSY["n"]


def body():
    data = request.get_json(silent=True)
    if data is None:
        # old clients sent form posts
        data = request.form.to_dict() if request.form else {}
    if not isinstance(data, dict):
        raise BadRequest("invalid_json")
    return data


def ok(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    return resp


def passthrough(res):
    """Return an upstream ServiceResponse to our caller unchanged."""
    payload = res.body if isinstance(res.body, (dict, list)) else {"raw": res.body}
    resp = jsonify(payload)
    resp.status_code = res.status
    return resp


def raise_for(res):
    """Raise PassThrough if `res` (a ServiceResponse) is not 2xx."""
    if not res.ok:
        raise PassThrough(res.status, res.body if isinstance(res.body, dict) else {})
    return res


def _health_blueprint(service):
    bp = Blueprint("health", __name__)

    @bp.route("/healthz")
    def healthz():
        return ok({"ok": True, "service": service})

    @bp.route("/_status")
    def status():
        # internal debug page (2019). not linked anywhere
        return ok(
            {
                "service": service,
                "version": larder_common.__version__,
                "env": settings.ENV,
                "pricing_engine": settings.PRICING_ENGINE,
                "busy_workers": busy_workers(),
                "data_dir": settings.DATA_DIR,
            }
        )

    return bp


def create_service_app(service, import_name=None):
    larder_log.configure()
    app = Flask(import_name or ("larder-" + service))
    app.config["JSON_SORT_KEYS"] = False
    app.config["TESTING"] = settings.TESTING
    app.url_map.strict_slashes = False
    app.larder_service = service

    telemetry.init_telemetry(app, service_name=service)
    telemetry.register_gauge("busy_workers", busy_workers)

    app.register_blueprint(_health_blueprint(service))
    _install_request_hooks(app)
    _install_error_handlers(app)

    larder_common.APP = app
    larder_common.SERVICE = service
    return app


def _install_request_hooks(app):
    @app.before_request
    def _start():
        g._t0 = time.time()
        with _BUSY_LOCK:
            _BUSY["n"] += 1
        g._busy = True

    @app.teardown_request
    def _end(exc):
        if getattr(g, "_busy", False):
            with _BUSY_LOCK:
                _BUSY["n"] -= 1
            g._busy = False
        took = (time.time() - getattr(g, "_t0", time.time())) * 1000
        if took > SLOW_REQUEST_MS:
            log.warning("slow request %s %s %.0fms", request.method, request.path, took)


def _install_error_handlers(app):
    @app.errorhandler(LarderError)
    def _larder_error(e):
        resp = jsonify(e.to_dict())
        resp.status_code = e.status
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(e):
        resp = jsonify({"error": e.name.lower().replace(" ", "_")})
        resp.status_code = e.code
        return resp

    @app.errorhandler(Exception)
    def _unhandled(e):
        log.exception("unhandled error on %s %s", request.method, request.path)
        resp = jsonify({"error": "internal_error"})
        resp.status_code = 500
        return resp
