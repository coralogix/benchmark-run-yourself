"""Shared helpers for the evaluator: time windows and artifact readers."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def parse_ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def rec_ts(rec: dict) -> float | None:
    for k in ("ts_epoch", "ts", "at", "time", "timestamp"):
        if k in rec:
            t = parse_ts(rec[k])
            if t is not None:
                return t
    return None


def read_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            r["ts_epoch"] = rec_ts(r)
            out.append(r)
    return out


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def windows(meta: dict) -> dict:
    """Epoch-second windows for warm-up and the agent's run."""
    ws = parse_ts(meta.get("workload_started_at"))
    ho = parse_ts(meta.get("handover_at"))
    end = parse_ts(meta.get("deadline_at")) or parse_ts(meta.get("stopped_at"))
    stopped = parse_ts(meta.get("stopped_at"))
    if end and stopped:
        end = min(end, stopped)
    return {"warmup": (ws, ho), "run": (ho, end)}


def in_window(ts, win) -> bool:
    a, b = win
    return ts is not None and (a is None or ts >= a) and (b is None or ts < b)
