"""Thin wrapper around `cx -p $CX_PROFILE metrics query-range` returning parsed series."""
import json, os, subprocess


def query_range(expr, start, end, step):
    out = subprocess.run(
        ["cx", "-p", os.environ["CX_PROFILE"], "-o", "json", "metrics", "query-range", expr,
         "--start", str(start), "--end", str(end), "--step", step],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("["):])

