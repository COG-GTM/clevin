"""Session driver for workstream E probes.

Primitive: `client.beta.sessions` with `resources=[{type: "memory_store", ...}]` and
`agent={type: "agent_with_overrides", ...}`. The overrides form is what lets these
probes vary the system prompt per experiment without ever mutating the production
agent version.

This module only creates sessions, streams native events, and records them. It
contains no agent loop, no tool execution, and no memory logic of its own.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

CLOUD_ENVIRONMENT_ID = "env_01F4KCNxYngRzYKG5a1QLRZT"
TRANSCRIPT_DIR = Path(__file__).resolve().parent / "evidence" / "transcripts"

TERMINAL_STATUSES = {"idle", "terminated"}


@dataclass
class Turn:
    """Flattened view of one session's native event stream."""

    session_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    def texts(self, event_type: str) -> list[str]:
        return [e["text"] for e in self.events if e["type"] == event_type and e["text"]]

    def assistant_text(self) -> str:
        return "\n\n".join(self.texts("agent.message"))

    def tool_calls(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] in {"agent.tool_use", "agent.mcp_tool_use"}]

    def event_types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def contains(self, needle: str) -> bool:
        return any(needle in json.dumps(e, default=str) for e in self.events)


def _flatten(event: Any) -> dict[str, Any]:
    payload = event.model_dump() if hasattr(event, "model_dump") else dict(event)
    text_chunks: list[str] = []
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    text_chunks.append(block["text"])
                elif isinstance(block.get("thinking"), str):
                    text_chunks.append(block["thinking"])
    elif isinstance(content, str):
        text_chunks.append(content)
    flat: dict[str, Any] = {
        "id": payload.get("id"),
        "type": payload.get("type"),
        "text": "\n".join(text_chunks),
    }
    for key in ("name", "input", "tool_use_id", "is_error", "usage", "reason", "status", "server_name"):
        if key in payload and payload[key] is not None:
            flat[key] = payload[key]
    if flat["type"] in {"agent.tool_result", "agent.mcp_tool_result"} and not flat["text"]:
        flat["text"] = json.dumps(payload.get("output") or payload.get("result") or "", default=str)[:4000]
    return flat


class SessionLab:
    def __init__(self, api: anthropic.Anthropic, *, environment_id: str = CLOUD_ENVIRONMENT_ID) -> None:
        self.api = api
        self.environment_id = environment_id
        # Event ids already reported for a session, so a follow-up turn carries only its
        # own events instead of replaying the whole session again.
        self._seen: dict[str, set[str]] = {}

    def create(
        self,
        *,
        agent_id: str,
        message: str,
        system: str | None = None,
        model: Any = "claude-sonnet-5",
        resources: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        budget_usd: str = "8",
        title: str = "clevin-swarm-E probe",
        metadata: dict[str, str] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> Any:
        agent: dict[str, Any] = {"type": "agent_with_overrides", "id": agent_id, "model": model}
        if system is not None:
            agent["system"] = system
        if tools is not None:
            agent["tools"] = tools
        initial: list[dict[str, Any]] = [
            {"type": "user.message", "content": [{"type": "text", "text": message}]}
        ]
        if outcome is not None:
            initial.append({"type": "user.define_outcome", **outcome})
        return self.api.beta.sessions.create(
            agent=agent,
            environment_id=self.environment_id,
            resources=resources or [],
            budget={"type": "limit", "max_list_cost": {"amount": budget_usd, "currency": "USD"}},
            title=title,
            metadata={"swarm_workstream": "E", **(metadata or {})},
            initial_events=initial,
        )

    def send(self, session_id: str, text: str) -> None:
        self.api.beta.sessions.events.send(
            session_id, events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}]
        )

    def interrupt(self, session_id: str) -> None:
        self.api.beta.sessions.events.send(session_id, events=[{"type": "user.interrupt"}])

    def drain(self, session_id: str, *, timeout: float = 900.0, quiet_after_idle: float = 6.0) -> Turn:
        """Replay + follow a session's events until it reaches a terminal status."""
        turn = Turn(session_id=session_id)
        seen = self._seen.setdefault(session_id, set())
        deadline = time.monotonic() + timeout
        idle_since: float | None = None
        while time.monotonic() < deadline:
            for event in self.api.beta.sessions.events.list(session_id, order="asc"):
                flat = _flatten(event)
                key = flat.get("id") or json.dumps(flat, default=str)
                if key in seen:
                    continue
                seen.add(key)
                turn.events.append(flat)
            status = self.api.beta.sessions.retrieve(session_id).status
            if status in TERMINAL_STATUSES:
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since >= quiet_after_idle:
                    turn.events.append({"id": None, "type": f"session.{status}", "text": ""})
                    return turn
            else:
                idle_since = None
            time.sleep(5.0)
        turn.events.append({"id": None, "type": "probe.timeout", "text": f"no terminal status within {timeout}s"})
        return turn

    def run(self, *, label: str, **create_kwargs: Any) -> tuple[Any, Turn]:
        session = self.create(**create_kwargs)
        turn = self.drain(session.id)
        self.save(label, session.id, turn)
        return session, turn

    def follow_up(self, session_id: str, text: str, *, label: str) -> Turn:
        self.send(session_id, text)
        time.sleep(4.0)
        turn = self.drain(session_id)
        self.save(label, session_id, turn)
        return turn

    @staticmethod
    def save(label: str, session_id: str, turn: Turn) -> Path:
        TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        path = TRANSCRIPT_DIR / f"{label}.json"
        path.write_text(
            json.dumps(
                {
                    "label": label,
                    "session_id": session_id,
                    "saved_at": datetime.now(UTC).isoformat(),
                    "events": turn.events,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
        return path

    def all_events(self, session_id: str) -> list[dict[str, Any]]:
        return [_flatten(e) for e in self.api.beta.sessions.events.list(session_id, order="asc")]

    def cost(self, session_id: str) -> dict[str, Any]:
        session = self.api.beta.sessions.retrieve(session_id)
        usage = session.usage.model_dump() if session.usage else {}
        return {"session_id": session_id, "status": session.status, "usage": usage}
