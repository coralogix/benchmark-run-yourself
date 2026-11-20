"""Compute every published figure from results/runs into results/study.json, and print the headline table.

    python3 analysis/score.py

Scenario-reps where any arm averaged more than 60 s per model call are left out of every figure: at that pace the
model endpoint, not the agent, set the time and the number of retries. Medians are over the remaining scenario-reps,
ratios compare totals, and 95% intervals come from 4,000 bootstrap resamples of the paired scenario-reps.
"""
import json, os, random, re, statistics as st
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, 'results', 'runs')
ARMS = ['none', 'discover', 'prompt']
LABEL = {'none': 'No Dataset', 'discover': 'Discover Datasets', 'prompt': 'Datasets in system prompt'}
METRICS = ['tokens_total', 'tokens_output', 'wall_seconds', 'model_calls', 'tool_calls', 'queries', 'query_errors', 'wrong_answers']
DS_RE = re.compile(r'default/([a-z]+\.[a-z_]+)')
Q_RE = re.compile(r'\bcx\s+(logs|spans|dataprime\s+query|metrics|search-fields)\b')
random.seed(11)

rows = {}
for line in open(os.path.join(R, 'results.jsonl')):
    r = json.loads(line); rows[(r['arm'], r['scenario'], r['rep'])] = r
groups = defaultdict(dict)
for (a, s, rep), r in rows.items():
    groups[(s, rep)][a] = r
excluded = {k for k, g in groups.items() if any(r['wall_seconds'] / max(r['model_calls'], 1) > 60 for r in g.values())}
clean = {k: g for k, g in groups.items() if k not in excluded and all(a in g for a in ARMS)}
keys = sorted(clean)
vals = lambda arm, m, ks=keys: [clean[k][arm][m] for k in ks]


def boot(base, treat, n=4000):
    idx = range(len(base)); out = []
    for _ in range(n):
        s = [random.choice(idx) for _ in idx]
        out.append(sum(treat[i] for i in s) / sum(base[i] for i in s))
    out.sort(); return [round(out[int(.025 * n)], 3), round(out[int(.975 * n)], 3)]


arms = {}
for arm in ARMS:
    rs = [clean[k][arm] for k in keys]
    arms[arm] = {'median': {m: st.median(r[m] for r in rs) for m in METRICS}, 'total': {m: sum(r[m] for r in rs) for m in METRICS},
                 'solved': sum(r['outcome'] == 'solved' for r in rs),
                 'solved_first_try': sum(r['outcome'] == 'solved' and r['wrong_answers'] == 0 for r in rs),
                 'timed_out': sum(r['outcome'] == 'dnf_wall' for r in rs), 'sessions': len(rs),
                 'dataset_queries': sum(r['dataset_queries'] for r in rs)}
ratios = {t: {m: {'total_ratio': round(sum(vals(t, m)) / sum(vals('none', m)), 3), 'ci95': boot(vals('none', m), vals(t, m)),
                  'median_change_pct': round(100 * (arms[t]['median'][m] / arms['none']['median'][m] - 1), 1)}
              for m in ('tokens_total', 'wall_seconds', 'tool_calls', 'queries', 'model_calls')} for t in ('discover', 'prompt')}
ratios['discover_vs_prompt'] = {'tokens_total': {'total_ratio': round(sum(vals('discover', 'tokens_total')) / sum(vals('prompt', 'tokens_total')), 3),
                                                 'ci95': boot(vals('prompt', 'tokens_total'), vals('discover', 'tokens_total'))}}
tiers = {}
for t in (1, 2, 3, 4):
    ks = [k for k in keys if clean[k]['none']['tier'] == t]
    tiers[str(t)] = {a: {'median_tokens': st.median(vals(a, 'tokens_total', ks)), 'median_seconds': st.median(vals(a, 'wall_seconds', ks))} for a in ARMS}
    tiers[str(t)]['n'] = len(ks)
