"""Create, fill, check and delete the 12 datasets.

    python3 datasets/build.py create     # define the 12 datasets with their descriptions
    python3 datasets/build.py fill       # one Background Query per chunk of the replayed week, results appended to the dataset
    python3 datasets/build.py status     # wait until every fill query has finished
    python3 datasets/build.py verify     # row counts per dataset, and raw-vs-dataset totals for the four event datasets
    python3 datasets/build.py delete     # remove the 12 datasets

Needs CX_API_KEY, CX_DOMAIN (for example eu1.coralogix.com) and RUN_START (the first hour of the replayed week, UTC).
Datasets are created over gRPC with grpcurl; fills use the Background Queries HTTP API; checks use the cx CLI.
"""
import datetime as dt, json, os, subprocess, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from queries import DATASETS  # noqa: E402

KEY = os.environ['CX_API_KEY']
DOMAIN = os.environ['CX_DOMAIN']
API = f'api.{DOMAIN}'
START = dt.datetime.fromisoformat(os.environ['RUN_START'].replace('Z', '+00:00'))
END = START + dt.timedelta(days=7)
JOBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results', 'fill_jobs.jsonl')
DATASPACE = 'default'
SERVICE = 'com.coralogix.archive.dataset.v2.UserDefinedDatasetService'
iso = lambda t: t.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def grpc(method, body):
    p = subprocess.run(['grpcurl', '-H', f'Authorization: Bearer {KEY}', '-d', json.dumps(body), f'{API}:443', f'{SERVICE}/{method}'],
                       capture_output=True, text=True)
    return p.returncode == 0, (p.stdout or p.stderr).strip()


def http(path, body):
    # Background Queries sit behind a CDN that rejects requests without a user agent.
    r = urllib.request.Request(f'https://{API}{path}', data=json.dumps(body).encode(),
                               headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {KEY}', 'User-Agent': 'curl/8'})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(r, timeout=60).read() or b'{}')
        except urllib.error.HTTPError as e:
            if e.code != 429: return {'http': e.code, 'error': e.read().decode()[:500]}
            time.sleep(10 * (attempt + 1))
    return {'http': 429}


def cx(query, source):
    for attempt in range(6):
        p = subprocess.run(['cx', 'dataprime', 'query', query, '-s', source, '--start', iso(START), '--end', iso(END),
                            '--limit', '10', '-o', 'json'], capture_output=True, text=True)
        if '429' not in p.stderr: break
        time.sleep(10 * (attempt + 1))
    if p.returncode: raise SystemExit(f'cx failed on {source}: {p.stderr.strip()[-300:]}')
    return json.loads(p.stdout)


def chunks(kind):
    if kind == 'week': return [(START, END)]
    return [(START + dt.timedelta(days=d), START + dt.timedelta(days=d + 1)) for d in range(7)]


def count(rows):
    """First numeric value in a cx JSON result, wherever cx nests it."""
    if isinstance(rows, dict): rows = rows.get('results') or rows.get('rows') or [rows]
    for r in rows:
        r = r.get('userData', r) if isinstance(r, dict) else r
        for v in (r.values() if isinstance(r, dict) else []):
            if isinstance(v, (int, float)): return int(v)
            if isinstance(v, str) and v.isdigit(): return int(v)
    return 0


def create():
    for name, d in DATASETS.items():
        ok, out = grpc('SetUserDefinedDataset', {'dataset': {'dataspace': {'dataspace': DATASPACE}, 'dataset': name},
                                                 'enable_ingestion': True, 'description': d['description']})
        print(('created ' if ok else 'FAILED  ') + name, '' if ok else out[-300:], flush=True)


def fill():
    os.makedirs(os.path.dirname(JOBS), exist_ok=True)
    with open(JOBS, 'a') as jobs:
        for name, d in DATASETS.items():
            for s, e in chunks(d['chunks']):
                body = {'query': d['query'], 'syntax': 'QUERY_SYNTAX_DATAPRIME', 'startDate': iso(s), 'endDate': iso(e),
                        'name': 'fill_' + name.replace('.', '_'), 'description': f'Fill {DATASPACE}/{name}',
                        'resultsDestination': {'persistentDestination': {
                            'datasets': [{'dataspace': DATASPACE, 'dataset': name, 'writeMode': 'DATASET_WRITE_MODE_APPEND'}],
                            'partitioning': {'dateHourPartitioned': {'dataprimePath': d['partition']}}}}}
                r = http('/api/v1/dataprime/background-query', body)
                jobs.write(json.dumps({'dataset': name, 'start': iso(s), 'end': iso(e), 'response': r}) + '\n')
                print(f'{name:36} {iso(s)[:10]}  {r.get("queryId") or r}', flush=True)
                time.sleep(1.5)


def status(wait=True):
    while True:
        state = {}
        for line in open(JOBS):
            j = json.loads(line); qid = j['response'].get('queryId')
            if not qid: state.setdefault(j['dataset'], []).append('NOT SUBMITTED'); continue
            t = http('/api/v1/dataprime/background-query/status', {'queryId': qid}).get('terminated')
            state.setdefault(j['dataset'], []).append('running' if not t else 'ok' if 'success' in t else 'FAILED ' + json.dumps(t)[:200])
        running = sum(s == 'running' for v in state.values() for s in v)
        for name, v in state.items():
            print(f'{name:36} ' + ', '.join(f'{v.count(s)} {s}' for s in dict.fromkeys(v)))
        if not wait or not running: break
        print(f'{running} still running, checking again in 60 s\n', flush=True); time.sleep(60)


def verify():
    for name, d in DATASETS.items():
        rows = count(cx('count', f'{DATASPACE}/{name}'))
        line = f'{name:36} {rows:>10,} rows'
        if d['partition'] == '$m.timestamp' and 'groupby' not in d['query']:
            # Event datasets are one row per raw record, so the totals must match the raw query exactly.
            raw = count(cx(d['query'].split('| choose')[0] + '| count', 'logs'))
            line += f'   raw {raw:>10,}   {"match" if raw == rows else "MISMATCH"}'
        print(line, flush=True)


def delete():
    for name in DATASETS:
        ok, out = grpc('DeleteUserDefinedDatasets', {'dataset': {'dataspace': {'dataspace': DATASPACE}, 'dataset': name}})
        print(('deleted ' if ok else 'FAILED  ') + name, '' if ok else out[-300:], flush=True)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    {'create': create, 'fill': fill, 'status': status, 'verify': verify, 'delete': delete}.get(
        cmd, lambda: sys.exit(__doc__))()
