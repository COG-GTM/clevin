"""K5b — how is an attached Skill actually surfaced to the session?

K5 showed a Skill attached through `agent_with_overrides` is present on the
session resource yet the model denied having it when told not to run commands.
This probe asks the same session to find it, with filesystem tools allowed, to
establish whether Skills are delivered as sandbox files, as prompt content, or
not at all — i.e. whether Skills are usable as user-invokable playbooks.

Usage:
  uv run --project runtime python experiments/K/k5b_skill_discovery.py skill_0...
"""

from __future__ import annotations

import sys
from typing import Any

from common import (
    AGENT_TOOLSET,
    SMOKE_PREFIX,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

PROMPT = f"""{SMOKE_PREFIX}
Harmless local read-only inspection only: no Git, no MCP, no network, no writes.

A Skill named `clevin-verification` is attached to this session's configuration.
Find out how it is delivered to you:

1. State whether any skill is described in your context or tool definitions.
2. Search the sandbox filesystem for it: check /mnt, /mnt/skills, /skills,
   ~/.claude/skills, /opt, and the working directory, and run
   `find / -maxdepth 6 -iname 'SKILL.md' 2>/dev/null` and
   `find / -maxdepth 6 -ipath '*clevin-verification*' 2>/dev/null`.
3. If you find it, print the file and report the numbered command list and the
   rules section verbatim, plus its path and any version marker it contains.
4. If you cannot find it, say so plainly and list exactly where you looked.

Then stop.
"""


def main() -> int:
    skill_id = sys.argv[1]
    version = sys.argv[2] if len(sys.argv) > 2 else None
    skill: dict[str, Any] = {"type": "custom", "skill_id": skill_id}
    if version:
        skill["version"] = version
    session = create_session(
        title="clevin-swarm-K-k5b-skill-discovery",
        prompt=PROMPT,
        overrides={"skills": [skill], "tools": [AGENT_TOOLSET], "mcp_servers": []},
        max_cost="60",
        with_vault=False,
        metadata={"probe": "k5b-skill-discovery"},
    )
    print("session:", session.id, flush=True)
    settled = wait_for_turn_end(session.id, timeout=2400, poll=15)
    events = summarize_events(session.id)
    record = {
        "session_id": session.id,
        "skill": skill,
        "settled": settled,
        "events": events,
        "usage": usage(session.id),
    }
    print(save("k5b-skill-discovery.json", record))
    print("settled:", settled)
    for event in events:
        if event["type"] == "agent.message":
            print(event["text"][:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
