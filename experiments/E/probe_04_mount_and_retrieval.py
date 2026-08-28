"""Probe 04 — how the Memory Store attachment reaches the model, and what the mount can do.

Primitive: the session attachment `resources=[{type: "memory_store", access,
instructions}]` and its `/mnt/memory/<store-slug>` filesystem projection.

Questions: what does Managed Agents put in the system prompt, how is retrieval
triggered (injection vs model-driven search), is `read_only` enforced at the mount,
how do multiple attached stores present themselves, and which mount filesystem
operations map onto which memory operations.

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_04_mount_and_retrieval.py [experiment ...]
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import anthropic

from harness import Probe, client, slug_for, temp_name
from session_lab import SessionLab

CONVENTIONS: dict[str, str] = {
    "/repos/COG-GTM/clevin/setup.md": (
        "# clevin setup (verified 2026-08-20)\n"
        "Install: `pnpm install --frozen-lockfile` then `uv sync --project runtime`.\n"
        "Canary: MOUNT-CANARY-3312\n"
    ),
    "/repos/COG-GTM/clevin/tests.md": (
        "# clevin tests (verified 2026-08-20)\n"
        "Full gate: `pnpm verify` then `uv run --project runtime pytest -c runtime/pyproject.toml`.\n"
        "Canary: TEST-CANARY-9041\n"
    ),
    "/preferences/user.md": (
        "# operator preferences\nAlways answer with a markdown table when comparing options.\n"
        "Canary: PREF-CANARY-7788\n"
    ),
}

ENVIRONMENT_NOTES: dict[str, str] = {
    "/environment/sandbox.md": (
        "# sandbox facts (verified 2026-08-20)\n"
        "The probe sandbox has no outbound network access and no git credentials.\n"
        "Canary: ENV-CANARY-5521\n"
    )
}

PROMPT_RENDERING = """CLEVIN_SMOKE_TEST Answer from your context only. Run no tools at all.

1. Quote verbatim, inside a fenced block, every sentence in your system prompt that
   mentions memory, /mnt/memory, or an attached store.
2. List each attached store: its name, mount path, access mode, and description.
3. State whether the *contents* of any memory file are already present in your
   context, and list every memory file path you know of without running a tool.
"""

RETRIEVAL_HIT = """CLEVIN_SMOKE_TEST What is the verified install command for the COG-GTM/clevin
repository, and what is its canary token? Answer in one line. Do not touch git or the network.
"""

RETRIEVAL_MISS = """CLEVIN_SMOKE_TEST What is the verified deploy command for the
COG-GTM/nonexistent-service repository? Answer in one line, and say plainly if you do not know.
Do not touch git or the network.
"""

READ_ONLY_WRITE = """CLEVIN_SMOKE_TEST Attempt to record a new memory file at
repos/COG-GTM/clevin/probe04-readonly.md with the content "READONLY-PROBE-8123" inside the attached
store's mount. Then attempt to append a line to the existing setup.md in that mount. Report the exact
command you ran and the exact error text for each attempt, and state whether you have write access.
"""

MULTI_STORE = """CLEVIN_SMOKE_TEST Answer these, and for each answer name the exact file path you
took it from:
1. The verified test gate for COG-GTM/clevin.
2. Whether the sandbox has outbound network access.
3. Which of the attached stores you are allowed to write to, and how you determined that.
"""

MOUNT_OPS = """CLEVIN_SMOKE_TEST Perform exactly these operations in the attached read-write store
mount, reporting the command and result of each:
1. Create a new file at repos/COG-GTM/clevin/probe04-created.md containing "CREATED-4501".
2. Append the line "appended: APPENDED-4502" to repos/COG-GTM/clevin/tests.md.
3. Move repos/COG-GTM/clevin/setup.md to repos/COG-GTM/clevin/setup-renamed.md.
4. Delete preferences/user.md.
5. Create a nested directory repos/COG-GTM/clevin/deep/deeper/ and a file note.md inside it
   containing "DEEP-4503".
