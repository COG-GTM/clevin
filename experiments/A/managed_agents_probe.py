#!/usr/bin/env python3
"""Rerunnable control-plane and session-semantics probes for Managed Agents."""

from __future__ import annotations

import argparse
import io
import json
import os
import time
import uuid
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import modal
from anthropic import Anthropic


AGENT_TOOLSET = {
    "type": "agent_toolset_20260401",
    "default_config": {
        "enabled": True,
        "permission_policy": {"type": "always_allow"},
    },
}
DISABLED_AGENT_TOOLSET = {
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": False},
    "configs": [
        {
            "name": "read",
            "enabled": True,
            "permission_policy": {"type": "always_allow"},
        }
    ],
}
V1_SYSTEM = """\
You are a harmless Managed Agents control-plane probe.
The first line of every final answer must be VERSION_ONE.
For CLEVIN_SMOKE_TEST STATE_MARKER, use bash to write exactly alpha-state to
/workspace/control-plane-marker.txt, then read it and report the value.
For CLEVIN_SMOKE_TEST TOOL_INTERRUPT, use bash to run exactly:
sleep 30; printf tool-finished > /workspace/tool-interrupt.txt
Then report whether the command completed.
For CLEVIN_SMOKE_TEST GENERATION_INTERRUPT, do not use tools. Immediately start
writing 20,000 numbered occurrences of VERSION_ONE and do not summarize.
For any other CLEVIN_SMOKE_TEST message, answer briefly and do not access external
services.
"""
V2_SYSTEM = """\
You are a harmless Managed Agents control-plane probe.
The first line of every final answer must be VERSION_TWO.
For any CLEVIN_SMOKE_TEST message, answer briefly without using tools or external
services.
"""
HELPER_SYSTEM = """\
You are a harmless subagent configuration marker. Reply HELPER_VERSION_ONE and do
not use tools or external services.
"""
COMPACTION_SYSTEM = """\
You are a context-retention probe. The non-negotiable constraint is:
EARLY_CONSTRAINT_ORANGE_7. Never omit or alter that exact string when asked for
the early constraint. For filler messages, acknowledge in one short sentence.
"""


def now_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def text_event(text: str) -> dict[str, object]:
    return {"type": "user.message", "content": [{"type": "text", "text": text}]}


def budget(dollars: int = 10) -> dict[str, object]:
    return {
        "type": "limit",
        "max_list_cost": {"amount": str(dollars * 100), "currency": "USD"},
    }


def dump_model(value: object) -> dict[str, object]:
    if not hasattr(value, "model_dump"):
        raise TypeError(f"expected SDK model, got {type(value)!r}")
    return value.model_dump(mode="json")


def list_events(client: Anthropic, session_id: str) -> list[object]:
    return list(client.beta.sessions.events.list(session_id, order="asc", limit=100))


def latest_idle_stop_reason(events: list[object]) -> str | None:
    for event in reversed(events):
        if event.type == "session.status_running":
            return None
        if event.type == "session.status_idle":
            return event.stop_reason.type
    return None


def wait_for(
    client: Anthropic,
    session_id: str,
    predicate: Callable[[list[object]], bool],
    *,
    timeout_seconds: float = 180,
    poll_seconds: float = 0.5,
) -> list[object]:
    deadline = time.monotonic() + timeout_seconds
    events: list[object] = []
    while time.monotonic() < deadline:
        events = list_events(client, session_id)
        if predicate(events):
            return events
        time.sleep(poll_seconds)
    counts = Counter(event.type for event in events)
    raise TimeoutError(
        f"session {session_id} did not reach expected state; events={dict(counts)}"
    )


def wait_for_idle(
    client: Anthropic, session_id: str, *, timeout_seconds: float = 180
) -> list[object]:
    return wait_for(
        client,
        session_id,
        lambda events: latest_idle_stop_reason(events)
        in {"end_turn", "budget_reached", "retries_exhausted"},
        timeout_seconds=timeout_seconds,
    )


