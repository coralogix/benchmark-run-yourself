"""Fresh-state evaluation of a run's final code.

The agent's final working tree (runs/<id>/final_code) is copied into an
isolated evaluation workspace, started with freshly seeded data, quiet
simulators and no telemetry export on a separate port range, and then:

  1. the hidden black-box acceptance suite (harness/evaluator/acceptance) runs
     against it: SMOKE (regressions), FEAT-n (features) and SF-n/FF-n/HC-n (seeded defects);
  2. the codebases' own visible test suites run;
  3. diff-derived signals (tests added, commits) are recorded.

    ./bench evaluate <run-id>
    uv run python -m harness.evaluator.evaluate --baseline   # calibrate the suite against the untouched baseline
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = Path(__file__).resolve().parent / "acceptance"
CODEBASES = ("commerce", "ops")
IGNORE = shutil.ignore_patterns(".venv", "node_modules", "var", "data", "__pycache__", ".pytest_cache", ".git")


def _run(cmd, cwd, timeout=900, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _install(ws: Path) -> dict:
    out = {}
    rc, log = _run(["uv", "sync", "--frozen", "-q"], ws / "commerce")
    if rc != 0:
        rc, log = _run(["uv", "sync", "-q"], ws / "commerce")
    out["commerce"] = rc == 0
    rc, log2 = _run(["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"], ws / "ops")
    if rc != 0:
        rc, log2 = _run(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], ws / "ops")
    out["ops"] = rc == 0
    return out


def _wait(url, timeout_s):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def _visible_tests(ws: Path) -> dict:
    res = {}
    rc, out = _run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider"], ws / "commerce", timeout=900,
                   env={**{k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}})
    m = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)", out)}
    res["commerce"] = {"exit_code": rc, "passed": m.get("passed", 0), "failed": m.get("failed", 0) + m.get("error", 0) + m.get("errors", 0),
                         "skipped": m.get("skipped", 0), "tail": out[-600:]}
    rc, out = _run(["npm", "test", "--silent"], ws / "ops", timeout=900,
                   env={k: v for k, v in os.environ.items() if not k.startswith("OTEL_")})
    passing = re.search(r"(\d+) passing", out)
    failing = re.search(r"(\d+) failing", out)
    pending = re.search(r"(\d+) pending", out)
    res["ops"] = {"exit_code": rc, "passed": int(passing.group(1)) if passing else 0,
                         "failed": int(failing.group(1)) if failing else 0,
                         "skipped": int(pending.group(1)) if pending else 0, "tail": out[-600:]}
    return res


def _diff_signals(rd: Path) -> dict:
    added, commits = {}, {}
    for cb in CODEBASES:
        patch = rd / "diffs" / f"{cb}.patch"
        text = patch.read_text(errors="replace") if patch.exists() else ""
        added[cb] = len(re.findall(r"^\+\s*(?:async\s+)?def test_\w+|^\+\s*it\(\s*['\"`]", text, re.M))
        c = rd / "diffs" / f"{cb}.commits"
        commits[cb] = len([ln for ln in (c.read_text().splitlines() if c.exists() else []) if ln.strip()])
    return {"tests_added": added, "agent_commits": commits}


def _unhealthy_services(offset: int) -> list[str]:
    import yaml
    reg = yaml.safe_load((ROOT / "harness" / "runtime" / "services.yaml").read_text())
    bad = []
    for svc in reg["services"]:
        if not _wait(f"http://127.0.0.1:{8100 + int(svc['index']) + offset}/healthz", 20):
            bad.append(svc["name"])
    return bad


def run_evaluation(meta: dict | None, rd: Path, offset: int = 500, source: Path | None = None) -> dict:
    src = source or (rd / "final_code")
    evdir = rd / "eval"
    if evdir.exists():
        shutil.rmtree(evdir)
    ws = evdir / "ws"
    art = evdir / "artifacts"
    (ws / ".larder" / "logs").mkdir(parents=True)
    art.mkdir(parents=True)
    for cb in CODEBASES:
        shutil.copytree(src / cb, ws / cb, ignore=IGNORE)
    installed = _install(ws)
    token = secrets.token_hex(16)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "CX_"))}
    env.update({"LARDER_RUN_ID": f"{(meta or {}).get('run_id', 'baseline')}-eval", "LARDER_PORT_OFFSET": str(offset),
                "LARDER_ARTIFACTS_DIR": str(art), "LARDER_ADMIN_TOKEN": token})
    sup_log = open(art / "supervisor.out", "ab")
    sup = subprocess.Popen(["uv", "run", "--frozen", "python", "-m", "harness.runtime.supervisor", "--workspace", str(ws),
                            "--artifacts", str(art), "--run-id", env["LARDER_RUN_ID"], "--port-offset", str(offset), "--eval"],
                           cwd=ROOT, env=env, stdout=sup_log, stderr=subprocess.STDOUT, start_new_session=True)
    failed_start = []
    try:
        _wait(f"http://127.0.0.1:{8000 + offset}/healthz", 240)
        failed_start = _unhealthy_services(offset)
        results_path = evdir / "acceptance.json"
        penv = {**env, "LARDER_EVAL_OFFSET": str(offset), "LARDER_EVAL_RESULTS": str(results_path),
                "LARDER_EVAL_WORKSPACE": str(ws), "LARDER_EVAL_ARTIFACTS": str(art)}
        t0 = time.time()
        rc, out = _run(["uv", "run", "--frozen", "pytest", str(ACCEPTANCE), "-q", "-p", "no:cacheprovider"],
                       ROOT, timeout=1800, env=penv)
        (evdir / "acceptance.log").write_text(out)
        acceptance = json.loads(results_path.read_text()) if results_path.exists() else {"tests": [], "error": out[-2000:]}
        acceptance["duration_s"] = round(time.time() - t0, 1)
    finally:
        os.killpg(sup.pid, 15)
        try:
            sup.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(sup.pid, 9)
    visible = _visible_tests(ws)
    # The baseline's own suites contain time-of-day-dependent tests, so the regression comparison
    # uses the pristine baseline's results measured right now, on this machine.
    baseline_visible = None
    if source is None or Path(source).resolve() != (ROOT / "baseline").resolve():
        bws = evdir / "baseline_ws"
        for cb in CODEBASES:
            shutil.copytree(ROOT / "baseline" / cb, bws / cb, ignore=IGNORE)
        _install(bws)
        baseline_visible = _visible_tests(bws)
        shutil.rmtree(bws, ignore_errors=True)
    regressed = None
    if baseline_visible:
        regressed = {cb: visible[cb]["failed"] > baseline_visible[cb]["failed"] or visible[cb]["passed"] < baseline_visible[cb]["passed"]
                     for cb in CODEBASES}
    res = {
        "source": str(src),
        "dependencies_installed": installed,
        "services_failed_to_start": failed_start,
        "acceptance": acceptance,
        "visible_tests": visible,
        "visible_tests_regressed": regressed,
        "visible_tests_baseline_now": baseline_visible and {cb: {k: baseline_visible[cb][k] for k in ("passed", "failed", "skipped")}
                                                            for cb in CODEBASES},
        **(_diff_signals(rd) if meta else {}),
    }
    by_item = {}
    for t in acceptance.get("tests", []):
        it = by_item.setdefault(t.get("item") or "?", {"passed": 0, "total": 0})
        it["total"] += 1
        it["passed"] += t.get("outcome") == "passed"
    res["summary"] = {"by_item": by_item, "services_failed_to_start": failed_start,
                      "visible_tests": {cb: {k: visible[cb][k] for k in ("passed", "failed", "skipped")} for cb in CODEBASES}}
    (rd / "evaluation.json").write_text(json.dumps(res, indent=2))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="evaluate the untouched baseline (suite calibration)")
    ap.add_argument("--offset", type=int, default=600)
    a = ap.parse_args()
    if not a.baseline:
        sys.exit("use ./bench evaluate <run-id> for runs")
    rd = ROOT / "runs" / "_baseline"
    rd.mkdir(parents=True, exist_ok=True)
    res = run_evaluation(None, rd, a.offset, source=ROOT / "baseline")
    print(json.dumps(res["summary"], indent=2))


if __name__ == "__main__":
    main()
