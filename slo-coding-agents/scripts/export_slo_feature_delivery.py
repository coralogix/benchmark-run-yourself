"""Reconstruct feature completion times from the study transcripts.

Usage: uv run python scripts/export_slo_feature_delivery.py [LARDER_ROOT]
LARDER_ROOT defaults to this folder. Reads LARDER_ASSETS_DIR/slo-agent-study.json and writes
LARDER_ASSETS_DIR/slo-feature-delivery.json (LARDER_ASSETS_DIR defaults to results/assets).
The tools/ship completion log keeps at most 12 item IDs per request. This script matches each
oversized ship command in the transcript to its successful server record and recovers the
IDs after the twelfth.
Only IDs, timestamps and source locations are exported, never transcript prose.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(os.environ.get('LARDER_ASSETS_DIR') or str(ROOT / 'results' / 'assets'))


def export(root):
    sys.path.insert(0, str(root.resolve()))
    from harness.evaluator.records import completion_requests, static_ship_calls, timestamp

    study = json.loads((ASSETS / 'slo-agent-study.json').read_text())
    result = {
        'description': 'First feature completion declaration per run, reconstructed from full agent transcripts and matched successful ship records. Repeated declarations are deduplicated. This measures agent completion decisions, not the time of final acceptance tests.',
        'duration_min': 180,
        'conditions': {c: {'runs': 0, 'events': []} for c in ('basic', 'slo')},
        'audit': [],
    }
    for pair in study['session_context']['pair_order']:
        for condition in ('basic', 'slo'):
            rid = pair[f'{condition}_run_id']
            rd = root / 'runs' / rid
            meta = json.loads((rd / 'run.json').read_text())
            start, end = timestamp(meta['handover_at']), timestamp(meta['deadline_at'])
            raw = [json.loads(line) for line in (rd / 'artifacts/ship_decisions.jsonl').read_text().splitlines()]
            groups = [g for g in completion_requests(raw, rid) if start <= g['at'] <= end]
            first = {}

            def record(item, at, source):
                if item.startswith('FEAT-') and at < first.get(item, (float('inf'), None))[0]:
                    first[item] = (at, source)

            for index, group in enumerate(groups):
                for item in group['items']:
                    record(item, group['at'], {'file': f'runs/{rid}/artifacts/ship_decisions.jsonl', 'request_index': index})
            recorded = set(first)
            seen, matched = set(), set()
            oversized = 0
            transcript = rd / 'agent_transcript.log'
            for line_number, line in enumerate(transcript.read_text().splitlines(), 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                part = row.get('part') or {}
                state = part.get('state') or {}
                command = (state.get('input') or {}).get('command', '')
                if part.get('tool') != 'bash' or state.get('status') != 'completed' or 'tools/ship' not in command:
                    continue
                tool_id = part.get('callID') or part.get('id') or line_number
                if tool_id in seen:
                    continue
                seen.add(tool_id)
                calls, _ = static_ship_calls(command)
                times = state.get('time') or {}
                began = (times.get('start') or row['timestamp']) / 1000
                finished = (times.get('end') or row['timestamp']) / 1000
                for ids, score in calls:
                    if len(ids) <= 12:
                        continue
                    candidates = [(i, g) for i, g in enumerate(groups)
                                  if i not in matched and tuple(ids[:12]) == g['items']
                                  and score == g['rows'][0]['accepted_score']
                                  and began - .5 <= g['at'] <= finished + 10]
                    if not candidates:
                        continue
                    index, group = min(candidates, key=lambda item: abs(item[1]['at'] - finished))
                    matched.add(index)
                    oversized += 1
                    for item in ids:
                        record(item, group['at'], {'file': f'runs/{rid}/agent_transcript.log', 'line': line_number})
            bucket = result['conditions'][condition]
            bucket['runs'] += 1
            bucket['events'].extend({
                'run_id': rid, 'pair': pair['global_pair'], 'feature': feature,
                'minute': round((at - start) / 60, 4), 'source': source,
            } for feature, (at, source) in first.items())
            result['audit'].append({
                'run_id': rid, 'condition': condition,
                'transcript_lines_scanned': line_number,
                'matched_oversized_commands': oversized,
                'recorded_features': len(recorded),
                'recovered_features': len(set(first) - recorded),
                'reconstructed_features': len(first),
            })
    for condition, bucket in result['conditions'].items():
        assert bucket['runs'] == 20
        bucket['events'].sort(key=lambda e: (e['minute'], e['run_id'], e['feature']))
        keys = {(e['run_id'], e['feature']) for e in bucket['events']}
        assert len(keys) == len(bucket['events'])
        assert all(0 <= e['minute'] <= 180 for e in bucket['events'])
    return result


if __name__ == '__main__':
    data = export(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT)
    (ASSETS / 'slo-feature-delivery.json').write_text(json.dumps(data, indent=2) + '\n')
    print({c: len(b['events']) for c, b in data['conditions'].items()})
    print('Recovered:', sum(r['recovered_features'] for r in data['audit']))
