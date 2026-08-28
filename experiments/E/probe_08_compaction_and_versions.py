"""Probe 08 — memory across compaction, and memory across agent versions.

Primitives: automatic session context compaction, and agent versions
(`agents.update` publishing a new version) combined with the session-level
`memory_store` attachment.

Part A: fill a session's context with large tool outputs until compaction shows up in
the native event stream, then ask for a fact that was only ever in memory. What is being
tested is whether the store survives as the durable layer and whether the model still
knows to go back to the mount after its context has been rewritten.

Part B: attachments are session-scoped (agent create/update takes no `resources`), so
this part checks the consequences: a new agent version published mid-session, and two
sessions pinned to different versions sharing one store.

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_08_compaction_and_versions.py [compaction|versions]
"""

from __future__ import annotations

import os
import sys
from typing import Any

import anthropic

from harness import Probe, client, temp_name
from session_lab import SessionLab

CANARY_ENTRY = (
    "# clevin-fixture deploy note (verified 2026-08-20)\n"
    "The release gate token is COMPACT-CANARY-6620 and it is only recorded here.\n"
)

FILL_TASK = """CLEVIN_SMOKE_TEST Do not touch git or the network.

Step 1 — read every file in the attached memory store mount and state the release gate
token you found.

Step 2 — now deliberately fill your context. Run this command twelve times in a row,
one call per turn, reporting only the first 40 characters of each output:

```bash
head -c 700000 /dev/urandom | base64 | head -c 600000
```

Do not summarise or skip iterations; run all twelve.

Step 3 — state the release gate token again, and say explicitly whether you had to
re-read the memory mount to answer, or whether you still had it in context.
"""

QUOTE_INSTRUCTIONS = """CLEVIN_SMOKE_TEST Run no tools. Quote verbatim the block of your
system prompt that lists the attached memory stores and any per-store instructions.
"""


def compaction_events(turn: Any) -> list[str]:
    return [t for t in turn.event_types() if "compact" in t.lower()]


def run_compaction(api: anthropic.Anthropic, probe: Probe, lab: SessionLab, agent_id: str) -> None:
    store = api.beta.memory_stores.create(
        name=temp_name("compaction"),
        description="Single canary fact for the probe 08 compaction test.",
        metadata={"swarm_workstream": "E", "probe": "08"},
    )
    api.beta.memory_stores.memories.create(
        store.id, path="/repos/clevin-fixture/deploy.md", content=CANARY_ENTRY
    )
    probe.record("compaction_store_created", id=store.id, name=store.name)

    session, turn = lab.run(
        label="probe_08_compaction",
        agent_id=agent_id,
        message=FILL_TASK,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_write",
                "instructions": "Verified release facts live under repos/<repo-name>/.",
            }
        ],
        title="clevin-swarm-E probe_08 compaction",
        metadata={"probe": "08"},
        budget_usd="60",
    )
    text = turn.assistant_text()
    probe.record(
        "compaction_run",
        session_id=session.id,
        compaction_event_types=compaction_events(turn),
        event_type_counts={t: turn.event_types().count(t) for t in sorted(set(turn.event_types()))},
        tool_call_count=len(turn.tool_calls()),
        canary_recalled=("COMPACT-CANARY-6620" in text),
        reread_mount_after_fill=text.lower().count("re-read") + text.lower().count("reread"),
        usage=lab.cost(session.id)["usage"],
        assistant_tail=text[-3000:],
    )
    try:
        api.beta.memory_stores.delete(store.id)
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", "deleted")
    except Exception as error:  # noqa: BLE001
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", f"FAILED: {error}")


