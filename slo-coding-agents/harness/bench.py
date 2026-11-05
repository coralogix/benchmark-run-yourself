"""Operator CLI for the Larder SLO-guidance benchmark.

    ./bench doctor
    ./bench agent-key  --condition basic|slo-guided          # one-off: read-only key + cx profile for the evaluated agent
    ./bench prepare    --condition basic|slo-guided [--port-offset N] [--warmup-min 15] [--run-min 60]
    ./bench start      <run-id> [--wait]                     # supervisor + workload; --wait blocks until handover
    ./bench handover   <run-id>                              # start the agent clock (--run-min minutes) now
    ./bench run        <run-id> --agent-cmd "..."            # start, wait for warm-up, hand over, run agent with a hard timeout, stop
    ./bench status     <run-id>
    ./bench stop       <run-id>                              # freeze: stop workload and services, collect diffs + report
    ./bench evaluate   <run-id>                              # fresh-state acceptance tests against the agent's final code
    ./bench collect    <run-id>                              # Coralogix alert events + SLO status, local SLIs, resources
    ./bench score      <run-id>                              # results/<run-id>.json + .md
    ./bench verify-isolation <run-id>
    ./bench teardown   <run-id> [--keep-workspace] [--keep-cx]

Configuration: harness/config/accounts.env (see accounts.env.example). No
credential is ever written into the repository, the run directory or the
agent workspace.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
BASELINE = ROOT / "baseline"
KIT = ROOT / "agent-kit"
CONFIG = ROOT / "harness" / "config" / "accounts.env"
CODEBASES = ("commerce", "ops")
CONDITIONS = ("basic", "slo-guided")
COPY_IGNORE = shutil.ignore_patterns(".venv", "node_modules", "var", "__pycache__", ".pytest_cache", "*.pyc", ".git")
UV = shutil.which("uv") or "uv"
NPM = shutil.which("npm") or "npm"


# ------------------------------------------------------------------ helpers

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd, cwd=None, env=None, check=True, capture=False, timeout=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=capture, timeout=timeout)
    if check and p.returncode != 0:
        msg = (p.stderr or "")[-2000:] if capture else ""
        raise SystemExit(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n{msg}")
    return p


def load_config() -> dict:
    cfg = {}
    if CONFIG.exists():
        for ln in CONFIG.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    for k, v in os.environ.items():
        if k.startswith("LARDER_"):
            cfg[k] = v
    return cfg


def cond_key(condition: str) -> str:
    return {"basic": "BASIC", "slo-guided": "SLO"}[condition]


def account(cfg: dict, condition: str) -> dict:
    k = cond_key(condition)
    return {
        "operator_profile": cfg.get(f"LARDER_{k}_OPERATOR_PROFILE"),
        "agent_profile": cfg.get(f"LARDER_{k}_AGENT_PROFILE"),
        "ingest_key_id": cfg.get(f"LARDER_{k}_INGEST_KEY_ID"),
        "ingest_key_env": f"LARDER_{k}_INGEST_KEY",
    }


def profile_region(profile: str) -> str:
    p = Path.home() / ".cx" / "profiles" / f"{profile}.toml"
    for ln in p.read_text().splitlines():
        if ln.strip().startswith("region"):
            return ln.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"no region in cx profile {profile}")


def cx_json(profile: str, *args: str):
    p = sh(["cx", "-p", profile, "--no-console-link", "-o", "json", *args], capture=True)
    out = p.stdout.strip()
    if not out:
        return None
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0], default=0)
    return json.loads(out[start:])


def fetch_ingest_key(cfg: dict, condition: str) -> str:
    acc = account(cfg, condition)
    if os.environ.get(acc["ingest_key_env"]):
        return os.environ[acc["ingest_key_env"]]
    if not acc["ingest_key_id"]:
        raise SystemExit(f"set LARDER_{cond_key(condition)}_INGEST_KEY_ID in {CONFIG} (id of a SendData key) or export {acc['ingest_key_env']}")
    d = cx_json(acc["operator_profile"], "iam", "api-keys", "get", acc["ingest_key_id"])
    return d["keyInfo"]["value"]


def run_dir(run_id: str) -> Path:
    d = RUNS / run_id
    if not (d / "run.json").exists():
        raise SystemExit(f"unknown run {run_id}")
    return d


def load_run(run_id: str) -> dict:
    return json.loads((run_dir(run_id) / "run.json").read_text())


def save_run(meta: dict):
    (RUNS / meta["run_id"] / "run.json").write_text(json.dumps(meta, indent=2))


def secrets_path(run_id: str) -> Path:
    # Per-run admin token for the simulators. Lives outside the workspace, mode 0600.
    return RUNS / run_id / ".admin_token"


def ports(offset: int) -> dict:
    return {"edge": 8000 + offset, "orders_mesh": 8211 + offset, "psp": 8090 + offset, "psp_admin": 9090 + offset,
            "partner": 8091 + offset, "partner_admin": 9091 + offset, "email": 8092 + offset,
            "email_admin": 9092 + offset, "control": 7070 + offset}


def all_ports(offset: int) -> set[int]:
    """Every port one trial instance listens on."""
    base = {7070, 8000, 8090, 8091, 8092, 9090, 9091, 9092}
    base |= {8100 + i for i in range(1, 22)} | {8200 + i for i in range(1, 22)}
    return {b + offset for b in base}


def check_offset_free(offset: int, run_id: str):
    for other in RUNS.glob("*/run.json"):
        m = json.loads(other.read_text())
        if m["run_id"] == run_id or not pid_alive(m.get("supervisor_pid")):
            continue
        clash = all_ports(offset) & all_ports(m["port_offset"])
        if clash:
            raise SystemExit(f"port offset {offset} collides with live run {m['run_id']} (offset {m['port_offset']}), "
                             f"e.g. port {min(clash)}. Use offsets 2000 apart (0, 2000, 4000).")


def http_json(method: str, url: str, body=None, headers=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in (".venv", "node_modules", ".git", "var", "data", "__pycache__", ".larder") for part in rel.parts):
            continue
        if p.is_file():
            h.update(str(rel).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ agent kit

def render_instructions(condition: str, meta: dict) -> str:
    tpl = (KIT / "AGENT_INSTRUCTIONS.template.md").read_text()
    block = (KIT / condition / "CONDITION_BLOCK.md").read_text()
    return (tpl.replace("{{CONDITION_BLOCK}}", block.rstrip() + "\n")
               .replace("{{RUN_MINUTES}}", str(meta["run_min"]))
               .replace("{{RUN_ID}}", meta["run_id"]))


def write_slo_catalog(ws: Path, manifest: dict, run_id: str):
    from harness.cx import sli as sli_mod
    by_key = {s.key: s for s in sli_mod.MEASURED_SLIS}
    entries = []
    for s in manifest.get("slos", []):
        d = by_key[s["sli"]]
        entries.append({
            "id": s["id"], "name": s["name"], "sli": d.key, "service": d.service, "criticality": d.criticality,
            "target": d.target, "description": d.description, "slo_type": "window" if d.kind == "window" else "request",
            "good_events_promql": sli_mod.good_total_promql(d, run_id)[0],
            "total_events_promql": sli_mod.good_total_promql(d, run_id)[1],
            "burn_rate_2m": sli_mod.burn_rate_promql(d, run_id, "2m"),
            "burn_rate_10m": sli_mod.burn_rate_promql(d, run_id, "10m"),
            "burn_rate_60m": sli_mod.burn_rate_promql(d, run_id, "60m"),
            "budget_remaining_60m": sli_mod.budget_remaining_promql(d, run_id, "60m"),
            "total_10m": (f"sum({sli_mod.good_total_promql(d, run_id, '10m')[1]})" if d.group_by
                          else sli_mod.good_total_promql(d, run_id, "10m")[1]),
            "group_by": d.group_by,
            "worst_entities_60m": sli_mod.worst_entities_promql(d, run_id, "60m", 5) if d.group_by else None,
            "alerts": [a["name"] for a in manifest.get("alerts", []) if a.get("asset_class") == "slo"
                       and a["name"].startswith("Larder · SLO · " + s["name"].split("· ", 1)[1] + " ·")],
        })
    (ws / ".larder" / "slo_catalog.json").write_text(json.dumps({"run_id": run_id, "slos": entries}, indent=2))
    lines = [
        "# Larder SLO catalogue",
        "",
        "Service level objectives for the Larder platform. Every SLO is measured at the ingress proxy in front of",
        "each service (the customer edge for the public API, the internal mesh for internal services), over a rolling 1-day window.",
        "Error budget = 100% - target. Burn rate 1.0 means the budget is being consumed exactly as fast as the",
        "target allows; a burn rate of 10 would exhaust a full day's budget in about 2.4 hours.",
        "",
        "How to read them:",
        "",
        "- `tools/slo-status` - attainment, remaining budget, live burn rates and which SLO alerts are firing",
        "- Coralogix dashboard **Larder · SLOs and error budgets**",
        "- `cx slos list` / `cx slos get <id>` for definitions",
        "",
        "SLO alerts (Coralogix, `cx alerts list` / `cx alerts events --start now-15m`), per SLO:",
        "",
        "| Alert | Fires when | Priority | Reacts within |",
        "|---|---|---|---|",
        "| `Larder · SLO · <slo> · fast burn` | burn rate >= 10x over BOTH the last 2 min and the last 10 min | P1 | ~2-4 min |",
        "| `Larder · SLO · <slo> · slow burn` | burn rate >= 2x over BOTH the last 10 min and the last 60 min | P2 | ~5-15 min |",
        "| `Larder · SLO · <slo> · session budget` | rolling 60-minute error budget below 50% (P3) or exhausted (P2) | P3/P2 | ~2-5 min |",
        "| `Larder · SLO · <slo> · error budget (SLO engine)` | Coralogix SLO engine's remaining budget <= 50% / 25% / 0% | P3/P2/P1 | ~8-12 min (engine delay) |",
        "| `Larder · SLO · <slo> · burn rate (SLO engine)` | SLO engine burn rate at or above the P1 / P2 threshold (14.4x / 6x, lower for loose targets) over both a 1-hour window and its short window | P1/P2 | ~8-15 min (engine delay) |",
        "",
        "Burn rate 10 sustained for 6 minutes spends a whole hour's budget. The metric-based alerts read the",
        "ingress metrics directly and react fastest; the SLO-engine alert is authoritative but lags.",
    ]
    grouped = [e for e in entries if e["group_by"]]
    if grouped:
        if len(grouped) == len(entries):
            grouped_note = f"All {len(grouped)} SLOs are grouped by entity (one permutation per product, customer or postcode)."
        else:
            grouped_note = f"{len(grouped)} of the SLOs are grouped by entity (one permutation per product, customer or postcode)."
        elig = "; ".join(
            f"{e['name'].split('· ')[1]}: at least {by_key[e['sli']].min_entity_events} request"
            f"{'s' if by_key[e['sli']].min_entity_events != 1 else ''}"
            + (" in the last hour" if by_key[e['sli']].min_entity_events > sli_mod.MIN_ENTITY_EVENTS else " in the window")
            for e in grouped)
        lines += [
            "",
            grouped_note + " For those, the status, burn rates and budget are those of the WORST entity with",
            f"enough traffic to count ({elig}), and `tools/slo-status` lists the five worst entities. An aggregate",
            "view can look healthy while a group of entities is failing completely.",
        ]
    lines += [
        "",
        "Request-based SLOs count good requests out of all requests. Window-based SLOs count good one-minute",
        "windows out of all minutes with traffic: a minute is good when the statistic stays within its threshold.",
        "",
        "| SLO | Type | Criticality | Target | What counts as good |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(f"| {e['name']} | {e['slo_type']} | {e['criticality']} | {e['target']:g}% | {e['description']} |")
    lines += ["", "PromQL behind each SLO (good / total events, per minute):", ""]
    for e in entries:
        lines += [f"- **{e['name']}** (`{e['id']}`)", f"  - good: `{e['good_events_promql']}`", f"  - total: `{e['total_events_promql']}`"]
    (ws / "SLO_CATALOG.md").write_text("\n".join(lines) + "\n")


def build_workspace(meta: dict, cfg: dict) -> Path:
    ws = Path(meta["workspace"])
    if ws.exists():
        raise SystemExit(f"workspace {ws} already exists")
    ws.mkdir(parents=True)
    (ws / ".larder" / "logs").mkdir(parents=True)
    for cb in CODEBASES:
        dst = ws / cb
        shutil.copytree(BASELINE / cb, dst, ignore=COPY_IGNORE)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Larder Baseline", "GIT_AUTHOR_EMAIL": "baseline@larder.test",
               "GIT_COMMITTER_NAME": "Larder Baseline", "GIT_COMMITTER_EMAIL": "baseline@larder.test",
               "GIT_AUTHOR_DATE": "2026-01-05T09:00:00Z", "GIT_COMMITTER_DATE": "2026-01-05T09:00:00Z"}
        sh(["git", "init", "-q", "-b", "main"], cwd=dst)
        sh(["git", "add", "-A"], cwd=dst, env=env)
        sh(["git", "commit", "-q", "-m", f"{cb}: state as of release 2026.01"], cwd=dst, env=env)
        sh(["git", "tag", "baseline"], cwd=dst)
    print("  installing commerce deps (uv sync)...")
    sh([UV, "sync", "--frozen", "-q"], cwd=ws / "commerce")
    print("  installing ops deps (npm ci)...")
    sh([NPM, "ci", "--no-audit", "--no-fund", "--loglevel=error"], cwd=ws / "ops")

    shutil.copy(KIT / "common" / "BACKLOG.md", ws / "BACKLOG.md")
    shutil.copy(KIT / "common" / "WORKSPACE.md", ws / "WORKSPACE.md")
    shutil.copy(KIT / "common" / "REPORT_TEMPLATE.md", ws / "REPORT_TEMPLATE.md")
    shutil.copy(KIT / "common" / "larderctl", ws / "larderctl")
    os.chmod(ws / "larderctl", 0o755)
    (ws / "AGENT_INSTRUCTIONS.md").write_text(render_instructions(meta["condition"], meta))
    (ws / "tools").mkdir()
    for tools in (KIT / "common" / "tools", KIT / meta["condition"] / "tools"):
        if tools.exists():
            shutil.copytree(tools, ws / "tools", dirs_exist_ok=True)
    for t in (ws / "tools").iterdir():
        os.chmod(t, 0o755)
    acc = account(cfg, meta["condition"])
    (ws / ".larder" / "agent.env").write_text(
        f"CX_PROFILE={acc['agent_profile'] or ''}\nLARDER_RUN_ID={meta['run_id']}\n"
        f"LARDER_CONTROL_URL=http://127.0.0.1:{ports(meta['port_offset'])['control']}\n"
        f"LARDER_REVIEW_URL=http://127.0.0.1:{7170 + meta['port_offset']}\n")
    return ws


# ------------------------------------------------------------------ commands

def cmd_doctor(a):
    cfg = load_config()
    ok = True
    for tool in ("cx", "uv", "node", "npm", "git"):
        found = shutil.which(tool)
        print(f"{'ok ' if found else 'MISSING'} {tool} {found or ''}")
        ok &= bool(found)
    print(f"config: {CONFIG} {'(found)' if CONFIG.exists() else '(MISSING - copy accounts.env.example)'}")
    for c in CONDITIONS:
        acc = account(cfg, c)
        for role in ("operator_profile", "agent_profile"):
            prof = acc[role]
            exists = bool(prof) and (Path.home() / ".cx" / "profiles" / f"{prof}.toml").exists()
            print(f"{'ok ' if exists else 'MISSING'} {c:10} {role:17} {prof or '-'}")
            ok &= exists
        print(f"{'ok ' if acc['ingest_key_id'] or os.environ.get(acc['ingest_key_env']) else 'MISSING'} {c:10} ingest key       {'id ' + acc['ingest_key_id'] if acc['ingest_key_id'] else acc['ingest_key_env']}")
    b, s = account(cfg, "basic"), account(cfg, "slo-guided")
    if b["operator_profile"] and b["operator_profile"] == s["operator_profile"]:
        print("WARNING: both conditions use the same Coralogix account. SLOs would be visible to the basic agent. "
              "Use two accounts for real trials (single-account mode is only for harness validation).")
    sys.exit(0 if ok else 1)


AGENT_READ_PERMISSIONS = [
    "logs.data-api#high:ReadData", "logs.data-api#low:ReadData",
    "metrics.data-api#high:ReadData", "metrics.data-api#low:ReadData",
    "spans.data-api#high:ReadData", "spans.data-api#low:ReadData",
    "alerts:ReadConfig", "alerts:MapRead", "alerts:LogsReadConfig", "alerts:SpansReadConfig", "alerts:MetricsReadConfig",
    "team-dashboards:Read", "service-catalog:Read", "service-map:Read", "livetail:Read",
    "incidents:Read", "cases:Read", "team-saved-views:Read",
]
AGENT_SLO_PERMISSIONS = ["slo:ReadConfig", "alerts:SloMgmtReadConfig"]


def cmd_agent_key(a):
    cfg = load_config()
    acc = account(cfg, a.condition)
    op, agent = acc["operator_profile"], acc["agent_profile"]
    if not op or not agent:
        raise SystemExit("set operator and agent profile names in accounts.env first")
    keys = cx_json(op, "iam", "api-keys", "list") or []
    team = next((k.get("owner", {}).get("teamId") for k in keys if isinstance(k.get("owner"), dict) and k["owner"].get("teamId")), None)
    if team is None:
        raise SystemExit("could not determine teamId from existing team keys")
    perms = AGENT_READ_PERMISSIONS + ([] if a.condition == "basic" else AGENT_SLO_PERMISSIONS)
    body = {"name": f"larder-bench-agent-{a.condition}-readonly", "owner": {"teamId": team}, "hashed": False,
            "keyPermissions": {"permissions": perms, "presets": []}}
    tmp = RUNS / f".agentkey-{secrets.token_hex(4)}.json"
    RUNS.mkdir(exist_ok=True)
    tmp.write_text(json.dumps(body))
    try:
        res = cx_json(op, "iam", "api-keys", "create", "--from-file", str(tmp), "--yes")
    finally:
        tmp.unlink()
    key_id = res.get("key_id") or res.get("keyId") or res.get("id") or (res.get("keyInfo") or {}).get("id") or next(
        (k["id"] for k in (cx_json(op, "iam", "api-keys", "list") or []) if k.get("name") == body["name"]), None)
    value = res.get("value") or res.get("key") or (res.get("keyInfo") or {}).get("value")
    if not value and key_id:
        value = cx_json(op, "iam", "api-keys", "get", key_id)["keyInfo"]["value"]
    if not value:
        raise SystemExit(f"key created but value not returned: {json.dumps(res)[:300]}")
    sh(["cx", "profiles", "add", "--name", agent, "--region", profile_region(op), "--api-key", value, "--force"], capture=True)
    print(f"created read-only key {key_id} and cx profile '{agent}' ({len(perms)} permissions)")


def cmd_prepare(a):
    cfg = load_config()
    stamp = now_utc().strftime("%Y%m%d-%H%M")
    run_id = a.run_id or f"{stamp}-{cond_key(a.condition).lower()}-{secrets.token_hex(2)}"
    check_offset_free(a.port_offset, run_id)
    rd = RUNS / run_id
    rd.mkdir(parents=True, exist_ok=False)
    ws_root = Path(cfg.get("LARDER_WORKSPACE_ROOT", str(Path.home() / "larder-workspaces"))).expanduser()
    meta = {
        "run_id": run_id, "condition": a.condition, "created_at": iso(now_utc()),
        "port_offset": a.port_offset, "warmup_min": a.warmup_min, "run_min": a.run_min, "seed": a.seed,
        "time_scale": a.time_scale, "workspace": str(ws_root / run_id), "artifacts": str(rd / "artifacts"),
        "harness_commit": sh(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture=True, check=False).stdout.strip() or None,
        "account": {k: v for k, v in account(cfg, a.condition).items() if k != "ingest_key_env"},
    }
    (rd / "artifacts").mkdir()
    save_run(meta)
    tok = secrets_path(run_id)
    tok.write_text(secrets.token_hex(16))
    os.chmod(tok, 0o600)
    print(f"[{run_id}] building workspace {meta['workspace']}")
    ws = build_workspace(meta, cfg)
    if a.skip_cx:
        print("  skipping Coralogix deployment (--skip-cx)")
        meta["cx_manifest"] = None
    else:
        op = meta["account"]["operator_profile"]
        print(f"  deploying {a.condition} Coralogix assets via profile '{op}' (tears down previous Larder assets first)")
        from harness.cx import deploy
        manifest = deploy.deploy(op, a.condition, run_id)
        (rd / "cx_manifest.json").write_text(json.dumps(manifest, indent=2))
        meta["cx_manifest"] = "cx_manifest.json"
        if a.condition != "basic":
            write_slo_catalog(ws, manifest, run_id)
    meta["workspace_digest"] = {cb: tree_digest(ws / cb) for cb in CODEBASES}
    meta["kit_digest"] = tree_digest_kit(ws)
    meta["prepared_at"] = iso(now_utc())
    save_run(meta)
    print(f"[{run_id}] prepared. next: ./bench start {run_id} --wait")


def tree_digest_kit(ws: Path) -> dict:
    out = {}
    for name in ("BACKLOG.md", "WORKSPACE.md", "REPORT_TEMPLATE.md", "larderctl"):
        out[name] = hashlib.sha256((ws / name).read_bytes()).hexdigest()
    return out


def supervisor_env(meta: dict, cfg: dict, with_telemetry: bool = True) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "CX_"))}
    env["LARDER_RUN_ID"] = meta["run_id"]
    env["LARDER_PORT_OFFSET"] = str(meta["port_offset"])
    env["LARDER_ARTIFACTS_DIR"] = meta["artifacts"]
    env["LARDER_ADMIN_TOKEN"] = secrets_path(meta["run_id"]).read_text().strip()
    if with_telemetry and meta.get("account", {}).get("operator_profile"):
        region = profile_region(meta["account"]["operator_profile"])
        key = fetch_ingest_key(cfg, meta["condition"])
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"https://ingress.{region}.coralogix.com"
        env["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Bearer%20{key}"
        env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
        env["OTEL_METRIC_EXPORT_INTERVAL"] = "10000"
        env["OTEL_BLRP_SCHEDULE_DELAY"] = "2000"
    return env


def spawn(cmd, env, log: Path, cwd=ROOT) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "ab")
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    return p.pid


def wait_http(url: str, timeout_s: int) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def cmd_start(a):
    meta = load_run(a.run_id)
    cfg = load_config()
    if pid_alive(meta.get("supervisor_pid")):
        raise SystemExit("already started")
    check_offset_free(meta["port_offset"], meta["run_id"])
    p = ports(meta["port_offset"])
    env = supervisor_env(meta, cfg, with_telemetry=not a.no_telemetry)
    art = Path(meta["artifacts"])
    meta["supervisor_pid"] = spawn([UV, "run", "--frozen", "python", "-m", "harness.runtime.supervisor",
                                    "--workspace", meta["workspace"], "--artifacts", meta["artifacts"],
                                    "--run-id", meta["run_id"], "--port-offset", str(meta["port_offset"])],
                                   env, art / "supervisor.out")
    meta["started_at"] = iso(now_utc())
    save_run(meta)
    print(f"[{a.run_id}] supervisor pid {meta['supervisor_pid']}; waiting for services...")
    healthy = wait_http(f"http://127.0.0.1:{p['edge']}/healthz", 240) and \
        wait_http(f"http://127.0.0.1:{p['orders_mesh']}/healthz", 60)
    if not healthy:
        raise SystemExit(f"services did not become healthy; see {art}/logs and {art}/supervisor.out")
    meta["workload_pid"] = spawn([UV, "run", "--frozen", "python", "-m", "harness.workload.loadgen",
                                  "--target", f"http://127.0.0.1:{p['edge']}", "--warmup-min", str(meta["warmup_min"]),
                                  "--run-min", str(meta["run_min"]), "--seed", str(meta["seed"]),
                                  "--port-offset", str(meta["port_offset"]), "--artifacts", meta["artifacts"],
                                  "--time-scale", str(meta["time_scale"])], env, art / "loadgen.out")
    meta["workload_started_at"] = iso(now_utc())
    warm_s = meta["warmup_min"] * 60 * meta["time_scale"]
    meta["handover_due_at"] = iso(now_utc() + dt.timedelta(seconds=warm_s))
    save_run(meta)
    print(f"[{a.run_id}] workload pid {meta['workload_pid']}; warm-up ends at {meta['handover_due_at']}")
    if a.wait:
        while now_utc() < dt.datetime.fromisoformat(meta["handover_due_at"].replace("Z", "+00:00")):
            time.sleep(5)
        cmd_handover(argparse.Namespace(run_id=a.run_id, at=meta["handover_due_at"]))


def cmd_handover(a):
    meta = load_run(a.run_id)
    p = ports(meta["port_offset"])
    t = dt.datetime.fromisoformat(a.at.replace("Z", "+00:00")) if getattr(a, "at", None) else now_utc()
    run_s = meta["run_min"] * 60 * meta["time_scale"]
    meta["handover_at"] = iso(t)
    meta["deadline_at"] = iso(t + dt.timedelta(seconds=run_s))
    save_run(meta)
    try:
        http_json("POST", f"http://127.0.0.1:{p['control']}/clock",
                  {"handover_at": meta["handover_at"], "duration_min": meta["run_min"] * meta["time_scale"]},
                  headers={"X-Admin-Token": secrets_path(a.run_id).read_text().strip()})
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not set supervisor clock: {e}")
    print(f"""
