"""Run the agents: every scenario x rep starts the three arms together, and the grader answers "No." to every wrong
FINAL ANSWER until the agent is right, abandons, or hits a cap.

    python3 harness/run.py [--reps 10] [--parallel 6] [--only ID ...] [--arms none discover prompt]

Resumable: a session with an outcome is never rerun. Model errors and timeouts are retried and kept out of results.
Needs the three arm servers (harness/arm.sh) and results/truth/ from harness/truth.py.
"""
import argparse, base64, glob, json, os, re, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RESULTS, grade, render, scenarios  # noqa: E402

PORTS = {"none": 47201, "prompt": 47202, "discover": 47203}
ARMS = ["none", "prompt", "discover"]
MODEL = {"providerID": "bench", "modelID": os.environ["MODEL_ID"]}
MAX_WRONG, MAX_NUDGES, MAX_WALL = 8, 3, 45 * 60
REJECT = "No. That answer is not correct. Keep working and reply with a new FINAL ANSWER line, or reply ABANDON to give up."
NUDGE = "Reply with your FINAL ANSWER line, or ABANDON."
QUERY_RE = re.compile(r"\bcx\s+(logs|spans|dataprime\s+query|metrics|search-fields)\b")
DATASET_RE = re.compile(r"\bsource\s+['\"]?(?:default/(?!(?:logs|spans)\b)|(?:gaming|ecommerce|fintech|platform|ops|ai)\.)|-s\s+['\"]?default/(?!(?:logs|spans)\b)")
LOCK = threading.Lock()


class OC:
    def __init__(self, arm):
        self.base = f"http://127.0.0.1:{PORTS[arm]}"
        self.auth = "Basic " + base64.b64encode(f"opencode:bench-{arm}".encode()).decode()

    def req(self, method, path, body=None, timeout=120):
        r = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json", "Authorization": self.auth})
        raw = urllib.request.urlopen(r, timeout=timeout).read()
        return json.loads(raw) if raw else None


def parse_reply(text):
    if "FINAL ANSWER:" in text:
        return "answer", text[text.rindex("FINAL ANSWER:") + 13:].strip()
    if re.search(r"\bABANDON\b", text):
        return "abandon", None
    return "none", None


def collect(oc, sid):
    msgs = oc.req("GET", f"/session/{sid}/message")
    m = dict(tokens_input=0, tokens_cache_read=0, tokens_cache_write=0, tokens_output=0, tokens_reasoning=0,
             model_calls=0, tool_calls=0, queries=0, query_errors=0, dataset_queries=0, discovery_calls=0)
    cmds, last, infra_err = [], "", None
    for msg in msgs:
        i = msg["info"]
        if i["role"] != "assistant":
            continue
        tk = i.get("tokens") or {}
        m["tokens_input"] += tk.get("input", 0)
        m["tokens_output"] += tk.get("output", 0)
        m["tokens_reasoning"] += tk.get("reasoning", 0)
        m["tokens_cache_read"] += (tk.get("cache") or {}).get("read", 0)
        m["tokens_cache_write"] += (tk.get("cache") or {}).get("write", 0)
        if i.get("error"):
            infra_err = json.dumps(i["error"])[:300]
        texts = []
        for p in msg["parts"]:
            if p["type"] == "step-start":
                m["model_calls"] += 1
            elif p["type"] == "tool":
                m["tool_calls"] += 1
                c = (p["state"].get("input") or {}).get("command", "") or ""
                out = str(p["state"].get("output", "") or p["state"].get("error", ""))
                cmds.append({"cmd": c[:4000], "out": out[:600], "status": p["state"].get("status")})
                nq = len(QUERY_RE.findall(c))
                m["queries"] += nq
                if nq and ("Error:" in out or p["state"].get("status") == "error"):
                    m["query_errors"] += 1
                if DATASET_RE.search(c):
                    m["dataset_queries"] += 1
                if re.search(r"\bcx\s+datasets\b", c):
                    m["discovery_calls"] += 1
            elif p["type"] == "text":
                texts.append(p["text"])
        if texts:
            last = "\n".join(texts)
    m["tokens_total"] = m["tokens_input"] + m["tokens_cache_read"] + m["tokens_cache_write"] + m["tokens_output"] + m["tokens_reasoning"]
    return m, cmds, last, infra_err


