"""Per-trial summary of the two batches, with each pair's validity.

    uv run python -m harness.evaluator.pair_summary --batch <batch-a> --batch <batch-b> --out results/pair_summary_flash.json

Reads runs/<batch>.json, runs/<run-id>/ and results/<run-id>.json for every fetched trial. A pair is
excluded when either of its trials fails the validity check (harness.bench.agent_integrity: a model or
API error reached the agent's session, or a model request failed). The check reads the raw artifacts
only, never the trial's outcome, and excludes the whole pair.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.bench import agent_integrity

ROOT = Path(__file__).resolve().parents[2]


def trial(rid: str) -> dict:
    rd = ROOT / "runs" / rid
    meta = json.loads((rd / "run.json").read_text())
    r = json.loads((ROOT / "results" / f"{rid}.json").read_text())
    ig = agent_integrity({"artifacts": str(rd / "artifacts")}, rd / "agent_transcript.log")
    t, bo = r["totals"], r["business_outcomes"]
    return {"run_id": rid, "condition": meta["condition"], "valid": ig["valid"], "invalid_reasons": ig["invalid_reasons"],
            "features": t["features_completed"], "defects": t["defects_fixed"],
            "fixed": [k for k, v in r["defects"].items() if v["fixed"]],
            "paid": bo["checkouts_succeeded"], "errors": (bo.get("checkout_outcomes") or {}).get("error", 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append", required=True)
    ap.add_argument("--out", default=str(ROOT / "results" / "pair_summary_flash.json"))
    a = ap.parse_args()
    out = []
    for batch in a.batch:
        m = json.loads((ROOT / "runs" / f"{batch}.json").read_text())
        for t in sorted(m["trials"], key=lambda t: (t["pair"], t["condition"])):
            tgz = next((ROOT / "runs" / f"ec2-{t['tag']}").glob("*.tgz"), None) if (ROOT / "runs" / f"ec2-{t['tag']}").exists() else None
            if tgz and (ROOT / "results" / f"{tgz.name[:-4]}.json").exists():
                out.append({"batch": batch, "pair": t["pair"], **trial(tgz.name[:-4])})
    excluded = {(x["batch"], x["pair"]) for x in out if not x["valid"]}
    for x in out:
        x["excluded"] = (x["batch"], x["pair"]) in excluded
    Path(a.out).write_text(json.dumps(out, indent=1))
    for x in out:
        print(f"{x['batch']} pair {x['pair']:02d} {x['condition']:10} {'EXCLUDED ' if x['excluded'] else ''}"
              f"valid={x['valid']} {'; '.join(x['invalid_reasons'])} features {x['features']} defects {x['defects']} "
              f"paid {x['paid']} errors {x['errors']}")


if __name__ == "__main__":
    main()
