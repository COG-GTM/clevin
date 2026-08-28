"""Shared helpers for workstream K experiment drivers.

Provenance: configures and observes the Managed Agents `session` primitive
(`beta.sessions`, `beta.sessions.events`) through the Anthropic SDK. No agent
loop, orchestration, or state layer lives here: every helper is a thin,
rerunnable wrapper around one SDK call plus evidence capture.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import anthropic

EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
SMOKE_PREFIX = "CLEVIN_SMOKE_TEST"

# The production agent's native toolset entry, restated so a session override can
# keep the native tools while replacing tool or MCP configuration.
AGENT_TOOLSET: dict[str, Any] = {
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
}


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def ids() -> dict[str, str]:
    return {
        "agent_id": os.environ["CLEVIN_AGENT_ID"],
        "environment_id": os.environ["CLEVIN_ENVIRONMENT_ID"],
        "vault_id": os.environ["CLEVIN_VAULT_ID"],
        "memory_store_id": os.environ["CLEVIN_MEMORY_STORE_ID"],
    }


def budget(amount: str) -> dict[str, Any]:
    return {
        "type": "limit",
        "max_list_cost": {"amount": amount, "currency": "USD"},
    }


def create_session(
    *,
    title: str,
    prompt: str,
    overrides: dict[str, Any] | None = None,
    max_cost: str = "60",
    with_memory: bool = False,
    with_vault: bool = True,
    metadata: dict[str, str] | None = None,
) -> Any:
    """Create one experiment session, optionally with agent-level overrides.

    `agent_with_overrides` is the native way to vary system prompt, tools, or
    skills for a single session without mutating the production agent version.
    """
    config = ids()
    agent: dict[str, Any] = {"type": "agent", "id": config["agent_id"]}
    if overrides:
        agent = {"type": "agent_with_overrides", "id": config["agent_id"], **overrides}
    kwargs: dict[str, Any] = {
        "agent": agent,
        "environment_id": config["environment_id"],
        "budget": budget(max_cost),
        "title": title,
        "metadata": {"experiment": "clevin-swarm-K", **(metadata or {})},
        "initial_events": [
            {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
        ],
    }
    if with_vault:
        kwargs["vault_ids"] = [config["vault_id"]]
    if with_memory:
        kwargs["resources"] = [
            {
                "type": "memory_store",
                "memory_store_id": config["memory_store_id"],
                "access": "read_write",
            }
        ]
    return client().beta.sessions.create(**kwargs)


def event_summary(event: Any) -> dict[str, Any]:
    data = event.model_dump(mode="json")
    kind = data.get("type", "?")
    out: dict[str, Any] = {"type": kind, "id": data.get("id")}
    if "processed_at" in data:
        out["at"] = data["processed_at"]
    if kind in {"agent.message", "user.message", "agent.thinking"}:
        blocks = data.get("content") or []
        text = " ".join(
            block.get("text", "") for block in blocks if isinstance(block, dict)
        )
        out["text"] = text[:1200]
    if kind in {"agent.tool_use", "agent.custom_tool_use", "agent.mcp_tool_use"}:
        out["tool"] = data.get("name") or data.get("tool_name")
        out["input"] = json.dumps(data.get("input"))[:600]
    if kind in {"agent.tool_result", "agent.mcp_tool_result", "user.tool_result"}:
        out["result"] = json.dumps(data.get("content"))[:600]
    if "tool_use_id" in data:
        out["tool_use_id"] = data["tool_use_id"]
    if "custom_tool_use_id" in data:
        out["tool_use_id"] = data["custom_tool_use_id"]
    if kind == "session.status_idle":
        out["stop_reason"] = data.get("stop_reason")
    if kind == "session.status_terminated":
        out["reason"] = data.get("reason") or data.get("stop_reason")
    if kind == "session.error":
        out["error"] = json.dumps(data.get("error"))[:600]
    return out


def list_events(session_id: str) -> list[Any]:
    return list(client().beta.sessions.events.list(session_id, order="asc"))


def summarize_events(session_id: str) -> list[dict[str, Any]]:
    return [event_summary(event) for event in list_events(session_id)]


def wait_for(
    session_id: str,
    predicate: Any,
    *,
    timeout: float,
    poll: float = 5.0,
) -> tuple[bool, Any]:
    """Poll the session resource until `predicate(session)` holds."""
    deadline = time.monotonic() + timeout
    session = client().beta.sessions.retrieve(session_id)
    while True:
        if predicate(session):
            return True, session
        if time.monotonic() >= deadline:
            return False, session
        time.sleep(poll)
        session = client().beta.sessions.retrieve(session_id)


TERMINAL_STOP_REASONS = {"end_turn", "budget_reached", "retries_exhausted"}


def last_idle_stop_reason(
    session_id: str, *, after_event_id: str | None = None
) -> dict[str, Any] | None:
    events = summarize_events(session_id)
    if after_event_id is not None:
        ids_seen = [event["id"] for event in events]
        if after_event_id in ids_seen:
            events = events[ids_seen.index(after_event_id) + 1 :]
        else:
            events = []
    for event in reversed(events):
        if event["type"] == "session.status_idle":
            stop = event.get("stop_reason")
            return stop if isinstance(stop, dict) else None
    return None


def latest_event_id(session_id: str) -> str | None:
    events = summarize_events(session_id)
    return events[-1]["id"] if events else None


def wait_for_turn_end(
    session_id: str,
    *,
    timeout: float,
    poll: float = 10.0,
    after_event_id: str | None = None,
) -> dict[str, Any]:
    """Wait until the agent's turn actually ends.

    A session parks in `idle`/`requires_action` for every native tool call while
    the `EnvironmentWorker` executes it, so `status == "idle"` is not a
    completion signal; only a terminal stop reason (or `terminated`) is.
    """
    deadline = time.monotonic() + timeout
    while True:
        session = client().beta.sessions.retrieve(session_id)
        stop = last_idle_stop_reason(session_id, after_event_id=after_event_id)
        reason = (stop or {}).get("type")
        if session.status == "terminated" or (
            session.status == "idle" and reason in TERMINAL_STOP_REASONS
        ):
            return {"status": session.status, "stop_reason": stop, "timed_out": False}
        if time.monotonic() >= deadline:
            return {"status": session.status, "stop_reason": stop, "timed_out": True}
        time.sleep(poll)


def wait_for_event(
    session_id: str,
    event_type: str,
    *,
    timeout: float,
    poll: float = 10.0,
) -> dict[str, Any] | None:
    """Wait for the first event of `event_type`, or None on timeout/terminal."""
    deadline = time.monotonic() + timeout
    while True:
        events = summarize_events(session_id)
        for event in events:
            if event["type"] == event_type:
                return event
        stop = last_idle_stop_reason(session_id)
        if (stop or {}).get("type") in TERMINAL_STOP_REASONS:
            return None
        if client().beta.sessions.retrieve(session_id).status == "terminated":
            return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def send_message(session_id: str, text: str) -> dict[str, Any]:
    """Attempt a bare `user.message` injection; record acceptance or rejection."""
    try:
        client().beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}],
        )
        return {"accepted": True}
    except Exception as error:  # noqa: BLE001 - the rejection text is the evidence
        return {"accepted": False, "error": str(error)[:700]}


def steer(
    session_id: str,
    text: str,
    *,
    interrupt_first: bool,
    attempts: int = 40,
    poll: float = 10.0,
) -> dict[str, Any]:
    """Inject a steering message into a live session.

    While a tool call is outstanding the API accepts only `user.interrupt`,
    `user.tool_result`, `user.custom_tool_result` or `user.tool_confirmation`, so
    steering a busy session is a two-step native sequence: interrupt, then send
    the message once the outstanding tool call has been resolved.
    """
    record: dict[str, Any] = {"interrupt_first": interrupt_first, "tries": []}
    if interrupt_first:
        try:
            client().beta.sessions.events.send(
                session_id, events=[{"type": "user.interrupt"}]
            )
            record["interrupt_accepted"] = True
        except Exception as error:  # noqa: BLE001
            record["interrupt_accepted"] = False
            record["interrupt_error"] = str(error)[:700]
    started = time.monotonic()
    for index in range(attempts):
        status = client().beta.sessions.retrieve(session_id).status
        result = send_message(session_id, text)
        record["tries"].append({"try": index, "status": status, **result})
        if result["accepted"]:
            record["accepted"] = True
            record["seconds_to_accept"] = round(time.monotonic() - started, 1)
            return record
        time.sleep(poll)
    record["accepted"] = False
    return record


def stream_until(
    session_id: str,
    *,
    stop_types: Iterable[str] = ("session.status_idle", "session.status_terminated"),
    limit: int = 400,
) -> Iterator[dict[str, Any]]:
    """Stream native SSE events, yielding summaries, until a stop type arrives."""
    stops = set(stop_types)
    seen = 0
    with client().beta.sessions.events.stream(session_id) as stream:
        for event in stream:
            summary = event_summary(event)
            yield summary
            seen += 1
            if summary["type"] in stops or seen >= limit:
                return


def save(name: str, payload: Any) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def usage(session_id: str) -> dict[str, Any]:
    session = client().beta.sessions.retrieve(session_id)
    return {
        "status": session.status,
        "usage": session.usage.model_dump(mode="json"),
        "budget": session.budget.model_dump(mode="json") if session.budget else None,
        "stats": session.stats.model_dump(mode="json"),
    }
