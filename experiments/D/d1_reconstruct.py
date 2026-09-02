"""D1 — can the agent be reconstructed declaratively from `packages/provision`?

Primitive: the Managed Agents *agent* resource and its version history.

The driver takes the code-declared desired state straight out of the
TypeScript provisioner (`pnpm --filter @clevin/provision drift
--desired-only`), creates a throwaway agent from it, and diffs what the API
stored against what the code asked for. It then diffs the same desired state
against the live production agent and against the previous production
version, which shows what code-managed reconstruction can and cannot
reproduce.

Read-only against production. Creates and archives one temporary agent.

    ANTHROPIC_API_KEY=... CLEVIN_AGENT_ID=... \
        uv run --project runtime python experiments/D/d1_reconstruct.py
"""

from __future__ import annotations

import json
import os
from typing import Any

import anthropic
from _common import (
    Ledger,
    agent_projection,
    api_error,
    archive_agents,
    client,
    desired_agent_state,
    diff_state,
    temp_name,
    write_evidence,
)


def main() -> None:
    api = client()
    ledger = Ledger()
    result: dict[str, Any] = {"experiment": "D1 declarative reconstruction"}
    try:
        run(api, ledger, result)
    finally:
        archive_agents(api, ledger, ledger.ids("agent"))
        result["cleanup"] = ledger.entries
        path = write_evidence("d1_reconstruct", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"evidence: {path}")


def run(api: anthropic.Anthropic, ledger: Ledger, result: dict[str, Any]) -> None:
    production_agent_id = os.environ["CLEVIN_AGENT_ID"]
    desired = desired_agent_state()
    result["desired_keys"] = sorted(desired)

    # --- reconstruction: build a fresh agent purely from the code definition.
    name = temp_name("reconstruct")
    create_body = {
        **desired,
        "name": name,
        "description": "workstream D declarative reconstruction probe",
        "metadata": {
            **dict(desired["metadata"] or {}),
            "swarm_workstream": "D",
            "swarm_experiment": "d1",
        },
    }
    created = api.beta.agents.create(**create_body)  # type: ignore[arg-type]
    ledger.created("agent", created.id, name)

    retrieved = api.beta.agents.retrieve(created.id)
    drift, server_added = diff_state(create_body, agent_projection(retrieved))
    result["reconstruction"] = {
        "agent_id": created.id,
        "version": retrieved.version,
        "drift": drift,
        "server_added": server_added,
    }

    # --- the same desired state against the live production agent.
    production = api.beta.agents.retrieve(production_agent_id)
    prod_actual = agent_projection(production)
    prod_drift, prod_added = diff_state(desired, prod_actual)
    result["production"] = {
        "agent_id": production.id,
        "current_version": production.version,
        "drift_paths": [entry["path"] for entry in prod_drift],
        "drift": [
            {
                "path": entry["path"],
                "desired_summary": _summarize(entry["desired"]),
                "actual_summary": _summarize(entry["actual"]),
            }
            for entry in prod_drift
        ],
        "server_added": prod_added,
    }

    # --- version-to-version delta inside production's own history.
    versions = [
        version.version
        for version in api.beta.agents.versions.list(production_agent_id, limit=100)
    ]
    result["production_versions"] = versions
    if production.version > 1:
        previous = api.beta.agents.retrieve(
            production_agent_id, version=production.version - 1
        )
        version_drift, version_added = diff_state(
            agent_projection(previous), prod_actual
        )
        result["version_delta"] = {
            "from": previous.version,
            "to": production.version,
            "changed_paths": [entry["path"] for entry in version_drift],
            "added_paths": version_added,
        }

    # --- is a historical version writable, or is history immutable?
    try:
        api.beta.agents.update(
            created.id,
            name=f"{name}-illegal-history-write",
            version=0,
        )
        result["historical_write"] = {"accepted": True}
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["historical_write"] = {"accepted": False, **api_error(error)}


def _summarize(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 200:
        return f"<str len={len(value)}> {value[:120]}..."
    return value


if __name__ == "__main__":
    main()
