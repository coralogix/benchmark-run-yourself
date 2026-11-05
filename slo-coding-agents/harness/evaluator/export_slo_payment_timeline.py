"""Cumulative captured transactions per 10 minutes for every trial in the study asset.

Uses the same deduplication and handover/deadline window as export_slo_revenue.
Each plotted value is a median of 20 cumulative trial counts, never a sum of
bucket medians. Warm-up captures and duplicate charges are excluded.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from pathlib import Path

from harness.evaluator.records import ROOT, timestamp


def export(study, revenue):
    minutes = list(range(0, study['duration_min'] + 1, 10))
    trials = []
    audited = {(r['pair'], r['condition']): r for r in revenue['trials']}
    for pair in study['session_context']['pair_order']:
        for condition in ('basic', 'slo'):
            rid = pair[condition + '_run_id']
            rd = ROOT / 'runs' / rid
            meta = json.loads((rd / 'run.json').read_text())
            start, end = timestamp(meta['handover_at']), timestamp(meta['deadline_at'])
            charges = json.loads((rd / 'artifacts/psp_ledger.json').read_text())['charges']
            seen, paid_times, cumulative_pence = set(), [], [0]
            for charge in sorted(charges, key=lambda r: r['created_at']):
                if charge['status'] != 'captured' or charge['order_ref'] in seen:
                    continue
                seen.add(charge['order_ref'])
                at = timestamp(charge['created_at'])
                if start <= at < end:
                    paid_times.append((at - start) / 60)
                    cumulative_pence.append(cumulative_pence[-1] + charge['amount_pence'])
            counts = [bisect.bisect_left(paid_times, minute) for minute in minutes]
            values = [cumulative_pence[count] / 100 for count in counts]
            original = audited[pair['global_pair'], condition]
            assert counts[-1] == original['orders_paid']
            assert values[-1] == original['revenue_gbp']
            assert counts[0] == 0 and all(a <= b for a, b in zip(counts, counts[1:]))
            trials.append({'pair': pair['global_pair'], 'condition': condition,
                           'transactions': counts, 'revenue_gbp': values,
                           'source': f'runs/{rid}/artifacts/psp_ledger.json'})
    conditions = {}
    for condition in ('basic', 'slo'):
        rows = [row for row in trials if row['condition'] == condition]
        assert len(rows) == study['pairs'] == 20
        conditions[condition] = {'trials_at_each_point': [len(rows)] * len(minutes),
            'median_transactions': [statistics.median(r['transactions'][i] for r in rows) for i in range(len(minutes))],
            'median_revenue_gbp': [statistics.median(r['revenue_gbp'][i] for r in rows) for i in range(len(minutes))]}
        assert conditions[condition]['median_revenue_gbp'][-1] == revenue['conditions'][condition]['median_gbp']
    return {'schema_version': 1, 'minutes': minutes,
            'definition': 'Cumulative first captured payment per order since agent handover. Excludes warm-up captures and duplicate charges. Pointwise medians across all 20 trials in each condition; no missing values, smoothing or imputation. Counts captured card transactions, which differ from completed checkout responses.',
            'conditions': conditions, 'trials': trials}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--study-asset', type=Path, required=True)
    parser.add_argument('--revenue-asset', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = export(json.loads(args.study_asset.read_text()), json.loads(args.revenue_asset.read_text()))
    content = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
    if args.check:
        assert args.out.read_text() == content, 'Payment timeline asset is stale'
    else:
        args.out.write_text(content)
    print({c: r['median_transactions'][-1] for c, r in result['conditions'].items()})


if __name__ == '__main__':
    main()
