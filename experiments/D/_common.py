"""Shared helpers for workstream D experiment drivers.

Every driver in this directory probes the Managed Agents *agent* primitive
(`/v1/agents`, its version history, and how sessions resolve an agent
reference). Nothing here implements agent behaviour: it only creates,
retrieves, diffs, and archives native resources and records the evidence.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"

WORKSTREAM = "D"
# Cheap model for throwaway probe sessions. Managed Agents rejects `effort`
# on this model family, so probe agents omit it.
PROBE_MODEL = "claude-haiku-4-5-20251001"
# Anthropic-hosted cloud environment. Probes deliberately avoid the
# self-hosted Modal environment so that workstream C's sandbox experiments
# and this workstream cannot interfere with each other.
CLOUD_ENVIRONMENT_ID = "env_01F4KCNxYngRzYKG5a1QLRZT"
# Small ceiling for throwaway probes; the swarm expects `budget_reached`
# rather than treating it as a failure. Observed list cost of one probe
# session is on the order of a dollar.
PROBE_BUDGET_USD = "20"

TERMINAL_STATUSES = frozenset({"idle", "terminated"})
# The last event a completed turn emits; a stale `idle` status during a new turn
# is still followed by mid-turn events, so this disambiguates the two.
SETTLED_EVENT_TYPES = frozenset(
    {"session.status_idle", "session.status_terminated", "session.updated"}
)


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def utc_stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def temp_name(suffix: str) -> str:
    """Swarm-mandated temporary resource name."""
    return f"clevin-swarm-{WORKSTREAM}-{utc_stamp()}-{secrets.token_hex(3)}-{suffix}"


# ---------------------------------------------------------------------------
# desired state from the code-managed provisioner


def desired_agent_state() -> dict[str, Any]:
    """Read the code-declared desired agent state from the TypeScript provisioner.

    The provisioner is the single source of truth for the production agent, so
    experiments must not re-declare it in Python.
    """
    completed = subprocess.run(  # noqa: S603
        [
            "pnpm",
            "--filter",
            "@clevin/provision",
            "--silent",
            "drift",
            "--desired-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    desired: dict[str, Any] = payload["desired"]
    return desired


# ---------------------------------------------------------------------------
# structural diffing (version-to-version and desired-to-actual)


def _normalize(value: Any) -> Any:
    """Apply the two documented API canonicalizations before comparing."""
    if isinstance(value, Mapping):
        result = {key: _normalize(item) for key, item in value.items()}
        effort = result.get("effort")
        if isinstance(effort, str):
            result["effort"] = {"type": effort}
        return result
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return [_normalize(item) for item in value]
    return value


def diff_state(
    desired: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    path: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Structural diff mirroring `packages/provision/src/drift.ts`.

    Returns (drift, server_added). Keys present only on the actual side are
    reported as server-added rather than as drift, because the API
    canonicalizes and augments what it stores.
    """
    drift: list[dict[str, Any]] = []
    server_added: list[str] = []
    left = _normalize(dict(desired))
    right = _normalize(dict(actual))

    for key in left:
        child = f"{path}.{key}" if path else key
        if key not in right:
            drift.append({"path": child, "desired": left[key], "actual": None})
            continue
        _compare(left[key], right[key], child, drift, server_added)
    for key in right:
        if key not in left:
            server_added.append(f"{path}.{key}" if path else key)
    return drift, server_added


def _compare(
    desired: Any,
    actual: Any,
    path: str,
    drift: list[dict[str, Any]],
    server_added: list[str],
) -> None:
    if isinstance(desired, Mapping) and isinstance(actual, Mapping):
        child_drift, child_added = diff_state(desired, actual, path=path)
        drift.extend(child_drift)
        server_added.extend(child_added)
        return
    if (
        isinstance(desired, list)
        and isinstance(actual, list)
        and len(desired) == len(actual)
    ):
        for index, (left, right) in enumerate(zip(desired, actual, strict=True)):
            _compare(left, right, f"{path}[{index}]", drift, server_added)
        return
    if desired != actual:
        drift.append({"path": path, "desired": desired, "actual": actual})


def agent_projection(agent: Any) -> dict[str, Any]:
    """Project an API agent onto the fields the provisioner declares."""
    dumped = agent.model_dump(mode="json")
    return {
        key: dumped.get(key)
        for key in (
            "name",
            "description",
            "model",
            "system",
            "metadata",
            "mcp_servers",
            "tools",
            "skills",
            "multiagent",
        )
    }


# ---------------------------------------------------------------------------
# evidence and cleanup


