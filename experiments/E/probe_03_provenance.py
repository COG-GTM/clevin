"""Probe 03 — provenance and version history of native Memory Store writes.

Primitive: `client.beta.memory_stores.memory_versions` (list/retrieve) plus a live
managed session writing through the `/mnt/memory` mount. Question: can an operator
tell who wrote a memory entry, when, which session did it, and can a bad entry's
history be inspected and undone using native surfaces only?

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_03_provenance.py
"""

from __future__ import annotations

import os
import time

from harness import Probe, client, sha256, summarize_error, temp_name
from session_lab import SessionLab

AGENT_WRITE_PROMPT = """CLEVIN_SMOKE_TEST Do not touch git, the network, or any external state.

Write exactly one memory file into the attached memory store mount, at the relative
path repos/COG-GTM/clevin/verified-commands.md, containing exactly these two lines:

verified: pnpm verify
source: probe_03 agent write PROV-AGENT-5581

Then report the absolute path you wrote and confirm the file exists.
"""


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    probe = Probe("probe_03_provenance")
    lab = SessionLab(api)

    store = api.beta.memory_stores.create(
        name=temp_name("provenance"),
        description="Workstream E probe: provenance of memory writes.",
        metadata={"swarm_workstream": "E", "probe": "03"},
    )
    probe.record("store_created", id=store.id, name=store.name)

    # 1. Operator (API key) write.
    api_write = api.beta.memory_stores.memories.create(
        store.id,
        path="/operating/api-key-write.md",
        content="Written by the operator API key during probe_03.",
    )
    probe.record(
        "api_key_write",
        memory_id=api_write.id,
        memory_version_id=api_write.memory_version_id,
    )

    # 2. Agent write through the mount, inside a real managed session.
    session, turn = lab.run(
        label="probe_03_agent_write",
        agent_id=agent_id,
        message=AGENT_WRITE_PROMPT,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_write",
                "instructions": "Store verified commands under repos/<owner>/<repo>/.",
            }
        ],
        title="clevin-swarm-E probe_03 agent write",
        metadata={"probe": "03"},
    )
    probe.record(
        "agent_write_session",
        session_id=session.id,
        event_types=turn.event_types(),
        tool_calls=[c.get("input") for c in turn.tool_calls()],
        assistant_tail=turn.assistant_text()[-1200:],
        usage=lab.cost(session.id),
    )

    # Mount writes may sync asynchronously; poll before asserting absence.
    agent_memory = None
    for attempt in range(12):
        listing = list(api.beta.memory_stores.memories.list(store.id, view="basic"))
        agent_memory = next((m for m in listing if "verified-commands" in m.path), None)
        probe.record(
            "post_session_store_listing",
            attempt=attempt,
            paths=[m.path for m in listing],
        )
        if agent_memory is not None:
            break
        time.sleep(10.0)
    probe.record(
        "agent_write_visible_via_api",
        found=agent_memory is not None,
        path=getattr(agent_memory, "path", None),
        memory_id=getattr(agent_memory, "id", None),
        sha256=getattr(agent_memory, "content_sha256", None),
    )

    # 3. Version history: fields and actor attribution.
    versions = list(api.beta.memory_stores.memory_versions.list(store.id, view="full"))
    probe.record(
        "version_history_full",
        versions=[
            {
                k: v
                for k, v in v_.model_dump().items()
                if k
                in {
                    "id",
                    "memory_id",
                    "operation",
                    "created_at",
                    "path",
                    "created_by",
                    "redacted_at",
                    "redacted_by",
                    "content_sha256",
                    "content_size_bytes",
                }
            }
            for v_ in versions
        ],
    )
    probe.record(
        "version_history_field_names",
        fields=sorted(versions[0].model_dump().keys()) if versions else [],
    )

    # 4. Do the documented provenance filters actually work, and do they discriminate?
    for label, kwargs in (
        ("filter_by_session_id", {"session_id": session.id}),
        ("filter_by_bogus_session_id", {"session_id": "sesn_01000000000000000000000000"}),
        ("filter_by_operation_created", {"operation": "created"}),
        ("filter_by_operation_deleted", {"operation": "deleted"}),
        ("filter_by_operation_create_wrong_enum", {"operation": "create"}),
    ):
        try:
            rows = list(api.beta.memory_stores.memory_versions.list(store.id, **kwargs))
            probe.record(
                label,
                outcome="ok",
                count=len(rows),
                paths=[getattr(v, "path", None) for v in rows],
                created_by=[getattr(v, "created_by", None) for v in rows],
            )
        except Exception as error:  # noqa: BLE001 - the rejection is the observation
            probe.record(label, outcome="error", **summarize_error(error))

    # 5. Can an operator read the exact content a past version held (audit + rollback)?
    if agent_memory is not None:
        current = api.beta.memory_stores.memories.retrieve(agent_memory.id, memory_store_id=store.id, view="full")
        probe.record(
            "agent_written_content",
            content=current.content,
            local_sha256=sha256(current.content or ""),
            reported_sha256=current.content_sha256,
        )
        api.beta.memory_stores.memories.update(
            agent_memory.id,
            memory_store_id=store.id,
            content="verified: pnpm verify --frozen-lockfile\nsource: probe_03 operator correction\n",
        )
        api.beta.memory_stores.memories.delete(
            agent_memory.id,
            memory_store_id=store.id,
        )
        probe.record("agent_memory_deleted", memory_id=agent_memory.id)

        lineage = list(
            api.beta.memory_stores.memory_versions.list(store.id, memory_id=agent_memory.id, view="full")
        )
        probe.record(
            "lineage_after_delete",
            versions=[
                {
                    "id": v.id,
                    "operation": v.operation,
                    "created_by": getattr(v, "created_by", None),
                    "content": getattr(v, "content", None),
                }
                for v in lineage
            ],
        )
        probe.attempt(
            "retrieve_deleted_memory_current",
            lambda: api.beta.memory_stores.memories.retrieve(
                agent_memory.id, memory_store_id=store.id, view="full"
            ).model_dump(),
        )
        if lineage:
            probe.attempt(
                "retrieve_historical_version_content",
                lambda: api.beta.memory_stores.memory_versions.retrieve(
                    lineage[0].id, memory_store_id=store.id, view="full"
                ).model_dump(),
            )
            probe.attempt(
                "redact_a_historical_version",
                lambda: api.beta.memory_stores.memory_versions.redact(
                    lineage[-1].id, memory_store_id=store.id
                ),
            )
            after_redaction = [
                {
                    "id": v.id,
                    "operation": v.operation,
                    "content": getattr(v, "content", None),
                    "redacted_at": getattr(v, "redacted_at", None),
                    "redacted_by": getattr(v, "redacted_by", None),
                }
                for v in api.beta.memory_stores.memory_versions.list(
                    store.id, memory_id=agent_memory.id, view="full"
                )
            ]
            probe.record("lineage_after_redaction", versions=after_redaction)

    probe.record("session_ids", agent_write=session.id)

    try:
        api.beta.memory_stores.delete(store.id)
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", "deleted")
    except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", f"FAILED: {error}")
    probe.add_cleanup(
        f"session {session.id}",
        "left in place (sessions are immutable history; no delete API)",
        "retained as evidence",
    )
    probe.write()


if __name__ == "__main__":
    main()
