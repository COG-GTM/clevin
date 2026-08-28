"""K1 — mid-run steering.

Question: can a user message injected into a session that is actively working
change the plan, or does it only abort the current tool call?

Primitive under test: session event ingestion (`beta.sessions.events.send` with
`user.message` and `user.interrupt`) against a session that is mid-turn. With a
self-hosted `EnvironmentWorker`, a session parked on an in-flight native tool
call reports `status == "idle"` with `stop_reason.type == "requires_action"`,
so the driver records the exact status and the pending tool call at send time.

Usage:
  uv run --project runtime python experiments/K/k1_midrun_steering.py [--interrupt]
"""

from __future__ import annotations

import sys
import time
from typing import Any

from common import (
    SMOKE_PREFIX,
    client,
    create_session,
    latest_event_id,
    save,
    steer,
    summarize_events,
    usage,
    wait_for_event,
    wait_for_turn_end,
)

LONG_TASK = f"""{SMOKE_PREFIX}
Harmless local pacing check only. Do not touch Git, MCP, or any external state.
Announce your plan in one message first, then execute it.
Plan A: run three bash commands in sequence, each with an explicit tool timeout
above the sleep length: `sleep 240 && echo phase-a-1`, then
`sleep 240 && echo phase-a-2`, then `sleep 240 && echo phase-a-3`. Report each
marker as it returns, then write the marker list to /workspace/k1-phase-a.txt
and stop.
"""

STEER = """Change of plan, effective immediately. Abandon plan A: run no further
`sleep` commands for it. New plan B: write the single line PLAN-B to
/workspace/k1-phase-b.txt, then report (1) that you switched plans, (2) exactly
which plan-A markers you had completed when this instruction arrived, (3) your
revised plan. Stay within the smoke-test restrictions and then stop."""


def pending_tool_call(session_id: str) -> dict[str, Any] | None:
    events = summarize_events(session_id)
    tool_uses = {
        event["id"]: event
        for event in events
        if event["type"] in {"agent.tool_use", "agent.custom_tool_use"}
    }
    resolved = {
        event.get("tool_use_id")
        for event in events
        if event["type"] in {"user.tool_result", "agent.tool_result"}
    }
    for event in reversed(events):
        if event["type"] == "session.status_idle":
            stop = event.get("stop_reason") or {}
            if isinstance(stop, dict) and stop.get("type") == "requires_action":
                for event_id in stop.get("event_ids") or []:
                    if event_id in tool_uses and event_id not in resolved:
                        return tool_uses[event_id]
            return None
    return None


def main() -> int:
    use_interrupt = "--interrupt" in sys.argv
    label = "interrupt" if use_interrupt else "message"
    session = create_session(
        title=f"clevin-swarm-K-k1-steering-{label}",
        prompt=LONG_TASK,
        max_cost="150",
        metadata={"probe": f"k1-{label}"},
    )
    print("session:", session.id, flush=True)
    record: dict[str, Any] = {"session_id": session.id, "mode": label}

    first_tool = wait_for_event(session.id, "agent.tool_use", timeout=900, poll=5)
    record["first_tool_call"] = first_tool
    if first_tool is None:
        record["events"] = summarize_events(session.id)
        save(f"k1-{label}-events.json", record)
        print("no tool call observed", flush=True)
        return 1
    # Steer 30 s into the first 240 s sleep: the agent is demonstrably busy.
    time.sleep(30)

    status_at_send = client().beta.sessions.retrieve(session.id).status
    record["status_at_send"] = status_at_send
    record["pending_tool_at_send"] = pending_tool_call(session.id)
    record["events_before_steer"] = len(summarize_events(session.id))
    print("status at send:", status_at_send, record["pending_tool_at_send"], flush=True)

    sent_at = time.time()
    record["steer"] = steer(session.id, STEER, interrupt_first=use_interrupt, poll=10)
    record["send_accepted"] = record["steer"]["accepted"]
    print("steer sent:", record["send_accepted"], record["steer"]["tries"][0], flush=True)

    # Measure the post-steer turn only: anchor on the injected user.message so the
    # `end_turn` produced by `user.interrupt` itself is not mistaken for the reply.
    baseline = next(
        (
            event["id"]
            for event in reversed(summarize_events(session.id))
            if event["type"] == "user.message"
        ),
        latest_event_id(session.id),
    )
    record["baseline_event_id"] = baseline

    record["settled"] = wait_for_turn_end(
        session.id, timeout=2700, poll=15, after_event_id=baseline
    )
    record["seconds_to_settle"] = round(time.time() - sent_at, 1)
    record["final"] = usage(session.id)
    record["events"] = summarize_events(session.id)
    print(save(f"k1-{label}-events.json", record))
    print("settled:", record["settled"], "in", record["seconds_to_settle"], "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
