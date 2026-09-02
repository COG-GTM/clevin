"""Workstream F harness: drive native Managed Agents subagent (session thread) experiments.

Every helper here only configures, observes, or tests a native primitive:

* ``beta.agents.create``/``versions``      -> roster member and coordinator configuration
* ``multiagent={"type": "coordinator"}``   -> the subagent primitive under test
* ``beta.sessions.create``/``events``      -> session + SSE observation
* ``beta.sessions.threads``                -> per-child thread inspection / archive
* ``beta.environments``                    -> where child tools execute

Nothing here implements delegation, planning, or orchestration for the Clevin product:
the coordinator topology is entirely server-side. This module is an experiment driver.

Usage: import from an ``experiments/F/exp*.py`` driver. Requires ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CLOUD_ENV_ID = "env_01F4KCNxYngRzYKG5a1QLRZT"
SELF_HOSTED_ENV_ID_ENV = "CLEVIN_ENVIRONMENT_ID"
RUN_PREFIX = "clevin-swarm-F"

ALWAYS_ALLOW = {"type": "always_allow"}
BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True, "permission_policy": ALWAYS_ALLOW},
    }
]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=4)


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


@dataclass
class Ledger:
    """Cleanup ledger: every temporary Anthropic resource this run created."""

    run_id: str
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: str, resource_id: str, name: str) -> None:
        self.entries.append(
            {
                "kind": kind,
                "id": resource_id,
                "name": name,
                "created_at": datetime.now(UTC).isoformat(),
                "cleanup": None,
            }
        )

    def mark(self, resource_id: str, result: str) -> None:
        for entry in self.entries:
            if entry["id"] == resource_id:
                entry["cleanup"] = result


class Runner:
    """One experiment run: temp agents, sessions, artifacts, cleanup ledger."""

    def __init__(self, experiment: str, *, environment_id: str | None = None) -> None:
        self.experiment = experiment
        self.run_id = f"{utc_stamp()}-{secrets.token_hex(3)}"
        self.client = client()
        self.environment_id = environment_id or CLOUD_ENV_ID
        self.ledger = Ledger(run_id=self.run_id)
        self.results: dict[str, Any] = {
            "experiment": experiment,
            "run_id": self.run_id,
            "environment_id": self.environment_id,
            "started_at": datetime.now(UTC).isoformat(),
            "sessions": {},
            "observations": {},
        }
        self.dir = ARTIFACTS / experiment / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- agents
    def name_for(self, role: str) -> str:
        return f"{RUN_PREFIX}-{role}-{self.run_id}"

    def create_agent(
        self,
        role: str,
        *,
        system: str,
        model: str | dict[str, Any] = "claude-sonnet-5",
        tools: Sequence[dict[str, Any]] | None = None,
        description: str | None = None,
        multiagent: dict[str, Any] | None = None,
        skills: Sequence[dict[str, Any]] | None = None,
        mcp_servers: Sequence[dict[str, Any]] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "name": self.name_for(role),
            "model": model,
            "system": system,
            "description": description or f"workstream F {role}",
            "tools": list(tools if tools is not None else BUILTIN_TOOLS),
            "metadata": {"experiment": "clevin-swarm-F", "run_id": self.run_id, "role": role},
        }
        if multiagent is not None:
            params["multiagent"] = multiagent
        if skills is not None:
            params["skills"] = list(skills)
        if mcp_servers is not None:
            params["mcp_servers"] = list(mcp_servers)
        agent = self.client.beta.agents.create(**params)
        self.ledger.record("agent", agent.id, agent.name)
        return agent

    # -------------------------------------------------------------- sessions
    def create_session(
        self,
        *,
        agent_id: str,
        prompt: str,
        label: str,
        max_list_cost: str = "150",
        resources: Iterable[dict[str, Any]] | None = None,
        vault_ids: Sequence[str] | None = None,
        agent_version: int | None = None,
        environment_id: str | None = None,
    ) -> Any:
        agent_ref: dict[str, Any] = {"type": "agent", "id": agent_id}
        if agent_version is not None:
            agent_ref["version"] = agent_version
        params: dict[str, Any] = {
            "agent": agent_ref,
            "environment_id": environment_id or self.environment_id,
            "budget": {
                "type": "limit",
                "max_list_cost": {"amount": max_list_cost, "currency": "USD"},
            },
            "metadata": {
                "experiment": "clevin-swarm-F",
                "run_id": self.run_id,
                "label": label,
            },
            "title": f"F/{self.experiment}/{label}",
            "initial_events": [
                {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
            ],
        }
        if resources is not None:
            params["resources"] = list(resources)
        if vault_ids is not None:
            params["vault_ids"] = list(vault_ids)
        session = self.client.beta.sessions.create(**params)
        self.ledger.record("session", session.id, f"{self.experiment}/{label}")
        self.results["sessions"][label] = {
            "session_id": session.id,
            "agent_id": agent_id,
            "prompt": prompt,
        }
        return session

    # ------------------------------------------------------------ observation
    def wait(
        self,
        session_id: str,
        *,
        timeout_s: float,
        stop_when: Any = None,
        poll_s: float = 10.0,
    ) -> str:
        """Poll session status until terminal/idle, budget-stopped, or timeout.

        ``stop_when`` receives the event list on each poll and may return True to stop
        early (used for mid-run probes such as cancel-a-running-child).
        """
        deadline = time.monotonic() + timeout_s
        last = "unknown"
        while time.monotonic() < deadline:
            session = self.client.beta.sessions.retrieve(session_id)
            last = session.status
            if last in {"idle", "terminated", "failed", "stopped", "completed"}:
                return last
            if stop_when is not None:
                events = list(self.client.beta.sessions.events.list(session_id, order="asc"))
                if stop_when(events):
                    return f"{last}:stopped_by_probe"
            time.sleep(poll_s)
        return f"{last}:timeout"

    def collect(self, session_id: str) -> dict[str, Any]:
        """Snapshot everything native observability offers about a session."""
        session = self.client.beta.sessions.retrieve(session_id)
        events = [jsonable(e) for e in self.client.beta.sessions.events.list(session_id, order="asc")]
        threads: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            for thread in self.client.beta.sessions.threads.list(session_id):
                record = jsonable(thread)
                thread_id = record.get("id") or record.get("session_thread_id")
                thread_events: list[dict[str, Any]] = []
                if thread_id:
                    with contextlib.suppress(Exception):
                        thread_events = [
                            jsonable(e)
                            for e in self.client.beta.sessions.threads.events.list(
                                thread_id, session_id=session_id
                            )
                        ]
                record["events"] = thread_events
                threads.append(record)
        snapshot = {
            "session": jsonable(session),
            "events": events,
            "threads": threads,
            "summary": summarize(events, threads),
        }
        (self.dir / f"{session_id}.json").write_text(json.dumps(snapshot, indent=2))
        return snapshot

    # --------------------------------------------------------------- cleanup
    def cleanup(self) -> None:
        for entry in self.ledger.entries:
            if entry["cleanup"] is not None:
                continue
            try:
                if entry["kind"] == "agent":
                    self.client.beta.agents.archive(entry["id"])
                    entry["cleanup"] = "archived"
                elif entry["kind"] == "session":
                    # Sessions are the evidence for every claim in the findings file and
                    # are deliberately retained; they hold no compute once idle.
                    entry["cleanup"] = "retained as evidence (idle, no live resources)"
                else:
                    entry["cleanup"] = "no action required"
            except Exception as error:  # cleanup failures are reported, never hidden
                entry["cleanup"] = f"FAILED: {type(error).__name__}: {error}"

    def finish(self, *, cleanup: bool = True) -> Path:
        if cleanup:
            self.cleanup()
        self.results["finished_at"] = datetime.now(UTC).isoformat()
        self.results["cleanup_ledger"] = self.ledger.entries
        path = self.dir / "result.json"
        path.write_text(json.dumps(self.results, indent=2))
        return path

    def note(self, key: str, value: Any) -> None:
        self.results["observations"][key] = jsonable(value)


# ------------------------------------------------------------------ analysis
THREAD_EVENTS = {
    "session.thread_created",
    "session.thread_status_running",
    "session.thread_status_idle",
    "session.thread_status_terminated",
    "session.thread_status_rescheduled",
}


def summarize(events: list[dict[str, Any]], threads: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[str(event.get("type"))] += 1

    spawned = [
        {
            "session_thread_id": e.get("session_thread_id"),
            "agent_name": e.get("agent_name"),
            "processed_at": e.get("processed_at"),
        }
        for e in events
        if e.get("type") == "session.thread_created"
    ]
    messages = [
        {
            "type": e.get("type"),
            "to": e.get("to_agent_name") or e.get("to_session_thread_id"),
            "from": e.get("from_agent_name") or e.get("from_session_thread_id"),
            "text": text_of(e),
        }
        for e in events
        if e.get("type") in {"agent.thread_message_sent", "agent.thread_message_received"}
    ]
    tool_uses = [
        {"type": e.get("type"), "name": e.get("name") or e.get("tool_name")}
        for e in events
        if "tool_use" in str(e.get("type"))
    ]
    agent_text = [text_of(e) for e in events if e.get("type") == "agent.message"]
    usage = [e for e in events if str(e.get("type")).startswith("session.usage")]
    child_ids = {s["session_thread_id"] for s in spawned}
    stop_reasons = [
        {
            "type": e.get("type"),
            "thread": e.get("session_thread_id"),
            "agent": e.get("agent_name"),
            "stop_reason": (e.get("stop_reason") or {}).get("type"),
        }
        for e in events
        if str(e.get("type")).endswith("status_idle") or str(e.get("type")).endswith("status_terminated")
    ]
    per_thread = [
        {
            "id": t.get("id"),
            "agent": (t.get("agent") or {}).get("name"),
            "parent_thread_id": t.get("parent_thread_id"),
            "status": t.get("status"),
            "stats": t.get("stats"),
            "list_cost": ((t.get("usage") or {}).get("list_cost") or {}).get("amount"),
            "output_tokens": (t.get("usage") or {}).get("output_tokens"),
            "event_count": len(t.get("events") or []),
        }
        for t in threads
    ]

    def list_cost_of(event: dict[str, Any]) -> str | None:
        payload = event.get("usage") if isinstance(event.get("usage"), dict) else event
        amount = ((payload or {}).get("list_cost") or {}).get("amount")
        return str(amount) if amount is not None else None

    session_costs = [c for c in (list_cost_of(e) for e in usage) if c is not None]
    thread_costs = [float(t["list_cost"]) for t in per_thread if t.get("list_cost") is not None]
    return {
        "per_thread": per_thread,
        "session_list_cost": session_costs[-1] if session_costs else None,
        "thread_list_cost_total": sum(thread_costs) if thread_costs else None,
        "stop_reasons": stop_reasons,
        "event_counts": dict(sorted(counts.items())),
        "threads_spawned": spawned,
        "thread_count": len(spawned),
        "declared_threads": [t.get("id") for t in threads],
        "agent_messages": messages,
        "tool_uses": tool_uses,
        "parent_text_tail": agent_text[-3:],
        "concurrency": concurrency_profile(events, child_ids),
        "usage_events": usage[-2:],
        "compactions": counts.get("agent.thread_context_compacted", 0),
    }


def text_of(event: dict[str, Any]) -> str:
    blocks = event.get("content") or []
    parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return " ".join(parts)[:4000]


def concurrency_profile(events: list[dict[str, Any]], child_ids: set[Any]) -> dict[str, Any]:
    """Max simultaneously-running child threads, from native status events only.

    The session's primary thread is excluded: only threads announced by
    ``session.thread_created`` are children.
    """
    timeline: list[tuple[str, int, str]] = []
    for event in events:
        etype = str(event.get("type"))
        thread_id = event.get("session_thread_id")
        if thread_id not in child_ids:
            continue
        stamp = str(event.get("processed_at"))
        if etype == "session.thread_status_running":
            timeline.append((stamp, 1, str(thread_id)))
        elif etype in {"session.thread_status_idle", "session.thread_status_terminated"}:
            timeline.append((stamp, -1, str(thread_id)))
    timeline.sort()
    running = 0
    peak = 0
    for _, delta, _ in timeline:
        running += delta
        peak = max(peak, running)
    return {"peak_concurrent_children": peak, "transitions": len(timeline)}


def in_parallel(tasks: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    """Run labelled zero-arg callables concurrently; return label -> result/exception."""
    out: dict[str, Any] = {}
    lock = threading.Lock()

    def wrap(label: str, fn: Any) -> None:
        try:
            value = fn()
        except Exception as error:  # recorded, not raised: partial results still inform
            value = {"error": f"{type(error).__name__}: {error}"}
        with lock:
            out[label] = value

    workers = [threading.Thread(target=wrap, args=(label, fn)) for label, fn in tasks]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return out


@contextlib.contextmanager
def runner(experiment: str, *, environment_id: str | None = None, cleanup: bool = True) -> Iterator[Runner]:
    run = Runner(experiment, environment_id=environment_id)
    try:
        yield run
    finally:
        path = run.finish(cleanup=cleanup)
        print(f"\n[harness] artifacts: {path}")
        print(json.dumps(run.results.get("observations", {}), indent=2)[:6000])
