"""Batch of matched trials: one Coralogix team and one EC2 instance per trial.

    uv run python -m harness.ec2.batch setup-accounts            # per team: ingest and agent keys, cx profile, archive wiring (idempotent)
    uv run python -m harness.ec2.batch check                     # profiles + keys + archive per team, no changes
    uv run python -m harness.ec2.batch launch --schedule --batch <id> --start <UTC ISO time> [--pairs 1-10]   # one EventBridge schedule per trial, in waves
    uv run python -m harness.ec2.batch watch  --batch <id>       # wait, fetch results as instances finish

Teams come from harness/config/batch_teams.json (copy batch_teams.example.json and fill in the team IDs).
Each team's operator key is a cx profile you create (default name larder-op-<basic|slo>-<NN>); setup-accounts
uses it to create the team's ingest and read-only agent keys and to wire its archives. No credential is written
to the repo.

Settings come from the environment (see .env.example): AWS_PROFILE, AWS_REGION, LARDER_LOGS_BUCKET,
LARDER_METRICS_BUCKET, LARDER_RESULTS_S3, LARDER_CX_REGION, LARDER_MODEL, LARDER_BATCH_TEAMS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEAMS = Path(os.environ.get("LARDER_BATCH_TEAMS") or str(ROOT / "harness" / "config" / "batch_teams.json"))
REGION_CX = os.environ.get("LARDER_CX_REGION") or "eu1"
GRPC = f"ng-api-grpc.{REGION_CX}.coralogix.com:443"
AWS_REGION = os.environ.get("AWS_REGION") or "eu-west-1"
AWS_PROFILE = os.environ.get("AWS_PROFILE")
LOGS_BUCKET = os.environ.get("LARDER_LOGS_BUCKET") or ""
METRICS_BUCKET = os.environ.get("LARDER_METRICS_BUCKET") or ""
S3_PREFIX = os.environ.get("LARDER_RESULTS_S3") or f"s3://{LOGS_BUCKET}/larder-bench-runner"


def short(cond):
    return {"basic": "basic", "slo-guided": "slo"}[cond]


def load_teams():
    return json.loads(TEAMS.read_text())


def save_teams(d):
    TEAMS.write_text(json.dumps(d, indent=2))


def grpc(key, method, body, check=True):
    p = subprocess.run(["grpcurl", "-max-time", "90", "-H", f"Authorization: Bearer {key}", "-H", "User-Agent: larder-bench/1.0",
                        "-d", json.dumps(body), GRPC, method], capture_output=True, text=True)
    if p.returncode != 0:
        if check:
            raise RuntimeError(f"{method.rsplit('/', 1)[-1]}: {(p.stdout + p.stderr).strip()[:300]}")
        return None
    return json.loads(p.stdout or "{}")


def grpc_retry(key, method, body, tries=4):
    for i in range(tries):
        try:
            return grpc(key, method, body)
        except RuntimeError as e:
            if i == tries - 1 or "does not expose service" not in str(e) and "Unavailable" not in str(e) and "Internal" not in str(e):
                raise
            time.sleep(3)


def profile_key(name):
    p = Path.home() / ".cx" / "profiles" / f"{name}.toml"
    if not p.exists():
        return None
    for ln in p.read_text().splitlines():
        if ln.startswith("api_key"):
            return ln.split("=", 1)[1].strip().strip('"')
    return None


def cx_json(profile, *args):
    p = subprocess.run(["cx", "-p", profile, "--no-console-link", "-o", "json", *args], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-300:])
    out = p.stdout.strip()
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0], default=0)
    return json.loads(out[start:]) if out else None


def add_profile(name, value):
    subprocess.run(["cx", "profiles", "add", "--name", name, "--region", REGION_CX, "--api-key", value, "--force"],
                   capture_output=True, text=True, check=True)


def op_create_key(op, team_id, name, presets=(), permissions=()):
    """Create an API key in the team with the team's operator key; returns (key id, value)."""
    body = {"name": name, "owner": {"teamId": team_id}, "hashed": False,
            "keyPermissions": {"presets": list(presets), "permissions": list(permissions)}}
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
        json.dump(body, f)
        f.flush()
        res = cx_json(op, "iam", "api-keys", "create", "--from-file", f.name, "--yes") or {}
    key_id = res.get("key_id") or res.get("keyId") or res.get("id") or (res.get("keyInfo") or {}).get("id") or next(
        (k["id"] for k in (cx_json(op, "iam", "api-keys", "list") or []) if k.get("name") == name), None)
    value = res.get("value") or res.get("key") or (res.get("keyInfo") or {}).get("value")
    if not value and key_id:
        value = cx_json(op, "iam", "api-keys", "get", key_id)["keyInfo"]["value"]
    if not key_id or not value:
        raise RuntimeError(f"key {name} created but its id or value was not returned")
    return key_id, value


