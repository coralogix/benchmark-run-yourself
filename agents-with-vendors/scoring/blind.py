#!/usr/bin/env python3
"""Blind scoring: transcripts out without platform names, marks back in by platform.

  python3 scoring/blind.py export [runs-dir] [blind-dir] [key-file]
      Writes one Markdown transcript per agent to <blind-dir>/transcripts/<ID>.md,
      under a random ID, with every platform name, product name, CLI name,
      profile, context, site, account and domain replaced by a neutral label,
      and every command the agent ran replaced by "[query N]" (its output
      stays, redacted the same way). Writes the blank marking sheet
      <blind-dir>/marks.csv and the ID map to <key-file>, outside the
      blind directory. Defaults: results/runs, results/blind,
      results/blind-key.csv.
  python3 scoring/blind.py prompt <ID> [blind-dir] [ground-truth]
      Prints the reviewer's full input for one transcript: scoring/review-prompt.md,
      the scenario's rubric (scoring/rubric.json), its section of the ground
      truth (default results/ground-truth.yaml) and the transcript.
  python3 scoring/blind.py collect [blind-dir]
      Reads the reviewer's <blind-dir>/reviews/<ID>.json files into marks.csv.
  python3 scoring/blind.py unblind [blind-dir] [key-file] [out]
      Maps marks.csv back to scenarios, platforms and replicates through the
      ID map and writes the scores file figures.py reads (default results/scores.csv).

Only the ID map (the key file) links an ID to a platform; keep it away from
the reviewer. The run for each scenario is the retry
(label suffix R) when one exists, else the original.
"""
import csv, json, os, random, re, sys
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUBRIC = json.load(open(os.path.join(HERE, "rubric.json")))
PLATFORMS = OrderedDict([("coralogix", "Coralogix"), ("datadog", "Datadog"), ("dynatrace", "Dynatrace"),
                         ("grafana", "Grafana Cloud"), ("newrelic", "New Relic")])
OUTPUT_LIMIT = int(os.environ.get("BLIND_OUTPUT_CHARS", "4000"))


def env_value(name):
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k == name:
                return v.strip().strip("'\"")
    return ""


# Terms that name or identify a platform, its products, its query languages
# and its CLI, matched case-insensitively as whole words. Extend with
# scoring/redact-terms.txt (one "regex<TAB>label" per line).
TERMS = [
    (r"cx|pup|dtctl|gcx|newrelic", "the CLI"),
    (r"grafana\s+cloud|grafana|loki|tempo|mimir|pyroscope|logql|traceql|alloy", "the platform"),
    (r"coralogix|dataprime|olly", "the platform"),
    (r"datadog|bits\s+ai|watchdog", "the platform"),
    (r"dynatrace|grail|davis(\s+ai)?|smartscape|oneagent|dql", "the platform"),
    (r"new\s*relic|nerdgraph|nrql|nerdpack", "the platform"),
    (r"promql", "the query language"),
    (r"(frequent\s+search|monitoring|archive|compliance)\s+tier|--tier\s+\w+|tier[\"']?\s*[:=]\s*[\"']?(archive|frequent_search|monitoring)", "a storage tier"),
    (r"[a-z0-9.-]*(coralogix\.com|coralogix\.us|coralogix\.in|cx498\.coralogix\.com|datadoghq\.(com|eu)|ddog-gov\.com|"
     r"dynatrace\.com|dynatracelabs\.com|grafana\.net|grafana\.com|newrelic\.com|nr-data\.net)", "[platform domain]"),
]
# Names that identify a platform even inside a longer token (an identifier,
# a metric name, a URL): the whole token is replaced.
EMBEDDED = r"grafana|datadog|dynatrace|coralogix|new_?relic|nrql|nerdgraph|dataprime|ddtrace|ddsource|ddtags"
EMBEDDED_RX = re.compile(r"[\w.:/@-]*(?:" + EMBEDDED + r")[\w.:/@-]*", re.I)

# Field syntax that identifies a platform's query language.
SYNTAX = [
    (r"\$[dlmp]\.[A-Za-z0-9_.]+", "[field]"),          # DataPrime paths
    (r"(?<![\w.])@[A-Za-z_][A-Za-z0-9_.]*", "[field]"),  # facet syntax
    (r"\bdt\.[a-z_.]+", "[field]"),                    # dt.* fields
    (r"\b(fetch|makeTimeseries|timeseries)\s+(logs|spans|bizevents|events)\b", "[query]"),
    (r"(/Users|/home|/private/tmp|/tmp)/[^\s'\"]*", "[path]"),           # paths on the agent host
    (r"--storage[= ]\S+", "[option]"),
]


