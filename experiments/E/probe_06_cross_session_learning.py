"""Probe 06 — does a native Memory Store measurably improve the next session?

Primitive: Memory Store attachment across separate sessions. Four rounds against the
identical fixture (`fixture.py`), whose one hard-to-find fact is the gate's required
token:

  1. cold, no store attached                      -> baseline cost of discovery
  2. cold, empty read_write store, asked to record -> does it recognise what to keep?
  3. warm, that same store, same task              -> does round 2's write pay off?
  4. warm again                                    -> stability and store churn

Metric per round: tool calls, failed gate attempts, output tokens, active seconds,
and store size after the round. No scoring or ranking logic beyond counting native
event/usage fields.

Run:
  ANTHROPIC_API_KEY=... CLEVIN_E_AGENT_ID=agent_... \
  uv run --project runtime python experiments/E/probe_06_cross_session_learning.py
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

from harness import Probe, client, temp_name
from fixture import gate_task
from session_lab import SessionLab, Turn

RECORD_LEARNINGS = """
Step 3 — write back to the attached memory store whatever a future session doing this
same task would need in order to skip the discovery you just did. Keep it to verified
facts, under a stable path of your choosing. Then state the path you wrote.
"""

STORE_INSTRUCTIONS = (
    "Verified, reusable repository setup and gate facts belong under "
    "repos/<repo-name>/. Confirm anything stale against the repository before relying "
    "on it. Never store secrets, task content, or speculation."
)

FAILURE_MARKER = "CLEVIN_FIXTURE_TOKEN is not set correctly"


def store_size(api: anthropic.Anthropic, store_id: str) -> dict[str, Any]:
    items = [m for m in api.beta.memory_stores.memories.list(store_id, view="full")]
    return {
        "memory_count": len(items),
        "total_bytes": sum(m.content_size_bytes or 0 for m in items),
        "paths": sorted(m.path for m in items),
        "contents": {m.path: m.content for m in items},
    }


def round_metrics(lab: SessionLab, session: Any, turn: Turn) -> dict[str, Any]:
    usage = lab.cost(session.id)["usage"]
    tool_inputs = [str(c.get("input")) for c in turn.tool_calls()]
    # Count only actual gate invocations, and only ones that did not print VERIFY-OK.
    # (An earlier version scanned every tool result for the failure string, which also
    # matched sessions that merely *read* a memory entry quoting that string.)
    gate_invocations = [c for c in tool_inputs if "verify.sh" in c]
    failed_gate = sum(
        1
        for e in turn.events
        if e["type"] == "agent.tool_result"
        and FAILURE_MARKER in e.get("text", "")
        and "EXIT_STATUS:1" in e.get("text", "")
    )
    return {
        "session_id": session.id,
        "tool_calls": len(tool_inputs),
        "gate_invocations": len(gate_invocations),
        "failed_gate_attempts": failed_gate,
        "gate_passed": turn.contains("VERIFY-OK"),
        "output_tokens": usage.get("output_tokens"),
        "input_tokens": usage.get("input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "active_seconds": usage.get("active_seconds"),
        "list_cost": usage.get("list_cost"),
        "commands": tool_inputs,
    }


def main() -> None:
    api = client()
    agent_id = os.environ.get("CLEVIN_E_AGENT_ID")
    if not agent_id:
        raise SystemExit("CLEVIN_E_AGENT_ID is not set (run setup_probe_agent.py)")
    probe = Probe("probe_06_cross_session_learning")
    lab = SessionLab(api)

    store = api.beta.memory_stores.create(
        name=temp_name("learning"),
        description="Empty store for the workstream E cross-session learning A/B.",
        metadata={"swarm_workstream": "E", "probe": "06"},
    )
    attachment = {
        "type": "memory_store",
        "memory_store_id": store.id,
        "access": "read_write",
        "instructions": STORE_INSTRUCTIONS,
    }
    probe.record("store_created", id=store.id, name=store.name)

    rounds: list[tuple[str, list[dict[str, Any]], str]] = [
        ("round1_cold_no_store", [], ""),
        ("round2_cold_with_empty_store", [attachment], RECORD_LEARNINGS),
        ("round3_warm_with_learned_store", [attachment], ""),
        ("round4_warm_repeat", [attachment], RECORD_LEARNINGS),
    ]

    for label, resources, extra in rounds:
        session, turn = lab.run(
            label=f"probe_06_{label}",
            agent_id=agent_id,
            message=gate_task(extra),
            resources=resources,
            title=f"clevin-swarm-E probe_06 {label}",
            metadata={"probe": "06", "round": label},
            budget_usd="15",
        )
        probe.record(
            label,
            store_attached=bool(resources),
            asked_to_record=bool(extra),
            **round_metrics(lab, session, turn),
            store_after=store_size(api, store.id),
            assistant_tail=turn.assistant_text()[-2000:],
        )

    probe.record(
        "memory_write_provenance",
        versions=[
            {
                "operation": v.operation,
                "path": getattr(v, "path", None),
                "created_by": getattr(v, "created_by", None),
            }
            for v in api.beta.memory_stores.memory_versions.list(store.id)
        ],
    )

    try:
        api.beta.memory_stores.delete(store.id)
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", "deleted")
    except Exception as error:  # noqa: BLE001 - cleanup failures must be visible
        probe.add_cleanup(f"memory_store {store.id}", "memory_stores.delete", f"FAILED: {error}")
    probe.add_cleanup(
        "probe_06 sessions", "left in place deliberately (sessions are the evidence)", "retained as evidence"
    )
    probe.write()


if __name__ == "__main__":
    main()
