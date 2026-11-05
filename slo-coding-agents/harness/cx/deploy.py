"""Deploy, inspect and tear down the run-scoped Coralogix assets for one condition.

    uv run python -m harness.cx.deploy deploy   --profile P --condition basic|slo-guided --run-id R --out runs/R/cx_manifest.json
    uv run python -m harness.cx.deploy teardown --profile P            # removes every Larder-managed asset
    uv run python -m harness.cx.deploy inspect  --profile P            # lists Larder-managed assets (JSON)
    uv run python -m harness.cx.deploy render   --condition C --run-id R --out-dir DIR   # write JSON only, no API calls

All writes go through the operator's cx profile. Credentials are never written
to disk by this module.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.cx import assets

PREFIX = "Larder · "


class CxError(RuntimeError):
    pass


def cx(profile: str, *args: str, stdin: str | None = None, want_json: bool = True):
    cmd = ["cx", "-p", profile, "--no-console-link"]
    if want_json:
        cmd += ["-o", "json"]
    cmd += list(args)
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise CxError(f"{' '.join(cmd[3:])}: {p.stderr.strip()[-800:]}")
    if not want_json:
        return p.stdout
    out = p.stdout.strip()
    if not out:
        return None
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0], default=0)
    return json.loads(out[start:])


def _write_tmp(obj) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


def ensure_folder(profile: str) -> str | None:
    folders = cx(profile, "dashboards", "folders", "list") or []
    for f in folders:
        if f.get("name") == assets.DASHBOARD_FOLDER:
            return f.get("id")
    cx(profile, "dashboards", "folders", "create", "--name", assets.DASHBOARD_FOLDER, "--yes", want_json=False)
    for f in cx(profile, "dashboards", "folders", "list") or []:
        if f.get("name") == assets.DASHBOARD_FOLDER:
            return f.get("id")
    return None


def inspect(profile: str) -> dict:
    dashboards = [d for d in (cx(profile, "dashboards", "catalog") or []) if str(d.get("name", "")).startswith(PREFIX)]
    alerts = [a for a in (cx(profile, "alerts", "list") or []) if str(a.get("name", "")).startswith(PREFIX)]
    slos_raw = cx(profile, "slos", "list") or []
    if isinstance(slos_raw, dict):
        slos_raw = slos_raw.get("slos", [])
    return {
        "dashboards": [{"id": d["id"], "name": d["name"]} for d in dashboards],
        "alerts": [{"id": a["id"], "name": a["name"], "enabled": a.get("enabled")} for a in alerts],
        "slos": [{"id": s.get("id"), "name": s.get("name")} for s in slos_raw],
        "slos_managed": [{"id": s.get("id"), "name": s.get("name")} for s in slos_raw if str(s.get("name", "")).startswith(PREFIX)],
    }


def teardown(profile: str) -> dict:
    found = inspect(profile)
    for d in found["dashboards"]:
        cx(profile, "dashboards", "delete", d["id"], "--yes", want_json=False)
    for a in found["alerts"]:
        cx(profile, "alerts", "delete", a["id"], "--yes", want_json=False)
    for s in found["slos_managed"]:
        cx(profile, "slos", "delete", s["id"], "--yes", want_json=False)
    return {"deleted": {k: len(found[k]) for k in ("dashboards", "alerts", "slos_managed")}}


def render(condition: str, run_id: str) -> dict:
    from harness.cx.sli import slo_set

    out = {
        "dashboards": assets.common_dashboards(run_id),
        "alerts": assets.common_alerts(run_id),
        "slos": [],
    }
    if condition == "basic":
        return out
    if condition != "slo-guided":
        raise SystemExit(f"unknown condition {condition!r}")
    study = slo_set(condition)
    out["dashboards"] += assets.slo_dashboards(run_id, study)
    out["slos"] = assets.slos(run_id, study)
    return out


def deploy(profile: str, condition: str, run_id: str) -> dict:
    from harness.cx.sli import slo_set

    teardown(profile)
    defs = render(condition, run_id)
    folder = ensure_folder(profile)
    manifest = {"condition": condition, "run_id": run_id, "profile": profile, "dashboards": [], "alerts": [], "slos": []}

    for d in defs["dashboards"]:
        args = ["dashboards", "create", "--from-file", _write_tmp(d), "--yes"]
        if folder:
            args += ["--folder", folder]
        cx(profile, *args, want_json=False)
        manifest["dashboards"].append({"name": d["name"]})

    def create_alert(a):
        res = cx(profile, "alerts", "create", "--from-file", _write_tmp({"alertDefProperties": a}), "--yes")
        manifest["alerts"].append({"name": a["name"], "id": _find_id(res), "priority": a["priority"],
                                   "asset_class": a.get("entityLabels", {}).get("asset_class", "common")})

    for a in defs["alerts"]:
        create_alert(a)

    for s in defs["slos"]:
        res = cx(profile, "slos", "create", "--from-file", _write_tmp(s), "--yes")
        manifest["slos"].append({"name": s["name"], "id": _find_id(res), "target": s["targetThresholdPercentage"],
                                 "sli": s["labels"]["sli"], "criticality": s["labels"]["criticality"],
                                 "service": s["labels"]["service"], "description": s["description"]})

    if condition != "basic":
        for a in assets.slo_alerts(run_id, {m["sli"]: m["id"] for m in manifest["slos"]}, slo_set(condition)):
            create_alert(a)

    live = inspect(profile)
    by_name = {d["name"]: d["id"] for d in live["dashboards"]}
    for d in manifest["dashboards"]:
        d["id"] = by_name.get(d["name"])
    alert_ids = {a["name"]: a["id"] for a in live["alerts"]}
    for a in manifest["alerts"]:
        a["id"] = a["id"] or alert_ids.get(a["name"])
    return manifest


def _find_id(res):
    if isinstance(res, dict):
        for k in ("id",):
            if res.get(k):
                return res[k]
        for v in res.values():
            if isinstance(v, dict):
                got = _find_id(v)
                if got:
                    return got
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.cx.deploy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy")
    d.add_argument("--profile", required=True)
    d.add_argument("--condition", required=True, choices=["basic", "slo-guided"])
    d.add_argument("--run-id", required=True)
    d.add_argument("--out")
    t = sub.add_parser("teardown")
    t.add_argument("--profile", required=True)
    i = sub.add_parser("inspect")
    i.add_argument("--profile", required=True)
    r = sub.add_parser("render")
    r.add_argument("--condition", required=True, choices=["basic", "slo-guided"])
    r.add_argument("--run-id", required=True)
    r.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "deploy":
        m = deploy(a.profile, a.condition, a.run_id)
        if a.out:
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.out).write_text(json.dumps(m, indent=2))
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in m.items()}))
    elif a.cmd == "teardown":
        print(json.dumps(teardown(a.profile)))
    elif a.cmd == "inspect":
        print(json.dumps(inspect(a.profile), indent=2))
    elif a.cmd == "render":
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        defs = render(a.condition, a.run_id)
        for kind, items in defs.items():
            for n, item in enumerate(items):
                (out / f"{kind}-{n:02d}.json").write_text(json.dumps(item, indent=2))
        print(f"wrote {sum(len(v) for v in defs.values())} definitions to {out}")


if __name__ == "__main__":
    sys.exit(main())

