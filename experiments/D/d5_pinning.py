"""D5 — active sessions during version changes, and pinning as reproducibility.

Primitives: session creation against an agent reference (`"agent_id"` string,
`{type: "agent", version: n}`), the session's agent snapshot, and the
mid-session agent update (`sessions.update(agent=...)`).

The driver publishes v1 (marker A) and v2 (marker B) of a throwaway agent and
answers three questions with evidence:
  1. Does a live session follow the agent forward, or is it pinned to the
     version resolved at creation?
  2. What can be changed mid-session, and what is rejected?
  3. Does pinning a version make a run reproducible?

    ANTHROPIC_API_KEY=... uv run --project runtime python experiments/D/d5_pinning.py
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from _common import (
    Ledger,
    api_error,
    archive_agents,
    archive_sessions,
    client,
    collect_outcome,
    final_agent_text,
    session_events,
    start_session,
    temp_name,
    write_evidence,
)

PROBE_MODEL = "claude-haiku-4-5-20251001"
MARKER_V1 = "[[VERSION-1]]"
MARKER_V2 = "[[VERSION-2]]"
SYSTEM_TEMPLATE = (
    "You are a benchmark probe. Begin every message you send with the marker "
    "{marker}. Answer in at most eight words. Never use tools."
)
TURN_1 = "CLEVIN_SMOKE_TEST Say hello and nothing else."
TURN_2 = "CLEVIN_SMOKE_TEST Say goodbye and nothing else."
PINNED_PROMPT = "CLEVIN_SMOKE_TEST Name the capital of France."

AGENT_TOOLSET: dict[str, Any] = {
    "type": "agent_toolset_20260401",
    "default_config": {
        "enabled": True,
        "permission_policy": {"type": "always_allow"},
    },
}


def send_turn(api: anthropic.Anthropic, session_id: str, text: str) -> dict[str, Any]:
    before = len(session_events(api, session_id))
    api.beta.sessions.events.send(
        session_id,
        events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
    )
    outcome = collect_outcome(api, session_id, timeout_s=600.0, min_events=before + 2)
    return {
        "events_before": before,
        "events_after": len(outcome.event_types),
        "final_text": outcome.final_text[:200],
        "status": outcome.status,
        "resolved_agent_version": outcome.resolved_agent.get("version"),
        "marker_v1": MARKER_V1 in outcome.final_text,
        "marker_v2": MARKER_V2 in outcome.final_text,
    }


def main() -> None:
    api = client()
    ledger = Ledger()
    result: dict[str, Any] = {"experiment": "D5 version pinning and live sessions"}
    try:
        run(api, ledger, result)
    finally:
        archive_sessions(api, ledger, ledger.ids("session"))
        archive_agents(api, ledger, ledger.ids("agent"))
        result["cleanup"] = ledger.entries
        path = write_evidence("d5_pinning", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"evidence: {path}")


def run(api: anthropic.Anthropic, ledger: Ledger, result: dict[str, Any]) -> None:
    name = temp_name("pinning")
    agent = api.beta.agents.create(
        name=name,
        description="workstream D pinning probe",
        model={"id": PROBE_MODEL},
        system=SYSTEM_TEMPLATE.format(marker=MARKER_V1),
        metadata={"swarm_workstream": "D", "swarm_experiment": "d5"},
        tools=[AGENT_TOOLSET],  # type: ignore[list-item]
    )
    ledger.created("agent", agent.id, name)
    result["agent_id"] = agent.id

    # --- a session created by bare agent ID resolves the latest version (v1).
    live = start_session(
        api,
        ledger,
        agent=agent.id,
        prompt=TURN_1,
        title="D5 live session",
        metadata={"swarm_experiment": "d5", "role": "live"},
    )
    first_turn = collect_outcome(api, live.id, timeout_s=600.0)
    result["live_session"] = {
        "session_id": live.id,
        "turn_1": {
            "final_text": first_turn.final_text[:200],
            "resolved_agent_version": first_turn.resolved_agent.get("version"),
            "marker_v1": MARKER_V1 in first_turn.final_text,
        },
    }

    # --- publish v2 while that session exists.
    v2 = api.beta.agents.update(
        agent.id, system=SYSTEM_TEMPLATE.format(marker=MARKER_V2), version=1
    )
    result["published_v2"] = v2.version

    # --- does the live session follow the agent forward?
    result["live_session"]["turn_2_after_v2"] = send_turn(api, live.id, TURN_2)
    result["live_session"]["follows_agent_forward"] = bool(
        result["live_session"]["turn_2_after_v2"]["marker_v2"]
    )

    # --- a fresh bare-ID session resolves v2.
    fresh = start_session(
        api,
        ledger,
        agent=agent.id,
        prompt=TURN_1,
        title="D5 fresh latest session",
        metadata={"swarm_experiment": "d5", "role": "fresh-latest"},
    )
    fresh_outcome = collect_outcome(api, fresh.id, timeout_s=600.0)
    result["fresh_latest_session"] = {
        "session_id": fresh.id,
        "resolved_agent_version": fresh_outcome.resolved_agent.get("version"),
        "marker_v2": MARKER_V2 in fresh_outcome.final_text,
    }

    # --- mid-session agent updates: what is accepted, what is rejected?
    mid_session: dict[str, Any] = {}
    try:
        updated = api.beta.sessions.update(live.id, agent={"tools": []})
        mid_session["tools_replacement"] = {
            "accepted": True,
            "tools_after": [
                tool.model_dump(mode="json") for tool in updated.agent.tools
            ],
            "resolved_version_after": updated.agent.version,
        }
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        mid_session["tools_replacement"] = {"accepted": False, **api_error(error)}
    for field, value in (
        ("system", SYSTEM_TEMPLATE.format(marker="[[MID-SESSION]]")),
        ("model", PROBE_MODEL),
        ("skills", []),
        ("version", 1),
        ("multiagent", None),
    ):
        try:
            api.beta.sessions.update(
                live.id,
                agent={field: value},  # type: ignore[arg-type, misc]
            )
            mid_session[f"{field}_replacement"] = {"accepted": True}
        except Exception as error:  # noqa: BLE001 - the rejection is the evidence
            mid_session[f"{field}_replacement"] = {
                "accepted": False,
                **api_error(error),
            }
    result["mid_session_agent_update"] = mid_session

    # --- pinning: identical pinned config, run twice.
    pinned_runs: list[dict[str, Any]] = []
    for index in range(2):
        outcome = collect_outcome(
            api,
            start_session(
                api,
                ledger,
                agent={"type": "agent", "id": agent.id, "version": 1},
                prompt=PINNED_PROMPT,
                title=f"D5 pinned v1 run {index}",
                metadata={"swarm_experiment": "d5", "role": f"pinned-{index}"},
            ).id,
            timeout_s=600.0,
        )
        pinned_runs.append(
            {
                "session_id": outcome.session_id,
                "resolved_agent_version": outcome.resolved_agent.get("version"),
                "marker_v1": MARKER_V1 in outcome.final_text,
                "final_text": outcome.final_text[:200],
                "input_tokens": outcome.usage.get("input_tokens"),
                "output_tokens": outcome.usage.get("output_tokens"),
                "list_cost": outcome.usage.get("list_cost"),
                "event_types": outcome.event_types,
            }
        )
    result["pinned_reproducibility"] = {
        "runs": pinned_runs,
        "pinned_version_honoured": all(
            run["resolved_agent_version"] == 1 for run in pinned_runs
        ),
        "identical_final_text": pinned_runs[0]["final_text"]
        == pinned_runs[1]["final_text"],
        "identical_event_sequence": pinned_runs[0]["event_types"]
        == pinned_runs[1]["event_types"],
        "identical_output_tokens": pinned_runs[0]["output_tokens"]
        == pinned_runs[1]["output_tokens"],
    }

    # --- pinning a version that does not exist yet.
    try:
        start_session(
            api,
            ledger,
            agent={"type": "agent", "id": agent.id, "version": 99},
            prompt=PINNED_PROMPT,
            title="D5 nonexistent version",
            metadata={"swarm_experiment": "d5", "role": "bad-pin"},
        )
        result["nonexistent_version_pin"] = {"accepted": True}
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["nonexistent_version_pin"] = {"accepted": False, **api_error(error)}

    # --- what the live session's own history says about its agent.
    result["live_session"]["final_snapshot"] = api.beta.sessions.retrieve(
        live.id
    ).agent.model_dump(mode="json")
    result["live_session"]["last_text"] = final_agent_text(
        session_events(api, live.id)
    )[:200]


if __name__ == "__main__":
    main()
