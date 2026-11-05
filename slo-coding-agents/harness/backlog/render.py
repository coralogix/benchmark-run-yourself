"""Render the agent-visible BACKLOG.md from the feature catalogue.

    uv run python -m harness.backlog.render

Sources: features_core.yaml (FEAT-001..012), features_more.yaml (FEAT-013..100),
ops_tickets.md (REL/TD tickets). Evaluator-only keys (`services`, `risk`) are
never rendered.
"""
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "agent-kit" / "common" / "BACKLOG.md"

CORE_SIZES = {"FEAT-001": "L", "FEAT-002": "L", "FEAT-003": "S", "FEAT-004": "M", "FEAT-005": "M", "FEAT-006": "M",
              "FEAT-007": "L", "FEAT-008": "M", "FEAT-009": "L", "FEAT-010": "M", "FEAT-011": "S", "FEAT-012": "M"}


def load() -> list[dict]:
    core = yaml.safe_load((HERE / "features_core.yaml").read_text())["features"]
    for f in core:
        f.setdefault("size", CORE_SIZES[f["id"]])
    more = yaml.safe_load((HERE / "features_more.yaml").read_text())["features"]
    feats = core + more
    ids = [f["id"] for f in feats]
    assert len(ids) == len(set(ids)), "duplicate feature ids"
    return feats


def render() -> str:
    feats = load()
    L = [
        "# Larder backlog",
        "",
        "Exported from the team board this morning. Priorities are the product manager's (P1 highest).",
        "Sizes are the team's rough estimates (S ≈ under 15 minutes, M ≈ 15-40 minutes, L ≈ 40+ minutes).",
        "Money is always integer pence in new fields. Acceptance criteria are what QA will check.",
        "Internal (`/internal/...`) endpoints are served by the named service on its mesh port.",
        "",
        f"{len(feats)} feature tickets, followed by operational and tech-debt tickets.",
        "",
        "| Ticket | Title | Priority | Size |",
        "|---|---|---|---|",
    ]
    for f in feats:
        L.append(f"| {f['id']} | {f['title']} | {f['priority']} | {f['size']} |")
    L += ["| REL-1..REL-5 | Operational tickets (see below) | P2-P3 | ? |", "| TD-1, TD-2 | Tech debt (see below) | P3-P4 | ? |", "",
          "---", "", "## Features", ""]
    for f in feats:
        L.append(f"### {f['id']} · {f['title']} · {f['priority']} · {f['size']}")
        L.append(f["story"])
        L.append("")
        L.append("Acceptance criteria:")
        for a in f["acceptance"]:
            L.append(f"- {a}")
        L.append("")
    L.append("---")
    L.append("")
    L.append((HERE / "ops_tickets.md").read_text().rstrip())
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT} ({len(load())} features)")
