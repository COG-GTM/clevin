"""Cleanup sweep for workstream D temporary resources.

Every driver archives what it created, but a driver that dies mid-run (or an
interrupted session) can leak a resource. This sweep finds every unarchived
agent whose name matches the swarm's temporary-resource convention
(`clevin-swarm-D-*`) plus every unarchived session tagged
`swarm_workstream=D`, archives them, and prints the resulting ledger so the
cleanup ledger in the findings file can be regenerated on demand.

Refuses to touch the production agent.

    ANTHROPIC_API_KEY=... uv run --project runtime python experiments/D/sweep_cleanup.py
"""

from __future__ import annotations

import json

from _common import (
    Ledger,
    archive_agents,
    archive_sessions,
    client,
    write_evidence,
)

TEMP_PREFIX = "clevin-swarm-D-"


def main() -> None:
    api = client()
    ledger = Ledger()

    leaked_agents = [
        agent
        for agent in api.beta.agents.list(limit=100)
        if agent.name.startswith(TEMP_PREFIX) and agent.archived_at is None
    ]
    for agent in leaked_agents:
        ledger.created("agent", agent.id, agent.name)

    leaked_sessions = [
        session
        for session in api.beta.sessions.list(limit=100)
        if (session.metadata or {}).get("swarm_workstream") == "D"
        and session.archived_at is None
    ]
    for session in leaked_sessions:
        ledger.created("session", session.id, session.title or "")

    archive_sessions(api, ledger, ledger.ids("session"))
    archive_agents(api, ledger, ledger.ids("agent"))
    payload = {
        "experiment": "workstream D cleanup sweep",
        "leaked_agents": len(leaked_agents),
        "leaked_sessions": len(leaked_sessions),
        "cleanup": ledger.entries,
    }
    path = write_evidence("sweep_cleanup", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"evidence: {path}")


if __name__ == "__main__":
    main()