==================================================================================
 HAND OVER NOW  run={a.run_id}  condition={meta['condition']}
 deadline: {meta['deadline_at']} ({meta['run_min']} min)
 launch the evaluated agent with:
   cwd:          {meta['workspace']}
   environment:  set -a; . {meta['workspace']}/.larder/agent.env; set +a
   first prompt: "Read AGENT_INSTRUCTIONS.md and follow it."
 then at the deadline: ./bench stop {a.run_id}
==================================================================================""")


def build_agent_home(meta: dict, opencode_model: str | None, governor_port: int | None = None) -> Path:
    """An isolated HOME for the evaluated agent: only its condition's read-only cx profile and, for
    opencode, a minimal config with a single model (no MCP servers, plugins, skills or other providers).
    Lives under runs/<id>/ (never inside the workspace); credentials are removed when the run stops."""
    home = RUNS / meta["run_id"] / "agent_home"
    if home.exists():
        shutil.rmtree(home)
    prof = meta["account"]["agent_profile"]
    (home / ".cx" / "profiles").mkdir(parents=True)
    shutil.copy(Path.home() / ".cx" / "profiles" / f"{prof}.toml", home / ".cx" / "profiles" / f"{prof}.toml")
    (home / ".cx" / "config.toml").write_text(f'default_profile = "{prof}"\ndefault_output_format = "text"\n')
    if opencode_model:
        provider, model = opencode_model.split("/", 1)
        src = json.loads((Path.home() / ".config" / "opencode" / "opencode.json").read_text())
        pconf = json.loads(json.dumps(src["provider"][provider]))
        pconf["models"] = {model: pconf.get("models", {}).get(model, {"name": model})}
        oc = home / ".config" / "opencode"
        oc.mkdir(parents=True)
        key = pconf.get("options", {}).get("apiKey", "")
        m = re.match(r"\{file:(.+)\}", key)
        if m:
            kf = Path(os.path.expanduser(m.group(1)))
            shutil.copy(kf, oc / kf.name)
            os.chmod(oc / kf.name, 0o600)
            pconf["options"]["apiKey"] = "{file:" + str(oc / kf.name) + "}"
        if governor_port:  # all model traffic goes through the local LLM governor
            pconf.setdefault("options", {})["baseURL"] = f"http://127.0.0.1:{governor_port}/v1"
        (oc / "opencode.json").write_text(json.dumps({
            "$schema": "https://opencode.ai/config.json", "model": opencode_model, "small_model": opencode_model,
            "provider": {provider: pconf}, "autoupdate": False, "share": "disabled", "mcp": {},
            "compaction": {"auto": True}}, indent=2))
    return home


def scrub_agent_home(run_id: str):
    home = RUNS / run_id / "agent_home"
    for p in list(home.rglob("*")) if home.exists() else []:
        if p.is_file() and (p.parent.name == "profiles" or p.parent.name == "opencode" and p.name != "opencode.json"):
            p.unlink()


def agent_integrity(meta: dict, transcript: Path) -> dict:
    """A trial is invalid if any model or API error reached the agent's session, or any model request through the
    governor failed (status 400 or above after the governor's retries). Reads the raw artifacts only, never the
    trial's outcome, and applies the same rule to both conditions."""
    text = transcript.read_text(errors="replace") if transcript.exists() else ""
    errors = text.count('"type":"error"')
    gov = {"requests": 0, "failed": 0, "governor_wait_s": 0.0, "upstream_retries": 0, "retry_wait_s": 0.0}
    gl = Path(meta["artifacts"]) / "llm_governor.jsonl"
    if gl.exists():
        for ln in gl.read_text().splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not r.get("path", "").endswith(("/chat/completions", "/responses")):
                continue
            gov["requests"] += 1
            gov["governor_wait_s"] += r.get("governor_wait_s", 0)
            gov["upstream_retries"] += r.get("upstream_retries", 0)
            gov["retry_wait_s"] += r.get("retry_wait_s", 0)
            if r.get("status", 0) >= 400:
                gov["failed"] += 1
    gov = {k: round(v, 1) if isinstance(v, float) else v for k, v in gov.items()}
    reasons = []
    if errors:
        reasons.append(f"{errors} error event(s) reached the agent session")
    if gov["failed"]:
        reasons.append(f"{gov['failed']} model request(s) failed")
    return {"valid": not reasons, "invalid_reasons": reasons, "agent_error_events": errors, "governor": gov}


def cmd_run(a):
    meta = load_run(a.run_id)
    if not meta.get("started_at"):
        cmd_start(argparse.Namespace(run_id=a.run_id, wait=True, no_telemetry=False))
    elif not meta.get("handover_at"):
        cmd_handover(argparse.Namespace(run_id=a.run_id, at=None))
    meta = load_run(a.run_id)
    deadline = dt.datetime.fromisoformat(meta["deadline_at"].replace("Z", "+00:00"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "CX_", "LARDER_ADMIN"))}
    if a.agent_home:
        home = Path(a.agent_home)
    elif a.isolated_home or a.opencode_model:
        home = build_agent_home(meta, a.opencode_model, governor_port=(18080 + meta["port_offset"]) if a.llm_governor_rpm else None)
    else:
        home = None
    if home:
        env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                    "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_CACHE_HOME": str(home / ".cache"),
                    "XDG_STATE_HOME": str(home / ".local" / "state")})
        meta["agent_home"] = str(home)
    for ln in (Path(meta["workspace"]) / ".larder" / "agent.env").read_text().splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            env[k] = v
    governor = None
    if a.llm_governor_rpm:
        if not a.llm_upstream:
            raise SystemExit("set LARDER_MODEL_BASE_URL or pass --llm-upstream <your-endpoint>")
        gport = 18080 + meta["port_offset"]
        governor = subprocess.Popen([UV, "run", "--frozen", "python", "-m", "harness.runtime.llm_governor",
                                     "--upstream", a.llm_upstream, "--port", str(gport), "--rpm", str(a.llm_governor_rpm),
                                     "--log", str(Path(meta["artifacts"]) / "llm_governor.jsonl")],
                                    cwd=ROOT, stdout=open(run_dir(a.run_id) / "llm_governor.out", "ab"), stderr=subprocess.STDOUT,
                                    start_new_session=True)
        if not wait_http(f"http://127.0.0.1:{gport}/__governor/health", 60):
            raise SystemExit("LLM governor did not start")
        meta["llm_governor"] = {"rpm": a.llm_governor_rpm, "port": gport, "upstream": a.llm_upstream}
    reviewer = None
    if a.review_model:
        rport = 7170 + meta["port_offset"]
        rhome = Path(a.reviewer_home) if a.reviewer_home else run_dir(a.run_id) / "reviewer_home"
        if not a.reviewer_home:  # local runs: the reviewer gets the agent's model config, nothing else
            (rhome / ".config").mkdir(parents=True, exist_ok=True)
            if home and (home / ".config" / "opencode").exists():
                shutil.copytree(home / ".config" / "opencode", rhome / ".config" / "opencode", dirs_exist_ok=True)
        rargs = [UV, "run", "--frozen", "python", "-m", "harness.runtime.review_server", "--workspace", meta["workspace"],
                 "--artifacts", meta["artifacts"], "--run-dir", str(run_dir(a.run_id)), "--port", str(rport),
                 "--model", a.review_model, "--opencode-home", str(rhome)]
        if a.reviewer_user:
            rargs += ["--run-as", a.reviewer_user]
        if a.review_snapshot_root:
            rargs += ["--snapshot-root", str(Path(a.review_snapshot_root) / a.run_id)]
        reviewer = subprocess.Popen(rargs, cwd=ROOT, stdout=open(run_dir(a.run_id) / "review_server.out", "ab"),
                                    stderr=subprocess.STDOUT, start_new_session=True)
        if not wait_http(f"http://127.0.0.1:{rport}/health", 60):
            raise SystemExit("review server did not start")
        meta["review"] = {"model": a.review_model, "port": rport, "reviewer_user": a.reviewer_user}
    save_run(meta)
    cmd = a.agent_cmd
    if a.agent_user:
        # run the agent as a separate OS user: it cannot read the operator's credentials or the harness
        envargs = [f"{k}={v}" for k, v in env.items() if k in ("HOME", "PATH", "LANG", "TERM") or k.startswith(("XDG_", "CX_", "LARDER_"))]
        popen_args = ["sudo", "-n", "-u", a.agent_user, "env", "-i", *envargs, "sh", "-c", f"umask 002; {cmd}"]
        shell = False
    else:
        popen_args, shell = cmd, True
    log = run_dir(a.run_id) / "agent_transcript.log"
    print(f"[{a.run_id}] launching agent: {cmd}" + (f" (as {a.agent_user})" if a.agent_user else ""))
    with open(log, "ab") as f:
        p = subprocess.Popen(popen_args, shell=shell, cwd=meta["workspace"], env=env, stdout=f, stderr=subprocess.STDOUT,
                             start_new_session=True)
        meta["agent_pid"] = p.pid
        save_run(meta)
        try:
            p.wait(timeout=max(1, (deadline - now_utc()).total_seconds()))
            meta["agent_exit"] = {"code": p.returncode, "at": iso(now_utc()), "reason": "exited"}
        except subprocess.TimeoutExpired:
            if a.agent_user:
                subprocess.run(["sudo", "-n", "-u", a.agent_user, "pkill", "-TERM", "-u", a.agent_user], capture_output=True)
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
            meta["agent_exit"] = {"code": None, "at": iso(now_utc()), "reason": "deadline"}
    for proc in (reviewer, governor):
        if proc:
            os.killpg(proc.pid, signal.SIGTERM)
    meta["agent_integrity"] = agent_integrity(meta, log)
    save_run(meta)
    scrub_agent_home(a.run_id)
    # The system keeps serving traffic until the deadline even if the agent finished early.
    while now_utc() < deadline:
        time.sleep(5)
    cmd_stop(argparse.Namespace(run_id=a.run_id))


