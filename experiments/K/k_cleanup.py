"""Workstream K cleanup: remove the temporary Memory Store entry the K3 polling
deployment wrote, and record the state of every other temporary resource.

Primitives exercised: `beta.memory_stores.memories` (list/retrieve/delete) and
`beta.deployments.retrieve` — the native lifecycle surfaces that a temporary
resource has to be removed through. Rerunnable and idempotent.

    PYTHONPATH=experiments/K uv run --project runtime python experiments/K/k_cleanup.py
"""

from __future__ import annotations

import os
from typing import Any

from common import client, save

K3_DEPLOYMENT_ID = "depl_0118D9QiqeNRZPN5FUEocguk"
K3_MEMORY_PATH = "/k3-poll-log.md"
K5_SKILL_ID = "skill_01G8E6G4hGVRiXscLuhnsK8g"


def cleanup_k3_memory() -> dict[str, Any]:
    api = client()
    store_id = os.environ["CLEVIN_MEMORY_STORE_ID"]
    record: dict[str, Any] = {"memory_store_id": store_id, "path": K3_MEMORY_PATH}
    match = next(
        (
            memory
            for memory in api.beta.memory_stores.memories.list(store_id)
            if memory.path == K3_MEMORY_PATH
        ),
        None,
    )
    if match is None:
        record["result"] = "absent (already deleted)"
        return record
    record["memory_id"] = match.id
    record["content_size_bytes"] = match.content_size_bytes
    detail = api.beta.memory_stores.memories.retrieve(match.id, memory_store_id=store_id)
    record["archived_content"] = detail.content
    api.beta.memory_stores.memories.delete(match.id, memory_store_id=store_id)
    remaining = [
        memory.path for memory in api.beta.memory_stores.memories.list(store_id)
    ]
    record["result"] = "deleted"
    record["remaining_paths"] = remaining
    return record


def deployment_state() -> dict[str, Any]:
    deployment = client().beta.deployments.retrieve(K3_DEPLOYMENT_ID)
    state = deployment.model_dump()
    return {
        "id": state["id"],
        "archived_at": str(state.get("archived_at")),
        "schedule": state.get("schedule"),
        "status": state.get("status"),
    }


def skill_state() -> dict[str, Any]:
    skill = client().beta.skills.retrieve(K5_SKILL_ID)
    return {
        "id": skill.id,
        "display_title": skill.display_title,
        "latest_version": skill.latest_version,
        "disposition": "retained deliberately — referenced by the landed "
        "agent-definition skill-discovery change; attach via CLEVIN_SKILL_IDS",
    }


def main() -> None:
    record = {
        "k3_memory_entry": cleanup_k3_memory(),
        "k3_deployment": deployment_state(),
        "k5_skill": skill_state(),
    }
    save("k-cleanup.json", record)


if __name__ == "__main__":
    main()
