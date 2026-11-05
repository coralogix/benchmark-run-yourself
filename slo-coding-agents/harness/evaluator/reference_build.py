"""Build a source tree = baseline + selected reference patches, and optionally evaluate it.

    uv run python -m harness.evaluator.reference_build --select defects --out /tmp/ref-defects [--evaluate]
    uv run python -m harness.evaluator.reference_build --select FEAT-001,FEAT-017,SF-1 --out /tmp/ref-x

--select: `all`, `defects` (SF-*/FF-*/HC-*), `features` (FEAT-*), `none` (the baseline only), or a comma list of ids.
Patches live in harness/evaluator/reference/ as <ID>.patch or <ID>.<repo>.patch; the target repo is
detected by `git apply --check` (leading comment lines are ignored). Patches that don't apply on top
of the ones already applied are reported and skipped, never forced.

Used to validate the evaluator: the baseline must score 0 on features/defects, and a reference build
must score exactly the items it contains.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REF = Path(__file__).resolve().parent / "reference"
REPOS = ("commerce", "ops")
IGNORE = shutil.ignore_patterns(".venv", "node_modules", "var", "data", "__pycache__", ".pytest_cache", ".git")


def item_of(patch: Path) -> str:
    return re.match(r"^((?:FEAT-\d{3})|(?:SF-\d+)|(?:FF-\d+)|(?:HC-\d+))", patch.name).group(1)


def select(spec: str) -> list[Path]:
    patches = sorted(REF.glob("*.patch"))
    if spec == "all":
        return patches
    if spec == "defects":
        return [p for p in patches if not p.name.startswith("FEAT-")]
    if spec == "features":
        return [p for p in patches if p.name.startswith("FEAT-")]
    want = {s.strip() for s in spec.split(",") if s.strip()}
    return [p for p in patches if item_of(p) in want]


def _clean(patch: Path) -> str:
    lines = patch.read_text().splitlines(keepends=True)
    while lines and not lines[0].startswith(("diff --git", "--- ")):
        lines.pop(0)
    return "".join(lines)


def build(spec: str, out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    for repo in REPOS:
        shutil.copytree(ROOT / "baseline" / repo, out / repo, ignore=IGNORE)
        subprocess.run(["git", "init", "-q"], cwd=out / repo, check=True)
    applied, skipped = [], []
    for patch in select(spec):
        body = _clean(patch)
        tmp = out / ".patch.tmp"
        tmp.write_text(body)
        target = None
        for repo in REPOS:
            if subprocess.run(["git", "apply", "--check", str(tmp)], cwd=out / repo, capture_output=True).returncode == 0:
                target = repo
                break
        if target:
            subprocess.run(["git", "apply", str(tmp)], cwd=out / target, check=True)
            applied.append({"patch": patch.name, "item": item_of(patch), "repo": target})
        else:
            skipped.append({"patch": patch.name, "item": item_of(patch)})
        tmp.unlink()
    for repo in REPOS:
        shutil.rmtree(out / repo / ".git", ignore_errors=True)
    items = sorted({a["item"] for a in applied} - {s["item"] for s in skipped})
    res = {"select": spec, "applied": applied, "skipped": skipped, "complete_items": items}
    (out / "reference_build.json").write_text(json.dumps(res, indent=2))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--evaluate", action="store_true", help="run the full evaluation against the built tree")
    ap.add_argument("--offset", type=int, default=600)
    a = ap.parse_args()
    out = Path(a.out)
    res = build(a.select, out)
    print(f"applied {len(res['applied'])} patches, skipped {len(res['skipped'])}: {[s['patch'] for s in res['skipped']]}")
    if a.evaluate:
        from harness.evaluator.evaluate import run_evaluation
        rd = out.parent / (out.name + "-eval")
        rd.mkdir(exist_ok=True)
        ev = run_evaluation(None, rd, a.offset, source=out)
        print(json.dumps(ev["summary"], indent=2))


if __name__ == "__main__":
    main()
