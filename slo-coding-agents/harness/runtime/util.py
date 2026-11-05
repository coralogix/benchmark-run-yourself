"""Small shared helpers (timestamps, JSONL writers, ports)."""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

# Fixed harness ports. Per-service app/mesh ports (8100+i / 8200+i) come from
# harness.runtime.registry (services.yaml).
BASE_PORTS = {
    "edge": 8000,
    "psp": 8090,
    "partner": 8091,
    "email": 8092,
    "psp_admin": 9090,
    "partner_admin": 9091,
    "email_admin": 9092,
    "control": 7070,
}


def port(name: str, offset: int | None = None) -> int:
    if offset is None:
        offset = int(os.environ.get("LARDER_PORT_OFFSET", "0") or 0)
    return BASE_PORTS[name] + offset


def iso_now() -> str:
    return iso(_dt.datetime.now(_dt.timezone.utc))


def iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_from_epoch(t: float) -> str:
    return iso(_dt.datetime.fromtimestamp(t, _dt.timezone.utc))


def parse_iso(s: str) -> _dt.datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def artifacts_dir(default: str | None = None) -> Path:
    p = Path(os.environ.get("LARDER_ARTIFACTS_DIR") or default or "./runs/local")
    p.mkdir(parents=True, exist_ok=True)
    return p


class JsonlWriter:
    """Append-only JSONL writer. Buffered; call flush() periodically and close() at exit."""

    def __init__(self, path: str | os.PathLike, line_buffered: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", buffering=1 if line_buffered else 64 * 1024, encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, rec: Mapping[str, Any]) -> None:
        line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            self._fh.write(line)

    def flush(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
            except ValueError:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            except ValueError:
                pass


def write_json_atomic(path: str | os.PathLike, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, default=str)
    os.replace(tmp, path)
