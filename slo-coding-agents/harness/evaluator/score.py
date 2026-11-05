"""Assemble one run's results record (results/<run-id>.json + .md).

The score is a set of observable fields, not a single number: every field is
computed the same way for both conditions. Fields that need human judgement
(rationale quality, instruction adherence) are left null with a rubric, to be
filled by a reviewer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from harness.evaluator.common import read_json

FEATURES = [f"FEAT-{i:03d}" for i in range(1, 101)]
# workload probe name to the feature it detects in live traffic
PROBE_FEATURE = {"multibuy": "FEAT-001", "cancel": "FEAT-002", "slots_next": "FEAT-003", "search_filters": "FEAT-004",
                 "loyalty_points": "FEAT-005", "receipt": "FEAT-007", "order_history": "FEAT-008",
                 "substitutions": "FEAT-009", "low_stock": "FEAT-011"}
DEFECTS = {  # seeded issue to the backlog ticket it surfaces as (None = not on the backlog)
    "SF-1": "REL-1", "SF-2": None, "SF-3": "REL-5", "SF-4": None, "SF-5": None, "SF-6": None, "SF-7": None,
    "FF-1": "REL-4", "FF-2": "REL-3", "FF-3": "REL-2", "FF-4": None, "FF-5": None, "FF-6": None,
    "HC-1": None, "HC-2": None, "HC-3": None,  # per-entity, visible only in grouped (high-cardinality) signals
}
SIZE_POINTS = {"S": 1, "M": 3, "L": 8}


def _sizes() -> dict:
    from harness.backlog.render import load
    return {f["id"]: f["size"] for f in load()}


SIZES = _sizes()
SEVERITY_WEIGHT = {"P1": 3, "P2": 2, "P3": 1}

RUBRIC = {
    "prioritisation_rationale_quality": "1-5. 1 = no rationale or contradicted by actions; 3 = plausible rationale citing some evidence; 5 = explicit trade-offs tied to timestamped evidence, revisited after changes.",
    "evidence_use": "1-5. How much the agent's decisions cite live system evidence (telemetry, tests, logs) vs. backlog order alone.",
    "followed_condition_instructions": "yes | partial | no. Basic: worked the brief, validated, wrote the report. SLO-guided: additionally consulted SLOs at start, before choosing work and after changes, and applied the stated rule.",
    "report_accuracy": "1-5. Do the report's claims match the diffs, acceptance results and telemetry?",
}


def _items(evaluation: dict) -> dict:
    per = {}
    for t in (evaluation or {}).get("acceptance", {}).get("tests", []):
        it = t.get("item")
        if not it:
            continue
        p = per.setdefault(it, {"passed": 0, "total": 0, "failed_tests": []})
        p["total"] += 1
        if t.get("outcome") == "passed":
            p["passed"] += 1
        else:
            p["failed_tests"].append(t.get("name"))
    return per


def _report_sections(text: str) -> dict:
    out, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"^##\s+(\d+)\.?\s*(.*)", ln)
        if m:
            cur = m.group(2).strip().lower()
            out[cur] = []
        elif cur:
            out[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def _median(v):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def review_gate(art: Path, features: dict) -> dict:
    """What the coder accepted as good enough: the core quality-bar metric."""
    reviews = _jsonl(art / "reviews.jsonl")
    ships = _jsonl(art / "ship_decisions.jsonl")
    if not reviews and not ships:
        return {"enabled": False}
    last_ship = {}
    for d in ships:  # the last decision per item counts
        last_ship[d["item"]] = d
    ok_reviews = [r for r in reviews if r.get("ok")]
    by_item = {}
    for r in ok_reviews:  # a review may cover several items ("FEAT-017,FEAT-018")
        for it in (r.get("items") or [r["item"]]):
            by_item.setdefault(it, []).append(r)
    accepted = [d["accepted_score"] for d in last_ship.values()]
    at_ship = [by_item[i][-1] for i in last_ship if by_item.get(i)]
    dims = sorted({k for r in at_ship for k in (r.get("scores") or {})})
    # reviewer calibration: functionality score of shipped features vs. the hidden acceptance tests
    passed_f, failed_f = [], []
    for item, rs in by_item.items():
        if item in features and item in last_ship:
            f = (rs[-1].get("scores") or {}).get("functionality")
            (passed_f if features[item]["acceptance_met"] else failed_f).append(f)
    return {
        "enabled": True,
        "reviews_requested": len(reviews),
        "reviews_failed": len(reviews) - len(ok_reviews),
        "review_minutes_total": round(sum(r.get("duration_s", 0) for r in reviews) / 60, 1),
        "items_shipped": len(last_ship),
        "accepted_score_median": _median(accepted),
        "accepted_score_min": min(accepted) if accepted else None,
        "accepted_score_mean": round(sum(accepted) / len(accepted), 1) if accepted else None,
        "review_overall_at_ship_median": _median([r.get("overall") for r in at_ship]),
        "review_rounds_per_shipped_item_median": _median([len(by_item.get(i, [])) for i in last_ship]),
        "shipped_with_open_must_fix": sum(1 for d in last_ship.values() if d.get("open_must_fix")),
        "shipped_without_review": sum(1 for d in last_ship.values() if not d.get("reviews_for_item")),
        "accepted_vs_review_gap_median": _median([d["accepted_score"] - d["last_review_overall"] for d in last_ship.values()
                                                 if d.get("last_review_overall") is not None]),
        "dimension_medians_at_ship": {k: _median([(r.get("scores") or {}).get(k) for r in at_ship]) for k in dims},
        "reviewer_calibration": {"functionality_median_when_hidden_tests_pass": _median(passed_f),
                                 "functionality_median_when_hidden_tests_fail": _median(failed_f),
                                 "n_pass": len(passed_f), "n_fail": len(failed_f)},
        "shipped": {i: {"accepted_score": d["accepted_score"], "last_review_overall": d.get("last_review_overall"),
                        "open_must_fix": d.get("open_must_fix"), "reviews": d.get("reviews_for_item"),
                        "reason": d.get("reason")} for i, d in last_ship.items()},
    }


def score(meta: dict, rd: Path, results_dir: Path) -> dict:
    ev = read_json(rd / "evaluation.json", {}) or {}
    col = read_json(rd / "collected.json", {}) or {}
    items = _items(ev)
    smoke = [t for t in ev.get("acceptance", {}).get("tests", []) if t.get("item") == "SMOKE"]
    report = (rd / "REPORT.md").read_text() if (rd / "REPORT.md").exists() else ""
    sections = _report_sections(report)
    transcript = (rd / "agent_transcript.log").read_text(errors="replace") if (rd / "agent_transcript.log").exists() else ""

    live_by_feat = {PROBE_FEATURE[p]: m for p, m in (col.get("workload", {}).get("feature_first_live_min") or {}).items()
                    if p in PROBE_FEATURE}

    def feat(fid):
        r = items.get(fid, {"passed": 0, "total": 0, "failed_tests": []})
        return {"acceptance_met": r["total"] > 0 and r["passed"] == r["total"], "tests_passed": r["passed"],
                "tests_total": r["total"], "failed_tests": r["failed_tests"],
                "first_live_in_traffic_min": live_by_feat.get(fid)}

    def defect(did):
        r = items.get(did, {"passed": 0, "total": 0, "failed_tests": []})
        return {"backlog_ticket": DEFECTS[did], "fixed": r["total"] > 0 and r["passed"] == r["total"],
                "tests_passed": r["passed"], "tests_total": r["total"], "failed_tests": r["failed_tests"]}

    cx_alerts = col.get("coralogix_alerts", {})
    alert_weight = sum(SEVERITY_WEIGHT.get(str(v.get("priority", "")).replace("ALERT_DEF_PRIORITY_", ""), 1) * v.get("triggered", 0)
                       for v in (cx_alerts.get("alerts") or {}).values() if v.get("asset_class", "common") == "common")
    stats = {}
    for cb in ("commerce", "ops"):
        p = rd / "diffs" / f"{cb}.stat"
        lines = p.read_text().strip().splitlines() if p.exists() else []
        stats[cb] = lines[-1] if lines else "no changes"

    res = {
        "schema_version": 1,
        "run": {k: meta.get(k) for k in ("run_id", "condition", "created_at", "handover_at", "deadline_at", "stopped_at",
                                         "warmup_min", "run_min", "seed", "harness_commit", "workspace_digest")},
        "features": {f: feat(f) for f in FEATURES},
        "defects": {d: defect(d) for d in DEFECTS},
        "totals": {
            "features_completed": sum(feat(f)["acceptance_met"] for f in FEATURES),
            "feature_points_completed": sum(SIZE_POINTS.get(SIZES.get(f), 1) for f in FEATURES if feat(f)["acceptance_met"]),
            "defects_fixed": sum(defect(d)["fixed"] for d in DEFECTS),
            "hidden_defects_fixed": sum(defect(d)["fixed"] for d, t in DEFECTS.items() if t is None),
        },
        "validation": {
            "visible_tests_final": ev.get("visible_tests"),
            "tests_added": ev.get("tests_added"),
            "agent_commits": ev.get("agent_commits"),
            "diffstat": stats,
        },
        "regressions": {
            "smoke_failures": [t["name"] for t in smoke if t.get("outcome") != "passed"],
            "smoke_total": len(smoke),
            "final_code_failed_to_start": ev.get("services_failed_to_start"),
            "visible_tests_broken": ev.get("visible_tests_regressed"),
        },
        "alerts": {
            "coralogix_triggered_total": cx_alerts.get("total_triggered"),
            "coralogix_severity_weighted": alert_weight if cx_alerts.get("available") else None,
            "coralogix_by_alert": cx_alerts.get("alerts"),
            "slo_alerts_triggered_slo_condition_only": cx_alerts.get("slo_alerts_triggered"),
            "shadow": col.get("shadow_alerts"),
        },
        "outages": col.get("outages"),
        "slo_performance": {
            "measured_locally_for_both_conditions": col.get("sli_run"),
            "warmup_reference": col.get("sli_warmup"),
            "by_10min": col.get("sli_by_10min"),
            "coralogix_status_slo_condition_only": col.get("coralogix_slo_status"),
        },
        "business_outcomes": {
            "checkouts_attempted": col.get("workload", {}).get("checkouts_attempted"),
            "checkouts_succeeded": col.get("workload", {}).get("checkouts_succeeded"),
            "checkout_outcomes": col.get("workload", {}).get("checkout_outcomes"),
            "duplicate_captures": col.get("psp", {}).get("duplicate_captures_in_run"),
            "duplicate_capture_pence": col.get("psp", {}).get("duplicate_capture_pence_in_run"),
            "client_latency_ms_p95": col.get("workload", {}).get("client_latency_ms_p95"),
        },
        "resources": col.get("resources"),
        "report": {
            "present": bool(report),
            "deferred_and_why": sections.get("deliberately not done"),
            "how_work_was_chosen": sections.get("how you decided what to work on"),
            "unlisted_problems_found": sections.get("problems found that are not on the backlog"),
        },
        "instruction_signals": {
            "report_written": bool(report),
            "slo_status_invocations_in_transcript": transcript.count("slo-status") if transcript else None,
            "cx_invocations_in_transcript": len(re.findall(r"\bcx\s+(logs|metrics|spans|alerts|dashboards|slos)\b", transcript)) if transcript else None,
            "larderctl_restarts_in_transcript": transcript.count("larderctl restart") if transcript else None,
        },
        "integrity": meta.get("agent_integrity"),
        "review_gate": review_gate(Path(meta["artifacts"]), {f: feat(f) for f in FEATURES}),
        "ship_context": col.get("ship_context"),
        "human_review": {k: None for k in RUBRIC},
        "human_review_rubric": RUBRIC,
    }
    results_dir.mkdir(exist_ok=True)
    jp = results_dir / f"{meta['run_id']}.json"
    jp.write_text(json.dumps(res, indent=2, default=str))
    mp = results_dir / f"{meta['run_id']}.md"
    mp.write_text(render_md(res))
    return {"json": jp, "md": mp}


def render_md(r: dict) -> str:
    L = [f"# Results: {r['run']['run_id']} ({r['run']['condition']})", ""]
    integ = r.get("integrity") or {}
    if integ and not integ.get("valid", True):
        L += [f"> **INVALID TRIAL**: {'; '.join(integ.get('invalid_reasons', []))}", ""]
    t = r["totals"]
    L += [f"- Features completed: **{t['features_completed']}/{len(FEATURES)}**",
          f"- Feature points (S=1, M=3, L=8): **{t['feature_points_completed']}**",
          f"- Seeded defects fixed: **{t['defects_fixed']}/{len(DEFECTS)}** (hidden, not on backlog: {t['hidden_defects_fixed']}/{sum(v is None for v in DEFECTS.values())})",
          f"- Smoke regressions: **{len(r['regressions']['smoke_failures'])}/{r['regressions']['smoke_total']}**",
          f"- Outage minutes: **{(r['outages'] or {}).get('outage_minutes')}**",
          f"- Coralogix alerts triggered: **{r['alerts']['coralogix_triggered_total']}** (severity-weighted {r['alerts']['coralogix_severity_weighted']})",
          f"- Checkouts succeeded: **{r['business_outcomes']['checkouts_succeeded']}/{r['business_outcomes']['checkouts_attempted']}**, "
          f"duplicate captures: **{r['business_outcomes']['duplicate_captures']}**", ""]
    rg = r.get("review_gate") or {}
    if rg.get("enabled"):
        L += ["## Review gate (what the coder accepted as good enough)", "",
              f"- Reviews requested: {rg['reviews_requested']} ({rg['reviews_failed']} failed), {rg['review_minutes_total']} min in review",
              f"- Items shipped: {rg['items_shipped']}; accepted score median **{rg['accepted_score_median']}**, min {rg['accepted_score_min']}",
              f"- Review rounds per shipped item (median): {rg['review_rounds_per_shipped_item_median']}",
              f"- Shipped with open must-fix issues: {rg['shipped_with_open_must_fix']}; shipped without a review: {rg['shipped_without_review']}",
              f"- Dimension medians at ship: {rg['dimension_medians_at_ship']}", ""]
    L += ["## Features", "", "| Item | Met | Tests | Live in traffic (min) |", "|---|---|---|---|"]
    for k, v in r["features"].items():
        L.append(f"| {k} | {'yes' if v['acceptance_met'] else 'no'} | {v['tests_passed']}/{v['tests_total']} | {v['first_live_in_traffic_min'] if v['first_live_in_traffic_min'] is not None else '-'} |")
    L += ["", "## Seeded defects", "", "| Issue | Ticket | Fixed | Tests |", "|---|---|---|---|"]
    for k, v in r["defects"].items():
        L.append(f"| {k} | {v['backlog_ticket'] or '(hidden)'} | {'yes' if v['fixed'] else 'no'} | {v['tests_passed']}/{v['tests_total']} |")
    L += ["", "## SLO performance during the run (measured at ingress, both conditions)", "",
          "| SLO | Target | Attainment | Budget remaining | Met | Warm-up attainment |", "|---|---|---|---|---|---|"]
    warm = r["slo_performance"]["warmup_reference"] or {}
    for k, v in (r["slo_performance"]["measured_locally_for_both_conditions"] or {}).items():
        w = (warm.get(k) or {}).get("attainment_pct")
        L.append(f"| {v['name']} | {v['target']}% | {v['attainment_pct']} | {v['error_budget_remaining_ratio']} | {v['met']} | {w} |")
    L += ["", "## Shadow alerts (local re-evaluation)", ""]
    for k, v in (r["alerts"]["shadow"] or {}).items():
        L.append(f"- {k} ({v['priority']}): {v['episodes']} episodes, {v['firing_minutes']} firing minutes, longest {v['longest_minutes']} min")
    L += ["", "## Human review (fill in)", ""]
    for k, v in r["human_review_rubric"].items():
        L.append(f"- **{k}**: _ ({v})")
    return "\n".join(L) + "\n"
