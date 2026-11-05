"""
Shared HTTP client for service-to-service calls.

    from larder_common.http import client
    res = client("catalog").get("/internal/products/SKU-0001")
    if res.ok: ...

* One process-wide ``requests.Session`` (connection pooling; also the thing
  the test suite swaps out, see tests/fakes.py).
* Base URLs come from larder_common.discovery (``<NAME>_URL`` env vars).
* Every call is wrapped in larder_common.retry: transport errors and gateway
  errors (the mesh/ELB answering for a dead or slow upstream) are retried
  ``settings.HTTP_CLIENT_EXTRA_ATTEMPTS`` times. That includes POSTs.
* Timeout: ``settings.HTTP_CLIENT_TIMEOUT`` (see the comment in settings.py).
* Duration of every attempt is recorded as
  commerce_http_client_duration_seconds{target,method}.

Moved from larder/clients/fulfilment.py (2023). The fulfilment-specific bits
are gone but the semantics were kept "so nothing changes for checkout".
"""
import threading
import time

import requests

from larder_common import __version__, discovery, settings, telemetry
from larder_common.log import get_logger
from larder_common.retry import call_with_retry

log = get_logger("http")

_session = requests.Session()
_session.headers.update({"X-Larder-Lib": "larder_common/%s" % __version__})

# the ELB (and now the mesh) answer for upstreams that are down or slow. Those
# aren't real answers from the service, so treat them like a dropped connection
# and let the retry decorator have another go. (JW 2018, INC-211 follow-up)
GATEWAY_STATUSES = (503, 504)
GATEWAY_502_REASONS = ("upstream_unavailable", "bad_gateway", None)

_clients = {}
_clients_lock = threading.Lock()


class GatewayError(requests.RequestException):
    """The gateway answered instead of the service (502/503/504)."""

    def __init__(self, status, body, target):
        super().__init__("%s answered %s for %s" % ("gateway", status, target))
        self.status = status
        self.body = body or {}
        self.target = target


class ServiceResponse(object):
    """Thin wrapper so callers don't depend on requests directly."""

    def __init__(self, status, body, headers=None):
        self.status = status
        self.body = body if body is not None else {}
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def error(self):
        if isinstance(self.body, dict):
            return self.body.get("error")
        return None

    def json(self):
        return self.body

    def __repr__(self):
        return "<ServiceResponse %s %r>" % (self.status, self.body if len(repr(self.body)) < 200 else "...")


def _wrap(resp):
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": (resp.text or "")[:500]}
    return ServiceResponse(resp.status_code, body, dict(getattr(resp, "headers", {}) or {}))


def _headers(extra=None):
    h = {"X-Internal-Key": settings.INTERNAL_KEY, "X-Larder-Caller": settings.SERVICE}
    if extra:
        h.update(extra)
    return h


class ServiceClient(object):
    def __init__(self, target, base_url=None):
        self.target = target
        self._base_url = base_url

    @property
    def base_url(self):
        return (self._base_url or discovery.peer_url(self.target)).rstrip("/")

    def url(self, path):
        return self.base_url + path

    def _send(self, method, path, params=None, json=None, headers=None):
        t0 = time.perf_counter()
        try:
            resp = _session.request(
                method,
                self.url(path),
                params=params,
                json=json,
                headers=_headers(headers),
                timeout=settings.HTTP_CLIENT_TIMEOUT,
            )
        finally:
            telemetry.record_http_client(self.target, method, time.perf_counter() - t0)
        res = _wrap(resp)
        if res.status in GATEWAY_STATUSES or (res.status == 502 and res.error in GATEWAY_502_REASONS):
            raise GatewayError(res.status, res.body, self.target)
        return res

    def request(self, method, path, params=None, json=None, headers=None, retry=True):
        if not retry:
            return self._send(method, path, params=params, json=json, headers=headers)
        return call_with_retry(
            self._send,
            method,
            path,
            params=params,
            json=json,
            headers=headers,
            extra_attempts=settings.HTTP_CLIENT_EXTRA_ATTEMPTS,
            wait=settings.HTTP_CLIENT_RETRY_WAIT,
            label="%s %s%s" % (method, self.target, path),
        )

    def get(self, path, params=None, **kw):
        return self.request("GET", path, params=params, **kw)

    def post(self, path, json=None, **kw):
        return self.request("POST", path, json=json, **kw)

    def put(self, path, json=None, **kw):
        return self.request("PUT", path, json=json, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def __repr__(self):
        return "<ServiceClient %s %s>" % (self.target, self.base_url)


def client(target):
    """Process-wide client for `target` (a service name from discovery.SERVICE_INDEX)."""
    c = _clients.get(target)
    if c is None:
        with _clients_lock:
            c = _clients.get(target)
            if c is None:
                c = _clients[target] = ServiceClient(target)
    return c


def direct_client(target, base_url):
    """A client with a fixed base URL (bypasses discovery). Not cached."""
    return ServiceClient(target, base_url=base_url)


def get_json(target, path, params=None, default=None):
    """Convenience: GET and return the body, or `default` on any error / non-2xx."""
    try:
        res = client(target).get(path, params=params)
    except Exception as e:  # noqa
        log.warning("GET %s%s failed: %s", target, path, e)
        return default
    if not res.ok:
        return default
    return res.body