def run_session(arm, sc, rep, outdir):
    oc = OC(arm)
    sid = oc.req("POST", "/session", {"title": f"{sc['id']} r{rep} {arm}"})["id"]
    prompt = render(sc["question"])
    prompt = prompt[0].upper() + prompt[1:]
    log, wrong, nudges, outcome, infra = [{"role": "user", "text": prompt}], 0, 0, "dnf_wall", None
    t0, msg = time.time(), prompt
    while time.time() - t0 < MAX_WALL:
        try:
            oc.req("POST", f"/session/{sid}/message",
                   {"model": MODEL, "parts": [{"type": "text", "text": msg}]},
                   timeout=MAX_WALL - (time.time() - t0) + 30)
        except Exception as e:
            if time.time() - t0 >= MAX_WALL - 5:
                outcome = "dnf_wall"
                try:
                    oc.req("POST", f"/session/{sid}/abort")
                except Exception:
                    pass
                break
            infra = f"send: {e}"
            try:
                oc.req("POST", f"/session/{sid}/abort")
            except Exception:
                pass
            break
        m, cmds, reply, err = collect(oc, sid)
        if err and not reply:
            infra = err
            break
        log.append({"role": "agent", "text": reply[-3000:]})
        kind, ans = parse_reply(reply)
        if kind == "answer":
            nudges = 0
            if grade(ans, sc["expected"]):
                outcome = "solved"
                log.append({"role": "grader", "text": "correct", "answer": ans[:500]})
                break
            wrong += 1
            log.append({"role": "grader", "text": "wrong", "answer": ans[:500]})
            if wrong >= MAX_WRONG:
                outcome = "dnf_max_wrong"
                break
            msg = REJECT
        elif kind == "abandon":
            outcome = "abandoned"
            break
        else:
            nudges += 1
            if nudges > MAX_NUDGES:
                outcome = "dnf_no_answer"
                break
            msg = NUDGE
        log.append({"role": "user", "text": msg})
    m, cmds, _, _ = collect(oc, sid)
    if infra and outcome == "dnf_wall":
        outcome = "infra_error"
    if outcome not in ("infra_error",) and m["model_calls"] and (time.time() - t0) / m["model_calls"] > 60:
        # A healthy endpoint answers in seconds; over 60 s per model call means the endpoint, not the agent, set the pace.
        outcome, infra = "infra_error", f"degraded model endpoint: {(time.time() - t0) / m['model_calls']:.0f}s per model call"
    res = dict(scenario=sc["id"], domain=sc["domain"], tier=sc["tier"], template=sc.get("template"),
               dataset_answerable=bool(sc.get("datasets")), arm=arm, rep=rep, model=MODEL["modelID"], outcome=outcome,
               wrong_answers=wrong, wall_seconds=round(time.time() - t0, 1), session=sid, infra_error=infra,
               started=t0, **m)
    path = os.path.join(outdir, arm, f"{sc['id']}_r{rep}.json")
    if outcome == "infra_error":
        path = path.replace(".json", f".infra{int(t0)}.json")
    json.dump(dict(result=res, transcript=log, commands=cmds), open(path, "w"), indent=1)
    with LOCK:
        if outcome != "infra_error":
            with open(os.path.join(outdir, "results.jsonl"), "a") as f:
                f.write(json.dumps(res) + "\n")
        print(f"{time.strftime('%H:%M:%S')} {arm:8} {sc['id']:12} r{rep} {outcome:13} wrong={wrong} tok={m['tokens_total']:>8} "
              f"t={res['wall_seconds']:>6}s tools={m['tool_calls']:>3} q={m['queries']:>3}" + (f" INFRA {infra[:120]}" if infra else ""), flush=True)
    return outcome


def done(outdir, arm, sid, rep):
    return os.path.exists(os.path.join(outdir, arm, f"{sid}_r{rep}.json"))


def safe_session(arm, sc, rep, outdir):
    try:
        return run_session(arm, sc, rep, outdir)
    except Exception as e:
        with LOCK:
            print(f"{time.strftime('%H:%M:%S')} {arm:8} {sc['id']:12} r{rep} infra_error   (exception: {str(e)[:150]})", flush=True)
        return "infra_error"


_GW = {"t": 0, "ok": False}
_GW_LOCK = threading.Lock()


def wait_for_model():
    """Block until a trivial model call returns within 45 s (checked at most once a minute)."""
    key = open(os.environ["MODEL_KEY_FILE"]).read().strip()
    while True:
        with _GW_LOCK:
            if time.time() - _GW["t"] < 60 and _GW["ok"]:
                return
            t0 = time.time()
            try:
                r = urllib.request.Request(os.environ["MODEL_BASE_URL"].rstrip("/") + "/chat/completions", data=json.dumps(
                    {"model": MODEL["modelID"], "messages": [{"role": "user", "content": "Reply OK"}], "max_tokens": 16}).encode(),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
                urllib.request.urlopen(r, timeout=45).read()
                ok = time.time() - t0 < 45
            except Exception:
                ok = False
            _GW.update(t=time.time(), ok=ok)
            if ok:
                return
            print(f"{time.strftime('%H:%M:%S')} model endpoint unhealthy ({time.time() - t0:.0f}s); waiting 5 min", flush=True)
        time.sleep(300)


def run_pair(sc, rep, outdir):
    for attempt in range(4):
        todo = [a for a in ARMS if not done(outdir, a, sc["id"], rep)]
        if not todo:
            return
        wait_for_model()
        with ThreadPoolExecutor(len(todo)) as ex:
            outs = list(ex.map(lambda a: safe_session(a, sc, rep, outdir), todo))
        if "infra_error" not in outs:
            return
        time.sleep(60 * (attempt + 1))


def load():
    out = []
    for sc in scenarios():
        p = os.path.join(RESULTS, "truth", sc["id"] + ".json")
        if not os.path.exists(p):
            continue
        t = json.load(open(p))
        if t["status"] == "ok":
            out.append(dict(sc, expected=t["expected"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=int(os.environ.get("REPS", 10)))
    ap.add_argument("--rep-start", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=int(os.environ.get("PARALLEL", 6)))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--arms", nargs="*", default=ARMS)
    a = ap.parse_args()
    scs = [s for s in load() if not a.only or s["id"] in a.only]
    outdir = os.path.join(RESULTS, "runs")
    ARMS[:] = a.arms
    for arm in ARMS:
        os.makedirs(os.path.join(outdir, arm), exist_ok=True)
    jobs = [(sc, rep) for rep in range(a.rep_start, a.rep_start + a.reps) for sc in scs]
    print(f"{len(scs)} scenarios x {a.reps} reps = {len(jobs)} jobs, arms {ARMS}, {a.parallel} in parallel -> {outdir}", flush=True)
    with ThreadPoolExecutor(a.parallel) as ex:
        list(ex.map(lambda j: run_pair(j[0], j[1], outdir), jobs))


if __name__ == "__main__":
    main()
