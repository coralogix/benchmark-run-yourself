"""Shared settings: the replayed week, question rendering, cx queries and answer grading."""
import datetime as dt, json, os, re, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, 'results')
START = dt.datetime.fromisoformat(os.environ['RUN_START'].replace('Z', '+00:00')).astimezone(dt.timezone.utc)
END = START + dt.timedelta(days=7)
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')
WINDOW = f'between {iso(START)} and {iso(END)} (end exclusive)'
MON3 = {f'{m:02d}': dt.date(2000, m, 1).strftime('%b').lower() for m in range(1, 13)}
MONF = {f'{m:02d}': dt.date(2000, m, 1).strftime('%B').lower() for m in range(1, 13)}


def render(text):
    """Fill {W}, {START}, {END}, {DAYn} (a day of the replayed week in words) and {Tn} (that day's midnight, ISO)."""
    text = text.replace('{W}', WINDOW).replace('{START}', iso(START)).replace('{END}', iso(END))
    text = re.sub(r'\{DAY(\d)\}', lambda m: (START + dt.timedelta(days=int(m.group(1)))).strftime('%-d %B %Y'), text)
    return re.sub(r'\{T(\d)\}', lambda m: iso(START + dt.timedelta(days=int(m.group(1)))), text)


def scenarios():
    return json.load(open(os.path.join(ROOT, 'scenarios', 'questions.json')))


def cx_query(query, source, start, end, limit=5000):
    cmd = ['cx', 'dataprime', 'query', query, '-s', source, '--start', start, '--end', end, '--limit', str(limit), '-o', 'json']
    for attempt in range(10):
        p = subprocess.run(cmd, capture_output=True, text=True)
        if '429' not in p.stderr and 'Rate limited' not in p.stderr: break
        time.sleep(8 + 4 * attempt)
    if p.returncode: raise RuntimeError(p.stderr.strip()[-600:])
    out = p.stdout[p.stdout.find('['):] if '[' in p.stdout else '[]'
    return json.loads(out[:out.rfind(']') + 1]) if out.strip() else []


def _echo_re():
    """Strip echoes of the question's window from an answer, so the window dates are not read as answer numbers."""
    a, b = START.strftime('%Y-%m-%d'), END.strftime('%Y-%m-%d')
    t = r'(?:t00:00(?::00)?z?)?'
    return re.compile(rf'{a}{t}\s*(?:to|until|through|and|–|—|-|→)\s*{b}{t}|{a}t00:00(?::00)?z?|{b}t00:00(?::00)?z?|\(end exclusive\)')


ECHO = _echo_re()


def grade(answer, exp):
    a = ECHO.sub(' ', answer.lower().replace('−', '-'))
    for group in exp.get('must', []):
        if not any(str(g).lower() in a for g in group):
            return False
    nums = [float(x.replace(',', '')) for x in re.findall(r'-?\d[\d,]*\.?\d*(?:e-?\d+)?', a.replace('£', '').replace('$', ''))
            if x.replace(',', '').replace('-', '').replace('.', '')]
    ok = lambda t, tol: any(abs(n - t) <= max(abs(t) * tol, 1e-9) for n in nums)
    if not all(ok(t, tol) for t, tol in exp.get('numbers', [])):
        return False
    if exp.get('numbers_any') and not any(ok(t, tol) for t, tol in exp['numbers_any']):
        return False
    return True
