"""Harness ingress mesh: one aiohttp reverse proxy process, one listener per service.

  mesh  :8200+i+off to 127.0.0.1:8100+i+off   service=<name>  (i from services.yaml)
  edge  :8000+off   to 127.0.0.1:8101+off     service=edge-api (public ingress)

All listeners share one Application/AppRunner, one upstream ClientSession and one
JSONL writer; the target is picked from the local port the request arrived on.

Emits larder_ingress_requests / larder_ingress_request_duration_seconds and a
JSON access log per request (OTel logs, subsystem `ingress`), and appends each
request to $LARDER_ARTIFACTS_DIR/ingress.jsonl.

Usage: python -m harness.runtime.mesh [--port-offset N] [--artifacts DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import time

import aiohttp
from aiohttp import web
from yarl import URL

from harness.runtime import otel, registry
from harness.runtime.routes import normalise
from harness.runtime.util import JsonlWriter, artifacts_dir, iso_from_epoch, port

log = logging.getLogger("mesh")

BUCKETS = [0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 30]
UPSTREAM_TIMEOUT_S = 30.0
STREAM_THRESHOLD = 256 * 1024  # 2xx/3xx responses larger than this (or chunked) are streamed
REASON_BODY_MAX = 8 * 1024  # 4xx/5xx JSON bodies up to this size are parsed for `error`
REASON_RE = re.compile(r"^[a-z_]{1,40}$")


USER_RE = re.compile(r"^u-\d{4}$")
SKU_RE = re.compile(r"^SKU-\d{4}$")
POSTCODE_RE = re.compile(r"^LD\d \d[A-Z]{2}$")


def error_reason(code: int, body: bytes | None) -> str:
    """Bounded-cardinality `reason` label from an upstream error body's top-level `error`."""
    if code < 400 or not body or len(body) > REASON_BODY_MAX:
        return ""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(doc, dict):
        return ""
    err = doc.get("error")
    if not isinstance(err, str):
        return ""
    return err if REASON_RE.match(err) else "other"

HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "Connection",
        "Keep-Alive",
        "Proxy-Authenticate",
        "Proxy-Authorization",
        "Proxy-Connection",
        "TE",
        "Trailer",
        "Transfer-Encoding",
        "Upgrade",
        "Content-Length",
    )
)


