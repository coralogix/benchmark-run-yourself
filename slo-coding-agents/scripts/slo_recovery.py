"""The recovery curve: median checkout error rate after each trial's first serious failure.

Each affected trial is aligned at its first 10-minute bucket at or after minute 20 with more than 10%
checkout errors. The curve is the pointwise median error rate at +0, +10, +20, +30 and +40 minutes.
Every affected trial contributes to every point; a longer window would change the cohort. Reads
LARDER_ASSETS_DIR/slo-agent-study.json and writes LARDER_ASSETS_DIR/slo-recovery-curves.json.

    uv run python scripts/slo_recovery.py
    uv run python scripts/slo_recovery.py --check
"""
import hashlib
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(os.environ.get('LARDER_ASSETS_DIR') or str(ROOT / 'results' / 'assets'))
SOURCE = ASSETS / 'slo-agent-study.json'
ASSET = ASSETS / 'slo-recovery-curves.json'
CONDS = ('basic', 'slo')
OFFSETS = (0, 10, 20, 30, 40)


def build_curves(d):
    """Compute the curves and the per-trial values behind them."""
    minutes = d['timeline']['minutes']
    if minutes != list(range(0, d['duration_min'], 10)):
        raise ValueError('Recovery curves require consecutive 10-minute measurements')
    source = {c: d['timeline'][c]['trials'] for c in CONDS}
    result = {
        'schema_version': 1,
        'source': {
            'asset': 'results/assets/slo-agent-study.json',
            'fields': ['timeline.minutes', 'timeline.basic.trials', 'timeline.slo.trials', 'mttr'],
            'trial_series_sha256': hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'generator': 'scripts/slo_recovery.py:build_curves',
        },
        'definitions': {
            'serious_failure': 'First observed 10-minute bucket starting at or after minute 20 with reliability below 90% (more than 10% technical checkout errors).',
            'clock': 'Minutes after the start of that first serious-failure bucket, separately aligned within each trial.',
            'error_rate': '100 minus observed checkout reliability, in percent. These are technical failures; valid declines and out-of-stock responses do not count as failures.',
            'curve': 'Pointwise median of observed trial error rates. No smoothing, interpolation or missing-value imputation. A line joins consecutive measured medians.',
            'window': '0 to 40 minutes. Every affected trial has an observed measurement at every plotted offset; ending here avoids changes in the trial cohort.',
            'recovery_summary': 'Median per-trial elapsed time to the first later bucket with at most 10% errors. This differs from the point where the median curve crosses 10%.',
            'healthy': 'At most 1% errors, inclusive.',
        },
        'offsets_min': list(OFFSETS),
        'conditions': {},
    }
    for c in CONDS:
        rows, recovery_times = [], []
        for pair, series in enumerate(source[c], 1):
            onset = next((i for i, v in enumerate(series) if minutes[i] >= 20 and v is not None and v < 90), None)
            if onset is None:
                continue
            errors = []
            for offset in OFFSETS:
                index = onset + offset // 10
                if index >= len(series) or series[index] is None:
                    raise ValueError(f'{c} pair {pair:02d} lacks a measurement at +{offset} min')
                errors.append(round(100 - series[index], 6))
            recovered = next((i for i in range(onset + 1, len(series)) if series[i] is not None and series[i] >= 90), None)
            if recovered is not None:
                recovery_times.append((recovered - onset) * 10)
            rows.append({
                'pair': pair,
                'onset_min': minutes[onset],
                'error_rate_pct': errors,
                'recovery_min': (recovered - onset) * 10 if recovered is not None else None,
            })
        medians = [round(statistics.median(row['error_rate_pct'][i] for row in rows), 6) for i in range(len(OFFSETS))]
        mttr = statistics.median(recovery_times)
        if len(rows) != d['mttr'][c]['collapsed'] or mttr != d['mttr'][c]['mttr_to_90_min']:
            raise ValueError(f'{c} recovery figures disagree with the study asset')
        result['conditions'][c] = {
            'affected_trials': len(rows),
            'trials_at_each_point': [len(rows)] * len(OFFSETS),
            'median_error_rate_pct': medians,
            'median_recovery_min': mttr,
            'trials': rows,
        }
    return result


def write_asset(d, check=False):
    data = build_curves(d)
    content = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    if check:
        if not ASSET.exists() or ASSET.read_text() != content:
            raise ValueError('Recovery curve asset is stale; run uv run python scripts/slo_recovery.py')
    elif not ASSET.exists() or ASSET.read_text() != content:
        ASSET.write_text(content)
    return data


if __name__ == '__main__':
    data = write_asset(json.loads(SOURCE.read_text()), check='--check' in sys.argv)
    print({c: data['conditions'][c]['median_error_rate_pct'] for c in CONDS})
