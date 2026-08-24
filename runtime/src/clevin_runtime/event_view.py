from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

_SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+\S+|sk-ant-[A-Za-z0-9_-]+|whsec_[A-Za-z0-9_+/=-]+|gh[pousr]_[A-Za-z0-9_]+|lin_api_[A-Za-z0-9_]+)"
)


@dataclass(frozen=True)
class RenderedEvent:
    title: str
    body: str = ""
    style: str = "dim"


@dataclass
class EventState:
    status: str = "unknown"
    stop_reason: str | None = None
    terminal: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] | None = None


def event_data(event: object) -> dict[str, Any]:
    if isinstance(event, dict):
        return dict(event)
    for method_name in ("model_dump", "to_dict"):
        method = getattr(event, method_name, None)
        if method is not None:
            try:
                value = method(mode="json")
            except TypeError:
                value = method()
            if isinstance(value, dict):
                return value
    event_type = getattr(event, "type", None)
    return {"type": event_type} if isinstance(event_type, str) else {}


def event_id(event: object) -> str | None:
    value = event_data(event).get("id")
    return value if isinstance(value, str) else None


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in {
        "secret",
        "token",
        "api_key",
        "authorization",
        "password",
    } or normalized.endswith(("_secret", "_token", "_api_key", "_password"))


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_sensitive_key(key) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_PATTERN.sub("[REDACTED]", value)
    return value


def _text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        data = event_data(block)
        for key in ("text", "thinking"):
            value = data.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
                break
    return "\n".join(parts)


def _json(value: object) -> str:
    return json.dumps(_redact(value), indent=2, sort_keys=True, default=str)


def _stop_reason(data: dict[str, Any]) -> str | None:
    value = data.get("stop_reason")
    if isinstance(value, dict):
        reason_type = value.get("type")
        return reason_type if isinstance(reason_type, str) else None
    return value if isinstance(value, str) else None


def reduce_event(event: object, state: EventState) -> list[RenderedEvent]:
    data = event_data(event)
    event_type = data.get("type")
    if not isinstance(event_type, str):
        return [RenderedEvent("event.malformed", _json(data), "yellow")]

    if event_type in {"event_start", "event_delta"}:
        return []

    if event_type in {
        "user.message",
        "agent.message",
        "agent.thinking",
        "system.message",
    }:
        body = _text(data.get("content"))
        style = "bright_blue" if event_type == "user.message" else "orange1"
        return [RenderedEvent(event_type, body or _json(data), style)]

    if event_type in {
        "agent.tool_use",
        "agent.mcp_tool_use",
        "agent.custom_tool_use",
        "agent.tool_result",
        "agent.mcp_tool_result",
        "agent.custom_tool_result",
        "user.tool_result",
    }:
        server = data.get("mcp_server_name")
        name = data.get("name") or "tool"
        qualified = f"{server}.{name}" if isinstance(server, str) else str(name)
        if event_type.endswith("_use"):
            return [
                RenderedEvent(
                    f"{event_type}: {qualified}", _json(data.get("input", {}))
                )
            ]
        body = _text(data.get("content")) or _json(data)
        style = "red" if data.get("is_error") else "dim"
        return [RenderedEvent(f"{event_type}: {qualified}", body, style)]

    if event_type.startswith("session.status_"):
        state.status = event_type.removeprefix("session.status_")
        state.stop_reason = _stop_reason(data)
        state.terminal = state.status == "terminated" or (
            state.status == "idle" and state.stop_reason != "requires_action"
        )
        suffix = f" ({state.stop_reason})" if state.stop_reason else ""
        style = "red" if state.status == "terminated" else "green"
        return [RenderedEvent(event_type, f"{state.status}{suffix}", style)]

    if event_type == "session.usage":
        usage = data.get("usage")
        state.usage = usage if isinstance(usage, dict) else {}
        budget = data.get("budget")
        state.budget = budget if isinstance(budget, dict) else None
        return [
            RenderedEvent(
                event_type,
                _json({"usage": state.usage, "budget": state.budget}),
                "cyan",
            )
        ]

    if event_type == "session.updated":
        budget = data.get("budget")
        if isinstance(budget, dict) or budget is None:
            state.budget = budget
        return [RenderedEvent(event_type, _json(data), "cyan")]

    if "error" in event_type or event_type.endswith(".failed"):
        return [RenderedEvent(event_type, _json(data), "red")]

    return [RenderedEvent(event_type, _json(data), "dim")]


def render_events(console: Console, events: list[RenderedEvent]) -> None:
    for rendered in events:
        console.print(f"[{rendered.style}]{rendered.title}[/{rendered.style}]")
        if rendered.body:
            console.print(rendered.body)
