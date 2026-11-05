"""LLM request governor: a local OpenAI-compatible proxy in front of the model endpoint.

The evaluated agent (opencode) talks to http://127.0.0.1:<port>/v1 instead of the endpoint, so that:

  * requests are paced to a fixed budget (requests per minute) per agent. With N concurrent agents
    sharing one endpoint key, N x budget must stay under the endpoint's limit;
  * if the endpoint still answers 429, 500, 502, 503 or 504, the governor waits and resends the
    IDENTICAL request body. The agent never sees an error, and its
    context is never touched;
  * every request is logged (wait spent in the governor, upstream latency, status, attempts) to
    <artifacts>/llm_governor.jsonl, so queueing time is measured per trial and comparable across
    conditions.

    python -m harness.runtime.llm_governor --upstream <your-endpoint> --port 18080 \
        --rpm 22 --log /path/llm_governor.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time

from aiohttp import ClientSession, ClientTimeout, web

HOP = {"host", "content-length", "transfer-encoding", "connection", "keep-alive", "accept-encoding"}


class Bucket:
    """Token bucket: `rpm` requests per minute, burst of `burst` requests."""

    def __init__(self, rpm: float, burst: int):
        self.rate = rpm / 60.0
        self.cap = float(burst)
        self.tokens = float(burst)
        self.t = time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self) -> float:
        start = time.monotonic()
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.t) * self.rate)
                self.t = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return time.monotonic() - start
                await asyncio.sleep((1 - self.tokens) / self.rate)


def build_app(upstream: str, bucket: Bucket, log_path: str, max_retry_s: float) -> web.Application:
    session: dict = {}
    logf = open(log_path, "a", buffering=1)

    async def on_start(app):
        session["s"] = ClientSession(timeout=ClientTimeout(total=None, sock_read=600))

    async def on_stop(app):
        await session["s"].close()

    async def handle(req: web.Request) -> web.StreamResponse:
        body = await req.read()
        headers = {k: v for k, v in req.headers.items() if k.lower() not in HOP}
        url = upstream.rstrip("/") + req.rel_url.path_qs
        rec = {"ts": time.time(), "path": req.path, "body_sha256": hashlib.sha256(body).hexdigest()[:16],
               "body_bytes": len(body), "governor_wait_s": 0.0, "upstream_retries": 0, "retry_wait_s": 0.0}
        if req.path.endswith("/chat/completions") or req.path.endswith("/responses"):
            rec["governor_wait_s"] = round(await bucket.take(), 3)
        deadline = time.monotonic() + max_retry_s
        backoff = 2.0
        while True:
            t0 = time.monotonic()
            try:
                resp = await session["s"].request(req.method, url, data=body, headers=headers)
            except Exception as e:
                # a transport failure is a failed model request: log it so the validity check sees it
                rec.update(status=599, error=type(e).__name__, upstream_s=round(time.monotonic() - t0, 3))
                logf.write(json.dumps(rec) + "\n")
                raise
            # 429/503: rate limited or overloaded. 500/502/504: transient endpoint faults, which can come in
            # bursts that outlast a client's own quick retries. Resend the identical body after a backoff either way, so the
            # agent's session never sees the failure.
            if resp.status in (429, 500, 502, 503, 504) and time.monotonic() < deadline:
                ra = resp.headers.get("retry-after")
                wait = max(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 0, backoff)
                await resp.release()
                rec["upstream_retries"] += 1
                rec["retry_wait_s"] = round(rec["retry_wait_s"] + wait, 3)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 30)
                continue
            out = web.StreamResponse(status=resp.status, headers={k: v for k, v in resp.headers.items() if k.lower() not in HOP})
            await out.prepare(req)
            async for chunk in resp.content.iter_any():
                await out.write(chunk)
            await out.write_eof()
            rec.update(status=resp.status, upstream_s=round(time.monotonic() - t0, 3))
            logf.write(json.dumps(rec) + "\n")
            return out

    app = web.Application(client_max_size=64 * 2**20)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    async def health(req):
        return web.json_response({"ok": True, "rpm": round(bucket.rate * 60, 2)})

    app.router.add_get("/__governor/health", health)   # answered locally, never proxied
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True)
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--rpm", type=float, required=True, help="request budget per minute for this agent")
    ap.add_argument("--burst", type=int, default=3)
    ap.add_argument("--max-retry-s", type=float, default=900, help="give up resending after this long")
    ap.add_argument("--log", required=True)
    a = ap.parse_args()
    web.run_app(build_app(a.upstream, Bucket(a.rpm, a.burst), a.log, a.max_retry_s), host="127.0.0.1", port=a.port,
                print=None)


if __name__ == "__main__":
    main()
