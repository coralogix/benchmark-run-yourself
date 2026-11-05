"""Failed delivery-slot holds in the first hour after handover.

    uv run python -m harness.evaluator.audit_checkout_mechanics \
      --study-asset results/assets/slo-agent-study.json \
      --priorities-out results/assets/slo-reliability-priorities.json

Reads every trial's load-generator records (artifacts/loadgen.jsonl) between handover and deadline.
Counts primary slot-hold requests ("hold") in the first hour that ended in an error or a timeout. Valid
slot_full rejections are client rejections and do not count; retries ("hold_retry") are excluded. The
figure is a total across the 20 trials of each condition, not a median. As a cross-check, each trial's
successful checkouts in the same records must equal the study asset's per-trial value.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from harness.evaluator.records import ROOT, timestamp

CONDITIONS = ('basic', 'slo')
FAILED = ('error', 'timeout')


def trial_counts(run_id):
    rd = ROOT / 'runs' / run_id
    meta = json.loads((rd / 'run.json').read_text())
    start, end = timestamp(meta['handover_at']), timestamp(meta['deadline_at'])
    holds_first_hour, checkout = collections.Counter(), collections.Counter()
    with (rd / 'artifacts/loadgen.jsonl').open() as source:
        for line in source:
            row = json.loads(line)
            at = timestamp(row['ts'])
            if not start <= at < end:
                continue
            if row.get('step') == 'hold' and at - start < 3600:
                holds_first_hour[row['outcome']] += 1
            elif row.get('step') == 'checkout':
                checkout[row['outcome']] += 1
    collected = json.loads((rd / 'collected.json').read_text())['workload']
    assert dict(checkout) == collected['checkout_outcomes'], run_id
    return holds_first_hour, checkout


def export(study):
    conditions = {}
    for condition in CONDITIONS:
        failed, trials = 0, []
        for pair in study['session_context']['pair_order']:
            rid = pair[condition + '_run_id']
            holds, checkout = trial_counts(rid)
            assert checkout['ok'] == study['headline']['paid'][condition][pair['global_pair'] - 1], rid
            n = sum(holds[k] for k in FAILED)
            failed += n
            trials.append({'pair': pair['global_pair'], 'run_id': rid, 'failed_slot_holds_first_hour': n})
            print(f'pair {pair["global_pair"]:02d} {condition}: {n}', flush=True)
        conditions[condition] = {'trials': len(trials), 'failed_slot_holds_first_hour': failed, 'per_trial': trials}
    basic, slo = (conditions[c]['failed_slot_holds_first_hour'] for c in CONDITIONS)
    return {
        'schema_version': 1,
        'definition': 'First-hour primary delivery-slot hold requests from the load generator that ended in an error or timeout. Valid slot_full rejections and hold_retry requests are excluded. Totals across all trials of each condition.',
        'conditions': conditions,
        'reduction_pct': 100 * (1 - slo / basic) if basic else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-asset', type=Path, required=True)
    parser.add_argument('--priorities-out', type=Path, required=True)
    args = parser.parse_args()
    result = export(json.loads(args.study_asset.read_text()))
    args.priorities_out.write_text(json.dumps(result, indent=2) + '\n')
    print({c: r['failed_slot_holds_first_hour'] for c, r in result['conditions'].items()},
          f"reduction {result['reduction_pct']:.1f}%")


if __name__ == '__main__':
    main()