Do not touch git or the network.
"""


def seed(api: anthropic.Anthropic, store_id: str, entries: dict[str, str]) -> None:
    for path, content in entries.items():
        api.beta.memory_stores.memories.create(store_id, path=path, content=content)


def make_store(api: anthropic.Anthropic, probe: Probe, kind: str, description: str, entries: dict[str, str]) -> Any:
    store = api.beta.memory_stores.create(
        name=temp_name(kind),
        description=description,
        metadata={"swarm_workstream": "E", "probe": "04"},
    )
    seed(api, store.id, entries)
    probe.record(
        "store_seeded",
        kind=kind,
        id=store.id,
        name=store.name,
        expected_mount=f"/mnt/memory/{slug_for(store.name)}",
        paths=sorted(entries),
    )
    return store


def summarize(probe: Probe, label: str, lab: SessionLab, session: Any, turn: Any, **extra: Any) -> None:
    probe.record(
        label,
        session_id=session.id,
        event_types=turn.event_types(),
        tool_inputs=[c.get("input") for c in turn.tool_calls()],
        assistant_text=turn.assistant_text(),
        usage=lab.cost(session.id),
        **extra,
    )


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    selected = set(sys.argv[1:]) or {
        "prompt_rendering",
        "retrieval",
        "read_only",
        "multi_store",
        "mount_ops",
    }
    probe = Probe("probe_04_mount_and_retrieval")
    lab = SessionLab(api)

    conventions = make_store(
        api,
        probe,
        "conventions",
        "Verified repository setup, test and preference facts for probe 04.",
        CONVENTIONS,
    )
    environment = make_store(
        api,
        probe,
        "envfacts",
        "Verified sandbox environment facts for probe 04.",
        ENVIRONMENT_NOTES,
    )
    rw = {
        "type": "memory_store",
        "memory_store_id": conventions.id,
        "access": "read_write",
        "instructions": "Keep verified facts under repos/<owner>/<repo>/.",
    }
    ro = {"type": "memory_store", "memory_store_id": conventions.id, "access": "read_only"}

    if "prompt_rendering" in selected:
        # No `instructions` on the attachment: what does Managed Agents render by itself?
        bare = {"type": "memory_store", "memory_store_id": conventions.id, "access": "read_write"}
        session, turn = lab.run(
            label="probe_04_prompt_rendering_bare",
            agent_id=agent_id,
            message=PROMPT_RENDERING,
            resources=[bare],
            title="clevin-swarm-E probe_04 prompt rendering (no instructions)",
            metadata={"probe": "04"},
        )
        summarize(probe, "prompt_rendering_without_instructions", lab, session, turn)

        session, turn = lab.run(
            label="probe_04_prompt_rendering_instructed",
            agent_id=agent_id,
            message=PROMPT_RENDERING,
            resources=[rw],
            title="clevin-swarm-E probe_04 prompt rendering (with instructions)",
            metadata={"probe": "04"},
        )
        summarize(probe, "prompt_rendering_with_instructions", lab, session, turn)

    if "retrieval" in selected:
        session, turn = lab.run(
            label="probe_04_retrieval_hit",
            agent_id=agent_id,
            message=RETRIEVAL_HIT,
            resources=[rw],
            title="clevin-swarm-E probe_04 retrieval hit",
            metadata={"probe": "04"},
        )
        summarize(
            probe,
            "retrieval_unprompted_hit",
            lab,
            session,
            turn,
            found_canary=turn.contains("MOUNT-CANARY-3312"),
            searched_mount=any("/mnt/memory" in str(c.get("input")) for c in turn.tool_calls()),
        )

        session, turn = lab.run(
            label="probe_04_retrieval_miss",
            agent_id=agent_id,
            message=RETRIEVAL_MISS,
            resources=[rw],
            title="clevin-swarm-E probe_04 retrieval miss",
            metadata={"probe": "04"},
        )
        summarize(
            probe,
            "retrieval_unprompted_miss",
            lab,
            session,
            turn,
            searched_mount=any("/mnt/memory" in str(c.get("input")) for c in turn.tool_calls()),
        )

    if "read_only" in selected:
        before = {m.path for m in api.beta.memory_stores.memories.list(conventions.id)}
        session, turn = lab.run(
            label="probe_04_read_only",
            agent_id=agent_id,
            message=READ_ONLY_WRITE,
            resources=[ro],
            title="clevin-swarm-E probe_04 read_only enforcement",
            metadata={"probe": "04"},
        )
        time.sleep(20.0)
        after = {m.path for m in api.beta.memory_stores.memories.list(conventions.id)}
        summarize(
            probe,
            "read_only_enforcement",
            lab,
            session,
            turn,
            new_paths=sorted(after - before),
            store_unchanged=before == after,
        )

    if "multi_store" in selected:
        session, turn = lab.run(
            label="probe_04_multi_store",
            agent_id=agent_id,
            message=MULTI_STORE,
            resources=[
                rw,
                {
                    "type": "memory_store",
                    "memory_store_id": environment.id,
                    "access": "read_only",
                    "instructions": "Environment facts only; never write here.",
                },
            ],
            title="clevin-swarm-E probe_04 multi store",
            metadata={"probe": "04"},
        )
        summarize(
            probe,
            "multi_store_attachment",
            lab,
            session,
            turn,
            saw_test_canary=turn.contains("TEST-CANARY-9041"),
            saw_env_canary=turn.contains("ENV-CANARY-5521"),
        )

    if "mount_ops" in selected:
        ops_store = make_store(
            api,
            probe,
            "mountops",
            "Mount filesystem operation mapping for probe 04.",
            CONVENTIONS,
        )
        before = {m.path: m.content_sha256 for m in api.beta.memory_stores.memories.list(ops_store.id)}
        session, turn = lab.run(
            label="probe_04_mount_ops",
            agent_id=agent_id,
            message=MOUNT_OPS,
            resources=[
                {
                    "type": "memory_store",
                    "memory_store_id": ops_store.id,
                    "access": "read_write",
                    "instructions": "Keep verified facts under repos/<owner>/<repo>/.",
                }
            ],
            title="clevin-swarm-E probe_04 mount operations",
            metadata={"probe": "04"},
        )
        time.sleep(30.0)
        after = {m.path: m.content_sha256 for m in api.beta.memory_stores.memories.list(ops_store.id)}
        versions = [
            {
                "operation": v.operation,
                "path": getattr(v, "path", None),
                "session_id": getattr(v, "session_id", None),
            }
            for v in api.beta.memory_stores.memory_versions.list(ops_store.id)
        ]
        summarize(
            probe,
            "mount_operation_mapping",
            lab,
            session,
            turn,
            paths_before=sorted(before),
            paths_after=sorted(after),
            added=sorted(set(after) - set(before)),
            removed=sorted(set(before) - set(after)),
            modified=sorted(p for p in set(before) & set(after) if before[p] != after[p]),
            version_operations=versions,
        )
        cleanup_stores = [conventions.id, environment.id, ops_store.id]
    else:
        cleanup_stores = [conventions.id, environment.id]

    for store_id in cleanup_stores:
        try:
            api.beta.memory_stores.delete(store_id)
            probe.add_cleanup(f"memory_store {store_id}", "memory_stores.delete", "deleted")
        except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
            probe.add_cleanup(f"memory_store {store_id}", "memory_stores.delete", f"FAILED: {error}")
    probe.add_cleanup(
        "probe sessions",
        "left in place deliberately (sessions are the evidence; transcripts under evidence/transcripts)",
        "retained as evidence",
    )
    probe.write()


if __name__ == "__main__":
    main()
