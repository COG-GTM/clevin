"""D4 — canarying two agent versions over one benchmark, then rolling back.

Primitives: agent versions, version-pinned session creation, `session.usage`.

One throwaway agent carries three versions: v1 (canary A), v2 (canary B) and
v3 (deliberately broken). The same benchmark prompt runs against each pinned
version, so the comparison isolates the version as the only variable. The
driver then attempts to roll back to v1 and checks (a) whether a previous
version can be made current at all, and (b) whether the rolled-back
configuration is structurally identical to v1.

    ANTHROPIC_API_KEY=... uv run --project runtime python experiments/D/d4_canary_rollback.py
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from _common import (
    Ledger,
    agent_projection,
    api_error,
    archive_agents,
    archive_sessions,
    client,
    diff_state,
    run_probe,
    temp_name,
    write_evidence,
)

PROBE_MODEL = "claude-haiku-4-5-20251001"

# Deterministic benchmark: one arithmetic answer plus a version marker, so a
# version can be scored mechanically rather than judged.
BENCHMARK_PROMPT = (
    "CLEVIN_SMOKE_TEST Compute 17 * 23 using no tools. "
    "Reply with your marker on the first line and then exactly 'ANSWER=<number>'."
)
EXPECTED_ANSWER = "ANSWER=391"

VARIANTS = {
    "v1_canary_a": (
        "[[CANARY-A]]",
        "You are a benchmark probe. Begin your final message with the marker "
        "[[CANARY-A]]. Answer arithmetic exactly and be terse.",
    ),
    "v2_canary_b": (
        "[[CANARY-B]]",
        "You are a benchmark probe. Begin your final message with the marker "
        "[[CANARY-B]]. Think step by step out loud before answering, then "
        "answer arithmetic exactly.",
    ),
    "v3_broken": (
        "[[BROKEN]]",
        "You are a benchmark probe. Begin your final message with the marker "
        "[[BROKEN]]. This configuration is deliberately broken: never compute "
        "arithmetic, and reply only with 'ANSWER=unavailable'.",
    ),
}


def score(marker: str, final_text: str) -> dict[str, Any]:
    return {
        "marker_present": marker in final_text,
        "answer_correct": EXPECTED_ANSWER in final_text.replace(" ", ""),
        "final_text": final_text[:400],
    }


def main() -> None:
    api = client()
    ledger = Ledger()
    result: dict[str, Any] = {
        "experiment": "D4 canary and rollback",
        "benchmark": {"prompt": BENCHMARK_PROMPT, "expected": EXPECTED_ANSWER},
    }
    try:
        run(api, ledger, result)
    finally:
        archive_sessions(api, ledger, ledger.ids("session"))
        archive_agents(api, ledger, ledger.ids("agent"))
        result["cleanup"] = ledger.entries
        path = write_evidence("d4_canary_rollback", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"evidence: {path}")


def run(api: anthropic.Anthropic, ledger: Ledger, result: dict[str, Any]) -> None:
    name = temp_name("canary")
    marker_v1, system_v1 = VARIANTS["v1_canary_a"]
    agent = api.beta.agents.create(
        name=name,
        description="workstream D canary and rollback probe",
        model={"id": PROBE_MODEL},
        system=system_v1,
        metadata={"swarm_workstream": "D", "swarm_experiment": "d4"},
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    ledger.created("agent", agent.id, name)
    v1_state = agent_projection(api.beta.agents.retrieve(agent.id, version=1))

    published: dict[str, int] = {"v1_canary_a": agent.version}
    current_version = agent.version
    for label in ("v2_canary_b", "v3_broken"):
        _, system = VARIANTS[label]
        updated = api.beta.agents.update(
            agent.id, system=system, version=current_version
        )
        published[label] = updated.version
        current_version = updated.version
    result["published_versions"] = published

    # --- canary: identical benchmark, one variable (the pinned version).
    runs: dict[str, Any] = {}
    for label, version in published.items():
        marker, _ = VARIANTS[label]
        outcome = run_probe(
            api,
            ledger,
            agent={"type": "agent", "id": agent.id, "version": version},
            prompt=BENCHMARK_PROMPT,
            title=f"D4 {label}",
            metadata={"swarm_experiment": "d4", "variant": label},
            timeout_s=600.0,
        )
        runs[label] = {
            "pinned_version": version,
            "resolved_version": outcome.resolved_agent.get("version"),
            "score": score(marker, outcome.final_text),
            "usage": outcome.usage,
            "status": outcome.status,
            "session_id": outcome.session_id,
        }
    result["canary_runs"] = runs
    result["canary_verdict"] = {
        label: bool(run["score"]["answer_correct"]) for label, run in runs.items()
    }

    # --- rollback attempt 1: can a historical version be made current directly?
    try:
        api.beta.agents.update(agent.id, version=published["v1_canary_a"])
        result["rollback_by_version_guard_only"] = {
            "accepted": True,
            "note": "update with only a stale version guard was accepted",
        }
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["rollback_by_version_guard_only"] = {
            "accepted": False,
            **api_error(error),
        }

    # --- rollback attempt 2: roll forward by re-applying the old version's state.
    replay = api.beta.agents.retrieve(agent.id, version=published["v1_canary_a"])
    rolled_back = api.beta.agents.update(
        agent.id,
        name=replay.name,
        description=replay.description,
        model=replay.model.model_dump(mode="json"),  # type: ignore[arg-type]
        system=replay.system,
        metadata=replay.metadata,  # type: ignore[arg-type]
        mcp_servers=[server.model_dump(mode="json") for server in replay.mcp_servers],  # type: ignore[misc]
        tools=[tool.model_dump(mode="json") for tool in replay.tools],  # type: ignore[misc]
        skills=[skill.model_dump(mode="json") for skill in replay.skills],  # type: ignore[misc]
        multiagent=(
            replay.multiagent.model_dump(mode="json")  # type: ignore[arg-type]
            if replay.multiagent is not None
            else None
        ),
        version=published["v3_broken"],
    )
    rollback_drift, rollback_added = diff_state(v1_state, agent_projection(rolled_back))
    verification = run_probe(
        api,
        ledger,
        agent={"type": "agent", "id": agent.id, "version": rolled_back.version},
        prompt=BENCHMARK_PROMPT,
        title="D4 post-rollback",
        metadata={"swarm_experiment": "d4", "variant": "rolled_back"},
        timeout_s=600.0,
    )
    result["rollback"] = {
        "new_version": rolled_back.version,
        "matches_v1": not rollback_drift,
        "drift_vs_v1": rollback_drift,
        "server_added_vs_v1": rollback_added,
        "post_rollback_score": score(marker_v1, verification.final_text),
        "session_id": verification.session_id,
        "versions": [
            version.version
            for version in api.beta.agents.versions.list(agent.id, limit=100)
        ],
    }


if __name__ == "__main__":
    main()
