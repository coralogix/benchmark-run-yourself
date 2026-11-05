"""Larder supervisor: process manager + control API.

Usage:
  python -m harness.runtime.supervisor --workspace <ws> --artifacts <dir> --run-id <id>
      [--port-offset N] [--env-file <file>] [--handover-at ISO] [--duration-min M] [--eval]

Reads the service registry (harness/runtime/services.yaml). Start order: sims (psp, partner,
email), mesh, ops (bus first, then the rest), commerce (catalog first, then the rest, edge-api
last). Each stage waits for /healthz with a timeout ($LARDER_HEALTH_TIMEOUT_S, default 60) and
then moves on; a service that never gets healthy is reported (event `not_ready`), not waited on.

Services run with cwd = repo root:
  commerce: <ws>/commerce/.venv/bin/python -m services.<module>
  ops:      node --require ./lib/telemetry/otel.js services/<name>/server.js
Test override: $LARDER_SERVICE_CMD_OVERRIDE, a shell-style template formatted with
  {name} {repo} {module} {path} {index} {port} {mesh_port} {repo_dir}
  e.g. LARDER_SERVICE_CMD_OVERRIDE='python3 /tmp/stub.py {name} {port}'

Control API on 127.0.0.1:7070+offset:
  GET  /status
  POST /services/<target>/restart|stop|start    target = service name | commerce | ops | all
  GET  /services/<svc>/logs?lines=200
  POST /clock {"handover_at": iso, "duration_min": 60}   (requires X-Admin-Token)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import secrets
import shlex
import signal
import sys
import tarfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import psutil
from aiohttp import web

from harness.runtime import otel, registry
from harness.runtime.util import JsonlWriter, iso_now, parse_iso, port, write_json_atomic

log = logging.getLogger("supervisor")

REPO_ROOT = Path(__file__).resolve().parents[2]
GROUPS = registry.GROUPS
BACKOFF = (1, 2, 5, 10)
STABLE_AFTER_S = 60
STOP_GRACE_S = 10
SNAPSHOT_EXCLUDE = frozenset({".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache"})
SNAPSHOT_EXCLUDE_TOP = frozenset({"var", "data"})  # runtime state at the repo root
ENV_DROP_PREFIXES = ("UV_", "PYTHON", "VIRTUAL_ENV", "__PYVENV", "CONDA_")
ENV_DROP = frozenset({"LARDER_ADMIN_TOKEN", "LARDER_SIM_SEED"})


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_env_file(path: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path:
        return out
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


@dataclass
class Proc:
    name: str
    kind: str  # "service" | "infra"
    cmd: list[str]
    cwd: Path
    env: dict[str, str]
    log_paths: list[Path]
    ready_port: int | None = None
    health_url: str | None = None
    repo: str | None = None
    index: int | None = None
    app_port: int | None = None
    mesh_port: int | None = None
    stage: int = 0
    # runtime state
    proc: asyncio.subprocess.Process | None = None
    desired: str = "running"
    started_at: str | None = None
    started_mono: float = 0.0
    last_exit_code: int | None = None
    last_exit_at: str | None = None
    restarts: Counter = field(default_factory=Counter)
    up: bool = False
    backoff_idx: int = 0
    expected_exit: bool = False
    restart_pending: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: list = field(default_factory=list)
    cpu_percent: float = 0.0
    rss_bytes: int = 0

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc and self.proc.returncode is None else None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


class Supervisor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.ws = Path(args.workspace).resolve()
        self.art = Path(args.artifacts).resolve()
        self.offset = args.port_offset
        self.run_id = args.run_id
        self.eval_mode = args.eval
        self.shutting_down = False
        self.art.mkdir(parents=True, exist_ok=True)
        (self.art / "logs").mkdir(exist_ok=True)
        (self.ws / ".larder" / "logs").mkdir(parents=True, exist_ok=True)

        env_file = read_env_file(args.env_file)
        # the env-file configures the supervisor itself (OTEL_* creds, optional admin token)
        for k, v in env_file.items():
            if k == "LARDER_ADMIN_TOKEN":
                os.environ.setdefault(k, v)
            else:
                os.environ[k] = v
        os.environ["LARDER_RUN_ID"] = self.run_id
        os.environ["LARDER_PORT_OFFSET"] = str(self.offset)
        os.environ["LARDER_ARTIFACTS_DIR"] = str(self.art)

        self.admin_token = os.environ.get("LARDER_ADMIN_TOKEN") or secrets.token_hex(16)
        os.environ["LARDER_ADMIN_TOKEN"] = self.admin_token
        tok = self.art / "admin_token"
        tok.write_text(self.admin_token + "\n")
        tok.chmod(0o600)

        self.handover_at: dt.datetime | None = None
        self.duration_min: float | None = None
        if not self.eval_mode:
            if args.handover_at:
                self.handover_at = parse_iso(args.handover_at)
            if args.duration_min is not None:
                self.duration_min = float(args.duration_min)

        self.events = JsonlWriter(self.art / "events.jsonl", line_buffered=True)
        self.resources = JsonlWriter(self.art / "resources.jsonl", line_buffered=True)
        self.tel = otel.setup("supervisor", "supervisor")
        self.restart_counter = self.tel.counter("larder_service_restarts", "Service restarts by reason")
        self.tel.gauge("larder_service_up", lambda: [(1 if p.up else 0, {"service": p.name}) for p in self.services()])
        self.tel.gauge(
            "larder_process_cpu_percent", lambda: [(p.cpu_percent, {"service": p.name}) for p in self.procs.values() if p.running]
        )
        self.tel.gauge(
            "larder_process_rss_bytes", lambda: [(p.rss_bytes, {"service": p.name}) for p in self.procs.values() if p.running]
        )
        self.reg = registry.default()
        self.health_timeout = float(os.environ.get("LARDER_HEALTH_TIMEOUT_S", "60") or 60)
        self.snapshot_n: Counter = Counter()
        self.group_lock = asyncio.Lock()
        self.procs: dict[str, Proc] = self._build_procs()
        self._ps_cache: dict[int, psutil.Process] = {}

    # ------------------------------------------------------------------ setup
    def services(self) -> list[Proc]:
        return [self.procs[s] for s in self.reg.names]

    def repo_dir(self, repo: str) -> Path:
        return self.ws / self.reg.repos[repo]["dir"]

    def _base_env(self) -> dict[str, str]:
        venv_bin = str(Path(sys.executable).parent)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ENV_DROP and not k.startswith(ENV_DROP_PREFIXES)
        }
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != venv_bin)
        return env

    def service_env(self, svc: registry.Service, base: dict[str, str] | None = None) -> dict[str, str]:
        """Full environment for one service."""
        off = self.offset
        base = self._base_env() if base is None else base
        env = dict(base)
        env.pop("LARDER_ARTIFACTS_DIR", None)  # artifacts are harness-only
        env.update(self.reg.url_env(off))
        rc = self.reg.repos[svc.repo]
        env.update(
            {
                "PORT": str(svc.app_port(off)),
                "LARDER_SERVICE": svc.name,
                "LARDER_REPO": svc.repo,
                "LARDER_DATA_DIR": str(self.repo_dir(svc.repo) / rc["data_dir"].format(name=svc.name)),
                "LARDER_RUN_ID": self.run_id,
                "LARDER_PORT_OFFSET": str(off),
                "OTEL_EXPORTER_OTLP_PROTOCOL": os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
                "OTEL_METRIC_EXPORT_INTERVAL": os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "10000"),
                "OTEL_SERVICE_NAME": svc.name,
                "OTEL_RESOURCE_ATTRIBUTES": otel.resource_attributes_env(svc.name, repo=svc.repo),
            }
        )
        if svc.repo == "commerce":
            env["PYTHONUNBUFFERED"] = "1"
        return env

    def service_cmd(self, svc: registry.Service) -> list[str]:
        tmpl = os.environ.get("LARDER_SERVICE_CMD_OVERRIDE")
        if tmpl:
            return shlex.split(
                tmpl.format(
                    name=svc.name, repo=svc.repo, module=svc.module or "", path=svc.path or "", index=svc.index,
                    port=svc.app_port(self.offset), mesh_port=svc.mesh_port(self.offset),
                    repo_dir=str(self.repo_dir(svc.repo)),
                )
            )
        if svc.repo == "commerce":
            py = self.repo_dir("commerce") / ".venv" / "bin" / "python"
            return [str(py), "-m", f"services.{svc.module or svc.name.replace('-', '_')}"]
        return ["node", "--require", "./lib/telemetry/otel.js", svc.path or f"services/{svc.name}/server.js"]

    def _build_procs(self) -> dict[str, Proc]:
        off = str(self.offset)
        harness_env = dict(os.environ)
        harness_env["PYTHONUNBUFFERED"] = "1"
        harness_env["PYTHONPATH"] = str(REPO_ROOT)
        sims_env = {k: v for k, v in harness_env.items() if not k.startswith("OTEL_")}
        py = sys.executable
        common = ["--port-offset", off, "--artifacts", str(self.art)]
        procs: dict[str, Proc] = {}

        def infra(name: str, module: str, env: dict, ready: int) -> None:
            procs[name] = Proc(
                name=name,
                kind="infra",
                cmd=[py, "-m", module, *common],
                cwd=REPO_ROOT,
                env=env,
                log_paths=[self.art / "logs" / f"{name}.log"],
                ready_port=ready,
            )

        for sim in self.reg.sims:
            infra(sim.name, sim.module, sims_env, port(sim.admin_port, self.offset))
        # the mesh opens the edge listener last, so the edge port being up means all are
        infra("mesh", "harness.runtime.mesh", harness_env, port("edge", self.offset))

        base = self._base_env()
        for svc in self.reg.services:
            app_port = svc.app_port(self.offset)
            procs[svc.name] = Proc(
                name=svc.name,
                kind="service",
                cmd=self.service_cmd(svc),
                cwd=self.repo_dir(svc.repo),
                env=self.service_env(svc, base),
                log_paths=[self.ws / ".larder" / "logs" / f"{svc.name}.log", self.art / "logs" / f"{svc.name}.log"],
                health_url=f"http://127.0.0.1:{app_port}{svc.health}",
                repo=svc.repo,
                index=svc.index,
                app_port=app_port,
                mesh_port=svc.caller_port(self.offset),
                stage=svc.stage,
            )
        return procs

    def infra_names(self) -> list[str]:
        return [sim.name for sim in self.reg.sims] + ["mesh"]

    # ------------------------------------------------------------------ events
    def event(self, event: str, service: str, message: str | None = None, severity: str = "INFO", **fields: Any) -> None:
        rec = {"ts": iso_now(), "event": event, "service": service, "run_id": self.run_id, **fields}
        self.events.write(rec)
        log.info("%s %s %s", event, service, json.dumps(fields, default=str) if fields else "")
        if message:
            attrs = {"event": event, "service": service}
            attrs.update({k: v for k, v in fields.items() if isinstance(v, (str, int, float, bool))})
            self.tel.log(message, severity, attrs)

    def write_pids(self) -> None:
        write_json_atomic(
            self.art / "pids.json",
            {"supervisor": os.getpid(), **{n: p.pid for n, p in self.procs.items()}},
        )

    # ------------------------------------------------------------------ process control
    async def spawn(self, p: Proc) -> bool:
        if p.kind == "service":
            Path(p.env["LARDER_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
        fhs = [open(lp, "ab", buffering=0) for lp in p.log_paths]
        banner = f"\n=== [larder-supervisor {iso_now()}] starting {p.name}: {' '.join(p.cmd)} ===\n".encode()
        for fh in fhs:
            fh.write(banner)
        try:
            p.proc = await asyncio.create_subprocess_exec(
                *p.cmd,
                cwd=str(p.cwd),
                env=p.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except (OSError, ValueError) as e:
            msg = f"=== [larder-supervisor {iso_now()}] failed to start {p.name}: {e} ===\n".encode()
            for fh in fhs:
                fh.write(msg)
                fh.close()
            p.proc = None
            p.last_exit_code = 127
            p.last_exit_at = iso_now()
            self.event("spawn_failed", p.name, f"service exited code=127", "ERROR", error=str(e), code=127)
            return False
        p.started_at = iso_now()
        p.started_mono = time.monotonic()
        p.expected_exit = False
        self.event("start", p.name, "service started", pid=p.proc.pid, kind=p.kind)
        self.write_pids()
        p.tasks = [
            asyncio.create_task(self._pump(p.proc, fhs)),
            asyncio.create_task(self._watch(p, p.proc)),
        ]
        return True

    async def _pump(self, proc: asyncio.subprocess.Process, fhs) -> None:
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                for fh in fhs:
                    try:
                        fh.write(chunk)
                    except OSError:
                        pass
        finally:
            for fh in fhs:
                try:
                    fh.close()
                except OSError:
                    pass

    async def _watch(self, p: Proc, proc: asyncio.subprocess.Process) -> None:
        code = await proc.wait()
        p.last_exit_code = code
        p.last_exit_at = iso_now()
        p.up = False
        uptime = time.monotonic() - p.started_mono
        self.event(
            "exit", p.name, f"service exited code={code}", "INFO" if p.expected_exit else "ERROR",
            code=code, pid=proc.pid, uptime_s=round(uptime, 1), expected=p.expected_exit,
        )
        self._kill_group(proc.pid, signal.SIGKILL)  # stragglers in the old process group
        if p.expected_exit or self.shutting_down or p.desired != "running":
            return
        # crash: auto restart with backoff
        if uptime >= STABLE_AFTER_S:
            p.backoff_idx = 0
        delay = BACKOFF[min(p.backoff_idx, len(BACKOFF) - 1)]
        p.backoff_idx += 1
        asyncio.create_task(self._crash_restart(p, delay, code))

    async def _crash_restart(self, p: Proc, delay: float, code: int | None) -> None:
        p.restart_pending = True
        try:
            await asyncio.sleep(delay)
            async with p.lock:
                if self.shutting_down or p.desired != "running" or p.running:
                    return
                p.restarts["crash"] += 1
                self.restart_counter.add(1, {"service": p.name, "reason": "crash"})
                self.event("restart", p.name, "service restart requested", "WARN", reason="crash", backoff_s=delay, exit_code=code)
                ok = await self.spawn(p)
            if not ok and p.desired == "running" and not self.shutting_down:
                nd = BACKOFF[min(p.backoff_idx, len(BACKOFF) - 1)]
                p.backoff_idx += 1
                asyncio.create_task(self._crash_restart(p, nd, 127))
        finally:
            p.restart_pending = False

    @staticmethod
    def _kill_group(pid: int, sig: int) -> None:
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    async def terminate(self, p: Proc) -> None:
        proc = p.proc
        if proc is None or proc.returncode is not None:
            return
        p.expected_exit = True
        self._kill_group(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), STOP_GRACE_S)
        except asyncio.TimeoutError:
            self.event("kill", p.name, None, pid=proc.pid, reason="stop_timeout")
            self._kill_group(proc.pid, signal.SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                log.error("pid %s did not die after SIGKILL", proc.pid)
        self._kill_group(proc.pid, signal.SIGKILL)
        for t in p.tasks:
            try:
                await asyncio.wait_for(asyncio.shield(t), 2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def snapshot(self, repo: str, reason: str, service: str | None = None) -> str | None:
        """Tarball of a repo's source (excludes .venv/node_modules and the runtime var/ or data/ dir)."""
        src = self.repo_dir(repo)
        if self.eval_mode or not src.is_dir():
            return None
        self.snapshot_n[repo] += 1
        seq = self.snapshot_n[repo]
        dest = self.art / "snapshots" / repo / f"{utc_stamp()}-{seq}.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _make() -> tuple[int, int]:
            nfiles = 0

            def _filter(ti: tarfile.TarInfo):
                nonlocal nfiles
                parts = Path(ti.name).parts
                if any(part in SNAPSHOT_EXCLUDE for part in parts) or ti.name.endswith(".pyc"):
                    return None
                if len(parts) >= 2 and parts[1] in SNAPSHOT_EXCLUDE_TOP:
                    return None
                if ti.isfile():
                    nfiles += 1
                return ti

            with tarfile.open(dest, "w:gz", compresslevel=6) as tf:
                tf.add(str(src), arcname=repo, filter=_filter)
            return nfiles, dest.stat().st_size

        t0 = time.monotonic()
        try:
            nfiles, size = await asyncio.to_thread(_make)
        except Exception as e:
            self.event("snapshot_failed", service or repo, None, "ERROR", error=str(e), repo=repo)
            return None
        rel = str(dest.relative_to(self.art))
        self.event(
            "snapshot", service or repo, f"codebase snapshot {rel}", path=rel, files=nfiles, bytes=size, repo=repo,
            reason=reason, seq=seq, took_ms=round((time.monotonic() - t0) * 1000),
        )
        return rel

    # API-level operations (services only)
    async def api_restart(self, p: Proc) -> dict:
        async with p.lock:
            p.desired = "running"
            p.restarts["agent_request"] += 1
            self.restart_counter.add(1, {"service": p.name, "reason": "agent_request"})
            self.event("restart", p.name, "service restart requested", reason="agent_request", pid=p.pid)
            await self.terminate(p)
            snap = await self.snapshot(p.repo, "agent_restart", p.name)
            p.backoff_idx = 0
            ok = await self.spawn(p)
        return {"ok": ok, "service": p.name, "pid": p.pid, "snapshot": snap}

    async def api_stop(self, p: Proc) -> dict:
        async with p.lock:
            p.desired = "stopped"
            self.event("stop", p.name, "service stop requested", reason="agent_request", pid=p.pid)
            await self.terminate(p)
            p.up = False
        return {"ok": True, "service": p.name, "running": p.running}

    async def api_start(self, p: Proc) -> dict:
        async with p.lock:
            p.desired = "running"
            if p.running:
                return {"ok": True, "service": p.name, "pid": p.pid, "already_running": True}
            self.event("start_requested", p.name, None, reason="agent_request")
            snap = await self.snapshot(p.repo, "agent_start", p.name)
            p.backoff_idx = 0
            ok = await self.spawn(p)
        return {"ok": ok, "service": p.name, "pid": p.pid, "snapshot": snap}

    # group operations: target = commerce | ops | all, executed in dependency order
    async def start_stages(self, procs: list[Proc], timeout: float, reason: str | None = None) -> dict[str, bool]:
        """Start (not-running) services stage by stage; each stage waits for health up to `timeout`."""
        ready: dict[str, bool] = {}
        sel = [self.reg.get(p.name) for p in procs]
        for stage in self.reg.stages(sel):
            started = []
            for svc in stage:
                p = self.procs[svc.name]
                p.desired = "running"
                if p.running:
                    started.append(p)
                    continue
                p.backoff_idx = 0
                if await self.spawn(p):
                    started.append(p)
                else:
                    ready[p.name] = False
                    asyncio.create_task(self._crash_restart(p, 1, 127))
            oks = await asyncio.gather(*(self.wait_ready(p, timeout) for p in started))
            for p, ok in zip(started, oks):
                ready[p.name] = ok
                if not ok:
                    self.event("not_ready", p.name, f"{p.name} not ready after {timeout:g}s", "WARN", reason=reason)
        return ready

    async def api_group(self, action: str, target: str) -> dict:
        procs = [self.procs[s.name] for s in self.reg.resolve(target)]
        repos = list(dict.fromkeys(p.repo for p in procs))
        async with self.group_lock, contextlib.AsyncExitStack() as stack:
            for p in sorted(procs, key=lambda q: q.name):  # fixed order: no deadlock with other group ops
                await stack.enter_async_context(p.lock)
            if action == "stop":
                for p in procs:
                    p.desired = "stopped"
                    self.event("stop", p.name, "service stop requested", reason="agent_request", pid=p.pid, group=target)
                await asyncio.gather(*(self.terminate(p) for p in reversed(procs)))
                for p in procs:
                    p.up = False
                return {"ok": True, "target": target, "services": {p.name: {"running": p.running} for p in procs}}
            snaps = {}
            if action == "restart":
                for p in procs:
                    p.desired = "running"
                    p.restarts["agent_request"] += 1
                    self.restart_counter.add(1, {"service": p.name, "reason": "agent_request"})
                    self.event("restart", p.name, "service restart requested", reason="agent_request", pid=p.pid, group=target)
                await asyncio.gather(*(self.terminate(p) for p in reversed(procs)))
                for p in procs:
                    p.up = False
                for r in repos:
                    snaps[r] = await self.snapshot(r, "agent_restart", target)
            else:  # start
                if any(not p.running for p in procs):
                    for r in repos:
                        snaps[r] = await self.snapshot(r, "agent_start", target)
                self.event("start_requested", target, None, reason="agent_request")
            ready = await self.start_stages(procs, min(self.health_timeout, 30.0), reason=f"group_{action}")
        return {
            "ok": all(ready.values()),
            "target": target,
            "snapshots": snaps,
            "services": {p.name: {"pid": p.pid, "up": p.up, "ready": ready.get(p.name, False)} for p in procs},
        }

    # ------------------------------------------------------------------ monitoring
    async def health_loop(self) -> None:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as s:

            async def check(p: Proc) -> None:
                up = False
                if p.running and p.health_url:
                    try:
                        async with s.get(p.health_url) as r:
                            up = r.status == 200
                    except Exception:
                        up = False
                if up != p.up:
                    self.event("health", p.name, None, up=up)
                p.up = up

            while not self.shutting_down:
                await asyncio.gather(*(check(p) for p in self.services()))
                await asyncio.sleep(2)

    def _sample_tree(self, pid: int) -> tuple[float, int, int]:
        try:
            root = self._ps_cache.get(pid) or psutil.Process(pid)
            self._ps_cache[pid] = root
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            return 0.0, 0, 0
        cpu = 0.0
        rss = 0
        for pr in procs:
            cached = self._ps_cache.setdefault(pr.pid, pr)
            try:
                cpu += cached.cpu_percent(None)
                rss += cached.memory_info().rss
            except psutil.Error:
                self._ps_cache.pop(pr.pid, None)
        return cpu, rss, len(procs)

    def _sample_all(self) -> list[tuple[Proc, int, float, int, int]]:
        out = []
        for p in list(self.procs.values()):
            pid = p.pid
            if pid is None:
                continue
            cpu, rss, n = self._sample_tree(pid)
            out.append((p, pid, cpu, rss, n))
        for pid in list(self._ps_cache):
            if not psutil.pid_exists(pid):
                self._ps_cache.pop(pid, None)
        return out

    async def resource_loop(self) -> None:
        # first pass only primes psutil's cpu_percent counters
        first = True
        while not self.shutting_down:
            ts = iso_now()
            for p in self.procs.values():
                if p.pid is None:
                    p.cpu_percent, p.rss_bytes = 0.0, 0
            for p, pid, cpu, rss, n in await asyncio.to_thread(self._sample_all):
                p.cpu_percent, p.rss_bytes = round(cpu, 1), rss
                if not first:
                    self.resources.write(
                        {"ts": ts, "service": p.name, "pid": pid, "cpu_percent": p.cpu_percent, "rss_bytes": rss, "procs": n}
                    )
            await asyncio.sleep(1 if first else 10)
            first = False

    # ------------------------------------------------------------------ clock
    def clock(self) -> dict:
        if self.eval_mode:
            return {"handover_at": None, "deadline": None, "duration_min": None, "minutes_remaining": None}
        deadline = None
        remaining = None
        if self.handover_at and self.duration_min is not None:
            d = self.handover_at + dt.timedelta(minutes=self.duration_min)
            deadline = d.isoformat().replace("+00:00", "Z")
            remaining = round(max(0.0, (d - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60.0), 1)
        return {
            "handover_at": self.handover_at.isoformat().replace("+00:00", "Z") if self.handover_at else None,
            "deadline": deadline,
            "duration_min": self.duration_min,
            "minutes_remaining": remaining,
        }

    # ------------------------------------------------------------------ control API
    def status(self) -> dict:
        svcs = {}
        for p in self.services():
            svcs[p.name] = {
                "index": p.index,
                "repo": p.repo,
                "group": p.repo,
                "port": p.app_port,
                "mesh_port": p.mesh_port,
                "pid": p.pid,
                "up": p.up,
                "running": p.running,
                "desired": p.desired,
                "restarts": sum(p.restarts.values()),
                "restarts_by_reason": dict(p.restarts),
                "started_at": p.started_at,
                "last_exit_code": p.last_exit_code,
                "last_exit_at": p.last_exit_at,
                "restart_pending": p.restart_pending,
                "log": str(p.log_paths[0]),
            }
        infra = {n: {"pid": self.procs[n].pid, "running": self.procs[n].running} for n in self.infra_names()}
        return {
            "run_id": self.run_id,
            "mode": "eval" if self.eval_mode else "run",
            "groups": {g: [s.name for s in self.reg.resolve(g)] for g in GROUPS},
            "services": svcs,
            "infra": infra,
            **self.clock(),
        }

    def make_app(self) -> web.Application:
        app = web.Application()

        def unknown(name: str):
            return web.HTTPNotFound(
                text=json.dumps({"error": "unknown service", "services": list(self.reg.names), "groups": list(GROUPS)}),
                content_type="application/json",
            )

        def svc(request: web.Request) -> Proc:
            name = request.match_info["svc"]
            if name not in self.reg.names:
                raise unknown(name)
            return self.procs[name]

        def action(name: str, single):
            async def h(r: web.Request):
                target = r.match_info["svc"]
                if target in GROUPS:
                    return web.json_response(await self.api_group(name, target))
                return web.json_response(await single(svc(r)))

            return h

        async def status(_r):
            return web.json_response(self.status())

        restart = action("restart", self.api_restart)
        stop = action("stop", self.api_stop)
        start = action("start", self.api_start)

        async def logs(r):
            p = svc(r)
            try:
                n = max(1, min(int(r.query.get("lines", "200")), 5000))
            except ValueError:
                n = 200
            return web.Response(text=tail(p.log_paths[0], n), content_type="text/plain")

        async def set_clock(r):
            if not secrets.compare_digest(r.headers.get("X-Admin-Token", ""), self.admin_token):
                return web.json_response({"error": "forbidden"}, status=403)
            if self.eval_mode:
                return web.json_response({"error": "no clock in eval mode"}, status=409)
            try:
                body = await r.json()
                if body.get("handover_at"):
                    self.handover_at = parse_iso(body["handover_at"])
                if body.get("duration_min") is not None:
                    self.duration_min = float(body["duration_min"])
            except Exception as e:
                return web.json_response({"error": f"bad request: {e}"}, status=400)
            self.event("clock", "supervisor", "run clock set", **{k: v for k, v in self.clock().items()})
            return web.json_response(self.clock())

        async def healthz(_r):
            return web.json_response({"ok": True})

        app.router.add_get("/status", status)
        app.router.add_get("/healthz", healthz)
        app.router.add_post("/services/{svc}/restart", restart)
        app.router.add_post("/services/{svc}/stop", stop)
        app.router.add_post("/services/{svc}/start", start)
        app.router.add_get("/services/{svc}/logs", logs)
        app.router.add_post("/clock", set_clock)
        return app

    # ------------------------------------------------------------------ main
    async def wait_ready(self, p: Proc, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and p.running:
            if p.health_url:
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as s:
                        async with s.get(p.health_url) as r:
                            if r.status == 200:
                                p.up = True
                                return True
                except Exception:
                    pass
            elif p.ready_port:
                try:
                    _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", p.ready_port), 1)
                    w.close()
                    return True
                except Exception:
                    pass
            await asyncio.sleep(0.25)
        return False

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        stop_ev = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, stop_ev.set)

        runner = web.AppRunner(self.make_app(), access_log=None, handle_signals=False)
        await runner.setup()
        cport = port("control", self.offset)
        await web.TCPSite(runner, "127.0.0.1", cport, reuse_address=True).start()
        control_url = f"http://127.0.0.1:{cport}"
        ctl = self.ws / ".larder" / "control.env"
        ctl.write_text(f"LARDER_CONTROL_URL={control_url}\n")
        self.event(
            "supervisor_start", "supervisor", "supervisor started", control_url=control_url,
            mode="eval" if self.eval_mode else "run", workspace=str(self.ws), port_offset=self.offset,
        )

        if not self.eval_mode:
            for repo in self.reg.repos:
                await self.snapshot(repo, "initial")

        for name in self.infra_names():
            if stop_ev.is_set():
                break
            p = self.procs[name]
            async with p.lock:
                ok = await self.spawn(p)
            if not ok:
                asyncio.create_task(self._crash_restart(p, 1, 127))
                continue
            if not await self.wait_ready(p, 15):
                self.event("not_ready", name, f"{name} not ready after startup wait", "WARN")
        if not stop_ev.is_set():
            start_task = asyncio.create_task(self.start_stages(self.services(), self.health_timeout, reason="startup"))
            stop_wait = asyncio.create_task(stop_ev.wait())
            await asyncio.wait({start_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
            stop_wait.cancel()
            if start_task.done():
                ready = start_task.result()
                bad = sorted(n for n, ok in ready.items() if not ok)
                self.event("startup_complete", "supervisor", "all services started", healthy=len(ready) - len(bad),
                           unhealthy=",".join(bad))
                if bad:
                    print(f"supervisor: services not healthy after startup: {', '.join(bad)}", flush=True)
            else:
                start_task.cancel()
        print(f"supervisor ready control={control_url} run_id={self.run_id}", flush=True)

        bg = [asyncio.create_task(self.health_loop()), asyncio.create_task(self.resource_loop())]
        await stop_ev.wait()
        await self.shutdown(bg, runner, ctl)

    async def shutdown(self, bg, runner, ctl: Path) -> None:
        self.shutting_down = True
        self.event("supervisor_stop", "supervisor", "supervisor stopping")
        for p in self.procs.values():
            p.desired = "stopped"
        await asyncio.gather(*(self.terminate(p) for p in self.services()))
        await asyncio.gather(*(self.terminate(self.procs[n]) for n in self.infra_names()))
        for t in bg:
            t.cancel()
        await runner.cleanup()
        try:
            ctl.unlink()
        except OSError:
            pass
        self.write_pids()
        self.events.close()
        self.resources.close()
        self.tel.shutdown()


def tail(path: Path, n: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = 64 * 1024
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= n:
                step = min(block, pos)
                pos -= step
                fh.seek(pos)
                data = fh.read(step) + data
        lines = data.splitlines()[-n:]
        return "\n".join(l.decode("utf-8", "replace") for l in lines) + ("\n" if lines else "")
    except FileNotFoundError:
        return ""


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--port-offset", type=int, default=int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0))
    ap.add_argument("--env-file")
    ap.add_argument("--handover-at", help="ISO-8601 UTC time the agent got the workspace")
    ap.add_argument("--duration-min", type=float)
    ap.add_argument("--eval", action="store_true", help="acceptance mode: no run clock, no snapshots")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s supervisor %(levelname)s %(message)s")
    sup = Supervisor(args)
    asyncio.run(sup.run())


if __name__ == "__main__":
    main()
