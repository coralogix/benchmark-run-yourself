#!/usr/bin/env python3
"""Replay the profiled week of telemetry for three simulated businesses into a Coralogix account.

Every hour of the replay reproduces the matching hour of the profile (generator/profile.json.gz): the same log kinds in
the same numbers, the same trace roots with the same latency shape, and AI-assistant conversations that hit the same
hourly guardrail and error counts. Records are cloned from real exemplars with fresh timestamps and IDs.

Logs go to the REST logs API as JSON documents in the stored shape of each business; spans go to the OTLP/HTTP traces
endpoint. Both use the Send-Your-Data key in CX_SEND_DATA_KEY and the ingress base URL in CX_INGRESS_URL.

    python3 generator/generate.py run --start 2026-12-01T00:00:00Z      # replay 168 hours from that UTC hour
    python3 generator/generate.py check                                 # send one minute now and print how to verify it
    python3 generator/generate.py dry --hour 6 --minutes 1 --out /tmp/gen  # write one minute as files, send nothing
"""
import argparse, collections, datetime as dt, gzip, json, os, random, secrets, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SEVERITY = {'Debug': 1, 'Verbose': 2, 'Info': 3, 'Warning': 4, 'Error': 5, 'Critical': 6}
KIND = {'internal': 1, 'server': 2, 'client': 3, 'producer': 4, 'consumer': 5}
DERIVED = {'otel.status_code', 'span.kind', 'cx.application.name', 'cx.subsystem.name', 'otel.library.name', 'otel.library.version',
           'otel.scope.name', 'otel.scope.version', 'cx.event.id', 'otel.status_description', 'otel.span.parent.origin'}


def load_profile(path, substitutions):
    raw = gzip.open(path).read().decode()
    for old, new in substitutions:
        raw = raw.replace(old, new)
    return json.loads(raw)


def kv(k, v):
    if isinstance(v, bool): return {'key': k, 'value': {'boolValue': v}}
    if isinstance(v, int): return {'key': k, 'value': {'intValue': str(v)}}
    if isinstance(v, float): return {'key': k, 'value': {'doubleValue': v}}
    if isinstance(v, (dict, list)): return {'key': k, 'value': {'stringValue': json.dumps(v)}}
    return {'key': k, 'value': {'stringValue': str(v)}}


def split(count, parts, rng):
    """Spread an integer count over `parts` buckets at random (multinomial)."""
    out = [0] * parts
    for _ in range(count): out[rng.randrange(parts)] += 1
    return out