def load_terms():
    terms = list(TERMS)
    extra = os.path.join(HERE, "redact-terms.txt")
    if os.path.exists(extra):
        for line in open(extra):
            if line.strip() and not line.startswith("#"):
                rx, _, label = line.rstrip("\n").partition("\t")
                terms.append((rx, label or "[redacted]"))
    for name, label in (("CX_PROFILE", "[profile]"), ("GCX_CONTEXT", "[profile]"), ("NR_PROFILE", "[profile]"),
                        ("DTCTL_CONTEXT", "[profile]"), ("NEWRELIC_ACCOUNT_ID", "[account]"),
                        ("DD_SITE", "[platform domain]"), ("DATADOG_SITE", "[platform domain]")):
        v = env_value(name)
        if v:
            terms.insert(0, (re.escape(v), label))
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user:
        terms.insert(0, (re.escape(user), "[user]"))
    for v, label in (("competitor-comparison-readonly", "[profile]"), ("competitor-comparison", "[profile]")):
        terms.insert(0, (re.escape(v), label))
    return [(re.compile(r"(?<![A-Za-z0-9_])(?:" + rx + r")(?![A-Za-z0-9_])", re.I), label) for rx, label in terms]


TERM_RX = None


def redact(text):
    global TERM_RX
    if TERM_RX is None:
        TERM_RX = load_terms()
    for rx, label in SYNTAX:
        text = re.sub(rx, label, text)
    for rx, label in TERM_RX:
        text = rx.sub(label, text)
    return EMBEDDED_RX.sub("[platform term]", text)


def redact_answer(text):
    """Answers keep their prose and tables; code blocks and inline code that
    contain a command or a query are replaced."""
    def block(m):
        body = m.group(0)
        return "[query or command removed]" if redact(body) != body or re.search(r"\|\s*\w+|--\w", body) else body
    text = re.sub(r"```.*?```", block, text, flags=re.S)
    text = re.sub(r"`[^`\n]+`", lambda m: "`[query]`" if redact(m.group(0)) != m.group(0) else m.group(0), text)
    return redact(text)


def scenario_runs(runs_dir):
    """{scenario_id: label} choosing the retry (suffix R) when present."""
    labels = defaultdict(set)
    for d in os.listdir(runs_dir):
        m = re.match(r"(.+)-r(\d+)$", d)
        if m and os.path.isdir(os.path.join(runs_dir, d)):
            labels[m.group(1).split("-")[0]].add(m.group(1))
    chosen = {}
    for sid, ls in labels.items():
        retries = sorted(l for l in ls if l.endswith("R"))
        chosen[sid] = retries[-1] if retries else sorted(ls)[0]
    return chosen


# Commands that print the CLI's own documentation rather than telemetry.
HELP_RX = re.compile(r"(^|\s)(--help|-h|help)(\s|$)|\b(version|--version)\b")


def tool_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def transcript(path):
    """Questions, the evidence retrieved after each, and the answer, from a stream-json transcript."""
    turns, cur, n, done = [], None, 0, False
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t == "question":
            cur = {"question": d.get("text", ""), "evidence": [], "answer": ""}
            turns.append(cur)
        elif t == "done":
            done = True
        elif cur is None:
            continue
        elif t == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    n += 1
                    name = c.get("name", "")
                    label = {"Bash": f"[query {n}]", "Skill": f"[skill loaded {n}]",
                             "Read": f"[reference file read {n}]", "Agent": f"[sub-agent task {n}]"}.get(name, f"[tool call {n}]")
                    cmd = (c.get("input") or {}).get("command", "") if name == "Bash" else ""
                    cur["evidence"].append({"id": c.get("id"), "label": label, "output": "", "help": bool(HELP_RX.search(cmd))})
        elif t == "user":
            content = d.get("message", {}).get("content", [])
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    for e in cur["evidence"]:
                        if e["id"] == c.get("tool_use_id"):
                            out = tool_text(c.get("content"))
                            if e["label"].startswith(("[skill", "[reference")):
                                out = ""
                            elif e["help"]:
                                out = "[the CLI's own help text, not telemetry; removed]"
                            e["output"] = out
        elif t == "result":
            cur["answer"] = d.get("result") or ""
    return turns, done


