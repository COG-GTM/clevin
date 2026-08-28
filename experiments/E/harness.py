"""Shared helpers for workstream E (native Memory Store) probes.

Primitive under test: Managed Agents Memory Stores (`client.beta.memory_stores`,
its `memories` and `memory_versions` subresources) and the
`resources[{type: "memory_store"}]` session attachment.

Every probe here only *observes* or *configures* those primitives; nothing in
this directory implements storage, retrieval, or ranking of its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
SWARM = "E"


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def temp_name(kind: str) -> str:
    return f"clevin-swarm-{SWARM}-{utc_stamp()}-{secrets.token_hex(3)}-{kind}"


def client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slug_for(name: str) -> str:
    """Mirror the documented mount-slug rule: lowercase, non-alphanumeric runs -> '-'."""
    out: list[str] = []
    previous_hyphen = False
    for char in name.lower():
        if char.isalnum():
            out.append(char)
            previous_hyphen = False
        elif not previous_hyphen:
            out.append("-")
            previous_hyphen = True
    return "".join(out).strip("-")


def summarize_error(error: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {"error_class": type(error).__name__, "message": str(error)[:600]}
    status = getattr(error, "status_code", None)
    if status is not None:
        payload["status_code"] = status
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        inner = body.get("error")
        if isinstance(inner, dict):
            payload["api_error_type"] = inner.get("type")
            payload["api_error_message"] = str(inner.get("message"))[:600]
    return payload


@dataclass
class Probe:
    """Collects ordered, JSON-serialisable observations plus a cleanup ledger."""

    name: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def record(self, label: str, **payload: Any) -> dict[str, Any]:
        entry = {"label": label, "at": datetime.now(UTC).isoformat(), **payload}
        self.observations.append(entry)
        print(f"[{self.name}] {label}: {json.dumps(payload, default=str)[:1400]}", flush=True)
        return entry

    def attempt(self, label: str, fn: Callable[[], Any], **extra: Any) -> Any:
        """Run `fn`, recording either its outcome marker or the API error verbatim."""
        try:
            value = fn()
        except Exception as error:  # noqa: BLE001 - the failure itself is the observation
            self.record(label, outcome="error", **summarize_error(error), **extra)
            return None
        self.record(label, outcome="ok", **extra)
        return value

    def expect_error(self, label: str, fn: Callable[[], Any], **extra: Any) -> dict[str, Any] | None:
        try:
            fn()
        except Exception as error:  # noqa: BLE001
            return self.record(label, outcome="rejected", **summarize_error(error), **extra)
        self.record(label, outcome="accepted", **extra)
        return None

    def add_cleanup(self, resource: str, action: str, result: str) -> None:
        self.cleanup.append(
            {"resource": resource, "action": action, "result": result, "at": datetime.now(UTC).isoformat()}
        )

    def write(self) -> Path:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        path = EVIDENCE_DIR / f"{self.name}.json"
        path.write_text(
            json.dumps(
                {
                    "probe": self.name,
                    "started_at": self.started_at,
                    "finished_at": datetime.now(UTC).isoformat(),
                    "observations": self.observations,
                    "cleanup": self.cleanup,
                },
                indent=2,
                default=str,
            )
            + "\n"
        )
        print(f"[{self.name}] evidence -> {path}", flush=True)
        return path


def memory_map(
    api: anthropic.Anthropic, store_id: str, *, path_prefix: str | None = None, full: bool = False
) -> dict[str, dict[str, Any]]:
    """Return {path: {id, sha256, size, content?}} for every memory under a prefix."""
    kwargs: dict[str, Any] = {"limit": 20 if full else 100}
    if path_prefix:
        kwargs["path_prefix"] = path_prefix
    if full:
        kwargs["view"] = "full"
    result: dict[str, dict[str, Any]] = {}
    for item in api.beta.memory_stores.memories.list(store_id, **kwargs):
        if getattr(item, "type", None) != "memory":
            continue
        entry = {
            "id": item.id,
            "sha256": item.content_sha256,
            "size": item.content_size_bytes,
            "memory_version_id": item.memory_version_id,
        }
        if full:
            entry["content"] = item.content
        result[item.path] = entry
    return result


def purge_store(api: anthropic.Anthropic, store_id: str) -> int:
    deleted = 0
    for path, entry in memory_map(api, store_id).items():
        try:
            api.beta.memory_stores.memories.delete(entry["id"], memory_store_id=store_id)
            deleted += 1
        except Exception as error:  # noqa: BLE001
            print(f"purge failed for {path}: {error}", flush=True)
    return deleted


def poll(
    predicate: Callable[[], bool], *, timeout: float, interval: float = 5.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def iter_text_events(events: Iterator[Any]) -> Iterator[tuple[str, str]]:
    """Yield (event_type, flattened_text) for events that carry text content."""
    for event in events:
        event_type = getattr(event, "type", "") or ""
        content = getattr(event, "content", None)
        chunks: list[str] = []
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
        elif isinstance(content, str):
            chunks.append(content)
        yield event_type, "\n".join(chunks)