def setup_team(t):
    tid, cond, n = t["team_id"], t["condition"], t["pair"]
    op = t.get("operator_profile") or f"larder-op-{short(cond)}-{n:02d}"
    ag = t.get("agent_profile") or f"larder-agent-{short(cond)}-{n:02d}"
    # 1. operator key: created by you in the team and added as a cx profile
    if not profile_key(op):
        raise RuntimeError(f"no cx profile '{op}': create the team's operator key and run "
                           f"cx profiles add --name {op} --region {REGION_CX} --api-key <key>")
    t["operator_profile"] = op
    keys = cx_json(op, "iam", "api-keys", "list") or []
    names = {k["name"]: k["id"] for k in keys}
    # 2. ingest key
    ingest = names.get("larder-bench-send-data")
    if not ingest:
        ingest, _ = op_create_key(op, tid, "larder-bench-send-data", ["SendData"])
    t["ingest_key_id"] = ingest
    # 3. agent read-only key, the same permissions as `bench agent-key`
    if not profile_key(ag):
        from harness.bench import AGENT_READ_PERMISSIONS, AGENT_SLO_PERMISSIONS
        perms = AGENT_READ_PERMISSIONS + ([] if cond == "basic" else AGENT_SLO_PERMISSIONS)
        _, val = op_create_key(op, tid, f"larder-bench-agent-{cond}-readonly", permissions=perms)
        add_profile(ag, val)
    t["agent_profile"] = ag
    # 4. archive wiring to the shared buckets (Coralogix partitions by team id)
    opk = profile_key(op)
    tgt = grpc_retry(opk, "com.coralogix.archive.v2.TargetService/GetTarget", {})
    if (tgt.get("target", {}).get("s3", {}) or {}).get("bucket") != LOGS_BUCKET:
        spec = {"isActive": True, "s3": {"bucket": LOGS_BUCKET, "region": AWS_REGION}}
        if not grpc_retry(opk, "com.coralogix.archive.v2.TargetService/ValidateTarget", spec).get("isValid"):
            raise RuntimeError("logs archive target did not validate")
        grpc_retry(opk, "com.coralogix.archive.v2.TargetService/SetTarget", {**spec, "format": "TARGET_FORMAT_CX_DATA_V1"})
    mc = grpc(opk, "com.coralogix.metrics.metrics_configurator.MetricsConfiguratorPublicService/GetTenantConfig", {}, check=False)
    if not mc or (mc.get("tenantConfig", {}).get("s3", {}) or {}).get("bucket") != METRICS_BUCKET:
        s3 = {"s3": {"bucket": METRICS_BUCKET, "region": AWS_REGION}}
        grpc_retry(opk, "com.coralogix.metrics.metrics_configurator.MetricsConfiguratorPublicService/ValidateBucket", s3)
        grpc_retry(opk, "com.coralogix.metrics.metrics_configurator.MetricsConfiguratorPublicService/ConfigureTenant", s3)
    t["archive_ok"] = True
    return t


def team_check(t):
    res = {"operator": bool(profile_key(t.get("operator_profile", "") or "-")),
           "agent": bool(profile_key(t.get("agent_profile", "") or "-")), "ingest": bool(t.get("ingest_key_id"))}
    if res["operator"]:
        opk = profile_key(t["operator_profile"])
        tgt = grpc(opk, "com.coralogix.archive.v2.TargetService/GetTarget", {}, check=False) or {}
        mc = grpc(opk, "com.coralogix.metrics.metrics_configurator.MetricsConfiguratorPublicService/GetTenantConfig", {}, check=False) or {}
        res["archive_logs"] = (tgt.get("target", {}).get("s3", {}) or {}).get("bucket") == LOGS_BUCKET and \
            tgt.get("target", {}).get("archiveSpec", {}).get("isActive", False)
        res["archive_metrics"] = (mc.get("tenantConfig", {}).get("s3", {}) or {}).get("bucket") == METRICS_BUCKET
        assets = subprocess.run(["uv", "run", "--frozen", "python", "-m", "harness.cx.deploy", "inspect", "--profile", t["operator_profile"]],
                                cwd=ROOT, capture_output=True, text=True)
        try:
            res["slos_in_account"] = len(json.loads(assets.stdout)["slos"])
        except Exception:  # noqa: BLE001
            res["slos_in_account"] = None
    res["ready"] = bool(res["operator"] and res["agent"] and res["ingest"]
                        and res.get("archive_logs") and res.get("archive_metrics")
                        and (t["condition"] != "basic" or res.get("slos_in_account") == 0))
    return res