def render(bid, sid, turns):
    sc = RUBRIC["scenarios"][sid]
    lines = [f"# Transcript {bid}", "", f"Scenario: {sc['name']}", ""]
    for i, t in enumerate(turns, 1):
        lines += [f"## Question {i}", "", t["question"].strip(), "", "### Evidence the agent retrieved", ""]
        if not t["evidence"]:
            lines += ["(none)", ""]
        for e in t["evidence"]:
            out = redact(e["output"].strip())
            if len(out) > OUTPUT_LIMIT:
                out = out[:OUTPUT_LIMIT] + f"\n[output truncated: {len(out) - OUTPUT_LIMIT} more characters]"
            lines += [e["label"] + (" output:" if out else ""), ""]
            if out:
                lines += ["~~~", out, "~~~", ""]
        lines += [f"### Answer {i}", "", redact_answer(t["answer"].strip()) or "(no answer)", ""]
    return "\n".join(lines)


def dims(sid):
    return [d["id"] for d in RUBRIC["scenarios"][sid]["dimensions"]]


def export(runs_dir, blind_dir, key_file):
    if os.path.exists(key_file):
        print(f"{key_file} exists: transcripts are already exported (delete it and {blind_dir} to export again)")
        return
    chosen = scenario_runs(runs_dir)
    agents = []
    for sid, label in sorted(chosen.items()):
        if sid not in RUBRIC["scenarios"]:
            print(f"skip {label}: scenario {sid} is not in scoring/rubric.json")
            continue
        for d in sorted(os.listdir(runs_dir)):
            m = re.fullmatch(re.escape(label) + r"-r(\d+)", d)
            if not m:
                continue
            for p in PLATFORMS:
                f = os.path.join(runs_dir, d, p + ".jsonl")
                if os.path.exists(f):
                    agents.append((sid, label, int(m.group(1)), p, f))
    ids = random.SystemRandom().sample(range(16 ** 6), len(agents))
    order = list(range(len(agents)))
    random.SystemRandom().shuffle(order)
    os.makedirs(os.path.join(blind_dir, "transcripts"), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(key_file)), exist_ok=True)
    key_rows, sheet_rows = [], []
    for i in order:
        sid, label, rep, p, f = agents[i]
        bid = f"{ids[i]:06x}"
        turns, done = transcript(f)
        open(os.path.join(blind_dir, "transcripts", bid + ".md"), "w").write(render(bid, sid, turns))
        key_rows.append([bid, sid, label, rep, PLATFORMS[p], "complete" if done else "incomplete"])
        sheet_rows.append([bid, sid] + [""] * len(dims(sid)) + [""])
    with open(key_file, "w", newline="") as fh:
        csv.writer(fh).writerows([["id", "scenario_id", "label", "replicate", "platform", "transcript"]] + key_rows)
    write_sheet(blind_dir, sheet_rows)
    print(f"{len(agents)} transcripts in {blind_dir}/transcripts; marking sheet {blind_dir}/marks.csv; key {key_file}")
    left = [r for r in key_rows if r[5] != "complete"]
    if left:
        print(f"{len(left)} transcripts have no end marker (the agent was stopped); they are scored as they stand")