class Replay:
    def __init__(self, profile, scale, seed):
        self.p, self.scale, self.rng = profile, scale, random.Random(seed)
        self.services = profile['services']
        self.assistant_roots = {(a, v['root_svc'], v['root_op']) for a, v in profile['assistant'].items()}

    # ---------- plan an hour ----------
    def plan_hour(self, h):
        """Per-minute counts for every log kind and trace root, and the assistant traces chosen for this hour."""
        rng, s = self.rng, self.scale
        logs = [(i, split(round(k['hourly'][h] * s), 60, rng)) for i, k in enumerate(self.p['log_kinds']) if k['hourly'][h]]
        roots, convs, shared = [], {}, collections.defaultdict(list)
        for i, r in enumerate(self.p['roots']):
            n = round(r['hourly'][h] * s)
            if not n: continue
            if (r['app'], r['svc'], r['op']) in self.assistant_roots: shared[r['app']].append((i, n))
            else: roots.append((i, split(n, 60, rng)))
        # Assistant conversations share their root kind with ordinary traces: take this hour's conversation count out
        # of that root's volume and replay the rest as ordinary traces.
        for app, kinds in shared.items():
            total = sum(n for _, n in kinds)
            convs[app] = min(total, round(self.p['assistant'][app]['hourly']['conversations'][h] * s))
            for i, n in kinds:
                rest = n - round(convs[app] * n / total)
                if rest > 0: roots.append((i, split(rest, 60, rng)))
        assistant = {}
        for app, n in convs.items():
            if not n: continue
            a = self.p['assistant'][app]
            chosen = self.pick_conversations(a, h, n)
            mins = split(len(chosen), 60, rng); it = iter(chosen)
            assistant[app] = [[next(it) for _ in range(c)] for c in mins]
        return logs, roots, assistant

    def pick_conversations(self, a, h, n):
        """n conversations whose guardrail, tool-error and chat-error spans add up to this hour's targets.

        Feature traces are chosen so that no other feature goes past its target; the rest of the hour is filled with
        conversations that carry no feature at all."""
        rng = self.rng
        need = {f: round(v[h] * self.scale) for f, v in a['hourly'].items() if f != 'conversations'}
        feats = lambda t: {g: c for g, c in t['f'].items() if g != 'tool_error'}
        clean = [t for t in a['pool'] if not feats(t)]
        chosen = []
        for f in sorted(need, key=lambda f: -need[f]):
            while need[f] > 0 and len(chosen) < n:
                cands = [t for t in a['pool'] if feats(t).get(f)]
                if not cands: break
                fits = [t for t in cands if all(need.get(g, 0) >= c for g, c in feats(t).items())]
                t = rng.choice(fits) if fits else min(cands, key=lambda t: sum(max(0, c - need.get(g, 0)) for g, c in feats(t).items()))
                chosen.append(t)
                for g, c in feats(t).items(): need[g] = need.get(g, 0) - c
        while len(chosen) < n:
            chosen.append(rng.choice(clean or a['pool']))
        rng.shuffle(chosen)
        return chosen[:n]

    # ---------- build records ----------
    def spans_for(self, trace, start_ns, mult):
        tid = secrets.token_hex(16)
        ids = [secrets.token_hex(8) for _ in trace]
        out = []
        for i, s in enumerate(trace):
            st = start_ns + int(s['off'] * mult * 1000)
            out.append({'app': s['app'], 'svc': s['svc'], 'traceId': tid, 'spanId': ids[i],
                        'parentSpanId': ids[s['parent']] if s['parent'] is not None else '',
                        'name': s['op'], 'kind': KIND.get(s['tags'].get('span.kind'), 1), 'start': st,
                        'end': st + max(1, int(s['dur'] * mult * 1000)), 'tags': s['tags']})
        return out

    def minute(self, plan, h, m, t0_ns):
        """All spans and logs for minute m of replay hour h, starting at t0_ns."""
        logs_plan, roots_plan, assistant = plan
        rng, spans = self.rng, []
        for i, per in roots_plan:
            r = self.p['roots'][i]
            by_hour = r.get('by_hour')
            near = None
            if by_hour:  # real traces from this hour and its neighbours carry the hour's latency and error shape
                near = [j for d in (-1, 0, 1) if 0 <= h + d < 168 for j in by_hour[h + d]]
            for _ in range(per[m]):
                if not r['traces']: break
                if near:
                    spans += self.spans_for(r['traces'][rng.choice(near)], t0_ns + rng.randrange(60_000_000_000), 1.0)
                else:
                    spans += self.spans_for(rng.choice(r['traces']), t0_ns + rng.randrange(60_000_000_000), r['dur_mult'][h])
        for app, per in assistant.items():
            for conv in per[m]:
                spans += self.spans_for(conv['spans'], t0_ns + rng.randrange(60_000_000_000), 1.0)
        by_svc = collections.defaultdict(list)
        for s in spans: by_svc[(s['app'], s['svc'])].append((s['traceId'], s['spanId']))
        logs = []
        for i, per in logs_plan:
            k = self.p['log_kinds'][i]
            links = by_svc.get((k['app'], k['sub']))
            for _ in range(per[m]):
                doc = json.loads(json.dumps(rng.choice(k['ex'])))
                ts = t0_ns + rng.randrange(60_000_000_000)
                tid, sid = rng.choice(links) if links else (secrets.token_hex(16), secrets.token_hex(8))
                res = self.services.get(f"{k['app']}|{k['sub']}", {}).get('log_resource')
                if 'logRecord' in doc:
                    doc['logRecord'].update(timeUnixNano=ts, observedTimeUnixNano=ts, traceId=tid, spanId=sid)
                else:
                    doc.update(timeUnixNano=ts, observedTimeUnixNano=ts, traceId=tid, spanId=sid)
                if res: doc['resource'] = res
                logs.append({'applicationName': k['app'], 'subsystemName': k['sub'], 'severity': SEVERITY.get(k['sev'], 3),
                             'timestamp': ts / 1e6, 'text': json.dumps(doc, separators=(',', ':'))})
        return spans, logs

    def otlp(self, spans):
        groups = collections.defaultdict(list)
        for s in spans: groups[(s['app'], s['svc'])].append(s)
        rs = []
        for (app, svc), ss in groups.items():
            res = dict(self.services.get(f'{app}|{svc}', {}).get('span_resource') or {})
            res.update({'service.name': svc, 'cx.application.name': app, 'cx.subsystem.name': svc})
            scope = ss[0]['tags'].get('otel.library.name') or ss[0]['tags'].get('otel.scope.name') or 'telemetry-replay'
            rs.append({'resource': {'attributes': [kv(k, v) for k, v in res.items()]},
                       'scopeSpans': [{'scope': {'name': scope}, 'spans': [{
                           'traceId': s['traceId'], 'spanId': s['spanId'], 'parentSpanId': s['parentSpanId'], 'name': s['name'],
                           'kind': s['kind'], 'startTimeUnixNano': str(s['start']), 'endTimeUnixNano': str(s['end']),
                           'attributes': [kv(k, v) for k, v in s['tags'].items() if k not in DERIVED],
                           'status': {'code': 2 if s['tags'].get('otel.status_code') == 'ERROR' else (1 if s['tags'].get('otel.status_code') == 'OK' else 0)}}
                           for s in ss]}]})
        return {'resourceSpans': rs}