def cmd_status(a):
    meta = load_run(a.run_id)
    p = ports(meta["port_offset"])
    try:
        st = http_json("GET", f"http://127.0.0.1:{p['control']}/status")
    except Exception as e:  # noqa: BLE001
        st = {"error": str(e)}
    print(json.dumps({"run": {k: meta.get(k) for k in ("run_id", "condition", "started_at", "handover_at", "deadline_at", "stopped_at")},
                      "supervisor_alive": pid_alive(meta.get("supervisor_pid")),
                      "workload_alive": pid_alive(meta.get("workload_pid")), "control": st}, indent=2))


def _terminate(pid: int | None, wait_s: int = 20):
    if not pid_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    end = time.time() + wait_s
    while time.time() < end and pid_alive(pid):
        time.sleep(0.5)
    if pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def collect_code(meta: dict, rd: Path):
    ws = Path(meta["workspace"])
    (rd / "diffs").mkdir(exist_ok=True)
    final = rd / "final_code"
    if final.exists():
        shutil.rmtree(final)
    for cb in CODEBASES:
        src = ws / cb
        # Commit-agnostic diff: everything in the working tree vs the baseline tag, untracked included.
        tmp_index = rd / f".index-{cb}"
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        sh(["git", "read-tree", "baseline"], cwd=src, env=env)
        sh(["git", "add", "-A"], cwd=src, env=env)
        diff = sh(["git", "diff", "--cached", "--binary", "baseline"], cwd=src, env=env, capture=True).stdout
        stat = sh(["git", "diff", "--cached", "--stat", "baseline"], cwd=src, env=env, capture=True).stdout
        tmp_index.unlink(missing_ok=True)
        (rd / "diffs" / f"{cb}.patch").write_text(diff)
        (rd / "diffs" / f"{cb}.stat").write_text(stat)
        log = sh(["git", "log", "--format=%h %ad %s", "--date=iso", "baseline..HEAD"], cwd=src, capture=True, check=False).stdout
        (rd / "diffs" / f"{cb}.commits").write_text(log)
        shutil.copytree(src, final / cb, ignore=COPY_IGNORE)
    for name in ("REPORT.md",):
        if (ws / name).exists():
            shutil.copy(ws / name, rd / name)
    extra = [str(p.relative_to(ws)) for p in ws.iterdir() if p.name not in (*CODEBASES, ".larder", "tools", "BACKLOG.md", "WORKSPACE.md",
                                                                       "REPORT_TEMPLATE.md", "larderctl", "AGENT_INSTRUCTIONS.md", "SLO_CATALOG.md", "REPORT.md")]
    (rd / "diffs" / "workspace_extra_files.json").write_text(json.dumps(extra, indent=2))


