"""Probe 09 — does memory survive where in-context state does not?

Primitive: automatic session context compaction plus a session-attached Memory Store.

Probe 08 filled ~200k tokens of context and observed no compaction event in
`sessions.events.list`. This probe pushes much harder and, instead of relying on an
event name, uses a differential observable: two facts are planted at the start of the
session — one only in the *conversation* (`CHAT-CANARY-4417`, stated by the user and
never written anywhere) and one only in the *memory store*
(`STORE-CANARY-8842`). After the fill, the agent is asked to recall both with tools
forbidden, then asked again with tools allowed.

- both recalled with no tools  -> context still intact (no compaction pressure reached)
- chat canary lost, store canary recoverable with tools -> memory is the durable layer
- both lost -> memory did not help

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_09_compaction_pressure.py
"""

from __future__ import annotations

import os

from harness import Probe, client, temp_name
from session_lab import SessionLab

STORE_ENTRY = (
    "# clevin-fixture release note (verified 2026-08-20)\n"
    "The release gate token is STORE-CANARY-8842 and it is recorded only here.\n"
)

FILL_ROUNDS = 24

OPENING = f"""CLEVIN_SMOKE_TEST No git, no network.

Remember this for later: the operator passphrase is CHAT-CANARY-4417. It is not written
in any file and exists only in this conversation.

Now read every file in the attached memory store mount and quote the release gate token
you find there.
"""

FILL = f"""Now fill your context deliberately. Run this command {FILL_ROUNDS} times, one
tool call per turn, and after each one report only the first 30 characters of its output:

```bash
head -c 900000 /dev/urandom | base64 | head -c 800000
```

Run all {FILL_ROUNDS} iterations. Do not summarise, batch, or skip any of them, and do not
write anything to the memory store.
"""

RECALL_NO_TOOLS = """Answer from your context only. Do not run any tool at all.

1. What is the operator passphrase?
2. What is the release gate token?
3. For each: do you still see the original text in your context, or is it missing /
   only present as a summary? Say plainly if you cannot recall one.
"""

RECALL_WITH_TOOLS = """Now you may use tools again. Re-answer both questions, stating for
each whether you recovered it from context or had to read the memory-store mount.
"""


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    probe = Probe("probe_09_compaction_pressure")
    lab = SessionLab(api)

    store = api.beta.memory_stores.create(
        name=temp_name("compaction-pressure"),
        description="Single canary fact for the probe 09 compaction-pressure test.",
        metadata={"swarm_workstream": "E", "probe": "09"},
    )
    api.beta.memory_stores.memories.create(
        store.id, path="/repos/clevin-fixture/release.md", content=STORE_ENTRY
    )
    probe.record("store_created", id=store.id, name=store.name)

    session = lab.create(
        agent_id=agent_id,
        message=OPENING,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_only",
                "instructions": "Verified release facts live under repos/<repo-name>/.",
            }
        ],
        title="clevin-swarm-E probe_09 compaction pressure",
        metadata={"probe": "09"},
        budget_usd="400",
    )
    opening = lab.drain(session.id)
    lab.save("probe_09_opening", session.id, opening)
    probe.record(
        "opening",
        session_id=session.id,
        store_canary_read="STORE-CANARY-8842" in opening.assistant_text(),
        usage=lab.cost(session.id)["usage"],
    )

    fill = lab.follow_up(session.id, FILL, label="probe_09_fill")
    probe.record(
        "fill",
        session_id=session.id,
        tool_call_count=len(fill.tool_calls()),
        compaction_event_types=[t for t in fill.event_types() if "compact" in t.lower()],
        event_type_counts={t: fill.event_types().count(t) for t in sorted(set(fill.event_types()))},
        usage=lab.cost(session.id)["usage"],
    )

    no_tools = lab.follow_up(session.id, RECALL_NO_TOOLS, label="probe_09_recall_no_tools")
    text = no_tools.assistant_text()
    probe.record(
        "recall_without_tools",
        session_id=session.id,
        tool_call_count=len(no_tools.tool_calls()),
        chat_canary_recalled="CHAT-CANARY-4417" in text,
        store_canary_recalled="STORE-CANARY-8842" in text,
        text=text[:3000],
        usage=lab.cost(session.id)["usage"],
    )

    with_tools = lab.follow_up(session.id, RECALL_WITH_TOOLS, label="probe_09_recall_with_tools")
    text2 = with_tools.assistant_text()
    probe.record(
        "recall_with_tools",
        session_id=session.id,
        tool_inputs=[c.get("input") for c in with_tools.tool_calls()],
        chat_canary_recalled="CHAT-CANARY-4417" in text2,
        store_canary_recalled="STORE-CANARY-8842" in text2,
        text=text2[:3000],
        usage=lab.cost(session.id)["usage"],
    )

    all_events = lab.all_events(session.id)
    probe.record(
        "session_event_summary",
        session_id=session.id,
        total_events=len(all_events),
        distinct_types=sorted({e["type"] for e in all_events}),
        any_compaction_type=[t for t in {e["type"] for e in all_events} if "compact" in t.lower()],
    )

    try:
        api.beta.memory_stores.delete(store.id)
        probe.add_cleanup(store.id, "memory_stores.delete", "deleted")
    except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
        probe.add_cleanup(store.id, "memory_stores.delete", f"FAILED: {error}")
    probe.add_cleanup("probe_09 session", "left in place deliberately (sessions are the evidence)", "retained")
    probe.write()


if __name__ == "__main__":
    main()