class Sender:
    def __init__(self, base, key, workers=6):
        self.base, self.key = base.rstrip('/'), key
        self.pool = ThreadPoolExecutor(workers)
        self.sent, self.failed, self.lock = collections.Counter(), collections.Counter(), threading.Lock()

    def _post(self, path, body, kind, n):
        data = gzip.compress(json.dumps(body, separators=(',', ':')).encode())
        req = urllib.request.Request(self.base + path, data=data, method='POST', headers={
            'Content-Type': 'application/json', 'Content-Encoding': 'gzip', 'Authorization': 'Bearer ' + self.key})
        for attempt in range(6):
            try:
                urllib.request.urlopen(req, timeout=60).read()
                with self.lock: self.sent[kind] += n
                return
            except Exception as e:  # retry transient failures with backoff
                err = e; time.sleep(2 ** attempt)
        with self.lock: self.failed[kind] += n
        print(f'send failed ({kind}, {n} records): {err}', file=sys.stderr)

    def spans(self, payload, n): self.pool.submit(self._post, '/v1/traces', payload, 'spans', n)

    def logs(self, entries):
        for i in range(0, len(entries), 2000):
            chunk = entries[i:i + 2000]
            self.pool.submit(self._post, '/logs/v1/singles', chunk, 'logs', len(chunk))


def run(args, profile):
    start = dt.datetime.fromisoformat(args.start.replace('Z', '+00:00'))
    assert start.minute == start.second == 0, '--start must be on the hour (UTC)'
    rp, snd = Replay(profile, args.scale, args.seed), Sender(os.environ['CX_INGRESS_URL'], os.environ['CX_SEND_DATA_KEY'])
    for h in range(args.from_hour, args.hours):
        plan = rp.plan_hour(h)
        for m in range(60):
            t0 = start + dt.timedelta(hours=h, minutes=m)
            wait = (t0 - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if wait > 0: time.sleep(wait)
            spans, logs = rp.minute(plan, h, m, int(t0.timestamp() * 1e9))
            for i in range(0, len(spans), 1500):
                snd.spans(rp.otlp(spans[i:i + 1500]), len(spans[i:i + 1500]))
            snd.logs(logs)
        print(f'{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} hour {h + 1}/{args.hours} sent {dict(snd.sent)} failed {dict(snd.failed)}', flush=True)
    snd.pool.shutdown(wait=True)


def dry(args, profile):
    rp = Replay(profile, args.scale, args.seed)
    os.makedirs(args.out, exist_ok=True)
    plan = rp.plan_hour(args.hour)
    t0 = dt.datetime(2030, 1, 1, args.hour % 24, tzinfo=dt.timezone.utc)
    for m in range(args.minutes):
        spans, logs = rp.minute(plan, args.hour, m, int((t0 + dt.timedelta(minutes=m)).timestamp() * 1e9))
        json.dump(rp.otlp(spans), open(f'{args.out}/spans-{m:02d}.json', 'w'))
        json.dump(logs, open(f'{args.out}/logs-{m:02d}.json', 'w'))
        print(f'minute {m}: {len(spans)} spans, {len(logs)} logs')


def check(args, profile):
    """Send one minute of replay hour 0 now, so the shapes can be verified before the real run."""
    rp, snd = Replay(profile, 1.0, args.seed), Sender(os.environ['CX_INGRESS_URL'], os.environ['CX_SEND_DATA_KEY'])
    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    spans, logs = rp.minute(rp.plan_hour(0), 0, 0, int(now.timestamp() * 1e9))
    snd.spans(rp.otlp(spans), len(spans)); snd.logs(logs); snd.pool.shutdown(wait=True)
    print(f'sent {dict(snd.sent)} failed {dict(snd.failed)} at {now:%Y-%m-%dT%H:%MZ}. In a few minutes, check that they arrived:')
    print(f"  cx dataprime query 'groupby $l.applicationname aggregate count()' -s logs --start {now:%Y-%m-%dT%H:%M:00Z} --end now")
    print(f"  cx dataprime query 'groupby $l.applicationName aggregate count()' -s spans --start {now:%Y-%m-%dT%H:%M:00Z} --end now")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['run', 'check', 'dry'])
    ap.add_argument('--profile', default=os.path.join(HERE, 'profile.json.gz'))
    ap.add_argument('--start'); ap.add_argument('--hours', type=int, default=168); ap.add_argument('--from-hour', type=int, default=0)
    ap.add_argument('--scale', type=float, default=float(os.environ.get('GEN_SCALE', 1.0)))
    ap.add_argument('--seed', type=int, default=int(os.environ.get('GEN_SEED', 7)))
    ap.add_argument('--hour', type=int, default=6); ap.add_argument('--minutes', type=int, default=1); ap.add_argument('--out', default='/tmp/replay')
    a = ap.parse_args()
    subs = [tuple(x.split('=', 1)) for x in os.environ.get('GEN_SUBSTITUTE', '').split(';') if '=' in x]
    profile = load_profile(a.profile, subs)
    {'run': run, 'check': check, 'dry': dry}[a.mode](a, profile)


if __name__ == '__main__':
    main()