def wait_for_type(
    client: Anthropic,
    session_id: str,
    event_type: str,
    *,
    timeout_seconds: float = 180,
) -> list[object]:
    return wait_for(
        client,
        session_id,
        lambda events: any(event.type == event_type for event in events),
        timeout_seconds=timeout_seconds,
    )


def message_texts(events: list[object]) -> list[str]:
    texts: list[str] = []
    for event in events:
        if event.type != "agent.message":
            continue
        texts.append(
            "".join(block.text for block in event.content if block.type == "text")[:500]
        )
    return texts


def event_summary(events: list[object]) -> dict[str, object]:
    counts = Counter(event.type for event in events)
    usage: list[dict[str, object]] = []
    for event in events:
        if event.type == "span.model_request_end":
            usage.append(
                {
                    "event_id": event.id,
                    **dump_model(event.model_usage),
                }
            )
    return {
        "count": len(events),
        "counts": dict(sorted(counts.items())),
        "first_id": events[0].id if events else None,
        "last_id": events[-1].id if events else None,
        "agent_messages": message_texts(events),
        "model_request_usage": usage,
    }


def create_skill_archive() -> bytes:
    content = """\
---
name: control-plane-marker
description: A harmless marker used only by workstream A lifecycle experiments.
---

When explicitly asked for the skill marker, reply exactly SKILL_VERSION_ONE.
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("control-plane-marker/SKILL.md", content)
    return output.getvalue()


def create_session(
    client: Anthropic,
    *,
    environment_id: str,
    agent_id: str,
    agent_version: int | None,
    title: str,
    initial_message: str,
    max_dollars: int = 10,
) -> object:
    agent_ref: dict[str, object]
    if agent_version is None:
        agent_ref = {"type": "agent", "id": agent_id}
    else:
        agent_ref = {
            "type": "agent",
            "id": agent_id,
            "version": agent_version,
        }
    return client.beta.sessions.create(
        agent=agent_ref,
        environment_id=environment_id,
        title=title,
        budget=budget(max_dollars),
        initial_events=[text_event(initial_message)],
        metadata={"experiment": "workstream-A"},
    )


def send_message(client: Anthropic, session_id: str, text: str) -> list[object]:
    response = client.beta.sessions.events.send(session_id, events=[text_event(text)])
    if not response.data:
        raise RuntimeError("session events.send returned no event data")
    sent_id = response.data[-1].id
    return wait_for(
        client,
        session_id,
        lambda events: any(event.id == sent_id for event in events)
        and latest_idle_stop_reason(
            events[
                next(
                    index for index, event in enumerate(events) if event.id == sent_id
                ) :
            ]
        )
        in {"end_turn", "budget_reached", "retries_exhausted"},
    )


def send_message_allow_terminated(
    client: Anthropic, session_id: str, text: str
) -> list[object]:
    response = client.beta.sessions.events.send(session_id, events=[text_event(text)])
    if not response.data:
        raise RuntimeError("session events.send returned no event data")
    sent_id = response.data[-1].id

    def completed(events: list[object]) -> bool:
        sent_index = next(
            (index for index, event in enumerate(events) if event.id == sent_id),
            None,
        )
        if sent_index is None:
            return False
        turn_events = events[sent_index:]
        return any(
            event.type == "session.status_terminated" for event in turn_events
        ) or latest_idle_stop_reason(turn_events) in {
            "end_turn",
            "budget_reached",
            "retries_exhausted",
        }

    return wait_for(client, session_id, completed)


def run_versioning_probe(
    client: Anthropic,
    *,
    environment_id: str,
    run_name: str,
    cleanup: dict[str, object],
) -> dict[str, object]:
    helper = client.beta.agents.create(
        name=f"{run_name}-helper",
        description="Temporary workstream A helper marker",
        model="claude-haiku-4-5",
        system=HELPER_SYSTEM,
        tools=[],
        skills=[],
        multiagent=None,
        metadata={"experiment": "workstream-A"},
    )
    cleanup["agents"].append(helper.id)

    skill = client.beta.skills.create(
        files=[
            (
                "control-plane-marker.zip",
                create_skill_archive(),
                "application/zip",
            )
        ],
        display_title=f"{run_name} marker",
    )
    cleanup["skills"].append(skill.id)

    primary = client.beta.agents.create(
        name=f"{run_name}-primary",
        description="Temporary workstream A version pinning probe",
        model="claude-haiku-4-5",
        system=V1_SYSTEM,
        tools=[AGENT_TOOLSET],
        skills=[],
        multiagent=None,
        metadata={"experiment": "workstream-A", "marker": "v1"},
    )
    cleanup["agents"].append(primary.id)

    pinned_v1 = create_session(
        client,
        environment_id=environment_id,
        agent_id=primary.id,
        agent_version=primary.version,
        title=f"{run_name} pinned-v1",
        initial_message="CLEVIN_SMOKE_TEST STATE_MARKER",
    )
    cleanup["sessions"].append(pinned_v1.id)
    pinned_v1_initial = wait_for_idle(client, pinned_v1.id)

    v2 = client.beta.agents.update(
        primary.id,
        version=primary.version,
        model="claude-sonnet-4-5",
        system=V2_SYSTEM,
        tools=[DISABLED_AGENT_TOOLSET],
        skills=[{"type": "custom", "skill_id": skill.id}],
        multiagent={
            "type": "coordinator",
            "agents": [{"type": "agent", "id": helper.id, "version": helper.version}],
        },
        metadata={"experiment": "workstream-A", "marker": "v2"},
    )

    pinned_v1_after_update = client.beta.sessions.retrieve(pinned_v1.id)
    pinned_v1_resumed = send_message(
        client, pinned_v1.id, "CLEVIN_SMOKE_TEST report your version marker"
    )

    latest_v2 = create_session(
        client,
        environment_id=environment_id,
        agent_id=primary.id,
        agent_version=None,
        title=f"{run_name} latest-v2",
        initial_message="CLEVIN_SMOKE_TEST report your version marker",
    )
    cleanup["sessions"].append(latest_v2.id)
    latest_v2_events = wait_for_idle(client, latest_v2.id)

    mutable_update = client.beta.sessions.update(
        pinned_v1.id,
        agent={"tools": [], "mcp_servers": []},
    )
    mutable_snapshot = client.beta.sessions.retrieve(pinned_v1.id)

    v3 = client.beta.agents.update(
        primary.id,
        version=v2.version,
        model="claude-haiku-4-5",
        system=V1_SYSTEM,
        tools=[AGENT_TOOLSET],
        skills=[],
        multiagent=None,
        metadata={"experiment": "workstream-A", "marker": "rollback"},
    )
    latest_v3 = create_session(
        client,
        environment_id=environment_id,
        agent_id=primary.id,
        agent_version=None,
        title=f"{run_name} latest-v3-rollback",
        initial_message="CLEVIN_SMOKE_TEST report your version marker",
    )
    cleanup["sessions"].append(latest_v3.id)
    latest_v3_events = wait_for_idle(client, latest_v3.id)

    versions = list(client.beta.agents.versions.list(primary.id, limit=100))
    return {
        "agent_id": primary.id,
        "helper_agent_id": helper.id,
        "skill_id": skill.id,
        "versions": [agent.version for agent in versions],
        "v1_agent": dump_model(
            client.beta.agents.retrieve(primary.id, version=primary.version)
        ),
        "v2_agent": dump_model(
            client.beta.agents.retrieve(primary.id, version=v2.version)
        ),
        "v3_agent": dump_model(
            client.beta.agents.retrieve(primary.id, version=v3.version)
        ),
        "pinned_v1_session_id": pinned_v1.id,
        "pinned_v1_initial": event_summary(pinned_v1_initial),
        "pinned_v1_snapshot_after_agent_update": dump_model(
            pinned_v1_after_update.agent
        ),
        "pinned_v1_resumed": event_summary(pinned_v1_resumed),
        "latest_v2_session_id": latest_v2.id,
        "latest_v2_snapshot": dump_model(latest_v2.agent),
        "latest_v2_events": event_summary(latest_v2_events),
        "session_mutable_update_response": dump_model(mutable_update.agent),
        "session_snapshot_after_mutable_update": dump_model(mutable_snapshot.agent),
        "latest_v3_session_id": latest_v3.id,
        "latest_v3_snapshot": dump_model(latest_v3.agent),
        "latest_v3_events": event_summary(latest_v3_events),
    }


def run_event_order_probe(
    client: Anthropic,
    *,
    environment_id: str,
    agent_id: str,
    agent_version: int,
    run_name: str,
    cleanup: dict[str, object],
) -> dict[str, object]:
    session = create_session(
        client,
        environment_id=environment_id,
        agent_id=agent_id,
        agent_version=agent_version,
        title=f"{run_name} ordering",
        initial_message="CLEVIN_SMOKE_TEST ORDER-INITIAL",
    )
    cleanup["sessions"].append(session.id)
    wait_for_idle(client, session.id)

    ordered_response = client.beta.sessions.events.send(
        session.id,
        events=[
            text_event("CLEVIN_SMOKE_TEST ORDER-A"),
            text_event("CLEVIN_SMOKE_TEST ORDER-B"),
        ],
    )
    if not ordered_response.data:
        raise RuntimeError("ordered events.send returned no event data")
    last_ordered_id = ordered_response.data[-1].id
    wait_for(
        client,
        session.id,
        lambda events: any(event.id == last_ordered_id for event in events)
        and latest_idle_stop_reason(
            events[
                next(
                    index
                    for index, event in enumerate(events)
                    if event.id == last_ordered_id
                ) :
            ]
        )
        in {"end_turn", "budget_reached", "retries_exhausted"},
    )
    send_message(client, session.id, "CLEVIN_SMOKE_TEST DUPLICATE")
    events = send_message(client, session.id, "CLEVIN_SMOKE_TEST DUPLICATE")
    time.sleep(1)
    events = list_events(client, session.id)

    user_messages: list[dict[str, str]] = []
    for event in events:
        if event.type != "user.message":
            continue
        text = "".join(block.text for block in event.content if block.type == "text")
        user_messages.append(
            {
                "id": event.id,
                "processed_at": (
                    event.processed_at.isoformat()
                    if event.processed_at is not None
                    else ""
                ),
                "text": text,
            }
        )
    duplicate_ids = [
        message["id"]
        for message in user_messages
        if message["text"] == "CLEVIN_SMOKE_TEST DUPLICATE"
    ]
    replay_ids_1 = [event.id for event in events]
    replay_ids_2 = [event.id for event in list_events(client, session.id)]
    return {
        "session_id": session.id,
        "user_messages": user_messages,
        "duplicate_ids": duplicate_ids,
        "duplicate_ids_are_distinct": len(set(duplicate_ids)) == 2,
        "batch_order_preserved": [message["text"] for message in user_messages].index(
            "CLEVIN_SMOKE_TEST ORDER-A"
        )
        < [message["text"] for message in user_messages].index(
            "CLEVIN_SMOKE_TEST ORDER-B"
        ),
        "replayed_prefix_ids_stable": (
            replay_ids_2[: len(replay_ids_1)] == replay_ids_1
        ),
        "history_grew_between_replays": len(replay_ids_2) > len(replay_ids_1),
        "event_summary": event_summary(events),
    }


def run_sse_probe(
    client: Anthropic,
    *,
    environment_id: str,
    agent_id: str,
    agent_version: int,
    run_name: str,
    cleanup: dict[str, object],
) -> dict[str, object]:
    session = create_session(
        client,
        environment_id=environment_id,
        agent_id=agent_id,
        agent_version=agent_version,
        title=f"{run_name} sse-reconnect",
        initial_message="CLEVIN_SMOKE_TEST STATE_MARKER",
    )
    cleanup["sessions"].append(session.id)

    first_stream_ids: list[str] = []
    with client.beta.sessions.events.stream(session.id) as stream:
        for streamed in stream:
            if streamed.type in {"event_start", "event_delta"}:
                continue
            first_stream_ids.append(streamed.id)
            if len(first_stream_ids) >= 4:
                break

    second_stream_ids: list[str] = []
    with client.beta.sessions.events.stream(session.id) as stream:
        for streamed in stream:
            if streamed.type in {"event_start", "event_delta"}:
                continue
            second_stream_ids.append(streamed.id)
            if (
                streamed.type == "session.status_idle"
                and streamed.stop_reason.type != "requires_action"
            ):
                break

    final_events = wait_for_idle(client, session.id)
    persisted_ids = [event.id for event in final_events]
    observed_ids = list(dict.fromkeys(first_stream_ids + second_stream_ids))
    return {
        "session_id": session.id,
        "first_stream_ids_before_disconnect": first_stream_ids,
        "second_stream_ids_after_reconnect": second_stream_ids,
        "duplicate_ids_across_connections": sorted(
            set(first_stream_ids) & set(second_stream_ids)
        ),
        "all_streamed_ids_persisted": set(observed_ids).issubset(set(persisted_ids)),
        "missed_persisted_ids": [
            event_id for event_id in persisted_ids if event_id not in observed_ids
        ],
        "event_summary": event_summary(final_events),
    }


def run_generation_interrupt_probe(
    client: Anthropic,
    *,
    environment_id: str,
    agent_id: str,
    agent_version: int,
    run_name: str,
    cleanup: dict[str, object],
) -> dict[str, object]:
    session = create_session(
        client,
        environment_id=environment_id,
        agent_id=agent_id,
        agent_version=agent_version,
        title=f"{run_name} generation-interrupt",
        initial_message="CLEVIN_SMOKE_TEST GENERATION_INTERRUPT",
        max_dollars=20,
    )
    cleanup["sessions"].append(session.id)
    before_interrupt = wait_for_type(client, session.id, "span.model_request_start")
    interrupt_sent_at = datetime.now(UTC).isoformat()
    client.beta.sessions.events.send(session.id, events=[{"type": "user.interrupt"}])
    events = wait_for_idle(client, session.id)
    return {
        "session_id": session.id,
        "interrupt_sent_at": interrupt_sent_at,
        "events_before_interrupt": event_summary(before_interrupt),
        "events_after_interrupt": event_summary(events),
        "interrupt_event_ids": [
            event.id for event in events if event.type == "user.interrupt"
        ],
    }


def run_tool_interrupt_probe(
    client: Anthropic,
    *,
    environment_id: str,
    agent_id: str,
    agent_version: int,
    run_name: str,
    cleanup: dict[str, object],
) -> dict[str, object]:
    session = create_session(
        client,
        environment_id=environment_id,
        agent_id=agent_id,
        agent_version=agent_version,
        title=f"{run_name} tool-interrupt",
        initial_message="CLEVIN_SMOKE_TEST TOOL_INTERRUPT",
        max_dollars=20,
    )
    cleanup["sessions"].append(session.id)
    tool_use_events = wait_for_type(client, session.id, "agent.tool_use")
    time.sleep(2)
    interrupt_sent_at = datetime.now(UTC).isoformat()
    client.beta.sessions.events.send(session.id, events=[{"type": "user.interrupt"}])
    interrupted_events = wait_for_idle(client, session.id)
    time.sleep(35)
    final_events = list_events(client, session.id)
    return {
        "session_id": session.id,
        "interrupt_sent_at": interrupt_sent_at,
        "sandbox_file_after_interrupt": read_volume_file(
            session.id, "tool-interrupt.txt"
        ),
        "tool_use_before_interrupt": event_summary(tool_use_events),
        "events_at_idle_after_interrupt": event_summary(interrupted_events),
        "events_35s_after_interrupt": event_summary(final_events),
        "tool_uses": [
            {
                "id": event.id,
                "name": event.name,
                "input": event.input,
            }
            for event in final_events
            if event.type == "agent.tool_use"
        ],
        "tool_results": [
            dump_model(event)
            for event in final_events
            if event.type == "agent.tool_result"
        ],
    }


def run_compaction_probe(
    client: Anthropic,
    *,
    environment_id: str,
    run_name: str,
    cleanup: dict[str, object],
    turns: int,
    filler_bytes: int,
    model: str,
) -> dict[str, object]:
    agent = client.beta.agents.create(
        name=f"{run_name}-compaction",
        description="Temporary workstream A compaction probe",
        model=model,
        system=COMPACTION_SYSTEM,
        tools=[],
        skills=[],
        multiagent=None,
        metadata={"experiment": "workstream-A"},
    )
    cleanup["agents"].append(agent.id)
    session = create_session(
        client,
        environment_id=environment_id,
        agent_id=agent.id,
        agent_version=agent.version,
        title=f"{run_name} compaction",
        initial_message=(
            "CLEVIN_SMOKE_TEST Remember EARLY_CONSTRAINT_ORANGE_7. Acknowledge briefly."
        ),
        max_dollars=100,
    )
    cleanup["sessions"].append(session.id)
    wait_for_idle(client, session.id)

    completed_turns = 0
    for turn in range(turns):
        filler = (
            f"CLEVIN_SMOKE_TEST filler turn {turn}. "
            + ("0123456789abcdef" * ((filler_bytes // 16) + 1))[:filler_bytes]
        )
        current = send_message_allow_terminated(client, session.id, filler)
        completed_turns += 1
        if any(event.type == "session.status_terminated" for event in current):
            break
        if any(event.type == "agent.thread_context_compacted" for event in current):
            if (
                sum(event.type == "agent.thread_context_compacted" for event in current)
                >= 3
            ):
                break

    terminated = any(event.type == "session.status_terminated" for event in current)
    if terminated:
        final_events = current
    else:
        final_events = send_message_allow_terminated(
            client,
            session.id,
            "CLEVIN_SMOKE_TEST State the exact early constraint and nothing else.",
        )
    compactions = [
        dump_model(event)
        for event in final_events
        if event.type == "agent.thread_context_compacted"
    ]
    return {
        "session_id": session.id,
        "model": model,
        "completed_filler_turns": completed_turns,
        "filler_bytes_per_turn": filler_bytes,
        "compactions": compactions,
        "early_constraint_retained": any(
            "EARLY_CONSTRAINT_ORANGE_7" in text
            for text in message_texts(final_events)[-2:]
        ),
        "termination_errors": [
            dump_model(event) for event in final_events if event.type == "session.error"
        ],
        "event_summary": event_summary(final_events),
    }


def read_volume_file(session_id: str, file_name: str) -> str | None:
    volume = modal.Volume.from_name(
        "clevin-sessions",
        environment_name="clevin",
        create_if_missing=False,
    )
    path = f"/sessions/{session_id}/{file_name}"
    try:
        return b"".join(volume.read_file(path)).decode("utf-8")
    except modal.exception.NotFoundError:
        return None


def remove_volume_tree(session_id: str) -> None:
    volume = modal.Volume.from_name(
        "clevin-sessions",
        environment_name="clevin",
        create_if_missing=False,
    )
    volume.remove_file(f"/sessions/{session_id}", recursive=True)


def clean_up(
    client: Anthropic,
    cleanup: dict[str, object],
    *,
    keep_resources: bool,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if keep_resources:
        return [
            {
                "resource": "all",
                "action": "retained by --keep-resources",
                "result": "not cleaned",
            }
        ]

    for session_id in cleanup["sessions"]:
        try:
            session = client.beta.sessions.retrieve(session_id)
            if session.status == "running":
                client.beta.sessions.events.send(
                    session_id, events=[{"type": "user.interrupt"}]
                )
                wait_for_idle(client, session_id, timeout_seconds=60)
            client.beta.sessions.archive(session_id)
            results.append(
                {
                    "resource": session_id,
                    "action": "session archive",
                    "result": "success",
                }
            )
        except Exception as error:
            results.append(
                {
                    "resource": session_id,
                    "action": "session archive",
                    "result": f"failed: {type(error).__name__}: {error}",
                }
            )
        try:
            remove_volume_tree(session_id)
            results.append(
                {
                    "resource": f"clevin-sessions/sessions/{session_id}",
                    "action": "volume subtree removal",
                    "result": "success",
                }
            )
        except Exception as error:
            results.append(
                {
                    "resource": f"clevin-sessions/sessions/{session_id}",
                    "action": "volume subtree removal",
                    "result": f"failed: {type(error).__name__}: {error}",
                }
            )

    for agent_id in reversed(cleanup["agents"]):
        try:
            client.beta.agents.archive(agent_id)
            results.append(
                {
                    "resource": agent_id,
                    "action": "agent archive",
                    "result": "success",
                }
            )
        except Exception as error:
            results.append(
                {
                    "resource": agent_id,
                    "action": "agent archive",
                    "result": f"failed: {type(error).__name__}: {error}",
                }
            )

    for skill_id in cleanup["skills"]:
        try:
            for version in client.beta.skills.versions.list(skill_id, limit=100):
                client.beta.skills.versions.delete(version.version, skill_id=skill_id)
            client.beta.skills.delete(skill_id)
            results.append(
                {
                    "resource": skill_id,
                    "action": "skill delete",
                    "result": "success",
                }
            )
        except Exception as error:
            results.append(
                {
                    "resource": skill_id,
                    "action": "skill delete",
                    "result": f"failed: {type(error).__name__}: {error}",
                }
            )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/A/results/latest.json"),
    )
    parser.add_argument("--skip-compaction", action="store_true")
    parser.add_argument("--only-compaction", action="store_true")
    parser.add_argument("--compaction-turns", type=int, default=60)
    parser.add_argument("--filler-bytes", type=int, default=90_000)
    parser.add_argument("--compaction-model", default="claude-opus-4-6")
    parser.add_argument("--keep-resources", action="store_true")
    args = parser.parse_args()

    environment_id = os.environ["CLEVIN_ENVIRONMENT_ID"]
    client = Anthropic()
    run_name = f"clevin-swarm-A-{now_slug()}-{uuid.uuid4().hex[:6]}"
    cleanup: dict[str, object] = {"agents": [], "sessions": [], "skills": []}
    result: dict[str, object] = {
        "run_name": run_name,
        "started_at": datetime.now(UTC).isoformat(),
        "environment_id": environment_id,
        "probes": {},
        "cleanup": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not args.only_compaction:
            versioning = run_versioning_probe(
                client,
                environment_id=environment_id,
                run_name=run_name,
                cleanup=cleanup,
            )
            result["probes"]["versioning"] = versioning
            primary_id = versioning["agent_id"]
            primary_v1 = versioning["v1_agent"]["version"]

            result["probes"]["event_ordering"] = run_event_order_probe(
                client,
                environment_id=environment_id,
                agent_id=primary_id,
                agent_version=primary_v1,
                run_name=run_name,
                cleanup=cleanup,
            )
            result["probes"]["sse_reconnect"] = run_sse_probe(
                client,
                environment_id=environment_id,
                agent_id=primary_id,
                agent_version=primary_v1,
                run_name=run_name,
                cleanup=cleanup,
            )
            result["probes"]["generation_interrupt"] = run_generation_interrupt_probe(
                client,
                environment_id=environment_id,
                agent_id=primary_id,
                agent_version=primary_v1,
                run_name=run_name,
                cleanup=cleanup,
            )
            result["probes"]["tool_interrupt"] = run_tool_interrupt_probe(
                client,
                environment_id=environment_id,
                agent_id=primary_id,
                agent_version=primary_v1,
                run_name=run_name,
                cleanup=cleanup,
            )

            state_session_id = versioning["pinned_v1_session_id"]
            result["probes"]["anthropic_vs_sandbox"] = {
                "session_id": state_session_id,
                "anthropic_history": event_summary(
                    list_events(client, state_session_id)
                ),
                "sandbox_file": {
                    "path": "control-plane-marker.txt",
                    "content": read_volume_file(
                        state_session_id, "control-plane-marker.txt"
                    ),
                },
            }

        if not args.skip_compaction:
            result["probes"]["compaction"] = run_compaction_probe(
                client,
                environment_id=environment_id,
                run_name=run_name,
                cleanup=cleanup,
                turns=args.compaction_turns,
                filler_bytes=args.filler_bytes,
                model=args.compaction_model,
            )
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        result["cleanup"] = clean_up(
            client, cleanup, keep_resources=args.keep_resources
        )
        result["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
