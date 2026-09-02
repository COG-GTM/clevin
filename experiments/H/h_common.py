"""Shared helpers for workstream H (Deployments and automation) experiments.

Every helper here only touches native Managed Agents primitives through the
Anthropic SDK: `beta.deployments`, `beta.deployment_runs`, `beta.sessions`,
`beta.agents`, `beta.memory_stores`. Nothing here implements scheduling,
queueing, or orchestration itself; it observes what the native Deployment
primitive does.

Run any experiment with:
    uv run --project runtime python experiments/H/<script>.py
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

WORKSTREAM = "H"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
CLOUD_ENVIRONMENT_ID = "env_01F4KCNxYngRzYKG5a1QLRZT"
SELF_HOSTED_ENVIRONMENT_ID = os.environ.get(
    "CLEVIN_ENVIRONMENT_ID", "env_0152FZKRpy9f8uVw38Guzosy"
)
MEMORY_STORE_ID = os.environ.get(
    "CLEVIN_MEMORY_STORE_ID", "memstore_01JCboyFNzqNzucVq3xFpnYZ"
)
VAULT_ID = os.environ.get("CLEVIN_VAULT_ID", "vlt_011CeLyihmq1GNjHGxvtWw1q")
CHEAP_MODEL = "claude-haiku-4-5"
SMOKE_PREFIX = "CLEVIN_SMOKE_TEST"


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=2, timeout=120.0)


def utc_stamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


def temp_name(suffix: str) -> str:
    return f"clevin-swarm-{WORKSTREAM}-{utc_stamp()}-{secrets.token_hex(3)}-{suffix}"


def now() -> str:
    return datetime.now(tz=UTC).isoformat()


def dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


@dataclass
class Recorder:
    """Collects observations and a cleanup ledger, then writes one JSON artifact."""

    experiment: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=now)

    def note(self, label: str, **payload: Any) -> dict[str, Any]:
        entry = {
            "at": now(),
            "label": label,
            **{k: dump(v) for k, v in payload.items()},
        }
        self.observations.append(entry)
        print(json.dumps(entry, default=str)[:4000], flush=True)
        return entry

    def cleaned(self, kind: str, identifier: str, action: str, result: str) -> None:
        self.cleanup.append(
            {
                "at": now(),
                "kind": kind,
                "id": identifier,
                "action": action,
                "result": result,
            }
        )

    def write(self) -> Path:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = RESULTS_DIR / f"{self.experiment}.json"
        path.write_text(
            json.dumps(
                {
                    "experiment": self.experiment,
                    "started_at": self.started_at,
                    "finished_at": now(),
                    "observations": self.observations,
                    "cleanup": self.cleanup,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
        print(f"wrote {path}", flush=True)
        return path


def api_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, anthropic.APIStatusError):
        try:
            body = exc.response.json()
        except Exception:
            body = exc.response.text[:800]
        return {"status": exc.status_code, "body": body}
    return {"error": f"{type(exc).__name__}: {exc}"}


def attempt(recorder: Recorder, label: str, thunk: Any, **payload: Any) -> Any:
    """Call `thunk`, recording either its result or the API error it raised."""
    try:
        result = thunk()
    except Exception as exc:  # noqa: BLE001 - the failure mode is the observation
        recorder.note(f"{label}.rejected", **payload, **api_error(exc))
        return None
    recorder.note(f"{label}.accepted", **payload, result=result)
    return result


def user_message(text: str) -> dict[str, Any]:
    return {"type": "user.message", "content": [{"type": "text", "text": text}]}


def small_budget(amount: str = "2") -> dict[str, Any]:
    return {"type": "limit", "max_list_cost": {"amount": amount, "currency": "USD"}}


def create_probe_agent(
    api: anthropic.Anthropic,
    recorder: Recorder,
    *,
    suffix: str,
    system: str,
    tools: Iterable[dict[str, Any]] | None = None,
    mcp_servers: Iterable[dict[str, Any]] | None = None,
    multiagent: dict[str, Any] | None = None,
    model: str = CHEAP_MODEL,
) -> Any:
    kwargs: dict[str, Any] = {
        "name": temp_name(suffix),
        "model": model,
        "system": system,
        "description": f"Temporary workstream {WORKSTREAM} probe agent.",
        "metadata": {"experiment": "clevin-swarm-H"},
    }
    if tools is not None:
        kwargs["tools"] = list(tools)
    if mcp_servers is not None:
        kwargs["mcp_servers"] = list(mcp_servers)
    if multiagent is not None:
        kwargs["multiagent"] = multiagent
    agent = api.beta.agents.create(**kwargs)
    recorder.note(
        "agent.created", agent_id=agent.id, version=agent.version, name=agent.name
    )
    return agent


AGENT_TOOLSET = {
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": True, "permission_policy": {"type": "always_allow"}},
}


def archive_agent(api: anthropic.Anthropic, recorder: Recorder, agent_id: str) -> None:
    try:
        api.beta.agents.archive(agent_id)
    except Exception as exc:  # noqa: BLE001
        recorder.cleaned("agent", agent_id, "archive", f"failed: {type(exc).__name__}")
    else:
        recorder.cleaned("agent", agent_id, "archive", "archived")


def archive_deployment(
    api: anthropic.Anthropic, recorder: Recorder, deployment_id: str
) -> None:
    try:
        api.beta.deployments.archive(deployment_id)
    except Exception as exc:  # noqa: BLE001
        recorder.cleaned(
            "deployment", deployment_id, "archive", f"failed: {type(exc).__name__}"
        )
    else:
        recorder.cleaned("deployment", deployment_id, "archive", "archived")


def archive_memory_store(
    api: anthropic.Anthropic, recorder: Recorder, store_id: str
) -> None:
    try:
        api.beta.memory_stores.archive(store_id)
    except Exception as exc:  # noqa: BLE001
        recorder.cleaned(
            "memory_store", store_id, "archive", f"failed: {type(exc).__name__}"
        )
    else:
        recorder.cleaned("memory_store", store_id, "archive", "archived")


def stop_session(api: anthropic.Anthropic, recorder: Recorder, session_id: str) -> None:
    """Interrupt (if still working) and archive a probe session.

    `user.interrupt` is asynchronous: archiving immediately after sending it races the
    still-running session and returns 400, so wait for a non-running status first.
    """
    try:
        session = api.beta.sessions.retrieve(session_id)
        if session.status in {"running", "rescheduling"}:
            api.beta.sessions.events.send(
                session_id, events=[{"type": "user.interrupt"}]
            )
            for _ in range(12):
                time.sleep(5)
                session = api.beta.sessions.retrieve(session_id)
                if session.status not in {"running", "rescheduling"}:
                    break
        api.beta.sessions.archive(session_id)
    except Exception as exc:  # noqa: BLE001
        recorder.cleaned(
            "session", session_id, "interrupt+archive", f"failed: {type(exc).__name__}"
        )
    else:
        recorder.cleaned("session", session_id, "interrupt+archive", "archived")


def list_runs(
    api: anthropic.Anthropic, deployment_id: str, limit: int = 100
) -> list[Any]:
    return list(api.beta.deployment_runs.list(deployment_id=deployment_id, limit=limit))


def wait_for_runs(
    api: anthropic.Anthropic,
    deployment_id: str,
    *,
    count: int,
    timeout_s: float,
    poll_s: float = 10.0,
) -> list[Any]:
    """Poll the native run log until `count` runs exist or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    runs: list[Any] = []
    while time.monotonic() < deadline:
        runs = list_runs(api, deployment_id)
        if len(runs) >= count:
            return runs
        time.sleep(poll_s)
    return runs


