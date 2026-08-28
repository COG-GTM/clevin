"""K5c — making an attached Skill actually invocable by name.

K5b established that a Skill attached to the agent is materialised as
`/workspace/skills/<name>/SKILL.md` inside the sandbox but is *not* announced in
the system prompt and has no listing or loading tool, so the model denies having
it. This probe tests the minimal native fix: the same Skill, plus one paragraph
appended to the agent's own system prompt telling it where Skills live.

Primitive under test: agent `skills` configuration and the `system` override —
i.e. exactly the two fields that would change in
`packages/provision/src/agent-definition.ts` if this works.

Usage:
  uv run --project runtime python experiments/K/k5c_skill_discovery_prompt.py \
      skill_0... [version]
"""

from __future__ import annotations

import os
import sys
from typing import Any

from common import (
    AGENT_TOOLSET,
    SMOKE_PREFIX,
    client,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

# The candidate agent-definition paragraph. Kept verbatim in sync with
# packages/provision/src/agent-definition.ts (SKILL_DISCOVERY).
SKILL_DISCOVERY = """
Skills (named playbooks):
- Skills attached to your configuration are materialized in your workspace as
  `/workspace/skills/<skill-name>/SKILL.md`. They are not listed in this prompt.
- Before starting a task, list `/workspace/skills` and read the `SKILL.md` of any
  skill whose description matches the task, or that the user names directly.
- A skill's procedure takes precedence over your default approach for that task.
  Follow its steps in order and report deviations.
"""

PROMPT = f"""{SMOKE_PREFIX}
Harmless local read-only inspection only: no Git, no MCP, no writes.
Invoke the clevin-verification playbook. Do not run any of the commands it
prescribes; instead report, verbatim and in order, its numbered command list,
then quote its rules section exactly, then state the skill's path and any version
marker it contains. Then stop.
"""


def agent_system_prompt() -> str:
    agent = client().beta.agents.retrieve(os.environ["CLEVIN_AGENT_ID"])
    return agent.system or ""


def main() -> int:
    skill_id = sys.argv[1]
    skill: dict[str, Any] = {"type": "custom", "skill_id": skill_id}
    if len(sys.argv) > 2:
        skill["version"] = sys.argv[2]
    system = agent_system_prompt().rstrip() + "\n" + SKILL_DISCOVERY
    session = create_session(
        title="clevin-swarm-K-k5c-skill-discovery-prompt",
        prompt=PROMPT,
        overrides={
            "skills": [skill],
            "tools": [AGENT_TOOLSET],
            "mcp_servers": [],
            "system": system,
        },
        max_cost="60",
        with_vault=False,
        metadata={"probe": "k5c-skill-discovery-prompt"},
    )
    print("session:", session.id, flush=True)
    settled = wait_for_turn_end(session.id, timeout=2400, poll=15)
    events = summarize_events(session.id)
    texts = " ".join(e.get("text", "") for e in events if e["type"] == "agent.message")
    record = {
        "session_id": session.id,
        "skill": skill,
        "system_suffix": SKILL_DISCOVERY,
        "settled": settled,
        "events": events,
        "quoted_v2_marker": "Version marker: v2" in texts,
        "quoted_commands": "uv run --project runtime mypy runtime/src" in texts,
        "usage": usage(session.id),
    }
    print(save("k5c-skill-discovery-prompt.json", record))
    print(
        "settled:",
        settled,
        "quoted commands:",
        record["quoted_commands"],
        "quoted v2 marker:",
        record["quoted_v2_marker"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