@dataclass
class Ledger:
    """Cleanup ledger required by the swarm output contract."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def created(self, kind: str, identifier: str, note: str = "") -> None:
        self.entries.append(
            {
                "kind": kind,
                "id": identifier,
                "note": note,
                "cleanup_action": None,
                "cleanup_result": None,
            }
        )

    def record_cleanup(self, identifier: str, action: str, result: str) -> None:
        for entry in self.entries:
            if entry["id"] == identifier:
                entry["cleanup_action"] = action
                entry["cleanup_result"] = result
                return
        self.entries.append(
            {
                "kind": "unknown",
                "id": identifier,
                "note": "cleanup recorded without creation",
                "cleanup_action": action,
                "cleanup_result": result,
            }
        )

    def ids(self, kind: str) -> list[str]:
        return [entry["id"] for entry in self.entries if entry["kind"] == kind]


def archive_agents(
    api: anthropic.Anthropic, ledger: Ledger, agent_ids: Iterable[str]
) -> None:
    for agent_id in agent_ids:
        try:
            api.beta.agents.archive(agent_id)
            archived = api.beta.agents.retrieve(agent_id)
            result = (
                "archived"
                if archived.archived_at is not None
                else "archive call returned but archived_at is null"
            )
        except Exception as error:  # noqa: BLE001 - cleanup failures are evidence
            result = f"failed: {type(error).__name__}: {error}"
        ledger.record_cleanup(agent_id, "beta.agents.archive", result)


def archive_sessions(
    api: anthropic.Anthropic, ledger: Ledger, session_ids: Iterable[str]
) -> None:
    for session_id in session_ids:
        try:
            api.beta.sessions.archive(session_id)
            result = "archived"
        except Exception as error:  # noqa: BLE001 - cleanup failures are evidence
            result = f"failed: {type(error).__name__}: {error}"
        ledger.record_cleanup(session_id, "beta.sessions.archive", result)


def write_evidence(name: str, payload: Mapping[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def api_error(error: Exception) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    message = str(error)
    return {
        "error_type": type(error).__name__,
        "status_code": status,
        "message": message[:600],
        "body": body if isinstance(body, dict | list | str | None) else str(body),
    }


# ---------------------------------------------------------------------------
# probe sessions


@dataclass
class SessionOutcome:
    session_id: str
    status: str
    final_text: str
    event_types: list[str]
    usage: dict[str, Any]
    resolved_agent: dict[str, Any]
    elapsed_s: float
    error_events: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "final_text": self.final_text,
            "event_types": self.event_types,
            "usage": self.usage,
            "resolved_agent": self.resolved_agent,
            "elapsed_s": round(self.elapsed_s, 1),
            "error_events": self.error_events,
        }


def start_session(
    api: anthropic.Anthropic,
    ledger: Ledger,
    *,
    agent: Any,
    prompt: str,
    title: str,
    environment_id: str = CLOUD_ENVIRONMENT_ID,
    metadata: Mapping[str, str] | None = None,
) -> Any:
    session = api.beta.sessions.create(
        agent=agent,
        environment_id=environment_id,
        budget={
            "type": "limit",
            "max_list_cost": {"amount": PROBE_BUDGET_USD, "currency": "USD"},
        },
        metadata={"swarm_workstream": WORKSTREAM, **dict(metadata or {})},
        title=title,
        initial_events=[
            {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
        ],
    )
    ledger.created("session", session.id, title)
    return session


def await_session(
    api: anthropic.Anthropic,
    session_id: str,
    *,
    timeout_s: float = 900.0,
    poll_s: float = 10.0,
    min_events: int = 0,
) -> Any:
    # min_events guards the follow-up-turn race: a session that has not yet
    # picked up a just-sent event still reports the previous turn's idle state.
    deadline = time.monotonic() + timeout_s
    while True:
        session = api.beta.sessions.retrieve(session_id)
        settled = session.status in TERMINAL_STATUSES
        if settled and min_events:
            events = api.beta.sessions.events.list(session_id, order="asc").data
            settled = len(events) >= min_events and (
                str(events[-1].type) in SETTLED_EVENT_TYPES
            )
        if settled or time.monotonic() >= deadline:
            return session
        time.sleep(poll_s)


def session_events(api: anthropic.Anthropic, session_id: str) -> list[dict[str, Any]]:
    return [
        event.model_dump(mode="json")
        for event in api.beta.sessions.events.list(session_id, order="asc")
    ]


def final_agent_text(events: Iterable[Mapping[str, Any]]) -> str:
    texts: list[str] = []
    for event in events:
        if event.get("type") != "agent.message":
            continue
        for block in event.get("content") or []:
            if isinstance(block, Mapping) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    return texts[-1] if texts else ""


def collect_outcome(
    api: anthropic.Anthropic,
    session_id: str,
    *,
    timeout_s: float = 900.0,
    min_events: int = 0,
) -> SessionOutcome:
    started = time.monotonic()
    session = await_session(api, session_id, timeout_s=timeout_s, min_events=min_events)
    events = session_events(api, session_id)
    return SessionOutcome(
        session_id=session_id,
        status=session.status,
        final_text=final_agent_text(events),
        event_types=[str(event.get("type")) for event in events],
        usage=session.usage.model_dump(mode="json"),
        resolved_agent=session.agent.model_dump(mode="json"),
        elapsed_s=time.monotonic() - started,
        error_events=[
            event
            for event in events
            if str(event.get("type", "")).startswith("session.error")
        ],
    )


def run_probe(
    api: anthropic.Anthropic,
    ledger: Ledger,
    *,
    agent: Any,
    prompt: str,
    title: str,
    environment_id: str = CLOUD_ENVIRONMENT_ID,
    metadata: Mapping[str, str] | None = None,
    timeout_s: float = 900.0,
) -> SessionOutcome:
    session = start_session(
        api,
        ledger,
        agent=agent,
        prompt=prompt,
        title=title,
        environment_id=environment_id,
        metadata=metadata,
    )
    return collect_outcome(api, session.id, timeout_s=timeout_s)


def version_history(api: anthropic.Anthropic, agent_id: str) -> Iterator[Any]:
    return iter(list(api.beta.agents.versions.list(agent_id, limit=100)))
