#!/usr/bin/env python3
"""Computes every figure on the results page from the scores and the budget inputs.

  python3 scoring/figures.py results/scores.csv [--budget config/budget.json] [--json results/figures.json]

results/scores.csv comes from `scoring/blind.py unblind`: one row per scenario,
platform and dimension; r1 to r5 are each agent's mark (1, 0.5 or 0; a count
for Optimize's real_items), `score` their sum, `max` the dimension's maximum
over the five agents and `counts_toward_total` whether it adds to the
scenario total. `false_claims` rows give each agent's count of false claims.

Prints:
  scoreboard        per scenario and platform, in two views. Cause + impact:
                    the total of the counted dimensions over their maximum.
                    Cause only: the cause points over the cause maximum (five
                    faults: the five causes, out of 25). Icon in each view:
                    Best (star) for the highest score in the scenario, ties
                    included; otherwise Strong (check) at 80% or more of the
                    maximum, Mixed (dot) from 55% to under 80%, Weak (cross)
                    under 55%. Optimize: the average count of real
                    improvements per agent, Best for the highest; it has no
                    cause score.
  cause and impact  over the scenarios with a cause score (nine), weighted
                    equally: cause = mean of cause over cause maximum; impact
                    = mean of (total minus cause) over (maximum minus cause
                    maximum); cause + impact = mean of total over maximum, and
                    the rank by it.
  false claims      each platform's count over all ten scenarios, fewest first.
  affordable share  from config/budget.json: each other platform's blended share,
                    the spend-weighted combination of its per-signal shares,
                    1 / sum(split[s] / share[s]); Coralogix is 100%.
  cost              the reference budget divided by the blended share,
                    rounded to the nearest $1,000.
"""
import csv, json, os, sys
from collections import OrderedDict

PLATFORMS = ["Coralogix", "Datadog", "Dynatrace", "Grafana Cloud", "New Relic"]
BUDGET_KEYS = OrderedDict([("datadog", "Datadog"), ("dynatrace", "Dynatrace"),
                           ("grafana", "Grafana Cloud"), ("newrelic", "New Relic")])
CAUSE_DIMENSION = {"FT2": "cause_total"}
ITEMS_SCENARIO = "FT1"
STRONG, MIXED = 0.80, 0.55
LABEL = {"star": "Best", "check": "Strong", "dot": "Mixed", "cross": "Weak", "": ""}


def num(v):
    return float(v) if v not in ("", None) else None


def load(path):
    data = OrderedDict()
    for r in csv.DictReader(open(path)):
        if r["platform"] not in PLATFORMS:
            sys.exit(f"unknown platform {r['platform']!r}; expected one of {PLATFORMS}")
        data.setdefault(r["scenario_id"], OrderedDict((p, []) for p in PLATFORMS))[r["platform"]].append(r)
    return data


def icons(values, maxima):
    """Best for the top value (ties included), else by share of the maximum."""
    top = max(v for v in values.values() if v is not None)
    out = {}
    for p, v in values.items():
        if v is None:
            out[p] = ""
        elif abs(v - top) < 1e-9:
            out[p] = "star"
        elif maxima.get(p):
            share = v / maxima[p]
            out[p] = "check" if share >= STRONG else "dot" if share >= MIXED else "cross"
        else:
            out[p] = ""
    return out


def scenario_figures(sid, by_platform):
    out = OrderedDict()
    for p, rows in by_platform.items():
        if not rows:
            sys.exit(f"{sid}: no rows for {p}")
        fc = sum(num(r["score"]) or 0 for r in rows if r["dimension"] == "false_claims")
        if sid == ITEMS_SCENARIO:
            row = next(r for r in rows if r["dimension"] == "real_items")
            marks = [num(row[f"r{i}"]) for i in range(1, 6) if num(row[f"r{i}"]) is not None]
            top = next((r for r in rows if r["dimension"] == "top_item"), None)
            out[p] = {"items": sum(marks) / len(marks), "agents": len(marks),
                      "found_top_item": num(top["score"]) if top else None, "false_claims": fc}
            continue
        counted = [r for r in rows if r["counts_toward_total"] == "yes"]
        if any(num(r["score"]) is None or num(r["max"]) is None for r in counted):
            sys.exit(f"{sid} {p}: a counted dimension has no score or maximum")
        crow = next(r for r in rows if r["dimension"] == CAUSE_DIMENSION.get(sid, "cause"))
        out[p] = {"total": sum(num(r["score"]) for r in counted), "maximum": sum(num(r["max"]) for r in counted),
                  "cause": num(crow["score"]), "cause_maximum": num(crow["max"]), "false_claims": fc}
    if sid == ITEMS_SCENARIO:
        ic = icons({p: f["items"] for p, f in out.items()}, {})
        for p in out:
            out[p]["icon"] = ic[p]
        return out
    full = icons({p: f["total"] for p, f in out.items()}, {p: f["maximum"] for p, f in out.items()})
    cause = icons({p: f["cause"] for p, f in out.items()}, {p: f["cause_maximum"] for p, f in out.items()})
    for p in out:
        out[p]["icon"], out[p]["cause_icon"] = full[p], cause[p]
    return out