def cmd_stop(a):
    meta = load_run(a.run_id)
    rd = run_dir(a.run_id)
    meta["stopped_at"] = iso(now_utc())
    save_run(meta)
    print(f"[{a.run_id}] stopping workload and services...")
    _terminate(meta.get("agent_pid"), 30)
    _terminate(meta.get("workload_pid"))
    _terminate(meta.get("supervisor_pid"), 60)
    collect_code(meta, rd)
    meta["code_collected_at"] = iso(now_utc())
    save_run(meta)
    print(f"[{a.run_id}] stopped. diffs in {rd/'diffs'}; report {'found' if (rd/'REPORT.md').exists() else 'MISSING'}")


def cmd_evaluate(a):
    from harness.evaluator import evaluate as ev
    meta = load_run(a.run_id)
    res = ev.run_evaluation(meta, run_dir(a.run_id), a.port_offset_eval)
    print(json.dumps(res["summary"], indent=2))


def cmd_collect(a):
    from harness.evaluator import collect as col
    meta = load_run(a.run_id)
    res = col.collect(meta, run_dir(a.run_id), load_config())
    print(json.dumps(res.get("summary", {}), indent=2))


def cmd_score(a):
    from harness.evaluator import score as sc
    meta = load_run(a.run_id)
    out = sc.score(meta, run_dir(a.run_id), ROOT / "results")
    print(f"wrote {out['json']} and {out['md']}")


