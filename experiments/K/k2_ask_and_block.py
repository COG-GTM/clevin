"""K2 — ask-a-question-and-block, resume later with the workspace intact.

Question: can the agent stop for a human decision and resume much later with the
Modal sandbox workspace intact, or does the worker lease / idle timeout destroy
it? What is the actual survivable wait?

Primitives under test:
  * custom tool (`type: "custom"`) as the native ask-and-block mechanism: the
    session goes `idle` with `stop_reason.type == "requires_action"` until a
    `user.custom_tool_result` event arrives;
  * `EnvironmentWorker` idle timeout (`max_idle`, 120 s in this deployment) and
    the Modal sandbox lifetime;
  * the `clevin-sessions` volume sub-path per session, which is what a resumed
    session re-mounts.

The driver writes a marker file before blocking, waits, then answers the custom
tool and asks the agent to re-read the marker. Modal state is sampled during the
wait to record whether the sandbox died while the session was blocked.

Usage:
  uv run --project runtime python experiments/K/k2_ask_and_block.py [wait_seconds]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from common import (
    AGENT_TOOLSET,
    SMOKE_PREFIX,
    client,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_event,
    wait_for_turn_end,
)

ASK_TOOL: dict[str, Any] = {
    "type": "custom",
    "name": "ask_human",
    "description": (
        "Ask the human operator one blocking question and wait for their answer. "
        "Use this when a decision is genuinely required before continuing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Discrete choices, if any.",
            },
        },
        "required": ["question"],
    },
}

PROMPT_TEMPLATE = """{smoke}
Harmless local workspace checks only. No Git, no MCP, no external state.

Step 1: create /workspace/k2-marker.txt containing exactly the line {marker}.
Step 2: run `hostname` and `date -u` and report both.
Step 3: you must not guess the next step. Call the ask_human tool exactly once
with the question "Which follow-up file should I create: alpha or beta?" and the
options ["alpha", "beta"]. Then stop and wait for the answer.
Step 4 (only after the answer arrives): re-read /workspace/k2-marker.txt, run
`hostname`, `date -u`, and `ls -la /workspace`, then report: the marker line you
read back, whether the marker survived, whether the hostname changed, and how
long the gap was. Finally create the file the human chose (/workspace/k2-<choice>.txt
containing that choice) and stop.
"""


async def modal_sandbox_state(session_id: str) -> dict[str, Any]:
    """Sample Modal-side state for the session's named sandbox and volume."""
    try:
        import modal

        from clevin_runtime.sandbox_runtime import SandboxRuntime

        os.environ.setdefault("MODAL_ENVIRONMENT", "clevin")
        snapshot = await SandboxRuntime().snapshot(session_id)
        state: dict[str, Any] = {
            "sandbox_id": snapshot.sandbox_id,
            "status": snapshot.status,
            "volume_path": snapshot.volume_path,
        }
        volume = modal.Volume.from_name("clevin-sessions", version=2)
        entries = [
            entry.path
            for entry in volume.listdir(f"/sessions/{session_id}", recursive=True)
        ]
        state["volume_entries"] = entries[:50]
        return state
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def blocked_event_id(session_id: str) -> tuple[str | None, dict[str, Any] | None]:
    """Find the pending custom tool call, if the session is blocked on one.

    Native tool calls also park the session in `idle`/`requires_action` while the
    `EnvironmentWorker` executes them, so the stop reason alone is ambiguous: the
    ask-and-block signal is a `requires_action` whose event_ids point at an
    `agent.custom_tool_use` event.
    """
    events = summarize_events(session_id)
    custom_tool_uses = {
        event["id"] for event in events if event["type"] == "agent.custom_tool_use"
    }
    for event in reversed(events):
        if event["type"] != "session.status_idle":
            continue
        stop = event.get("stop_reason") or {}
        if not isinstance(stop, dict):
            return None, None
        pending = [
            event_id
            for event_id in (stop.get("event_ids") or [])
            if event_id in custom_tool_uses
        ]
        return (pending[0] if pending else None), stop
    return None, None


def main() -> int:
    wait_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 900
    marker = f"K2-MARKER-{int(time.time())}"
    session = create_session(
        title="clevin-swarm-K-k2-ask-and-block",
        prompt=PROMPT_TEMPLATE.format(smoke=SMOKE_PREFIX, marker=marker),
        # `tools` is a full replacement and is cross-validated against
        # `mcp_servers`, so the MCP list must be cleared alongside it.
        overrides={"tools": [AGENT_TOOLSET, ASK_TOOL], "mcp_servers": []},
        max_cost="150",
        with_vault=False,
        metadata={"probe": "k2-ask-and-block"},
    )
    print("session:", session.id, "marker:", marker, flush=True)
    record: dict[str, Any] = {
        "session_id": session.id,
        "marker": marker,
        "requested_wait_seconds": wait_seconds,
        "samples": [],
    }

    ask_event = wait_for_event(
        session.id, "agent.custom_tool_use", timeout=1800, poll=10
    )
    tool_use_id, stop_reason = blocked_event_id(session.id)
    record["blocked"] = ask_event is not None
    record["ask_event"] = ask_event
    record["stop_reason_at_block"] = stop_reason
    record["custom_tool_use_id"] = tool_use_id
    print("blocked:", ask_event is not None, "stop_reason:", stop_reason, flush=True)
    if tool_use_id is None:
        record["events"] = summerize = summarize_events(session.id)
        save(f"k2-ask-and-block-{wait_seconds}s.json", record)
        print("no requires_action block; see evidence", len(summerize), flush=True)
        return 1

    blocked_at = time.time()
    record["samples"].append(
        {
            "elapsed_s": 0,
            "modal": asyncio.run(modal_sandbox_state(session.id)),
            "session_status": client().beta.sessions.retrieve(session.id).status,
        }
    )
    # Sample across the worker idle timeout (120 s) and well beyond it.
    targets = [150, 300]
    targets += list(range(900, wait_seconds, 900)) + [wait_seconds]
    for target in sorted({t for t in targets if t <= wait_seconds}):
        if target <= 0:
            continue
        remaining = target - (time.time() - blocked_at)
        if remaining > 0:
            time.sleep(remaining)
        record["samples"].append(
            {
                "elapsed_s": round(time.time() - blocked_at),
                "modal": asyncio.run(modal_sandbox_state(session.id)),
                "session_status": client().beta.sessions.retrieve(session.id).status,
            }
        )
        print("sample at", round(time.time() - blocked_at), "s", flush=True)

    client().beta.sessions.events.send(
        session.id,
        events=[
            {
                "type": "user.custom_tool_result",
                "custom_tool_use_id": tool_use_id,
                "content": [
                    {
                        "type": "text",
                        "text": "alpha — proceed with alpha and report the gap.",
                    }
                ],
            }
        ],
    )
    answered_at = time.time()
    record["gap_seconds"] = round(answered_at - blocked_at, 1)
    print("answered after", record["gap_seconds"], "s", flush=True)

    resumed = wait_for_event(session.id, "user.custom_tool_result", timeout=600, poll=5)
    record["resumed"] = resumed is not None
    record["seconds_to_resume"] = round(time.time() - answered_at, 1)
    record["modal_after_resume"] = asyncio.run(modal_sandbox_state(session.id))

    record["settled"] = wait_for_turn_end(session.id, timeout=1800, poll=10)
    record["final"] = usage(session.id)
    record["modal_final"] = asyncio.run(modal_sandbox_state(session.id))
    record["events"] = summarize_events(session.id)
    print(save(f"k2-ask-and-block-{wait_seconds}s.json", record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
