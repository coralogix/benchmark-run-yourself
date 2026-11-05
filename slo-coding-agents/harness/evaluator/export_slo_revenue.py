"""Export captured, deduplicated order value for the 40-trial SLO study."""
from __future__ import annotations
import argparse
import json
import statistics
from pathlib import Path
from harness.evaluator.common import parse_ts
from harness.evaluator.records import ROOT, timestamp


def revenue(art: Path, h0: float, h1: float) -> dict:
    """Independent cross-check: card payments the PSP simulator captured between handover and deadline,
    first capture per order only."""
    led = json.loads((art / "psp_ledger.json").read_text()) if (art / "psp_ledger.json").exists() else {}
    seen, gbp, dup_pence, dup_n = set(), 0, 0, 0
    for ch in led.get("charges") or []:
        t = parse_ts(ch.get("created_at"))
        if ch.get("status") != "captured" or t is None or not (h0 <= t < h1):
            continue
        if ch["order_ref"] in seen:
            dup_pence += ch["amount_pence"]
            dup_n += 1
            continue
        seen.add(ch["order_ref"])
        gbp += ch["amount_pence"]
    return {"orders_paid": len(seen), "revenue_gbp": round(gbp / 100, 2),
            "duplicate_charges": dup_n, "duplicate_charged_gbp": round(dup_pence / 100, 2)}


def export(study):
    trials = []
    for pair in study['session_context']['pair_order']:
        for condition in ('basic', 'slo'):
            rid = pair[condition + '_run_id']
            rd = ROOT / 'runs' / rid
            meta = json.loads((rd / 'run.json').read_text())
            start, end = timestamp(meta['handover_at']), timestamp(meta['deadline_at'])
            ledger = json.loads((rd / 'artifacts/psp_ledger.json').read_text())
            seen, pence, paid, duplicate_pence, duplicates = set(), 0, 0, 0, 0
            for charge in sorted(ledger['charges'], key=lambda row: row['created_at']):
                if charge['status'] != 'captured':
                    continue
                at, order = timestamp(charge['created_at']), charge['order_ref']
                if order in seen:
                    if start <= at < end:
                        duplicate_pence += charge['amount_pence']; duplicates += 1
                    continue
                seen.add(order)
                if start <= at < end:
                    assert isinstance(charge['amount_pence'], int) and charge['amount_pence'] >= 0
                    pence += charge['amount_pence']; paid += 1
            prior = revenue(rd / 'artifacts', start, end)
            assert (pence / 100, paid, duplicates) == (prior['revenue_gbp'], prior['orders_paid'], prior['duplicate_charges'])
            trials.append({'pair': pair['global_pair'], 'condition': condition, 'run_id': rid,
                           'revenue_gbp': pence / 100, 'orders_paid': paid,
                           'excluded_duplicate_charges': duplicates, 'excluded_duplicate_gbp': duplicate_pence / 100,
                           'source': f'runs/{rid}/artifacts/psp_ledger.json'})
    conditions = {}
    for condition in ('basic', 'slo'):
        rows = [row for row in trials if row['condition'] == condition]
        conditions[condition] = {'trials': len(rows), 'median_gbp': statistics.median(row['revenue_gbp'] for row in rows),
                                 'total_gbp': round(sum(row['revenue_gbp'] for row in rows), 2)}
    return {'schema_version': 1, 'currency': 'GBP', 'definition': 'Simulated card payments captured during each three-hour trial. Count the first captured charge per order; exclude warm-up captures and all duplicate charges.',
            'source_fields': ['charges.status', 'charges.created_at', 'charges.order_ref', 'charges.amount_pence'],
            'conditions': conditions, 'median_increase_pct': 100 * (conditions['slo']['median_gbp'] / conditions['basic']['median_gbp'] - 1),
            'trials': trials}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--study-asset', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = export(json.loads(args.study_asset.read_text()))
    text = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
    if args.check:
        assert args.out.read_text() == text, 'Revenue asset is stale'
    else:
        args.out.write_text(text)
    print(json.dumps(result['conditions']), f"median increase {result['median_increase_pct']:.2f}%")


if __name__ == '__main__':
    main()
