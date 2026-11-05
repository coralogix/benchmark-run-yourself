"""
Print search's cache/index stats (AS 2020-09, "why is search slow today").

    uv run python -m scripts.search_report [http://127.0.0.1:8103]
"""
import sys

import requests

DEFAULT = "http://127.0.0.1:8103"


def main(base=DEFAULT):
    r = requests.get(base.rstrip("/") + "/internal/search/stats", timeout=5)
    r.raise_for_status()
    s = r.json()
    total = (s.get("hits") or 0) + (s.get("misses") or 0)
    print("entries   %s" % s.get("entries"))
    print("hits      %s" % s.get("hits"))
    print("misses    %s" % s.get("misses"))
    print("hit rate  %.1f%%" % (100.0 * (s.get("hits") or 0) / total if total else 0))
    print("flushes   %s" % s.get("flushes"))
    idx = s.get("index") or {}
    for k in sorted(idx):
        print("index.%-10s %s" % (k, idx[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT))
