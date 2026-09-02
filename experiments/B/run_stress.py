"""Workstream B stress arms: resumption, changing requirements, interruption, compaction.

Each arm runs the same graded workload as ``run_arm.py`` but perturbs the session
through a native session primitive only:

* ``b5_resume``    -- one workload, several resumptions: the driver stops attending the
  session for ``B_IDLE_S`` seconds between turns and then sends a ``user.message``.
* ``b6_changing``  -- new requirements injected mid-run as a ``user.message``.
* ``b7_interrupt`` -- ``user.interrupt`` fired while a tool call is in flight, then a
  redirect message (planned worker interruption).
* ``b8_compaction``-- the same workload with a procedure that inflates real tool output
  until the native compactor runs; measures constraint retention across compaction.

Usage: ``python run_stress.py b5_resume b6_changing b7_interrupt b8_compaction``
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle  # noqa: E402
import harness  # noqa: E402
import prompts  # noqa: E402

IDLE_S = float(os.environ.get("B_IDLE_S", "900"))


def base_arm(name: str, **kw: Any) -> harness.Arm:
    return harness.Arm(name=name, system=prompts.PLANNED, **kw)


def _finish(run: harness.Run, result: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    run.send(prompts.RECALL_PROBE)
    run.wait_idle(timeout_s=900)
    recall = [harness.text_of(e) for e in run.events() if e.get("type") == "agent.message"][-1:]
    report = run.finish(
        {
            "supervision": result,
            "recall_answer": recall,
            "codename_retained": any(prompts.CODENAME in t for t in recall),
            **extra,
        }
    )
    print(f"[{run.arm.name}] report -> {report}", flush=True)
    return json.loads(Path(report).read_text())


def resume_arm(run: harness.Run) -> dict[str, Any]:
    """Several resumptions of one session with real idle gaps in between.

    Tests the parity row "ask-and-block / resume later with the workspace intact":
    the driver deliberately does nothing for IDLE_S, then resumes with a probe that
    asks the agent to restate the constraints it must still be holding.
    """
    gaps: list[dict[str, Any]] = []
    outcome = "unknown"
    for index in range(2):
        # One supervised stretch, then an idle gap regardless of outcome: the gap is the
        # measurement (does the sandbox workspace and the early constraints survive it?).
        result = harness.supervise(run, nudge=prompts.NUDGE, timeout_s=1800)
        outcome = str(result["outcome"])
        run.log("idle_gap_start", seconds=IDLE_S, index=index)
        time.sleep(IDLE_S)
        before = run.grade()
        run.send(prompts.RESUME_PROBE)
        run.wait_idle(timeout_s=1800)
        after = run.grade()
        gaps.append(
            {
                "index": index,
                "idle_s": IDLE_S,
                "score_before": (before or {}).get("score"),
                "score_after": (after or {}).get("score"),
                "workspace_survived": bool(after and after.get("touched_src_files")),
            }
        )
    return _finish(run, {"outcome": outcome, "nudges": len(gaps)}, {"resumptions": gaps})


def changing_arm(run: harness.Run) -> dict[str, Any]:
    """Inject C6/C7 once the agent has started editing, then require PASS anyway."""
    injected = False

    def on_idle(r: harness.Run, _n: int) -> str | None:
        nonlocal injected
        if not injected:
            injected = True
            return prompts.NEW_REQUIREMENT
        return None

    # Inject mid-work rather than at an idle boundary: wait for the first edit, then send.
    deadline = time.time() + 900
    while time.time() < deadline:
        if any(str(e.get("type")).endswith("tool_use") for e in run.events()[3:]):
            break
        time.sleep(20)
    run.send(prompts.NEW_REQUIREMENT)
    injected = True
    result = harness.supervise(run, nudge=prompts.NUDGE, timeout_s=2400, on_idle=on_idle)
    run.send(prompts.REQUIREMENT_CHECK)
    run.wait_idle(timeout_s=900)
    check = [harness.text_of(e) for e in run.events() if str(e.get("type")).endswith("tool_result")]
    return _finish(run, result, {"requirement_check": check[-1:]})


def interrupt_arm(run: harness.Run) -> dict[str, Any]:
    """Fire ``user.interrupt`` during an in-flight tool call, then redirect."""
    interrupts: list[dict[str, Any]] = []
    for _ in range(2):
        deadline = time.time() + 600
        fired = False
        while time.time() < deadline:
            events = run.events()
            # A tool call is in flight when the newest tool_use has no tool_result after it.
            # Matching only on `events[-1]` misses it: a 15 s poll almost never lands in that
            # window (b7 run sesn_015eCLRAcPtEFt8DKaHaZTTZ never fired an interrupt in 600 s).
            last = next(
                (e for e in reversed(events) if str(e.get("type")).endswith("tool_use")), {}
            )
            answered = any(
                str(e.get("type")).endswith("tool_result")
                and e.get("tool_use_id") == last.get("id")
                for e in events
            )
            if last and not answered:
                before = len(events)
                run.send(
                    "Stop what you are doing this second. Before continuing, tell me in one "
                    "line what command you were running and what the release codename is, "
                    "then resume the task from your plan.",
                    interrupt=True,
                )
                fired = True
                run.wait_idle(timeout_s=900)
                after = run.events()
                interrupts.append(
                    {
                        "events_before": before,
                        "interrupted_tool": last.get("name"),
                        "reply": [
                            harness.text_of(e)
                            for e in after[before:]
                            if e.get("type") == "agent.message"
                        ][:2],
                    }
                )
                break
            time.sleep(3)
        if not fired:
            interrupts.append({"note": "no in-flight tool call observed within 600s"})
            break
    result = harness.supervise(run, nudge=prompts.NUDGE, timeout_s=2400)
    return _finish(run, result, {"interrupts": interrupts})


def compaction_arm(run: harness.Run) -> dict[str, Any]:
    result = harness.supervise(run, nudge=prompts.NUDGE, timeout_s=5400)
    return _finish(run, result, {"compaction_arm": True})


ARMS = {
    "b5_resume": (
        base_arm("b5_resume", notes="one workload, several resumptions with real idle gaps"),
        prompts.task_prompt,
        resume_arm,
    ),
    "b6_changing": (
        base_arm("b6_changing", notes="requirements changed mid-run (C6/C7 injected)"),
        prompts.task_prompt,
        changing_arm,
    ),
    "b7_interrupt": (
        base_arm("b7_interrupt", notes="planned interruption via user.interrupt mid-tool-call"),
        prompts.task_prompt,
        interrupt_arm,
    ),
    "b8_compaction": (
        base_arm(
            "b8_compaction",
            max_list_cost="120",
            nudge_limit=6,
            notes="context inflated with real tool output until native compaction runs",
        ),
        lambda: prompts.task_prompt() + "\n\n" + prompts.INFLATE,
        compaction_arm,
    ),
}


def run_one(name: str, *, environment_id: str, ledger: harness.Ledger, seed: str) -> Any:
    arm, prompt, driver = ARMS[name]
    arm.seed_file_id = seed
    run = harness.Run(arm, environment_id=environment_id, ledger=ledger)
    agent = run.create_agent()
    run.create_session(agent.id, prompt())
    return driver(run)


def main() -> int:
    names = sys.argv[1:] or list(ARMS)
    ledger = harness.Ledger()
    c = harness.client()
    env = harness.ensure_environment(c, ledger)
    seed = bundle.upload(c)
    ledger.record("file", seed, "seed tarball (native file resource)")
    print(f"environment: {env} seed_file: {seed}", flush=True)
    results = harness.in_parallel(
        [
            (n, (lambda n=n: run_one(n, environment_id=env, ledger=ledger, seed=seed)))
            for n in names
        ]
    )
    print(
        json.dumps(
            {
                name: (
                    {
                        "session": r.get("session_id"),
                        "outcome": (r.get("supervision") or {}).get("outcome"),
                        "score": (r.get("grade") or {}).get("score"),
                        "compactions": (r.get("metrics") or {}).get("compactions"),
                        "codename_retained": r.get("codename_retained"),
                        "elapsed_s": r.get("elapsed_s"),
                    }
                    if isinstance(r, dict) and "error" not in r
                    else r
                )
                for name, r in results.items()
            },
            indent=2,
        )
    )
    ledger.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
