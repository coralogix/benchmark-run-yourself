"""Validate the manual product-page (HC-1) diagnosis audit and compute its published figures.

    uv run python -m harness.evaluator.hc_diagnostics_cohort --write
    uv run python -m harness.evaluator.hc_diagnostics_cohort

The two audit manifests (docs/slo-hc-diagnostics-audit-01-10.json, -11-20.json) record, for HC-1
in every trial, the first correct causal explanation and the first successful targeted change, each
chosen by reading the transcript. This script checks each selection against the original transcript
and the final hidden tests, and computes per condition the number of trials whose first event came
within five minutes of handover and the median time over all 20 trials. It never infers a diagnosis
from first exposure to an affected ID or from a file read. Entries for other defects are ignored.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json

from harness.evaluator.records import ROOT, timestamp

OUTPUT = ROOT / "docs" / "slo-hc-diagnostics-cohort.json"
AUDITS = [ROOT / "docs" / f"slo-hc-diagnostics-audit-{part}.json"
          for part in ("01-10", "11-20")]
DEFECT = "HC-1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def reviewed_selections():
    first = json.loads(AUDITS[0].read_text())["records"]
    for item in first:
        if item["defect"] != DEFECT:
            continue
        yield {
            "pair": item["pair"], "condition": item["condition"],
            "run_id": item["run_id"], "defect": item["defect"],
            "diagnosis": item["diagnosis"], "repair": item["repair"],
            "audit_note": item.get("note", item["review"]),
        }
    second = json.loads(AUDITS[1].read_text())["runs"]
    for run in second:
        for defect, evidence in run["defects"].items():
            if defect != DEFECT:
                continue
            yield {
                "pair": run["pair"], "condition": run["condition"],
                "run_id": run["run_id"], "defect": defect,
                "diagnosis": evidence["diagnosis"],
                "repair": evidence["first_targeted_repair"],
                "audit_note": evidence["audit_note"],
            }


def build_cohort():
    valid = [row for row in json.loads((ROOT / "results/pair_summary_flash.json").read_text())
                 if not row.get("excluded")]
    pairs = sorted({(row["batch"], row["pair"]) for row in valid})
    records = []
    cache = {}
    for selected in reviewed_selections():
        rid, defect = selected["run_id"], selected["defect"]
        pair, condition = selected["pair"], selected["condition"]
        expected = next(row for row in valid
                        if (row["batch"], row["pair"]) == pairs[pair - 1]
                        and row["condition"].startswith("slo") == (condition == "slo"))
        require(expected["run_id"] == rid, "Audit entry is not a valid paired run")
        if rid not in cache:
            directory = ROOT / "runs" / rid
            meta = json.loads((directory / "run.json").read_text())
            transcript = [json.loads(line) for line in
                          (directory / "agent_transcript.log").read_text().splitlines()]
            result = json.loads((ROOT / "results" / f"{rid}.json").read_text())
            cache[rid] = meta, transcript, result
        meta, transcript, result = cache[rid]
        handover, deadline = timestamp(meta["handover_at"]), timestamp(meta["deadline_at"])
        item = {
            "pair": pair, "condition": condition, "run_id": rid, "defect": defect,
            "diagnosis": None, "first_targeted_repair": None,
            "audit_note": selected["audit_note"],
        }
        for role in ("diagnosis", "repair"):
            selection = selected[role]
            if selection is None:
                continue
            source = selection["source"]
            require(source["file"] == f"runs/{rid}/agent_transcript.log", "Wrong event file")
            line = source["line"]
            record = transcript[line - 1]
            part = record.get("part") or {}
            state = part.get("state") or {}
            inp = state.get("input") or {}
            tool = part.get("tool")
            if role == "diagnosis" and part.get("type") == "text":
                at, clock = record["timestamp"] / 1000, "transcript.timestamp"
                require(selection["quote"] == part.get("text"), "Diagnosis quote mismatch")
                quote = selection["quote"]
                kind = "agent_text"
            elif role == "diagnosis":
                require(tool == "todowrite" and state.get("status") == "completed", "Not a completed diagnostic plan")
                todos = [entry["content"] for entry in inp.get("todos", [])]
                quote = selection["quote"]
                # The review file may retain the entire task plan. Publish
                # only its relevant line, never the full backlog.
                matches = [entry for entry in todos if entry in quote
                           and ("meta_description" in entry or "product page %" in entry)]
                require(bool(matches), "Diagnostic task is absent")
                quote = matches[0]
                at, clock = state["time"]["end"] / 1000, "part.state.time.end"
                kind = "task_plan"
            else:
                require(tool in ("edit", "write") and state.get("status") == "completed", "Repair was not a completed edit")
                require("successfully" in state.get("output", "").lower(), "Targeted edit did not succeed")
                target = inp.get("filePath", "")
                new = inp.get("newString", inp.get("content", ""))
                require(target.endswith("/edge_api/shaping.py"), "Wrong product repair file")
                require("META_SUFFIX" in new or "humanise_category" in new, "Unrelated shaping edit")
                active = "\n".join(line for line in new.splitlines()
                                   if not line.lstrip().startswith("#"))
                require("truncate(text, room) + META_SUFFIX) %" not in active,
                        f"Edit still formats untrusted product text: {rid}:{line}")
                target = target.split("/services/")[-1]
                at, clock = state["time"]["end"] / 1000, "part.state.time.end"
                kind = "targeted_edit"
            require(handover <= at <= deadline, "Selected event is outside the session")
            event = {
                "kind": kind,
                "clock": clock,
                "source": {
                    "file": source["file"], "line": line,
                    "minute": round((at - handover) / 60, 6),
                    "at_utc": dt.datetime.fromtimestamp(at, dt.timezone.utc)
                    .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "record_sha256": digest(record),
                },
            }
            if role == "diagnosis":
                event["quote"] = quote
                item["diagnosis"] = event
            else:
                event["tool"], event["target_file"] = tool, target
                item["first_targeted_repair"] = event
        events = [event for event in (item["diagnosis"], item["first_targeted_repair"])
                  if event is not None]
        first = min(events, key=lambda event: event["source"]["minute"]) if events else None
        item["observed"] = first is not None
        item["minute"] = first["source"]["minute"] if first else None
        item["first_event_kind"] = first["kind"] if first else None
        item["censor_minute"] = round((deadline - handover) / 60, 6)
        final = result["defects"][defect]
        require(final["fixed"] == (defect in expected["fixed"]), "Final acceptance sources disagree")
        require(item["observed"] or not final["fixed"], "Passed defect has no observed diagnosis or repair; review needed")
        item["final_acceptance"] = {
            "fixed": final["fixed"], "tests_passed": final["tests_passed"],
            "tests_total": final["tests_total"],
            "source": {"file": f"results/{rid}.json", "json_pointer": f"/defects/{defect}"},
        }
        records.append(item)
    require(len(records) == 40, "Expected HC-1 in all 40 trials")
    require(len({(item["pair"], item["condition"]) for item in records}) == 40,
            "Duplicate or missing cohort case")
    records.sort(key=lambda item: (item["pair"], item["condition"], item["defect"]))
    summary = {"label": "Product pages"}
    for condition in ("basic", "slo"):
        sample = [item for item in records if item["condition"] == condition]
        require(len(sample) == 20, "Expected all 20 trials per condition")
        observed = sorted(item["minute"] for item in sample if item["observed"])
        # Unresolved causes are censored at the deadline, after every observed event. When the 10th and
        # 11th times are both observed, their mean is the ordinary 20-trial median and no time is assigned
        # to unresolved trials. Otherwise the median is reported as censored (null).
        median_censored = len(observed) < 11
        require(median_censored or all(item["censor_minute"] > max(observed) for item in sample if not item["observed"]),
                "Censoring before observed events requires different interval handling")
        median = None if median_censored else (observed[9] + observed[10]) / 2
        summary[condition] = {
            "trials": 20, "identified": len(observed), "censored": 20 - len(observed),
            "median_minute": None if median is None else round(median, 6),
            "median_censored": median_censored,
            "middle_order_statistics": observed[9:11],
            "identified_by_5_minutes": sum(value <= 5 for value in observed),
        }
    return {
        "schema_version": 1,
        "defect": DEFECT,
        "definition": "Time from handover to the first recorded correct causal explanation in agent text/task plan, or the first successful completed edit directed at that cause, whichever comes first. A targeted edit counts even when the agent did not narrate the diagnosis.",
        "excluded": "Generic error mentions, first affected entity IDs, tool/source reads, unrelated feature edits, unsuccessful tools and incorrect repair attempts do not count.",
        "median": "All 20 trials per condition are included. Unresolved causes are right-censored at the deadline and sorted after observed events. When the 10th and 11th times are both observed, their arithmetic mean is the ordinary 20-trial median and no time is assigned to unresolved cases; otherwise the median is reported as censored (null).",
        "review": "Manual read of every transcript. Selections are recorded in the two audit manifests; this script checks original records, successful tool completion, clocks, pair membership and final hidden-test results.",
        "summary": summary, "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    cohort = build_cohort()
    if args.write:
        OUTPUT.write_text(json.dumps(cohort, indent=2, ensure_ascii=False) + "\n")
    else:
        require(json.loads(OUTPUT.read_text()) == cohort, "Cohort export is stale; run with --write")
    concise = {condition: {key: value for key, value in cohort["summary"][condition].items()
                           if key in ("trials", "identified", "censored", "median_minute", "median_censored",
                                      "identified_by_5_minutes")}
               for condition in ("basic", "slo")}
    print(json.dumps({"validated": True, "records": len(cohort["records"]), "summary": concise}, indent=2))


if __name__ == "__main__":
    main()
