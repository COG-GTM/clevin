"""Probe 07 — one store shared by several subagents, and the scoping ceiling.

Primitives: `multiagent={"type": "coordinator", "agents": [...]}` on an agent version,
combined with a single `memory_store` session attachment; and the attachment parameter
surface itself, which offers only `access` and `instructions` — no path scope.

Part A (subagents): do delegated threads see the same mount, are their writes
attributable, and what happens when two of them write the same memory path at once?

Part B (scoping): with 20 repository namespaces and 200 entries in one store, does the
attachment cost more context than a 3-entry store, and can the model still land on the
right namespace? This is the empirical answer to "can naming and structure approximate
the missing dynamic scoping?".

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_07_subagents_and_scoping.py [subagents|scoping]
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import anthropic

from harness import Probe, client, temp_name
from session_lab import SessionLab

WRITER_SYSTEM = """You are a memory-writing worker subagent in a Managed Agents experiment.
You touch no external state. When asked to record something in the attached memory store
mount, do exactly what you are told, then report the exact commands you ran, the exact
file contents you wrote, and any error text verbatim.
"""

SHARED_WRITE_TASK = """CLEVIN_SMOKE_TEST Do not touch git or the network.

Delegate to BOTH of your worker subagents in the same turn so they run concurrently.
Give each worker this instruction, substituting its own number N (1 for the first, 2 for
the second):

  In the attached memory store mount, (a) create repos/shared/writer-N.md containing
  "writer-N present", and (b) append the line "writer-N was here" to
  repos/shared/index.md, creating that file if missing. Report the full final contents
  of index.md as you saw it.

When both have reported, read repos/shared/index.md yourself and report:
1. its exact final contents,
2. what each worker said it wrote,
3. whether any worker's line is missing, and your explanation.
"""

SCOPING_QUESTION = """CLEVIN_SMOKE_TEST Do not touch git or the network.

