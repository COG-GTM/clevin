"""Roll the per-arm ``report.json`` files up into the table used in the findings.

Reads only artifacts produced by ``run_arm.py`` / ``run_stress.py`` / ``run_fault.py``;
every number in it comes from native session events (``session.usage``, event counts)
or from the fixture's own ``grade.py`` output captured in a ``tool_result`` event.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"
COLUMNS = [
    "arm",
    "workload",
    "session",
    "outcome",
    "score",
    "nudges",
    "tool_calls",
    "compactions",
    "tool_errors",
    "elapsed_s",
    "list_cost",
    "output_tokens",
    "cache_read",
    "codename",
]


def row(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics") or {}
    usage = metrics.get("usage") or {}
    supervision = report.get("supervision") or {}
    grade = report.get("grade") or {}
    tests = (grade.get("tests") or {}).get("n_passed") or 0
    return {
        "arm": report.get("arm"),
        # 14 tests = the original hand-written core only; 16 = core + the 18-module wide tier.
        "workload": "wide" if tests >= 16 else ("core" if tests else "?"),
        "session": report.get("session_id"),
        "outcome": supervision.get("outcome") or (report.get("stop_reason") or {}).get("type"),
        "score": (report.get("grade") or {}).get("score"),
        "nudges": supervision.get("nudges"),
        "tool_calls": metrics.get("tool_calls"),
        "compactions": metrics.get("compactions"),
        "tool_errors": metrics.get("error_tool_results"),
        "elapsed_s": report.get("elapsed_s"),
        "list_cost": (usage.get("list_cost") or {}).get("amount"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read": usage.get("cache_read_input_tokens"),
        "codename": report.get("codename_retained"),
    }


def main() -> int:
    reports = sorted(ARTIFACTS.glob("b*/*/report.json"))
    rows = [row(json.loads(p.read_text())) for p in reports]
    rows = [r for r in rows if r["arm"] != "b0_smoke"]
    (ARTIFACTS / "summary.json").write_text(json.dumps(rows, indent=2))
    widths = {c: max(len(c), *(len(str(r.get(c))) for r in rows)) for c in COLUMNS}
    line = lambda vals: "| " + " | ".join(str(v).ljust(widths[c]) for c, v in vals) + " |"  # noqa: E731
    print(line([(c, c) for c in COLUMNS]))
    print("|" + "|".join("-" * (widths[c] + 2) for c in COLUMNS) + "|")
    for r in rows:
        print(line([(c, r.get(c)) for c in COLUMNS]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
