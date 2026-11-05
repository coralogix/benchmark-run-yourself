"""Verify that a prepared run exposes exactly its condition's information.

Checks:
  workspace  - SLO files/tools present only for slo-guided; instructions identical to the
               other condition outside the condition block; shared kit files unmodified
  account    - basic account: no SLOs, no SLO dashboard; both: the common dashboards/alerts
  agent key  - the agent profile can read telemetry; basic agent profile cannot see SLOs
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harness import bench
from harness.cx import assets, deploy

SLO_WORDS = re.compile(r"\b(SLOs?|error[- ]budgets?|burn[- ]rates?|service level objectives?)\b", re.I)


def _agent_cx(profile, *args):
    p = subprocess.run(["cx", "-p", profile, "--read-only", "--no-console-link", "-o", "json", *args],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def verify(meta: dict, cfg: dict) -> dict:
    checks = []

    def check(name, ok, detail="", warn_only=False):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "warn_only": warn_only})

    ws = Path(meta["workspace"])
    cond = meta["condition"]
    partner = {"basic": "slo-guided", "slo-guided": "basic"}[cond]
    slo_files = [p for p in ("SLO_CATALOG.md", "tools/slo-status", ".larder/slo_catalog.json") if (ws / p).exists()]
    instr = (ws / "AGENT_INSTRUCTIONS.md").read_text()

    if cond == "basic":
        check("workspace has no SLO files", not slo_files, ",".join(slo_files))
        hits = []
        for p in ws.rglob("*"):
            if p.is_file() and not any(x in p.parts for x in ("node_modules", ".venv", ".git", "var", "data")) and p.suffix in (".md", ".txt", ".env", ".json", "") and p.stat().st_size < 2_000_000:
                try:
                    if SLO_WORDS.search(p.read_text(errors="ignore")):
                        hits.append(str(p.relative_to(ws)))
                except (OSError, UnicodeDecodeError):
                    pass
        check("no SLO vocabulary in basic workspace docs/config", not hits, ",".join(hits[:10]))
    else:
        check("workspace has SLO catalogue and tool", len(slo_files) == 3, ",".join(slo_files))

    # instructions differ from the partner condition only by the condition block
    mine = bench.render_instructions(cond, meta)
    theirs = bench.render_instructions(partner, meta)
    check("instructions match the rendered template", instr == mine)
    block_mine = (bench.KIT / cond / "CONDITION_BLOCK.md").read_text().rstrip() + "\n"
    block_partner = (bench.KIT / partner / "CONDITION_BLOCK.md").read_text().rstrip() + "\n"
    check("instructions identical outside the condition block", mine.replace(block_mine, "") == theirs.replace(block_partner, ""))
    check("shared kit files unchanged", meta.get("kit_digest") == bench.tree_digest_kit(ws))
    check("codebases identical to baseline at prepare", all(
        meta.get("workspace_digest", {}).get(cb) == bench.tree_digest(bench.BASELINE / cb) for cb in bench.CODEBASES))

    op = (meta.get("account") or {}).get("operator_profile")
    agent = (meta.get("account") or {}).get("agent_profile")
    if op:
        inv = deploy.inspect(op)
        names = {d["name"] for d in inv["dashboards"]}
        common = {d["name"] for d in assets.common_dashboards(meta["run_id"])}
        check("common dashboards present", common <= names, str(sorted(common - names)))
        check("common alerts present", {a["name"] for a in assets.common_alerts(meta["run_id"])} <= {a["name"] for a in inv["alerts"]})
        if cond == "basic":
            check("basic account has no SLOs at all", not inv["slos"], f"{len(inv['slos'])} SLOs")
            check("basic account has no SLO dashboard", not any("SLO" in n for n in names))
            check("basic account has no SLO alerts", not any(a["name"].startswith("Larder · SLO") for a in inv["alerts"]))
        else:
            from harness.cx.sli import slo_set
            study = slo_set(cond)
            check("SLO account has the run's SLOs", len(inv["slos_managed"]) == len(assets.slos(meta["run_id"], study)))
            n_slo_alerts = sum(a["name"].startswith("Larder · SLO") for a in inv["alerts"])
            expected = len(assets.slo_alerts(meta["run_id"], {s.key: "x" for s in study}, study))
            check("SLO account has burn-rate, budget and SLO-engine alerts", n_slo_alerts == expected,
                  f"{n_slo_alerts} SLO alerts, {expected} expected")
        b_op = cfg.get("LARDER_BASIC_OPERATOR_PROFILE")
        s_op = cfg.get("LARDER_SLO_OPERATOR_PROFILE")
        check("conditions use different Coralogix accounts", b_op and s_op and b_op != s_op,
              "single-account mode: valid for harness validation only", warn_only=True)
    if agent:
        rc, out, err = _agent_cx(agent, "metrics", "query", f'count(larder_ingress_requests_total{{bench_run_id="{meta["run_id"]}"}})')
        check("agent profile can query metrics", rc == 0, err[-200:])
        rc, out, err = _agent_cx(agent, "slos", "list")
        if cond == "basic":
            empty = rc != 0 or out.strip() in ("", "[]") or json.loads(out[out.find("["):] or "[]") == []
            check("basic agent profile sees no SLOs", empty, (out or err)[-200:])
        else:
            check("SLO agent profile can list SLOs", rc == 0 and out.strip() not in ("", "[]"), err[-200:])
    return {"run_id": meta["run_id"], "condition": cond, "ok": all(c["ok"] for c in checks if not c["warn_only"]),
            "warnings": [c["check"] for c in checks if c["warn_only"] and not c["ok"]], "checks": checks}
