"""Compute every scenario's correct answer from the raw logs and spans of the replayed week.

    python3 harness/truth.py [ids...]      writes results/truth/<id>.json

Reference queries read raw `logs` and `spans` only, never a dataset, so the answer key does not depend on the datasets
being right. `compute` is a Python expression over R (one row list per reference query) that returns
{'must': [[alternative, ...], ...], 'numbers': [[value, relative tolerance], ...], 'numbers_any': [...]}.
`unique` asserts a clear margin over the runner-up for top-N questions; a scenario that fails it is marked NOT_UNIQUE
and left out of the run.
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import MON3, MONF, RESULTS, START, END, cx_query, iso, render, scenarios  # noqa: E402

OUT = os.path.join(RESULTS, 'truth')
DATASET_RE = re.compile(r'default/(?!(?:logs|spans)\b)')


def gbp(x): return (x or 0) / 100
def pct(a, b): return 100.0 * a / b if b else float('nan')
def top(rows, key): return sorted(rows, key=lambda r: (r.get(key) is None, -(r.get(key) or 0)))


def day_alts(group):
    """Accept the common ways of writing a date that the answer key gives as YYYY-MM-DD."""
    extra = []
    for g in group:
        m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2})?', str(g))
        if m and len(str(g)) > 10: extra.append(str(g)[:10])
        if m:
            d, mo = str(int(m.group(3))), m.group(2)
            extra += [f'{d} {MON3[mo]}', f'{MON3[mo]} {d}', f'{d} {MONF[mo]}', f'{MONF[mo]} {d}', f'{mo}-{m.group(3)}']
    return group + [e for e in extra if e not in group]


def resolve(sc):
    R = []
    for ref in sc['ref']:
        if DATASET_RE.search(ref['query']): raise ValueError('reference queries must read raw logs or spans')
        R.append(cx_query(ref['query'], 'spans' if ref.get('kind') == 'spans' else 'logs',
                          render(ref.get('start', '{START}')), render(ref.get('end', '{END}')), ref.get('limit', 5000)))
        time.sleep(1)
    env = dict(R=R, gbp=gbp, pct=pct, top=top, MON3=MON3, MONF=MONF, round=round, str=str, int=int, float=float, abs=abs,
               sum=sum, len=len, max=max, min=min, sorted=sorted)
    exp = eval(sc['compute'], {'__builtins__': {}, **env})
    unique = eval(sc['unique'], {'__builtins__': {}, **env}) if sc.get('unique') else True
    exp['must'] = [day_alts(list(g)) for g in exp.get('must', [])]
    if sc.get('tolerance'):
        exp['numbers'] = [[v, sc['tolerance']] for v, _ in exp.get('numbers', [])]
    return exp, bool(unique)


def main():
    os.makedirs(OUT, exist_ok=True)
    only = set(sys.argv[1:])
    for sc in scenarios():
        if only and sc['id'] not in only: continue
        try:
            exp, unique = resolve(sc)
            status = 'ok' if unique else 'NOT_UNIQUE'
            json.dump(dict(id=sc['id'], window=[iso(START), iso(END)], question=render(sc['question']), expected=exp, status=status),
                      open(os.path.join(OUT, sc['id'] + '.json'), 'w'), indent=1)
            print(f"{sc['id']:12} {status:10} {json.dumps(exp)[:140]}", flush=True)
        except Exception as e:
            print(f"{sc['id']:12} ERROR      {str(e)[:300]}", flush=True)


if __name__ == '__main__':
    main()
