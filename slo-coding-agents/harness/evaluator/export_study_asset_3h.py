"""Compute the study's outcome figures: the summary table, runs above 10% checkout errors, recovery time,
high-cardinality fixes and token use.

    uv run python -m harness.evaluator.export_study_asset_3h results/assets/slo-agent-study.json

Reads results/pair_summary_flash.json (pair_summary --batch ... --out results/pair_summary_flash.json),
so run that first. Pairs that pair_summary marks excluded (a trial failed the validity check) are left out.
Every median is over the trials of one condition; p values are exact two-sided Wilcoxon signed-rank tests
over the pairs (zero differences dropped, tied ranks averaged, no multiple-comparison correction).
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONDS = ("basic", "slo")


def ts(s):
    import datetime as dt
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def wilcoxon_exact(diffs):
    """Two-sided exact Wilcoxon signed-rank p for small n, zero diffs dropped."""
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        return 1.0
    ranked = sorted((abs(x), i) for i, x in enumerate(d))
    ranks = [0.0] * n
    i = 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        r = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[ranked[k][1]] = r
        i = j
    w = sum(r for r, x in zip(ranks, d) if x > 0)
    half = sum(ranks) / 2
    # exact null distribution: every sign assignment equally likely
    cnt = sum(1 for mask in range(1 << n)
              if abs(sum(r for k, r in enumerate(ranks) if mask >> k & 1) - half) >= abs(w - half) - 1e-9)
    return cnt / (1 << n)


def med(v):
    return st.median(v) if v else None


def trial_row(t):
    rid = t["run_id"]
    rd = ROOT / "runs" / rid
    meta = json.loads((rd / "run.json").read_text())
    coll = json.loads((rd / "collected.json").read_text())
    h0 = ts(meta["handover_at"])

    # checkout success (%) per 10-minute bucket from handover; None when the bucket had no checkouts
    cs10 = [b["checkout_success"] for b in coll["sli_by_10min"]]
    vals = [x for x in cs10 if x is not None]

    # tokens from the transcript's step_finish events (input plus output, including repeated context),
    # grouped by the checkout state of the 10-minute bucket they fall in
    token_total = 0
    tok_by_state = {0: 0, 1: 0, 2: 0}  # 0: at most 1% errors, 1: over 1% to 10%, 2: over 10%
    for ln in (rd / "agent_transcript.log").read_text(errors="ignore").splitlines():
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        p = e.get("part") or {}
        if e.get("type") == "step_finish" and (p.get("tokens") or {}).get("total"):
            token_total += p["tokens"]["total"]
            b = min(int((e["timestamp"] / 1000 - h0) // 600), len(cs10) - 1)
            v = cs10[b] if 0 <= b < len(cs10) else None
            if v is not None:
                tok_by_state[state(v)] += p["tokens"]["total"]

    return {
        "batch": t["batch"], "pair": t["pair"],
        "condition": "slo" if t["condition"].startswith("slo") else "basic", "run_id": rid,
        "paid": t["paid"], "errors": t["errors"], "features": t["features"], "defects": t["defects"],
        "fixed_defects": t["fixed"], "checkout_series": cs10,
        "minutes_below_90": sum(10 for x in vals if x < 90),
        "token_total": token_total, "tok_by_state": tok_by_state,
    }


def state(success):
    """Checkout state of a bucket: 0 at most 1% errors, 1 over 1% to 10%, 2 over 10%."""
    return 2 if success < 90 else 1 if success < 99 else 0


def paired(rows, pick):
    out = {}
    per = {c: [pick(x) for x in rows if x["condition"] == c] for c in CONDS}
    for c in CONDS:
        out[c] = per[c]
        out[f"median_{c}"] = med(per[c])
    diffs = [b - s for b, s in zip(per["basic"], per["slo"])]
    out["p"] = wilcoxon_exact(diffs)
    mb, ms = out["median_basic"], out["median_slo"]
    out["change_pct"] = 100 * (ms / mb - 1) if mb else None
    return out


def session_context(rows, pairs):
    """The recorded workload schedule (one cycle) and the pair order every exporter follows."""
    phase_labels = {
        "run-steady": "Steady traffic",
        "promo": "Promotion",
        "psp-degraded": "Payment provider degraded",
        "evening": "Evening traffic",
    }
    schedules, warmups, scales = [], set(), set()
    pair_order = []
    for global_pair, pair in enumerate(pairs, 1):
        paired_rows = {x["condition"]: x for x in rows if (x["batch"], x["pair"]) == pair}
        if set(paired_rows) != set(CONDS):
            raise ValueError(f"Incomplete pair for session context: {pair}")
        pair_order.append({
            "global_pair": global_pair, "batch": pair[0], "pair": pair[1],
            "basic_run_id": paired_rows["basic"]["run_id"],
            "slo_run_id": paired_rows["slo"]["run_id"],
        })
        for condition in CONDS:
            rd = ROOT / "runs" / paired_rows[condition]["run_id"]
            meta = json.loads((rd / "run.json").read_text())
            warmups.add(meta["warmup_min"])
            scales.add(meta["time_scale"])

            # Durations and rates come from the recorded workload. The next cycle's first phase
            # supplies the end of the first evening phase.
            phase_events = [json.loads(ln) for ln in (rd / "artifacts" / "loadgen_phases.jsonl").read_text().splitlines()]
            phases = []
            for i, event in enumerate(phase_events[:-1]):
                key = event["phase"]
                if key in phase_labels:
                    phases.append({
                        "key": key, "label": phase_labels[key],
                        "duration_min": phase_events[i + 1]["scenario_min"] - event["scenario_min"],
                        "sessions_per_min": event["rate"],
                    })
            if len(phases) != len(phase_labels):
                raise ValueError(f"Incomplete recorded workload cycle: {rd.name}")
            schedules.append(phases)

    if not schedules or any(schedule != schedules[0] for schedule in schedules):
        raise ValueError("Workload schedules differ between trials")
    if len(warmups) != 1 or len(scales) != 1:
        raise ValueError("Warm-up or time scale differs between trials")
    if next(iter(scales)) != 1:
        raise ValueError("Session context requires real-time workload phases")
    return {
        "phases": schedules[0],
        "cycle_min": sum(phase["duration_min"] for phase in schedules[0]),
        "warmup_min": next(iter(warmups)),
        "time_scale": next(iter(scales)),
        "source": "runs/<run_id>/artifacts/loadgen_phases.jsonl; runs/<run_id>/run.json",
        "pair_order": pair_order,
    }


def measured_month(rows):
    """Month and year of the first handover in the study, for example "October 2026"."""
    import datetime as dt
    first = min(ts(json.loads((ROOT / "runs" / x["run_id"] / "run.json").read_text())["handover_at"]) for x in rows)
    return dt.datetime.fromtimestamp(first, dt.timezone.utc).strftime("%B %Y")


def main():
    out = Path(sys.argv[1])
    summary = json.loads((ROOT / "results" / "pair_summary_flash.json").read_text())
    rows = [trial_row(t) for t in summary if not t.get("excluded")]
    rows.sort(key=lambda x: (x["batch"], x["pair"], x["condition"]))
    pairs = sorted({(x["batch"], x["pair"]) for x in rows})

    # summary table and At a glance: medians per condition, paired p, change of the medians
    headline = {
        "paid": paired(rows, lambda x: x["paid"]),
        "checkout_errors": paired(rows, lambda x: x["errors"]),
        "minutes_below_90": paired(rows, lambda x: x["minutes_below_90"]),
        "features": paired(rows, lambda x: x["features"]),
        "defects_fixed": paired(rows, lambda x: x["defects"]),
    }

    # runs above 10% checkout errors: per 10-minute bucket, trials below 90% success out of trials reporting
    n_blocks = max(len(x["checkout_series"]) for x in rows)
    timeline = {"minutes": [i * 10 for i in range(n_blocks)]}
    for c in CONDS:
        trials = [x["checkout_series"] for x in rows if x["condition"] == c]
        reporting = [[t[i] for t in trials if i < len(t) and t[i] is not None] for i in range(n_blocks)]
        timeline[c] = {
            "trials": trials,
            "above_10pct_errors": [sum(v < 90 for v in vs) for vs in reporting],
            "reporting": [len(vs) for vs in reporting],
        }

    # fixes per high-cardinality defect: the final code passes every hidden test for that defect
    hc = {c: {h: {"fixed": sum(1 for x in rows if x["condition"] == c and h in x["fixed_defects"]),
                  "n": sum(1 for x in rows if x["condition"] == c)}
              for h in ("HC-1", "HC-2", "HC-3")} for c in CONDS}

    # recovery time: onset is the first 10-minute bucket at or after minute 20 below 90% success; recovery
    # is the first later bucket at or above 90%. Median over the affected trials that recovered.
    def onset_idx(x):
        for i in range(2, len(x["checkout_series"])):
            v = x["checkout_series"][i]
            if v is not None and v < 90:
                return i
        return None

    def recovered_idx(x, i0):
        for j in range(i0 + 1, len(x["checkout_series"])):
            v = x["checkout_series"][j]
            if v is not None and v >= 90:
                return j
        return None

    mttr = {}
    for c in CONDS:
        rs = [x for x in rows if x["condition"] == c]
        collapsed = [(x, onset_idx(x)) for x in rs if onset_idx(x) is not None]
        times = [(j - i0) * 10 for x, i0 in collapsed if (j := recovered_idx(x, i0)) is not None]
        mttr[c] = {"trials": len(rs), "collapsed": len(collapsed), "recovered": len(times),
                   "mttr_to_90_min": med(times)}
    mb, ms = mttr["basic"]["mttr_to_90_min"], mttr["slo"]["mttr_to_90_min"]
    mttr["change_pct"] = 100 * (ms / mb - 1) if mb else None

    # tokens: median total per trial (millions), and tokens per hour in each checkout state, all tokens
    # over all hours in that state across the condition's trials
    tokens = {"total_median_millions": {c: round(med([x["token_total"] for x in rows if x["condition"] == c]) / 1e6, 1)
                                        for c in CONDS},
              "states": {"0": "at most 1% checkout errors", "1": "over 1% to 10%", "2": "over 10%"}}
    for c in CONDS:
        hrs = {0: 0.0, 1: 0.0, 2: 0.0}
        for x in rows:
            if x["condition"] != c:
                continue
            for v in x["checkout_series"]:
                if v is not None:
                    hrs[state(v)] += 10 / 60
        rate = {k: sum(x["tok_by_state"][k] for x in rows if x["condition"] == c) / hrs[k] / 1e6
                for k in (0, 1, 2) if hrs[k] > 0}
        tokens[c] = {str(k): round(v, 1) for k, v in rate.items()}
        tokens[c]["rise_1_to_10pct_vs_at_most_1pct"] = (
            round(100 * (rate[1] / rate[0] - 1), 1) if 0 in rate and 1 in rate else None)

    asset = {
        "measured": measured_month(rows),
        "model": "GLM 5.3 Flash",
        "duration_min": 180,
        "pairs": len(pairs),
        "headline": headline,
        "timeline": timeline,
        "high_cardinality": hc,
        "mttr": mttr,
        "tokens": tokens,
        "session_context": session_context(rows, pairs),
    }
    out.write_text(json.dumps(asset, indent=1) + "\n")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB), {len(pairs)} pairs")
    for k, v in headline.items():
        print(f"  {k}: {v['median_basic']} to {v['median_slo']} ({v['change_pct']:+.0f}%, p={v['p']:.3f})")
    print(f"  recovery: {mb} to {ms} min; tokens: {tokens}")


if __name__ == "__main__":
    main()
