"""Run one workstream-B arm end to end.

Usage:
  uv run --project ../../runtime python run_arm.py b1_baseline
  uv run --project ../../runtime python run_arm.py b1_baseline b3_subagents  # in parallel

Every arm runs the same workload (`prompts.TASK`) and differs only in native
configuration. Results land in `artifacts/<arm>/<run_id>/report.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle  # noqa: E402
import harness  # noqa: E402
import prompts  # noqa: E402


def arm_specs() -> dict[str, harness.Arm]:
    return {
        "b1_baseline": harness.Arm(
            name="b1_baseline",
            system=prompts.MINIMAL,
            notes="single session, minimal system prompt, no memory, no subagents",
        ),
        "b1_repeat": harness.Arm(
            name="b1_repeat",
            system=prompts.MINIMAL,
            notes="variance replicate of b1_baseline",
        ),
        "b2_planned": harness.Arm(
            name="b2_planned",
            system=prompts.PLANNED,
            notes="plan-file system-prompt strategy",
        ),
        "b3_subagents": harness.Arm(
            name="b3_subagents",
            system=prompts.DELEGATING,
            multiagent={"type": "coordinator"},
            roster=prompts.ROSTER,
            max_list_cost="90",
            notes="planned + native subagent roster",
        ),
        "b4_memory": harness.Arm(
            name="b4_memory",
            system=prompts.MEMORY,
            # The memory arm exhausted a $60 budget mid-migration (sesn_0173N5Tn7J7Tmau5H1ktx3Th)
            # while every non-memory arm finished for $41-$56; it gets headroom so the
            # comparison is completion-vs-completion rather than budget-vs-completion.
            max_list_cost="150",
            notes="planned + Memory Store mounted at /mnt/memory",
        ),
    }


def run_arm(
    name: str, *, environment_id: str, ledger: harness.Ledger, seed_file_id: str
) -> dict[str, Any]:
    arm = arm_specs()[name]
    arm.seed_file_id = seed_file_id
    if name == "b4_memory":
        arm.memory_store_id = memory_store_id()
    run = harness.Run(arm, environment_id=environment_id, ledger=ledger)
    agent = run.create_agent()
    run.create_session(agent.id, prompts.task_prompt())
    result = harness.supervise(run, nudge=prompts.NUDGE)
    # Constraint-retention probe after the work is over (or abandoned).
    run.send(prompts.RECALL_PROBE)
    run.wait_idle(timeout_s=900)
    recall = [
        harness.text_of(e)
        for e in run.events()
        if e.get("type") == "agent.message"
    ][-1:]
    report = run.finish(
        {
            "supervision": result,
            "recall_answer": recall,
            "codename_retained": any(prompts.CODENAME in text for text in recall),
        }
    )
    print(f"[{name}] report -> {report}")
    return json.loads(Path(report).read_text())


def memory_store_id() -> str:
    """Reuse the swarm's Memory Store; workstream B only reads/appends under its own path."""
    import os

    return os.environ["CLEVIN_MEMORY_STORE_ID"]


def main() -> int:
    names = sys.argv[1:] or ["b1_baseline"]
    ledger = harness.Ledger()
    c = harness.client()
    environment_id = harness.ensure_environment(c, ledger)
    seed_file_id = bundle.upload(c)
    ledger.record("file", seed_file_id, "seed tarball (native file resource)")
    print(f"environment: {environment_id} seed_file: {seed_file_id}")
    results = harness.in_parallel(
        [
            (
                n,
                (
                    lambda n=n: run_arm(
                        n,
                        environment_id=environment_id,
                        ledger=ledger,
                        seed_file_id=seed_file_id,
                    )
                ),
            )
            for n in names
        ]
    )
    summary = {
        name: {
            "session": r.get("session_id"),
            "outcome": (r.get("supervision") or {}).get("outcome"),
            "nudges": (r.get("supervision") or {}).get("nudges"),
            "score": (r.get("grade") or {}).get("score"),
            "elapsed_s": r.get("elapsed_s"),
            "list_cost": ((r.get("metrics") or {}).get("usage") or {}).get("list_cost"),
            "compactions": (r.get("metrics") or {}).get("compactions"),
            "codename_retained": r.get("codename_retained"),
        }
        if isinstance(r, dict) and "error" not in r
        else r
        for name, r in results.items()
    }
    print(json.dumps(summary, indent=2))
    ledger.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