What is the verified gate command for the repository COG-GTM/service-13, and what is the
canary token recorded with it? Answer in one line, then state the memory file path you
took the answer from and how you located it.
"""


def seed_many_namespaces(api: anthropic.Anthropic, store_id: str) -> dict[str, int]:
    """20 repository namespaces x 10 entries; only service-13 carries the answer."""
    written = 0
    for repo in range(20):
        for entry in range(10):
            path = f"/repos/COG-GTM/service-{repo}/note-{entry}.md"
            if repo == 13 and entry == 0:
                content = (
                    "# service-13 gate (verified 2026-08-20)\n"
                    "Gate: `make verify-13`\nCanary: SCOPE-CANARY-1313\n"
                )
                path = "/repos/COG-GTM/service-13/gate.md"
            else:
                content = (
                    f"# service-{repo} note {entry} (verified 2026-08-1{entry % 10})\n"
                    f"Unrelated operating detail {repo}-{entry}: rotate the cache weekly.\n"
                )
            api.beta.memory_stores.memories.create(store_id, path=path, content=content)
            written += 1
    return {"written": written}


def usage_of(lab: SessionLab, session_id: str) -> dict[str, Any]:
    return lab.cost(session_id)["usage"]


def run_subagents(api: anthropic.Anthropic, probe: Probe, lab: SessionLab, base_agent_id: str) -> None:
    workers: list[str] = []
    for index in (1, 2):
        worker = api.beta.agents.create(
            name=temp_name(f"writer-{index}"),
            description="Temporary memory-writing worker subagent for probe 07.",
            model={"id": "claude-sonnet-5", "effort": "medium"},
            system=WRITER_SYSTEM,
            metadata={"swarm_workstream": "E", "probe": "07"},
            tools=[
                {
                    "type": "agent_toolset_20260401",
                    "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
                }
            ],
        )
        workers.append(worker.id)
    probe.record("worker_agents_created", ids=workers)

    coordinator = api.beta.agents.create(
        name=temp_name("coordinator"),
        description="Temporary coordinator agent for probe 07 shared-store writes.",
        model={"id": "claude-sonnet-5", "effort": "medium"},
        system=(
            "You coordinate worker subagents in a Managed Agents experiment. Delegate as "
            "instructed, never do the workers' writes yourself, and report exactly what "
            "each worker reported."
        ),
        metadata={"swarm_workstream": "E", "probe": "07"},
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
            }
        ],
        multiagent={
            "type": "coordinator",
            "agents": [
                {"type": "self"},
                {"type": "agent", "id": workers[0]},
                {"type": "agent", "id": workers[1]},
            ],
        },
    )
    probe.record("coordinator_created", id=coordinator.id, roster=workers)

    store = api.beta.memory_stores.create(
        name=temp_name("sharedwrites"),
        description="Shared store written concurrently by two subagents (probe 07).",
        metadata={"swarm_workstream": "E", "probe": "07"},
    )
    probe.record("shared_store_created", id=store.id, name=store.name)

    session, turn = lab.run(
        label="probe_07_shared_subagent_writes",
        agent_id=coordinator.id,
        message=SHARED_WRITE_TASK,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_write",
                "instructions": "Shared scratch namespace for this probe is repos/shared/.",
            }
        ],
        title="clevin-swarm-E probe_07 shared subagent writes",
        metadata={"probe": "07"},
        budget_usd="20",
    )
    time.sleep(25.0)
    final = {
        m.path: m.content for m in api.beta.memory_stores.memories.list(store.id, view="full")
    }
    versions = [
        {
            "operation": v.operation,
            "path": getattr(v, "path", None),
            "created_by": getattr(v, "created_by", None),
            "content_size_bytes": v.content_size_bytes,
            "created_at": v.created_at,
        }
        for v in api.beta.memory_stores.memory_versions.list(store.id)
    ]
    index_content = final.get("/repos/shared/index.md", "")
    probe.record(
        "shared_subagent_writes",
        session_id=session.id,
        event_types=turn.event_types(),
        delegation_events=[e for e in turn.events if "subagent" in e["type"] or "thread" in e["type"]],
        tool_inputs=[c.get("input") for c in turn.tool_calls()],
        assistant_text=turn.assistant_text(),
        usage=usage_of(lab, session.id),
        store_paths=sorted(final),
        index_content=index_content,
        writer1_line_present="writer-1 was here" in (index_content or ""),
        writer2_line_present="writer-2 was here" in (index_content or ""),
        lost_update=not (
            "writer-1 was here" in (index_content or "") and "writer-2 was here" in (index_content or "")
        ),
        version_operations=versions,
    )

    for resource_id, action, delete in (
        (store.id, "memory_stores.delete", lambda: api.beta.memory_stores.delete(store.id)),
        # There is no agents.delete in the SDK; archive is the only teardown.
        (coordinator.id, "agents.archive", lambda: api.beta.agents.archive(coordinator.id)),
        (workers[0], "agents.archive", lambda: api.beta.agents.archive(workers[0])),
        (workers[1], "agents.archive", lambda: api.beta.agents.archive(workers[1])),
    ):
        try:
            delete()
            probe.add_cleanup(resource_id, action, "done")
        except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
            probe.add_cleanup(resource_id, action, f"FAILED: {error}")


def run_scoping(api: anthropic.Anthropic, probe: Probe, lab: SessionLab, base_agent_id: str) -> None:
    big = api.beta.memory_stores.create(
        name=temp_name("scope-wide"),
        description="Twenty repository namespaces of verified facts (probe 07).",
        metadata={"swarm_workstream": "E", "probe": "07"},
    )
    probe.record("wide_store_created", id=big.id, name=big.name, **seed_many_namespaces(api, big.id))

    narrow = api.beta.memory_stores.create(
        name=temp_name("scope-narrow"),
        description="Only COG-GTM/service-13 facts (probe 07).",
        metadata={"swarm_workstream": "E", "probe": "07"},
    )
    for path, content in (
        (
            "/repos/COG-GTM/service-13/gate.md",
            "# service-13 gate (verified 2026-08-20)\nGate: `make verify-13`\nCanary: SCOPE-CANARY-1313\n",
        ),
        (
            "/repos/COG-GTM/service-13/note-1.md",
            "# service-13 note 1\nUnrelated operating detail 13-1: rotate the cache weekly.\n",
        ),
    ):
        api.beta.memory_stores.memories.create(narrow.id, path=path, content=content)
    probe.record("narrow_store_created", id=narrow.id, name=narrow.name)

    for label, store_id in (("wide_200_entries", big.id), ("narrow_2_entries", narrow.id)):
        session, turn = lab.run(
            label=f"probe_07_scoping_{label}",
            agent_id=base_agent_id,
            message=SCOPING_QUESTION,
            resources=[
                {
                    "type": "memory_store",
                    "memory_store_id": store_id,
                    "access": "read_only",
                    "instructions": "Facts are filed under repos/<owner>/<repo>/.",
                }
            ],
            title=f"clevin-swarm-E probe_07 scoping {label}",
            metadata={"probe": "07", "variant": label},
            budget_usd="12",
        )
        probe.record(
            f"scoping_{label}",
            session_id=session.id,
            answered_correctly=turn.contains("SCOPE-CANARY-1313"),
            tool_calls=len(turn.tool_calls()),
            tool_inputs=[c.get("input") for c in turn.tool_calls()],
            usage=usage_of(lab, session.id),
            assistant_text=turn.assistant_text(),
        )

    for store_id in (big.id, narrow.id):
        try:
            api.beta.memory_stores.delete(store_id)
            probe.add_cleanup(f"memory_store {store_id}", "memory_stores.delete", "deleted")
        except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
            probe.add_cleanup(f"memory_store {store_id}", "memory_stores.delete", f"FAILED: {error}")


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    selected = set(sys.argv[1:]) or {"subagents", "scoping"}
    probe = Probe("probe_07_subagents_and_scoping")
    lab = SessionLab(api)
    if "subagents" in selected:
        run_subagents(api, probe, lab, agent_id)
    if "scoping" in selected:
        run_scoping(api, probe, lab, agent_id)
    probe.add_cleanup(
        "probe_07 sessions", "left in place deliberately (sessions are the evidence)", "retained as evidence"
    )
    probe.write()


if __name__ == "__main__":
    main()
