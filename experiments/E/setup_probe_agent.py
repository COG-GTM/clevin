"""Create (or print) the temporary workstream-E probe agent.

Primitive: `client.beta.agents`. A dedicated temporary agent keeps every probe off
the production agent version, as required by the swarm resource rules. Per-probe
prompt variation happens through `agent_with_overrides` at session creation, not by
publishing new versions of this agent.

Run: uv run --project runtime python experiments/E/setup_probe_agent.py
Prints the agent id; export it as CLEVIN_E_AGENT_ID for the probes.
"""

from __future__ import annotations

import sys

from harness import client, temp_name

BASE_SYSTEM = """You are a memory-store probe agent in a Claude Managed Agents experiment.

You never touch external state: no Git, no network, no package installs, no MCP calls.
You work only inside the session container: /workspace and any attached memory store
mount under /mnt/memory.

Answer exactly what is asked. When asked to report what you observe, quote observed
text verbatim rather than paraphrasing, and clearly separate what you already knew
from context from what you learned by running a tool. If an operation fails, report
the exact error text.
"""


def main() -> None:
    api = client()
    existing = [a for a in api.beta.agents.list(limit=100) if a.name.startswith("clevin-swarm-E-")]
    if existing and "--force" not in sys.argv:
        for agent in existing:
            print(f"{agent.id}\t{agent.name}\tv{agent.version}")
        return
    name = temp_name("probe-agent")
    agent = api.beta.agents.create(
        name=name,
        description="Temporary probe agent for workstream E (native Memory Store).",
        model={"id": "claude-sonnet-5", "effort": "medium"},
        system=BASE_SYSTEM,
        metadata={"experiment": "clevin-native-primitives", "swarm_workstream": "E"},
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
            }
        ],
        skills=[],
        multiagent=None,
    )
    print(f"{agent.id}\t{agent.name}\tv{agent.version}")


if __name__ == "__main__":
    main()