def write_sheet(blind_dir, rows):
    """marks.csv: id, scenario_id, then up to six dimension columns named d1..d6
    in the rubric's order for that scenario, then false_claims (a count)."""
    width = max(len(s["dimensions"]) for s in RUBRIC["scenarios"].values())
    with open(os.path.join(blind_dir, "marks.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "scenario_id"] + [f"d{i}" for i in range(1, width + 1)] + ["false_claims", "dimensions"])
        for r in rows:
            sid = r[1]
            ds = dims(sid)
            marks = r[2:2 + len(ds)]
            w.writerow(r[:2] + marks + [""] * (width - len(ds)) + [r[2 + len(ds)], " ".join(ds)])


def read_sheet(blind_dir):
    out = {}
    for r in csv.DictReader(open(os.path.join(blind_dir, "marks.csv"))):
        ds = r["dimensions"].split()
        out[r["id"]] = {"scenario_id": r["scenario_id"],
                        "marks": {d: r[f"d{i}"] for i, d in enumerate(ds, 1)},
                        "false_claims": r["false_claims"]}
    return out


def prompt(bid, blind_dir, truth_file):
    t = open(os.path.join(blind_dir, "transcripts", bid + ".md")).read()
    sid = next((s for s, sc in RUBRIC["scenarios"].items() if f"Scenario: {sc['name']}\n" in t), None)
    sc = RUBRIC["scenarios"][sid]
    truth = ground_truth_section(truth_file, sid)
    rubric = {"scenario": sc["name"], "marks": RUBRIC["marks"], "dimensions": sc["dimensions"]}
    print(open(os.path.join(HERE, "review-prompt.md")).read().rstrip())
    print("\n## Rubric\n\n```json\n" + json.dumps(rubric, indent=1) + "\n```")
    print("\n## Reference measurements\n\n```yaml\n" + truth.rstrip() + "\n```")
    print("\n## Transcript\n\n" + t)


def ground_truth_section(path, sid):
    if not os.path.exists(path):
        sys.exit(f"{path} not found: copy scoring/ground-truth-template.yaml there and fill it in")
    text = open(path).read()
    m = re.search(r"(?ms)^(" + re.escape(sid) + r"-[^\n:]*:\n.*?)(?=^\S|\Z)", text)
    if not m:
        sys.exit(f"{path} has no section for {sid}")
    return m.group(1)


def collect(blind_dir):
    sheet = read_sheet(blind_dir)
    rows, missing = [], []
    for bid, row in sheet.items():
        f = os.path.join(blind_dir, "reviews", bid + ".json")
        if not os.path.exists(f):
            missing.append(bid)
            rows.append([bid, row["scenario_id"]] + [row["marks"][d] for d in dims(row["scenario_id"])] + [row["false_claims"]])
            continue
        rev = json.load(open(f))
        marks = rev.get("marks", {})
        vals = []
        for d in dims(row["scenario_id"]):
            v = marks.get(d, "")
            vals.append("" if v == "" else str(v))
        rows.append([bid, row["scenario_id"]] + vals + [str(len(rev.get("false_claims", [])))])
    write_sheet(blind_dir, rows)
    print(f"collected {len(sheet) - len(missing)} reviews into {blind_dir}/marks.csv" +
          (f"; {len(missing)} still to review" if missing else ""))


def check_mark(sid, d, v, bid):
    spec = next(x for x in RUBRIC["scenarios"][sid]["dimensions"] if x["id"] == d)
    x = float(v)
    if spec.get("kind") == "count":
        if x < 0 or x != int(x):
            sys.exit(f"{bid} {d}: a count must be a whole number, got {v}")
    elif x not in RUBRIC["marks"]:
        sys.exit(f"{bid} {d}: a mark must be one of {RUBRIC['marks']}, got {v}")
    return x


def unblind(blind_dir, key_file, out):
    sheet = read_sheet(blind_dir)
    key = {r["id"]: r for r in csv.DictReader(open(key_file))}
    cells = defaultdict(dict)  # (sid, platform, dim) -> {rep: mark}
    for bid, row in sheet.items():
        k = key[bid]
        sid, plat, rep = k["scenario_id"], k["platform"], int(k["replicate"])
        for d, v in row["marks"].items():
            if v == "":
                sys.exit(f"{bid}: {d} is not marked")
            cells[(sid, plat, d)][rep] = check_mark(sid, d, v, bid)
        cells[(sid, plat, "false_claims")][rep] = float(row["false_claims"] or 0)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario_id", "platform", "dimension", "r1", "r2", "r3", "r4", "r5", "score", "max", "counts_toward_total"])
        for sid, sc in RUBRIC["scenarios"].items():
            for plat in PLATFORMS.values():
                if not any(k[0] == sid and k[1] == plat for k in cells):
                    continue
                reps = sorted({r for k, v in cells.items() if k[0] == sid and k[1] == plat for r in v})
                group = defaultdict(lambda: defaultdict(float))
                for spec in sc["dimensions"]:
                    marks = cells.get((sid, plat, spec["id"]), {})
                    counted = spec.get("counts", True) and "group" not in spec
                    mx = "" if spec.get("kind") == "count" else 1 * len(reps)
                    w.writerow([sid, plat, spec["id"]] + cols(marks) + [f"{sum(marks.values()):g}", mx, "yes" if counted else "no"])
                    if "group" in spec:
                        for r, v in marks.items():
                            group[spec["group"]][r] += v
                        group[spec["group"]]["_max"] += len(reps)
                for g, marks in group.items():
                    mx = marks.pop("_max")
                    w.writerow([sid, plat, g + "_total"] + cols(marks) + [f"{sum(marks.values()):g}", f"{mx:g}", "yes"])
                fc = cells.get((sid, plat, "false_claims"), {})
                w.writerow([sid, plat, "false_claims"] + cols(fc) + [f"{sum(fc.values()):g}", "", ""])
    print(f"wrote {out}")


def cols(marks):
    return [f"{marks[r]:g}" if r in marks else "" for r in range(1, 6)]


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    if cmd == "export":
        export(*(rest + ["results/runs", "results/blind", "results/blind-key.csv"][len(rest):]))
    elif cmd == "prompt":
        prompt(rest[0], *(rest[1:] + ["results/blind", "results/ground-truth.yaml"][len(rest) - 1:]))
    elif cmd == "collect":
        collect(*(rest + ["results/blind"][len(rest):]))
    elif cmd == "unblind":
        unblind(*(rest + ["results/blind", "results/blind-key.csv", "results/scores.csv"][len(rest):]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