def pairs_arg(s):
    a, _, b = s.partition("-")
    return list(range(int(a), int(b or a) + 1))


def require_buckets():
    if not LOGS_BUCKET or not METRICS_BUCKET:
        raise SystemExit("set LARDER_LOGS_BUCKET and LARDER_METRICS_BUCKET (see .env.example)")


def cmd_setup(a):
    require_buckets()
    d = load_teams()
    for t in d["teams"]:
        if t["pair"] not in a.pairs:
            continue
        try:
            setup_team(t)
            print(f"ok   {t['team_name']:42} op={t['operator_profile']} agent={t['agent_profile']} ingest={t['ingest_key_id']}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {t['team_name']:42} {e}")
        save_teams(d)


def cmd_check(a):
    require_buckets()
    d = load_teams()
    ready = 0
    for t in d["teams"]:
        if t["pair"] not in a.pairs:
            continue
        r = team_check(t)
        ready += r["ready"]
        print(f"{'READY' if r['ready'] else 'NOT  '} {t['team_name']:42} " + " ".join(f"{k}={v}" for k, v in r.items() if k != "ready"))
    print(f"{ready} ready")


def cmd_launch(a):
    from harness.ec2 import launch
    d = load_teams()
    batch = a.batch or f"batch-{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M}"
    mpath = ROOT / "runs" / f"{batch}.json"
    if mpath.exists():  # just-in-time launches: one call per wave, all appended to the same batch manifest
        manifest = json.loads(mpath.read_text())
    else:
        manifest = {"batch": batch, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "model": a.model,
                    "rpm_per_agent": a.rpm, "trials": []}
    first = not a.skip_bundle
    start = dt.datetime.fromisoformat(a.start.replace("Z", "+00:00")) if a.start else dt.datetime.now(dt.timezone.utc)
    order = sorted(a.pairs)
    manifest.setdefault("start", start.isoformat())
    manifest.update({"wave_size": a.wave_size, "wave_spacing_min": a.wave_spacing_min})
    for t in sorted(d["teams"], key=lambda t: (t["pair"], t["condition"])):
        if t["pair"] not in a.pairs:
            continue
        # both conditions of a pair always share a wave (same model endpoint conditions, same start time)
        wave = order.index(t["pair"]) // a.wave_size if a.wave_size else 0
        not_before = start + dt.timedelta(minutes=wave * a.wave_spacing_min)
        wait_min = max(0, int((not_before - dt.datetime.now(dt.timezone.utc)).total_seconds() // 60))
        tag = f"{batch}-p{t['pair']:02d}-{short(t['condition'])}"
        if a.schedule:  # EventBridge Scheduler starts the instance a.lead_min before its wave: no idle hours
            wait_min = a.lead_min
        argv = ["schedule" if a.schedule else "launch", "--condition", t["condition"], "--tag", tag, "--model", a.model, "--instance-type", a.instance_type,
                "--operator-profile", t["operator_profile"], "--agent-profile", t["agent_profile"],
                "--ingest-key-id", t["ingest_key_id"], "--max-minutes", str(wait_min + a.max_minutes), "--rpm", str(a.rpm),
                "--not-before", not_before.strftime("%Y-%m-%dT%H:%M:%SZ")]
        if a.prepare_args:
            argv += ["--prepare-args", a.prepare_args]
        if a.schedule:
            argv += ["--launch-at", (not_before - dt.timedelta(minutes=a.lead_min)).strftime("%Y-%m-%dT%H:%M:%SZ")]
        elif not first:
            argv.append("--skip-bundle")
        res = launch.main(argv)
        first = False
        if mpath.exists():  # re-read: the watcher may have marked trials fetched meanwhile
            manifest["trials"] = json.loads(mpath.read_text())["trials"]
        manifest["trials"].append({**res, "pair": t["pair"], "team_id": t["team_id"], "team_name": t["team_name"],
                                   "wave": a.wave if a.wave else a.wave_offset + wave + 1, "not_before": not_before.isoformat()})
        (ROOT / "runs").mkdir(exist_ok=True)
        mpath.write_text(json.dumps(manifest, indent=2))
        if a.stagger_s:
            time.sleep(a.stagger_s)
    print(f"launched {len(manifest['trials'])} trials; manifest runs/{batch}.json")


def aws(args, check=False):
    return subprocess.run(["aws", *args, *(["--profile", AWS_PROFILE] if AWS_PROFILE else []), "--region", AWS_REGION],
                          capture_output=True, text=True, check=check)


def cmd_watch(a):
    """Fetch each trial's results once its instance has finished. Re-reads the manifest every minute, so trials
    launched just in time (one wave at a time) are picked up; exits when `--expect` trials (default: all pairs x 2)
    have been fetched."""
    from harness.ec2 import launch
    mpath = ROOT / "runs" / f"{a.batch}.json"
    expect = a.expect or 2 * len(a.pairs)
    while True:
        m = json.loads(mpath.read_text())
        pending = [t for t in m["trials"] if not t.get("fetched")]
        if not pending and len(m["trials"]) >= expect:
            break
        for t in pending:
            tag = t["tag"]
            if aws(["s3", "ls", f"{S3_PREFIX}/{tag}/STATUS"]).returncode != 0:
                continue
            sel = (["--instance-ids", t["instance_id"]] if t.get("instance_id")
                   else ["--filters", f"Name=tag:larder-tag,Values={tag}"])
            state = aws(["ec2", "describe-instances", *sel, "--query",
                         "Reservations[0].Instances[0].State.Name", "--output", "text"]).stdout.strip()
            if state not in ("terminated", "shutting-down", "None", ""):  # terminated instances vanish from listings after ~1 h
                continue
            status = aws(["s3", "cp", f"{S3_PREFIX}/{tag}/STATUS", "-"]).stdout.strip()
            launch.main(["fetch", "--tag", tag])
            m = json.loads(mpath.read_text())  # merge: the launcher may have appended trials meanwhile
            for x in m["trials"]:
                if x["tag"] == tag:
                    x.update(status=status, fetched=True)
            mpath.write_text(json.dumps(m, indent=2))
            print(f"{dt.datetime.now(dt.timezone.utc):%H:%M:%S} {tag}: {status}", flush=True)
        time.sleep(60)
    print("all trials fetched", flush=True)


def main():
    ap = argparse.ArgumentParser(prog="harness.ec2.batch")
    ap.add_argument("action", choices=["setup-accounts", "check", "launch", "watch"])
    ap.add_argument("--pairs", type=pairs_arg, default=list(range(1, 11)))
    ap.add_argument("--batch")
    ap.add_argument("--model", default=os.environ.get("LARDER_MODEL"), help="<provider>/<model> as named in opencode.json")
    ap.add_argument("--instance-type", default="c7i.2xlarge")
    ap.add_argument("--max-minutes", type=int, default=210)
    ap.add_argument("--stagger-s", type=int, default=0, help="pause between instance launches")
    ap.add_argument("--rpm", type=float, default=22, help="per-agent model request budget; agents x rpm must stay under the endpoint's rate limit")
    ap.add_argument("--wave-size", type=int, default=0, help="pairs per wave (0 = all at once)")
    ap.add_argument("--wave-spacing-min", type=int, default=65, help="minutes between wave starts (agent hours must not overlap)")
    ap.add_argument("--skip-bundle", action="store_true", help="reuse the code bundle already uploaded (later waves)")
    ap.add_argument("--prepare-args", default="", help='passed to bench prepare on each instance, e.g. "--run-min 180"')
    ap.add_argument("--expect", type=int, default=0, help="watch: number of trials to wait for (default pairs x 2)")
    ap.add_argument("--schedule", action="store_true", help="launch: create EventBridge schedules instead of instances "
                    "(no operator machine or AWS session needed later)")
    ap.add_argument("--lead-min", type=int, default=12, help="schedule: start each instance this long before its wave")
    ap.add_argument("--wave-offset", type=int, default=0, help="wave numbers start after this (adding waves to a batch)")
    ap.add_argument("--wave", type=int, default=0, help="record this wave number (just-in-time launches of one wave)")
    ap.add_argument("--start", help="UTC ISO start of wave 1 (default now)")
    a = ap.parse_args()
    if a.action == "launch" and not a.model:
        raise SystemExit("set LARDER_MODEL or pass --model <provider>/<model>")
    {"setup-accounts": cmd_setup, "check": cmd_check, "launch": cmd_launch, "watch": cmd_watch}[a.action](a)


if __name__ == "__main__":
    main()