domains = {}
for d in sorted({clean[k]['none']['domain'] for k in keys}):
    ks = [k for k in keys if clean[k]['none']['domain'] == d]
    domains[d] = {a: {'median_tokens': st.median(vals(a, 'tokens_total', ks))} for a in ARMS}; domains[d]['n'] = len(ks)

# Behaviour, from the commands each agent ran.
scen = {s['id']: s for s in json.load(open(os.path.join(ROOT, 'scenarios', 'questions.json')))}
kind = lambda c: 'dataset' if DS_RE.search(c) and Q_RE.search(c) else 'raw' if Q_RE.search(c) and 'search-fields' not in c else 'other'
behaviour = {}
for arm in ('discover', 'prompt'):
    n = first_ds = used = fallback = peek = listed3 = right = right_n = 0
    for k in keys:
        r = clean[k][arm]
        cmds = [c['cmd'] for c in json.load(open(os.path.join(R, arm, f"{r['scenario']}_r{r['rep']}.json")))['commands']]
        q = [x for x in map(kind, cmds) if x != 'other']
        n += 1; first_ds += bool(q) and q[0] == 'dataset'; used += 'dataset' in q
        fallback += 'dataset' in q and 'raw' in q[q.index('dataset'):]
        peek += any(DS_RE.search(c) and re.search(r'limit\s+\d', c) and not re.search(r'groupby|aggregate|count\(', c) for c in cmds)
        listed3 += any('cx datasets' in c for c in cmds[:3])
        exp = scen[r['scenario']].get('datasets') or []
        firsts = [m for c in cmds if Q_RE.search(c) for m in DS_RE.findall(c)]
        if exp and firsts: right_n += 1; right += firsts[0] in exp
    pct = lambda x, d=n: round(100 * x / d, 1) if d else None
    behaviour[arm] = {'sessions': n, 'first_query_dataset_pct': pct(first_ds), 'used_dataset_pct': pct(used), 'raw_after_dataset_pct': pct(fallback),
                      'sampled_dataset_pct': pct(peek), 'listed_in_first_three_pct': pct(listed3), 'intended_dataset_first_pct': pct(right, right_n)}

study = {'design': {'scenarios': len({k[0] for k in groups}), 'reps': len({k[1] for k in groups}), 'arms': 3,
                    'clean_scenario_reps': len(keys), 'excluded_scenario_reps': len(excluded), 'wrong_answer_cap': 8, 'time_cap_minutes': 45},
         'arms': arms, 'ratios': ratios, 'tiers': tiers, 'domains': domains, 'behaviour': behaviour}
json.dump(study, open(os.path.join(ROOT, 'results', 'study.json'), 'w'), indent=1)

print(f"{len(keys)} clean scenario-reps ({len(excluded)} left out for a slow model endpoint)\n")
print(f"| Metric (median) | {' | '.join(LABEL[a] for a in ARMS)} |\n|---|---|---|---|")
for m, lab in (('tokens_total', 'Tokens'), ('wall_seconds', 'Time taken (s)'), ('tool_calls', 'Tool calls'), ('queries', 'Queries')):
    cells = [f"{arms['none']['median'][m]:,.0f}"] + [f"{arms[a]['median'][m]:,.0f} ({ratios[a][m]['median_change_pct']:+.0f}%)" for a in ('discover', 'prompt')]
    print(f"| {lab} | {' | '.join(cells)} |")
print(f"| Solved | {' | '.join(str(arms[a]['solved']) for a in ARMS)} |\n| Timed out | {' | '.join(str(arms[a]['timed_out']) for a in ARMS)} |")
print(f"\nDiscover used {ratios['discover_vs_prompt']['tokens_total']['total_ratio']}x the tokens of the system-prompt arm (total).")
print(f"No Dataset arm queries that named a dataset: {arms['none']['dataset_queries']} (should be 0)")