def cmd_verify_isolation(a):
    from harness.evaluator import isolation
    meta = load_run(a.run_id)
    res = isolation.verify(meta, load_config())
    (run_dir(a.run_id) / "isolation.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["ok"] else 1)


def cmd_teardown(a):
    meta = load_run(a.run_id)
    for k in ("agent_pid", "workload_pid", "supervisor_pid"):
        _terminate(meta.get(k))
    if not a.keep_cx and meta.get("account", {}).get("operator_profile"):
        from harness.cx import deploy
        print(json.dumps(deploy.teardown(meta["account"]["operator_profile"])))
    if not a.keep_workspace and Path(meta["workspace"]).exists():
        shutil.rmtree(meta["workspace"])
        print(f"removed {meta['workspace']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    k = sub.add_parser("agent-key")
    k.add_argument("--condition", required=True, choices=CONDITIONS)
    k.set_defaults(fn=cmd_agent_key)
    p = sub.add_parser("prepare")
    p.add_argument("--condition", required=True, choices=CONDITIONS)
    p.add_argument("--run-id")
    p.add_argument("--port-offset", type=int, default=0)
    p.add_argument("--warmup-min", type=float, default=15)
    p.add_argument("--run-min", type=float, default=60)
    p.add_argument("--seed", type=int, default=1337)
    p.add_argument("--time-scale", type=float, default=1.0, help="<1 compresses the scenario (smoke tests only)")
    p.add_argument("--skip-cx", action="store_true")
    p.set_defaults(fn=cmd_prepare)
    s = sub.add_parser("start")
    s.add_argument("run_id")
    s.add_argument("--wait", action="store_true")
    s.add_argument("--no-telemetry", action="store_true")
    s.set_defaults(fn=cmd_start)
    h = sub.add_parser("handover")
    h.add_argument("run_id")
    h.add_argument("--at", help="ISO time of handover (default now; use the warm-up end to match the workload phases)")
    h.set_defaults(fn=cmd_handover)
    for name, fn in (("status", cmd_status), ("stop", cmd_stop), ("collect", cmd_collect),
                     ("score", cmd_score), ("verify-isolation", cmd_verify_isolation)):
        q = sub.add_parser(name)
        q.add_argument("run_id")
        q.set_defaults(fn=fn)
    r = sub.add_parser("run")
    r.add_argument("run_id")
    r.add_argument("--agent-cmd", required=True)
    r.add_argument("--isolated-home", action="store_true", help="give the agent an isolated HOME (only its cx profile)")
    r.add_argument("--opencode-model", help="provider/model: also write a minimal opencode config into the isolated HOME")
    r.add_argument("--agent-user", help="run the agent as this OS user (needs passwordless sudo to it)")
    r.add_argument("--agent-home", help="use this pre-provisioned HOME for the agent instead of building one")
    r.add_argument("--llm-governor-rpm", type=float, help="route model traffic through the local governor at this request budget")
    r.add_argument("--llm-upstream", default=os.environ.get("LARDER_MODEL_BASE_URL"),
                   help="model endpoint base URL for the governor (default: LARDER_MODEL_BASE_URL)")
    r.add_argument("--review-model", help="enable the review gate with this reviewer model (provider/model)")
    r.add_argument("--reviewer-user", help="OS user to run the reviewer as")
    r.add_argument("--reviewer-home", help="pre-provisioned HOME for the reviewer (opencode config only)")
    r.add_argument("--review-snapshot-root", help="directory for review snapshots (readable by the reviewer user)")
    r.set_defaults(fn=cmd_run)
    e = sub.add_parser("evaluate")
    e.add_argument("run_id")
    e.add_argument("--port-offset-eval", type=int, default=500)
    e.set_defaults(fn=cmd_evaluate)
    t = sub.add_parser("teardown")
    t.add_argument("run_id")
    t.add_argument("--keep-workspace", action="store_true")
    t.add_argument("--keep-cx", action="store_true")
    t.set_defaults(fn=cmd_teardown)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
