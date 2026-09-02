"""Create/lookup the temporary workstream-C harness agent.

Primitive: agent configuration + versions. A dedicated temporary agent keeps
every reliability probe off the production agent version (§7) and pins a cheap
model so faults can be repeated many times; the tool surface under test
(`agent_toolset_20260401` served by a self-hosted `EnvironmentWorker`) is
identical to production.
"""

from __future__ import annotations

import datetime
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos  # noqa: E402

STATE = Path(__file__).resolve().parent / "artifacts" / "harness-agent.json"

SYSTEM = """You are a reliability test harness agent. You run inside a self-hosted \
sandbox with native bash and filesystem tools.

Rules:
- Do exactly what the user message asks, using the fewest tool calls possible.
- Never use git, never touch anything outside /workspace, never inspect or print \
environment variables, and never modify external state.
- When a tool call fails, report the failure verbatim in one short sentence, then \
decide: retry once only if the error looks transient, otherwise stop and explain.
- Keep your prose replies to at most three short sentences.
"""


def ensure_agent(model: str = "claude-haiku-4-5-20251001") -> tuple[str, int]:
    if STATE.exists():
        data = json.loads(STATE.read_text())
        return data["agent_id"], data["version"]
    c = chaos.client()
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"clevin-swarm-C-{stamp}-{uuid.uuid4().hex[:6]}"
    agent = c.beta.agents.create(
        name=name,
        description="Temporary workstream C reliability/tool-surface harness agent.",
        model={"id": model},
        system=SYSTEM,
        metadata={"experiment": "clevin-swarm-C", "temporary": "true"},
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    versions = [v.version for v in c.beta.agents.versions.list(agent.id)]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(
            {"agent_id": agent.id, "name": name, "version": max(versions)}, indent=2
        )
    )
    return agent.id, max(versions)


if __name__ == "__main__":
    print(ensure_agent())
