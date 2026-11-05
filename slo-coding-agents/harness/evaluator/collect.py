"""Collect run evidence: local SLIs, outages, shadow alerts, workload outcomes,
third-party ledgers, resources, and Coralogix alert events / SLO status.

Every measurement here is computed identically for both conditions. Coralogix
SLO status is collected for the SLO-guided condition.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import statistics
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from harness.cx import sli as sli_mod
from harness.evaluator.common import in_window, read_json, read_jsonl, windows

MIN = 60.0


def _bins(records, win, key=lambda r: r["ts_epoch"]):
    a, b = win
    out = collections.defaultdict(list)
    for r in records:
        t = key(r)
        if in_window(t, win):
            out[int((t - a) // MIN)].append(r)
    n = int(math.ceil((b - a) / MIN)) if a and b else 0
    return out, n


def outages(edge, win) -> dict:
    """Minutes where customers effectively could not use the site (edge 5xx > 50% or no successful responses)."""
    bins, n = _bins(edge, win)
    bad_minutes = []
    for m in range(n):
        recs = bins.get(m, [])
        if not recs:
            bad_minutes.append(m)
            continue
        five = sum(1 for r in recs if int(r.get("code") or 0) >= 500 or int(r.get("code") or 0) == 0)
        if five / len(recs) > 0.5:
            bad_minutes.append(m)
    episodes = _episodes(bad_minutes)
    return {"outage_minutes": len(bad_minutes), "episodes": [{"start_min": s, "minutes": l} for s, l in episodes]}


def _episodes(minutes):
    eps = []
    for m in sorted(minutes):
        if eps and m == eps[-1][0] + eps[-1][1]:
            eps[-1][1] += 1
        else:
            eps.append([m, 1])
    return [tuple(e) for e in eps]


def shadow_alerts(ingress, win) -> dict:
    """Re-evaluate the common alert rules locally (5-minute rolling windows, per minute).

    Mirrors the Coralogix alert definitions that are computable from ingress data, so alert counts
    can be compared without depending on the alerting pipeline.
    """
    sf = [r for r in ingress if r.get("service") == "edge-api"]
    sfb, n = _bins(sf, win)
    internal = collections.defaultdict(list)
    for r in ingress:
        if r.get("service") not in (None, "edge-api"):
            internal[r["service"]].append(r)
    internal_bins = {svc: _bins(recs, win)[0] for svc, recs in internal.items()}

    def roll(b, m):
        out = []
        for k in range(max(0, m - 4), m + 1):
            out += b.get(k, [])
        return out

    def is5(r):
        c = int(r.get("code") or 0)
        return c >= 500 or c == 0

    rules = {
        "customer_api_5xx_rate_gt_5pct": ("P2", lambda m: (lambda rs: bool(rs) and sum(map(is5, rs)) / len(rs) > 0.05)(roll(sfb, m))),
        "checkout_failure_rate_gt_10pct": ("P1", lambda m: (lambda rs: bool(rs) and sum(1 for r in rs if is5(r) or r.get("reason") == "price_mismatch") / len(rs) > 0.10)(
            [r for r in roll(sfb, m) if r.get("route") == "POST /api/checkout"])),
        "customer_api_p95_gt_2s": ("P2", lambda m: (lambda rs: len(rs) >= 20 and _pct([float(r.get("duration_ms") or 0) for r in rs], 95) > 2000)(roll(sfb, m))),
        "internal_service_5xx_rate_gt_5pct": ("P2", lambda m: any(
            (lambda rs: bool(rs) and sum(map(is5, rs)) / len(rs) > 0.05)(roll(b, m)) for b in internal_bins.values())),
    }
    out = {}
    for name, (prio, fn) in rules.items():
        firing = [m for m in range(n) if fn(m)]
        eps = _episodes(firing)
        out[name] = {"priority": prio, "episodes": len(eps), "firing_minutes": len(firing),
                     "longest_minutes": max((l for _, l in eps), default=0)}
    return out


def _pct(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(math.ceil(p / 100 * len(vals))) - 1))
    return vals[k]


def workload(loadgen, win) -> dict:
    recs = [r for r in loadgen if in_window(r["ts_epoch"], win)]
    by_outcome = collections.Counter(r.get("outcome") for r in recs)
    checkout = [r for r in recs if r.get("step") == "checkout"]
    co = collections.Counter(r.get("outcome") for r in checkout)
    co_status = collections.Counter(str(r.get("status")) for r in checkout)
    lat = [float(r.get("latency_ms") or 0) for r in recs if r.get("outcome") == "ok"]
    feature_first_live = {}
    for r in sorted(recs, key=lambda r: r["ts_epoch"]):
        feat = r.get("probe")
        if feat and r.get("feature_live") and feat not in feature_first_live:
            feature_first_live[feat] = round((r["ts_epoch"] - win[0]) / MIN, 1)
    probes = collections.defaultdict(collections.Counter)
    for r in recs:
        if r.get("probe"):
            probes[r["probe"]][r.get("outcome")] += 1
    return {
        "requests": len(recs),
        "outcomes": dict(by_outcome),
        "checkouts_attempted": len(checkout),
        "checkouts_succeeded": co.get("ok", 0),
        "checkout_outcomes": dict(co),
        "checkout_status_codes": dict(co_status),
        "client_latency_ms_p50": round(_pct(lat, 50), 1),
        "client_latency_ms_p95": round(_pct(lat, 95), 1),
        "feature_first_live_min": feature_first_live,
        "feature_probe_outcomes": {k: dict(v) for k, v in probes.items()},
    }


def psp(art: Path, win) -> dict:
    led = read_json(art / "psp_ledger.json", {}) or {}
    charges = led.get("charges", []) if isinstance(led, dict) else []
    refunds = led.get("refunds", []) if isinstance(led, dict) else []

    def ts(x):
        from harness.evaluator.common import rec_ts
        return rec_ts(x)

    def in_run(x):
        t = ts(x)
        return t is None or in_window(t, win)

    caps = collections.Counter(c.get("order_ref") for c in charges if in_run(c) and c.get("status", "captured") == "captured")
    dup_orders = {k: v for k, v in caps.items() if k and v > 1}
    refunded = collections.defaultdict(int)
    for r in refunds:
        if in_run(r):
            refunded[r.get("charge_id")] += int(r.get("amount_pence") or 0)
    return {
        "stats": led.get("stats") if isinstance(led, dict) else None,
        "captures_in_run": sum(caps.values()),
        "orders_with_duplicate_captures_in_run": len(dup_orders),
        "duplicate_captures_in_run": sum(v - 1 for v in dup_orders.values()),
        "duplicate_capture_pence_in_run": _extra_capture_pence(charges, dup_orders, in_run),
        "refunds_in_run": sum(1 for r in refunds if in_run(r)),
    }


def _extra_capture_pence(charges, dup_orders, in_run) -> int:
    """Money taken beyond the first capture of each double-charged order."""
    seen, extra = set(), 0
    for c in charges:
        o = c.get("order_ref")
        if o in dup_orders and in_run(c):
            if o in seen:
                extra += int(c.get("amount_pence") or 0)
            seen.add(o)
    return extra


def resources(res, events, win) -> dict:
    per = collections.defaultdict(lambda: {"cpu": [], "rss": []})
    for r in res:
        if in_window(r["ts_epoch"], win):
            s = r.get("service")
            if r.get("cpu_percent") is not None:
                per[s]["cpu"].append(float(r["cpu_percent"]))
            if r.get("rss_bytes") is not None:
                per[s]["rss"].append(float(r["rss_bytes"]))
    out = {}
    for s, v in per.items():
        out[s] = {"cpu_pct_avg": round(statistics.fmean(v["cpu"]), 1) if v["cpu"] else None,
                  "cpu_pct_max": round(max(v["cpu"]), 1) if v["cpu"] else None,
                  "rss_mb_max": round(max(v["rss"]) / 2**20, 1) if v["rss"] else None}
    restarts = collections.Counter()
    crashes = collections.Counter()
    for e in events:
        if not in_window(e["ts_epoch"], win):
            continue
        kind = e.get("event") or e.get("type")
        if kind in ("restart", "restart_requested", "service.restart"):
            restarts[e.get("service")] += 1
        if kind in ("exit", "exited", "crash", "service.exited") and e.get("reason", "") != "agent_request" and not e.get("requested"):
            crashes[e.get("service")] += 1
    return {"processes": out, "agent_restarts": dict(restarts), "unplanned_exits": dict(crashes)}


def coralogix_alerts(meta: dict, rd: Path, win) -> dict:
    prof = (meta.get("account") or {}).get("operator_profile")
    manifest = read_json(rd / "cx_manifest.json", {}) or {}
    if not prof or not manifest:
        return {"available": False}
    a, b = win
    start = dt.datetime.fromtimestamp(a, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = dt.datetime.fromtimestamp(b, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    version_to_alert = {}
    klass = {al["name"]: al.get("asset_class", "common") for al in manifest.get("alerts", [])}
    for al in manifest.get("alerts", []):
        if not al.get("id"):
            continue
        d = _cx(prof, "alerts", "get", al["id"])
        ad = (d or {}).get("alertDef", {})
        version_to_alert[ad.get("alertVersionId")] = al["name"]
        version_to_alert[al["id"]] = al["name"]
    events = _cx(prof, "alerts", "events", "--start", start, "--end", end) or []
    per = collections.defaultdict(lambda: {"triggered": 0, "resolved": 0, "priority": None, "first_min": None,
                                           "asset_class": None, "timeline": []})
    for ev in events:
        p = ev.get("cxEventPayload", {})
        name = version_to_alert.get(p.get("alertId")) or version_to_alert.get((ev.get("cxEventMetadata") or {}).get("alert_id"))
        if not name:
            continue
        st = p.get("status", "")
        rec = per[name]
        rec["priority"] = ((p.get("payload") or {}).get("severity") or {}).get("priority", rec["priority"])
        from harness.evaluator.common import parse_ts
        t = parse_ts(p.get("timestamp") or ev.get("cxEventTimestamp"))
        rec["asset_class"] = klass.get(name, "common")
        if t:
            rec["timeline"].append({"min": round((t - a) / MIN, 1), "status": st.replace("ALERT_STATUS_", "")})
        if "TRIGGERED" in st:
            rec["triggered"] += 1
            m = round((t - a) / MIN, 1) if t else None
            rec["first_min"] = m if rec["first_min"] is None else min(rec["first_min"], m)
        elif "RESOLVED" in st:
            rec["resolved"] += 1
    return {"available": True, "window": [start, end], "alerts": dict(per),
            # total_triggered counts the common alerts, which both conditions have
            "total_triggered": sum(v["triggered"] for v in per.values() if v["asset_class"] == "common"),
            "slo_alerts_triggered": sum(v["triggered"] for v in per.values() if v["asset_class"] == "slo")}


def coralogix_slo_status(meta: dict, rd: Path, win) -> dict:
    prof = (meta.get("account") or {}).get("operator_profile")
    manifest = read_json(rd / "cx_manifest.json", {}) or {}
    if not prof or not manifest.get("slos"):
        return {"available": False, "reason": "no SLOs in this condition's account"}
    key, region = _profile_key(prof)
    a, b = win
    fmt = lambda t: dt.datetime.fromtimestamp(t - (t % 60), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for s in manifest["slos"]:
        res = {}
        for label, q in (("run", {"from": fmt(a), "to": fmt(b)}), ("to_date", {})):
            url = f"https://api.{region}.coralogix.com/mgmt/openapi/5/slo/slos/v1/{s['id']}/status"
            if q:
                url += "?" + urllib.parse.urlencode(q)
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "larder-slo-status/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    res[label] = json.loads(r.read())
            except Exception as e:  # noqa: BLE001
                res[label] = {"error": str(e)}
        out[s["sli"]] = res
    return {"available": True, "slos": out}


def _profile_key(profile):
    p = Path.home() / ".cx" / "profiles" / f"{profile}.toml"
    kv = {}
    for ln in p.read_text().splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip().strip('"')
    return kv["api_key"], kv["region"]


def _cx(profile, *args):
    p = subprocess.run(["cx", "-p", profile, "--no-console-link", "-o", "json", *args], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return None
    out = p.stdout.strip()
    if not out:
        return None
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0], default=0)
    try:
        return json.loads(out[start:])
    except json.JSONDecodeError:
        return None


def per_bucket_sli(ingress, win, bucket_min=10):
    a, b = win
    out = []
    t = a
    while t < b:
        e = min(b, t + bucket_min * MIN)
        res = sli_mod.evaluate_records(ingress, t, e)
        out.append({"from_min": round((t - a) / MIN), "to_min": round((e - a) / MIN),
                    **{k: v["attainment_pct"] for k, v in res.items()}})
        t = e
    return out


def ship_context(meta: dict, rd: Path, art: Path, ingress: list) -> list:
    """For every ship decision: the SLO state at that moment, reconstructed from the harness's own ingress
    log (identically for both conditions), plus when the agent last looked at SLOs (SLO-guided only)."""
    import bisect
    from harness.evaluator.common import parse_ts, read_jsonl as _rj
    ships = _rj(art / "ship_decisions.jsonl")
    if not ships:
        return []
    recs = sorted((r for r in ingress if r.get("ts_epoch")), key=lambda r: r["ts_epoch"])
    times = [r["ts_epoch"] for r in recs]
    slo_calls = []
    tr = rd / "agent_transcript.log"
    if tr.exists():
        for ln in tr.read_text(errors="replace").splitlines():
            if "slo-status" in ln and '"tool_use"' in ln:
                try:
                    slo_calls.append(json.loads(ln)["timestamp"] / 1000.0)
                except (ValueError, KeyError):
                    pass
    out = []
    for d in ships:
        t = parse_ts(d.get("at"))
        if t is None:
            continue
        w60 = recs[bisect.bisect_left(times, t - 3600):bisect.bisect_left(times, t)]
        w10 = recs[bisect.bisect_left(times, t - 600):bisect.bisect_left(times, t)]
        s60, s10 = sli_mod.evaluate_records(w60), sli_mod.evaluate_records(w10)
        per = {}
        for k, v in s60.items():
            b10 = None
            a10 = s10[k].get("attainment_pct")
            if a10 is not None:
                b10 = round((100 - a10) / (100 - v["target"]), 2)
            per[k] = {"budget_remaining_60m": v.get("error_budget_remaining_ratio"), "attainment_60m": v.get("attainment_pct"),
                      "burn_rate_10m": b10, "criticality": v["criticality"], "met_60m": v.get("met"),
                      **({"entities_below_target": v.get("entities_below_target"), "worst": (v.get("worst") or [])[:3]}
                         if v.get("group_by") else {})}
        crit = [x["budget_remaining_60m"] for x in per.values() if x["criticality"] == "critical" and x["budget_remaining_60m"] is not None]
        allb = [x["budget_remaining_60m"] for x in per.values() if x["budget_remaining_60m"] is not None]
        last_look = max((c for c in slo_calls if c <= t), default=None)
        out.append({
            "item": d["item"], "at": d.get("at"), "minute": round((t - parse_ts(meta["handover_at"])) / 60, 1),
            "accepted_score": d.get("accepted_score"), "review_overall": d.get("last_review_overall"),
            "open_must_fix": d.get("open_must_fix"), "reviews_for_item": d.get("reviews_for_item"),
            "min_critical_budget_60m": min(crit) if crit else None, "min_budget_60m": min(allb) if allb else None,
            "slos_failing_60m": sum(1 for x in per.values() if x["met_60m"] is False),
            "minutes_since_slo_status": round((t - last_look) / 60, 1) if last_look else None,
            "slo_status_calls_before": sum(1 for c in slo_calls if c <= t),
            "per_slo": per,
        })
    return out


def collect(meta: dict, rd: Path, cfg: dict) -> dict:
    art = Path(meta["artifacts"])
    w = windows(meta)
    if not w["run"][0]:
        raise SystemExit("run has no handover time; nothing to collect")
    ingress = read_jsonl(art / "ingress.jsonl")
    edge = [r for r in ingress if r.get("service") == "edge-api"]
    loadgen = read_jsonl(art / "loadgen.jsonl")
    events = read_jsonl(art / "events.jsonl")
    res = read_jsonl(art / "resources.jsonl")
    run = w["run"]
    out = {
        "windows": {k: list(v) for k, v in w.items()},
        "sli_run": sli_mod.evaluate_records(ingress, *run),
        "sli_warmup": sli_mod.evaluate_records(ingress, *w["warmup"]) if w["warmup"][0] else None,
        "sli_by_10min": per_bucket_sli(ingress, run),
        "outages": outages(edge, run),
        "shadow_alerts": shadow_alerts(ingress, run),
        "workload": workload(loadgen, run),
        "psp": psp(art, run),
        "partner": read_json(art / "partner_stats.json"),
        "resources": resources(res, events, run),
        "coralogix_alerts": coralogix_alerts(meta, rd, run),
        "coralogix_slo_status": coralogix_slo_status(meta, rd, run),
        "ship_context": ship_context(meta, rd, art, ingress),
    }
    out["summary"] = {
        "checkouts_succeeded": out["workload"]["checkouts_succeeded"],
        "checkouts_attempted": out["workload"]["checkouts_attempted"],
        "outage_minutes": out["outages"]["outage_minutes"],
        "slo_met": {k: v["met"] for k, v in out["sli_run"].items()},
        "coralogix_alerts_triggered": out["coralogix_alerts"].get("total_triggered"),
        "shadow_alert_episodes": sum(v["episodes"] for v in out["shadow_alerts"].values()),
        "duplicate_captures": out["psp"]["duplicate_captures_in_run"],
    }
    (rd / "collected.json").write_text(json.dumps(out, indent=2, default=str))
    return out