def chart(figs):
    scored = [s for s, f in figs.items() if s != ITEMS_SCENARIO]
    res = OrderedDict()
    for p in PLATFORMS:
        c = sum(figs[s][p]["cause"] / figs[s][p]["cause_maximum"] for s in scored) / len(scored)
        i = sum((figs[s][p]["total"] - figs[s][p]["cause"]) / (figs[s][p]["maximum"] - figs[s][p]["cause_maximum"])
                for s in scored) / len(scored)
        t = sum(figs[s][p]["total"] / figs[s][p]["maximum"] for s in scored) / len(scored)
        res[p] = {"cause_pct": 100 * c, "impact_pct": 100 * i, "combined_pct": 100 * t}
    ranked = sorted(res, key=lambda p: -res[p]["combined_pct"])
    for n, p in enumerate(ranked):
        prev = ranked[n - 1] if n else None
        res[p]["rank"] = res[prev]["rank"] if prev and abs(res[p]["combined_pct"] - res[prev]["combined_pct"]) < 1e-9 else n + 1
    return scored, res


def pricing(path):
    cfg = json.load(open(path))
    split, table = cfg.get("split", {}), cfg.get("table", {})
    budget = cfg.get("reference_budget_usd")
    missing = [f"split.{k}" for k in "TLM" if split.get(k) is None]
    missing += [f"table.{p}.{k}" for p in BUDGET_KEYS for k in "TLM" if table.get(p, {}).get(k) is None]
    if budget is None:
        missing.append("reference_budget_usd")
    if missing:
        return None, f"{path}: not filled in yet ({', '.join(missing)})"
    out = OrderedDict([("Coralogix", {"T": 1.0, "L": 1.0, "M": 1.0, "blended": 1.0, "cost_usd": round(budget, -3)})])
    for key, name in BUDGET_KEYS.items():
        row = table[key]
        blended = 1 / sum(split[k] / row[k] for k in "TLM")
        out[name] = {"T": row["T"], "L": row["L"], "M": row["M"], "blended": blended,
                     "cost_usd": round(budget / blended, -3)}
    return out, None


def fmt(v):
    return f"{v:g}"


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    opts = {}
    for flag in ("--json", "--budget"):
        if flag in args:
            i = args.index(flag); opts[flag] = args[i + 1]; del args[i:i + 2]
    here = os.path.dirname(os.path.abspath(__file__))
    budget_path = opts.get("--budget", os.path.join(here, "..", "config", "budget.json"))
    data = load(args[0])
    figs = OrderedDict((sid, scenario_figures(sid, rows)) for sid, rows in data.items())

    w = 28
    print("Scoreboard: Cause + impact (Cause only)")
    print(f"{'scenario':9}" + "".join(f"{p:>{w}}" for p in PLATFORMS))
    for sid, f in figs.items():
        cells = []
        for p in PLATFORMS:
            x = f[p]
            if sid == ITEMS_SCENARIO:
                cells.append(f"{LABEL[x['icon']]} {x['items']:.1f} items")
            else:
                cells.append(f"{LABEL[x['icon']]} {fmt(x['total'])}/{fmt(x['maximum'])} "
                             f"({LABEL[x['cause_icon']]} {fmt(x['cause'])}/{fmt(x['cause_maximum'])})")
        print(f"{sid:9}" + "".join(f"{c:>{w}}" for c in cells))

    scored, ci = chart(figs)
    print(f"\nFinding the cause and measuring impact, over {len(scored)} scenarios ({', '.join(scored)})")
    for p in sorted(PLATFORMS, key=lambda p: ci[p]["rank"]):
        c = ci[p]
        print(f"  {c['rank']}. {p:14} cause {c['cause_pct']:5.1f}%  impact {c['impact_pct']:5.1f}%  cause + impact {c['combined_pct']:5.1f}%")

    claims = {p: sum(f[p]["false_claims"] for f in figs.values()) for p in PLATFORMS}
    print(f"\nFalse claims over {len(figs)} scenarios (fewer is better)")
    for p in sorted(PLATFORMS, key=lambda p: claims[p]):
        print(f"  {p:14} {fmt(claims[p])}")

    shares, why = pricing(budget_path)
    print("\nData affordable at the reference budget, and the cost to keep Coralogix's data")
    if shares is None:
        print("  " + why)
    else:
        for p, s in shares.items():
            print(f"  {p:14} traces {100*s['T']:5.1f}%  logs {100*s['L']:5.1f}%  metrics {100*s['M']:5.1f}%  "
                  f"blended {100*s['blended']:5.1f}%  ~${s['cost_usd']/1000:.0f}k")

    if "--json" in opts:
        json.dump({"scenarios": figs, "cause_and_impact": ci, "false_claims": claims, "pricing": shares},
                  open(opts["--json"], "w"), indent=1)
        print(f"\nwrote {opts['--json']}")


if __name__ == "__main__":
    main()
