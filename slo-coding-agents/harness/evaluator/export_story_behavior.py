"""Export the reliability-fix commits of every trial ("When agents committed reliability fixes").

    uv run python -m harness.evaluator.export_story_behavior \
      --asset results/assets/slo-agent-study.json \
      --out results/assets/slo-agent-behavior.json

The study asset supplies the cohort and the pair order. Every commit comes from the
trial's own commit logs (diffs/commerce.commits, diffs/ops.commits), between handover
and deadline. Only minutes and categories are exported, never commit subjects.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness.evaluator.records import ROOT, timestamp

CONDITIONS = ('basic', 'slo')
COMMIT = re.compile(r'^([0-9a-f]{7,40})\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+([+-]\d{4})\s+(.*)$')
FEAT = re.compile(r'FEAT-\d+')
ACTION_KINDS = ('fix', 'feature', 'rework', 'other')
REVIEW_CHANGE = re.compile(r'\breview(?:er)?\b|\bfollow[- ]?up\b|\bre[- ]review\b', re.I)
REPAIR = re.compile(
    r'\b(?:REL|HC|SF|FF)-\d+\b|\bINC(?:[-_:][A-Z0-9_-]+)?\b|'
    r'\b(?:fix(?:es|ed|ing)?|hotfix|repair(?:s|ed|ing)?|incident|'
    r'crash(?:es|ed|ing|loop)?|OOM|leak(?:s|ed|ing)?|bugs?|deadlock|race)\b', re.I)


def action_kind(title):
    """Classify a commit by its subject only: review first, then feature-only, then repair-only, else other."""
    if REVIEW_CHANGE.search(title):
        return 'rework'
    feature, repair = bool(FEAT.search(title)), bool(REPAIR.search(title))
    if feature and not repair:
        return 'feature'
    if repair and not feature:
        return 'fix'
    return 'other'


def trial_commits(run_id):
    rd = ROOT / 'runs' / run_id
    meta = json.loads((rd / 'run.json').read_text())
    start, end = timestamp(meta['handover_at']), timestamp(meta['deadline_at'])
    events, seen = [], set()
    for repo in ('commerce', 'ops'):
        for line in (rd / 'diffs' / f'{repo}.commits').read_text().splitlines():
            match = COMMIT.match(line)
            if not match:
                raise ValueError(f'{run_id}: cannot parse commit summary')
            sha, date, clock, zone, title = match.groups()
            if (repo, sha) in seen:
                raise ValueError(f'{run_id}: repeated commit in source summary')
            seen.add((repo, sha))
            at = timestamp(f'{date}T{clock}{zone}')
            if start <= at < end:
                events.append({'minute': round((at - start) / 60, 6), 'kind': action_kind(title)})
    events.sort(key=lambda event: event['minute'])
    return events


def export(asset):
    code_activity = {
        'minutes': list(range(0, 180, 10)),
        'categories': list(ACTION_KINDS),
        'classification': {
            'unit': 'One recorded Git commit in either repository, handover <= timestamp < deadline.',
            'fix': 'The subject has an explicit REL/HC/SF/FF ticket, an INC incident tag, or a fix/repair/incident/crash/OOM/leak/bug/deadlock/race word, and no FEAT ID, and does not name review or follow-up.',
            'feature': 'The subject names a FEAT-<number> and has no explicit repair marker.',
            'rework': 'The subject names review, reviewer, re-review or follow-up.',
            'other': 'Mixed feature-plus-repair subjects and every remaining change.',
        },
    }
    for condition in CONDITIONS:
        code_activity[condition] = {'trials': [
            {'pair': pair['global_pair'], 'run_id': pair[f'{condition}_run_id'],
             'events': trial_commits(pair[f'{condition}_run_id'])}
            for pair in asset['session_context']['pair_order']]}
    return {'schema_version': 1, 'pairs': len(asset['session_context']['pair_order']), 'code_activity': code_activity}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    output = export(json.loads(args.asset.read_text()))
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
    fixes = {c: sum(e['kind'] == 'fix' for t in output['code_activity'][c]['trials'] for e in t['events'])
             for c in CONDITIONS}
    print(f'Exported {output["pairs"]} pairs to {args.out}; reliability-fix commits {fixes}')


if __name__ == '__main__':
    main()