class Mesh:
    def __init__(self, offset: int, artifacts: str):
        self.offset = offset
        self.tel = otel.setup("mesh", "ingress")
        self.req_counter = self.tel.counter("larder_ingress_requests", "Requests seen at the harness ingress")
        self.req_hist = self.tel.histogram(
            "larder_ingress_request_duration_seconds", BUCKETS, "Ingress request latency"
        )
        # High-cardinality layer: one series per (entity_type, entity) for customer-facing traffic.
        # Labels are validated against their expected shape, so cardinality stays bounded by the seed data.
        self.entity_counter = self.tel.counter("larder_ingress_entity_requests",
                                               "Customer-edge requests by entity (customer, product SKU, postcode)")
        self.jsonl = JsonlWriter(os.path.join(artifacts, "ingress.jsonl"))
        self.session: aiohttp.ClientSession | None = None

    async def start_session(self) -> None:
        # keep idle upstream sockets shorter than Express's 5s keepAliveTimeout to avoid reuse races
        connector = aiohttp.TCPConnector(limit=0, keepalive_timeout=3, ttl_dns_cache=None)
        self.session = aiohttp.ClientSession(
            connector=connector,
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=UPSTREAM_TIMEOUT_S),
            cookie_jar=aiohttp.DummyCookieJar(),
            skip_auto_headers=("User-Agent", "Accept-Encoding", "Content-Type", "Accept"),
        )

    def make_app(self, targets: dict[int, tuple[str, str]]) -> web.Application:
        """targets: listen port to (service, upstream base URL)."""

        async def handler(request: web.Request) -> web.StreamResponse:
            sock = request.transport.get_extra_info("sockname") if request.transport else None
            target = targets.get(sock[1]) if sock else None
            if target is None:  # pragma: no cover - only if a listener was misconfigured
                return web.json_response({"error": "no_route"}, status=502)
            return await self.proxy(request, target[0], target[1])

        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", handler)
        return app

    async def proxy(self, request: web.Request, service: str, upstream_base: str) -> web.StreamResponse:
        t0 = time.perf_counter()
        method = request.method
        path = request.path
        code = 502
        err: str | None = None
        reason = ""
        resp: web.StreamResponse
        try:
            headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
            body = await request.read() if request.body_exists else None
            url = URL(upstream_base + request.raw_path, encoded=True)
            async with self.session.request(
                method, url, headers=headers, data=body, allow_redirects=False
            ) as up:
                code = up.status
                out_headers = [(k, v) for k, v in up.headers.items() if k.lower() not in HOP_BY_HOP]
                clen = up.content_length
                small_error = up.status >= 400 and (clen is None or clen <= REASON_BODY_MAX)
                if small_error or (clen is not None and clen <= STREAM_THRESHOLD):
                    data = await up.read()
                    if up.status >= 400:
                        reason = error_reason(up.status, data)
                    resp = web.Response(status=up.status, reason=up.reason, body=data)
                    for k, v in out_headers:
                        resp.headers.add(k, v)
                else:
                    resp = web.StreamResponse(status=up.status, reason=up.reason)
                    for k, v in out_headers:
                        resp.headers.add(k, v)
                    await resp.prepare(request)
                    async for chunk in up.content.iter_chunked(64 * 1024):
                        await resp.write(chunk)
                    await resp.write_eof()
        except asyncio.TimeoutError:
            code, err = 504, "upstream_timeout"
            reason = err
            resp = web.json_response({"error": err}, status=504)
        except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError, OSError) as e:
            # connection refused/reset, server disconnected, broken payload
            code, err = 502, "upstream_unavailable"
            reason = err
            resp = web.json_response({"error": err, "detail": type(e).__name__}, status=502)
        except asyncio.CancelledError:
            code, err = 499, "client_closed"
            self.record(service, method, path, code, t0, err, request, err)
            raise
        except Exception as e:  # pragma: no cover - defensive
            log.exception("proxy failure")
            code, err = 502, "proxy_error"
            reason = "upstream_unavailable"
            resp = web.json_response({"error": err, "detail": type(e).__name__}, status=502)
        self.record(service, method, path, code, t0, err, request, reason)
        return resp

    @staticmethod
    def entities(service: str, route: str, path: str, request) -> dict:
        """Entities a customer-edge request concerns: the customer (session header), the product SKU
        (product detail pages) and the postcode (delivery slot listing)."""
        if service != "edge-api":
            return {}
        out = {}
        user = request.headers.get("X-Larder-User", "")
        if USER_RE.match(user):
            out["user"] = user
        if route == "GET /api/products/:sku":
            sku = path.rstrip("/").rsplit("/", 1)[-1]
            if SKU_RE.match(sku):
                out["sku"] = sku
        if route == "GET /api/delivery/slots":
            pc = " ".join(request.query.get("postcode", "").upper().split())
            out["postcode"] = pc if POSTCODE_RE.match(pc) else "invalid"
        return out

    def record(self, service, method, path, code, t0, err, request, reason: str = "") -> None:
        dur = time.perf_counter() - t0
        route = normalise(service, method, path)
        ents = self.entities(service, route, path, request)
        scode = str(code)
        code_class = scode[0] + "xx"
        m = method if len(method) <= 10 else "OTHER"
        self.req_counter.add(
            1,
            {"service": service, "route": route, "method": m, "code": scode, "code_class": code_class, "reason": reason},
        )
        self.req_hist.record(dur, {"service": service, "route": route, "method": m, "code_class": code_class})
        for etype, ent in ents.items():
            self.entity_counter.add(1, {"service": service, "route": route, "entity_type": etype, "entity": ent, "code_class": code_class})
        now = time.time()
        rec = {
            "ts": iso_from_epoch(now),
            "ts_epoch": round(now, 3),
            "service": service,
            "route": route,
            "method": method,
            "path": path,
            "code": code,
            "duration_ms": round(dur * 1000, 2),
            "reason": reason,
            **ents,
        }
        if err:
            rec["error"] = err
        self.jsonl.write(rec)
        if self.tel.enabled:
            tp = request.headers.get("traceparent")
            body = dict(rec)
            body["code_class"] = code_class
            if tp:
                body["traceparent"] = tp
            sev = "ERROR" if code >= 500 else ("WARN" if code >= 400 else "INFO")
            attrs = {
                "service": service,
                "route": route,
                "method": method,
                "path": path,
                "code": scode,
                "code_class": code_class,
                "duration_ms": rec["duration_ms"],
                "error": err,
                "reason": reason,
                **ents,
            }
            if tp:
                parts = tp.split("-")
                if len(parts) >= 3:
                    attrs["trace_id"] = parts[1]
                    attrs["span_id"] = parts[2]
            self.tel.log(json.dumps(body, separators=(",", ":")), sev, attrs)

    async def flusher(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            self.jsonl.flush()


def listeners(offset: int, reg: registry.Registry | None = None) -> list[tuple[int, str, int]]:
    """[(listen_port, service, upstream_port)]; the edge listener is last."""
    reg = reg or registry.default()
    out = [(s.mesh_port(offset), s.name, s.app_port(offset)) for s in reg.services]
    edge = [s for s in reg.services if s.edge]
    for s in edge:
        out.append((port("edge", offset), s.name, s.app_port(offset)))
    return out


async def run(offset: int, artifacts: str) -> None:
    mesh = Mesh(offset, artifacts)
    await mesh.start_session()
    ls = listeners(offset)
    targets = {lp: (svc, f"http://127.0.0.1:{up}") for lp, svc, up in ls}
    runner = web.AppRunner(mesh.make_app(targets), access_log=None, handle_signals=False)
    await runner.setup()
    for lp, svc, up in ls:
        await web.TCPSite(runner, "127.0.0.1", lp, reuse_address=True, backlog=1024).start()
        log.info("mesh listening :%d -> :%d (%s)", lp, up, svc)
    runners = [runner]
    print(f"mesh ready listeners={len(ls)} edge=:{port('edge', offset)}", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    flush_task = asyncio.create_task(mesh.flusher())
    await stop.wait()
    flush_task.cancel()
    for r in runners:
        await r.cleanup()
    await mesh.session.close()
    mesh.jsonl.close()
    mesh.tel.shutdown()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--artifacts", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s mesh %(levelname)s %(message)s")
    art = str(artifacts_dir(args.artifacts))
    asyncio.run(run(args.port_offset, art))


if __name__ == "__main__":
    main()