def session_text(
    api: anthropic.Anthropic, session_id: str, limit: int = 400
) -> list[str]:
    """Flatten assistant/user text out of a session's native event log."""
    lines: list[str] = []
    for event in api.beta.sessions.events.list(session_id, order="asc", limit=limit):
        payload = dump(event)
        kind = payload.get("type")
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    lines.append(f"{kind}: {block.get('text', '')}")
        elif isinstance(content, str):
            lines.append(f"{kind}: {content}")
    return lines


SETTLED_STATUSES = ("idle", "terminated")


def iter_settled(
    api: anthropic.Anthropic,
    session_ids: Iterable[str],
    *,
    timeout_s: float,
    poll_s: float = 15.0,
) -> Iterator[tuple[str, Any]]:
    """Yield (session_id, session) once each session goes idle or terminated."""
    pending = list(session_ids)
    deadline = time.monotonic() + timeout_s
    terminal = set(SETTLED_STATUSES)
    while pending and time.monotonic() < deadline:
        for session_id in list(pending):
            session = api.beta.sessions.retrieve(session_id)
            if session.status in terminal:
                pending.remove(session_id)
                yield session_id, session
        if pending:
            time.sleep(poll_s)
    for session_id in pending:
        yield session_id, api.beta.sessions.retrieve(session_id)
