"""Shared plumbing for the third-party simulators (psp-sim, partner-sim, email-sim).

The sims never send telemetry anywhere: they are "third parties".
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
import random
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from harness.runtime.util import artifacts_dir, iso_now, write_json_atomic

log = logging.getLogger("sims")

Z99 = 2.3263478740408408  # standard normal quantile at 0.99


class LatencySampler:
    """Deterministic lognormal latency fitted to (p50, p99) in ms."""

    def __init__(self, seed: int | str):
        self.rng = random.Random(seed)

    def sample_ms(self, p50: float, p99: float) -> float:
        if p50 <= 0:
            return 0.0
        p99 = max(p99, p50)
        mu = math.log(p50)
        sigma = (math.log(p99) - mu) / Z99
        return self.rng.lognormvariate(mu, sigma) if sigma > 0 else float(p50)

    def chance(self, rate: float) -> bool:
        # always consume a draw so the stream stays aligned regardless of rate
        u = self.rng.random()
        return u < rate


def admin_middleware(token_env: str = "LARDER_ADMIN_TOKEN"):
    @web.middleware
    async def mw(request: web.Request, handler):
        expected = os.environ.get(token_env, "")
        got = request.headers.get("X-Admin-Token", "")
        if not expected or not hmac.compare_digest(expected, got):
            return web.json_response({"error": "forbidden"}, status=403)
        return await handler(request)

    return mw


async def read_json(request: web.Request) -> tuple[Any, bytes]:
    raw = await request.read()
    if not raw:
        return {}, raw
    try:
        return json.loads(raw), raw
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "invalid_json"}), content_type="application/json")


class Dumper:
    """Periodically dumps `snapshot()` to a JSON file in the artifacts dir."""

    def __init__(self, filename: str, snapshot: Callable[[], Any], artifacts: str | None = None, every_s: float = 30.0):
        self.dir = artifacts_dir(artifacts)
        self.path = self.dir / filename
        self.snapshot = snapshot
        self.every_s = every_s
        self.archived = 0

    def dump(self, path: Path | None = None) -> None:
        try:
            data = self.snapshot()
            data["dumped_at"] = iso_now()
            write_json_atomic(path or self.path, data)
        except Exception:
            log.exception("dump failed")

    def archive_before_reset(self) -> None:
        self.archived += 1
        stem = self.path.stem
        self.dump(self.dir / f"{stem}.pre-reset-{self.archived}.json")

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(self.every_s)
            self.dump()


async def serve(
    api: web.Application,
    admin: web.Application,
    api_port: int,
    admin_port: int,
    dumper: Dumper,
    name: str,
    on_stop: Callable[[], Awaitable[None]] | None = None,
) -> None:
    runners = []
    for app, p in ((api, api_port), (admin, admin_port)):
        r = web.AppRunner(app, access_log=None, handle_signals=False)
        await r.setup()
        await web.TCPSite(r, "127.0.0.1", p, reuse_address=True, backlog=512).start()
        runners.append(r)
    print(f"{name} ready api=:{api_port} admin=:{admin_port}", flush=True)
    dumper.dump()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    t = asyncio.create_task(dumper.loop())
    await stop.wait()
    t.cancel()
    if on_stop:
        await on_stop()
    dumper.dump()
    for r in runners:
        await r.cleanup()


def sim_seed(default: int) -> int:
    return int(os.environ.get("LARDER_SIM_SEED", str(default)))
