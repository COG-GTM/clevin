"""K6 — self-improvement: memory write-back, and Skill write-back.

Questions:
  a) Does a session write a durable learning to the native Memory Store, and does
     a later, independent session read it and measurably do better?
  b) Can the agent itself publish a new Skill version at task end?

Primitive under test: the `memory_store` session resource mounted at
`/mnt/memory` (write in the seed run, read in the replay run) and the agent's
native tool inventory with respect to Skill management.

The measured quantity is native tool traffic: how many sandbox commands the
session spends before the target import succeeds, with memory versus without.

Usage:
  uv run --project runtime python experiments/K/k6_self_improvement.py
"""

from __future__ import annotations

import sys
from typing import Any

from common import (
    SMOKE_PREFIX,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

MEMORY_PATH = "/mnt/memory/clevin-swarm-K/sandbox-facts.md"

SEED = f"""{SMOKE_PREFIX}
Harmless local sandbox checks only. No Git, no MCP, no external state.

Task: get `python3 -c "import numpy; print(numpy.__version__)"` to succeed inside
this sandbox. Work it out empirically; the image may not have the package.

When it works, append to {MEMORY_PATH} (creating directories as needed) a short
entry with: the exact working install command, the exact verification command,
and any command that failed and why. Keep it to verified facts only, no ticket
content and no secrets. Then report the number of shell commands you needed and
stop.
"""

REPLAY = f"""{SMOKE_PREFIX}
Harmless local sandbox checks only. No Git, no MCP, no external state.

Before doing anything else, check the attached memory store under /mnt/memory for
prior verified sandbox facts and say what you found (or that you found nothing).

Task: get `python3 -c "import numpy; print(numpy.__version__)"` to succeed inside
this sandbox, in as few shell commands as possible. Then report the number of
shell commands you used, and whether memory saved you any attempts. Stop.
"""

TOOL_INVENTORY = f"""{SMOKE_PREFIX}
Answer from your own tool definitions; run no commands and change no state.
List every tool you have available by exact name. Then state explicitly whether
any of them can create or publish a Claude Managed Agents Skill or a new Skill
version, or upload files to the Skills API. Then stop.
"""


def collect(label: str, session_id: str, *, with_memory: bool) -> dict[str, Any]:
    """Gather evidence for an already-created session (driver-crash recovery)."""
    settled = wait_for_turn_end(session_id, timeout=2700, poll=15)
    events = summarize_events(session_id)
    tool_calls = [event for event in events if event["type"] == "agent.tool_use"]
    print(label, session_id, settled, len(tool_calls), flush=True)
    return {
        "session_id": session_id,
        "with_memory": with_memory,
        "settled": settled,
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
        "tool_results": [
            event for event in events if event["type"] == "user.tool_result"
        ],
        "messages": [event for event in events if event["type"] == "agent.message"],
        "usage": usage(session_id),
    }


def run(label: str, prompt: str, *, with_memory: bool) -> dict[str, Any]:
    session = create_session(
        title=f"clevin-swarm-K-k6-{label}",
        prompt=prompt,
        max_cost="80",
        with_memory=with_memory,
        with_vault=False,
        metadata={"probe": f"k6-{label}"},
    )
    print(label, session.id, flush=True)
    settled = wait_for_turn_end(session.id, timeout=2700, poll=15)
    events = summarize_events(session.id)
    tool_calls = [event for event in events if event["type"] == "agent.tool_use"]
    return {
        "session_id": session.id,
        "with_memory": with_memory,
        "settled": settled,
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
        "tool_results": [
            event for event in events if event["type"] == "user.tool_result"
        ],
        "messages": [event for event in events if event["type"] == "agent.message"],
        "usage": usage(session.id),
    }


def main() -> int:
    record: dict[str, Any] = {}
    if len(sys.argv) > 1 and sys.argv[1] == "--collect":
        # --collect seed=sesn_... replay_with_memory=sesn_... ...
        for pair in sys.argv[2:]:
            label, session_id = pair.split("=", 1)
            record[label] = collect(
                label, session_id, with_memory=label != "replay_without_memory"
            )
        print(save("k6-self-improvement.json", record))
        for label in record:
            print(label, "tool calls:", record[label]["tool_call_count"])
        return 0
    record["seed"] = run("seed-write", SEED, with_memory=True)
    record["replay_with_memory"] = run("replay-memory", REPLAY, with_memory=True)
    record["replay_without_memory"] = run("replay-nomemory", REPLAY, with_memory=False)
    record["tool_inventory"] = run("tool-inventory", TOOL_INVENTORY, with_memory=False)
    print(save("k6-self-improvement.json", record))
    for label in ("seed", "replay_with_memory", "replay_without_memory"):
        print(label, "tool calls:", record[label]["tool_call_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