def run_versions(api: anthropic.Anthropic, probe: Probe, lab: SessionLab) -> None:
    store = api.beta.memory_stores.create(
        name=temp_name("versions"),
        description="Store shared by two agent versions (probe 08).",
        metadata={"swarm_workstream": "E", "probe": "08"},
    )
    api.beta.memory_stores.memories.create(
        store.id, path="/repos/clevin-fixture/deploy.md", content=CANARY_ENTRY
    )
    agent = api.beta.agents.create(
        name=temp_name("versioned"),
        description="Temporary agent for probe 08 version behaviour.",
        model={"id": "claude-sonnet-5", "effort": "medium"},
        system="You are version one of a probe agent. Answer exactly what is asked.",
        metadata={"swarm_workstream": "E", "probe": "08"},
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
            }
        ],
    )
    probe.record("versioned_agent_created", id=agent.id, version=agent.version, store=store.id)

    attachment = {
        "type": "memory_store",
        "memory_store_id": store.id,
        "access": "read_write",
        "instructions": "VERSION-A instructions: file facts under repos/<repo-name>/.",
    }
    session = lab.create(
        agent_id=agent.id,
        message=QUOTE_INSTRUCTIONS,
        resources=[attachment],
        title="clevin-swarm-E probe_08 live session across a version bump",
        metadata={"probe": "08"},
        budget_usd="12",
    )
    first = lab.drain(session.id)
    lab.save("probe_08_versions_turn1", session.id, first)

    updated = api.beta.agents.update(
        agent.id,
        system="You are version two of a probe agent. Answer exactly what is asked.",
        model={"id": "claude-sonnet-5", "effort": "medium"},
        name=agent.name,
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
            }
        ],
    )
    probe.record("agent_version_published", id=updated.id, version=updated.version)

    second = lab.follow_up(
        session.id,
        "CLEVIN_SMOKE_TEST Run no tools. Quote again the memory-store block of your system "
        "prompt, and state which version of the agent you are.",
        label="probe_08_versions_turn2",
    )
    probe.record(
        "live_session_after_version_bump",
        session_id=session.id,
        turn1_quoted_version_a="VERSION-A" in first.assistant_text(),
        turn2_quoted_version_a="VERSION-A" in second.assistant_text(),
        turn2_text=second.assistant_text()[:3000],
        usage=lab.cost(session.id)["usage"],
    )

    # A fresh session pinned to the old version, with a *different* attachment instruction:
    pinned_session, pinned_turn = lab.run(
        label="probe_08_versions_pinned_v1",
        agent_id=agent.id,
        message=QUOTE_INSTRUCTIONS,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_only",
                "instructions": "VERSION-B instructions: this attachment is read-only.",
            }
        ],
        title="clevin-swarm-E probe_08 pinned version",
        metadata={"probe": "08"},
        budget_usd="12",
        model="claude-sonnet-5",
    )
    probe.record(
        "attachment_is_session_scoped_not_version_scoped",
        session_id=pinned_session.id,
        quoted_version_b="VERSION-B" in pinned_turn.assistant_text(),
        text=pinned_turn.assistant_text()[:2000],
        note=(
            "agents.create/update accept no `resources` parameter, so a store cannot be "
            "bound to an agent version; every session must re-declare the attachment."
        ),
    )

    for resource_id, delete in (
        (store.id, lambda: api.beta.memory_stores.delete(store.id)),
        # There is no agents.delete in the SDK; archive is the only teardown.
        (agent.id, lambda: api.beta.agents.archive(agent.id)),
    ):
        try:
            delete()
            probe.add_cleanup(resource_id, "delete", "deleted")
        except Exception as error:  # noqa: BLE001
            probe.add_cleanup(resource_id, "delete", f"FAILED: {error}")


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    selected = set(sys.argv[1:]) or {"compaction", "versions"}
    probe = Probe("probe_08_compaction_and_versions")
    lab = SessionLab(api)
    if "compaction" in selected:
        run_compaction(api, probe, lab, agent_id)
    if "versions" in selected:
        run_versions(api, probe, lab)
    probe.add_cleanup("probe_08 sessions", "left in place deliberately (sessions are the evidence)", "retained")
    probe.write()


if __name__ == "__main__":
    main()
