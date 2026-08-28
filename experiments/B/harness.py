"""Workstream B harness: long-horizon agent quality on native Managed Agents.

Every helper configures, drives, or observes a native primitive only:

* ``beta.environments.create``      -> the sandbox the work happens in (cloud env, warm packages)
* ``beta.agents.create``/versions   -> the per-arm agent configuration under test
* ``multiagent``/``skills``         -> subagent and Skill arms
* ``beta.sessions.create``          -> one session per arm, budget-limited
* ``beta.sessions.events``          -> steering (``user.message``/``user.interrupt``), replay, grading
* ``beta.memory_stores``            -> the memory-on/off arm
* ``session.usage`` / compaction events -> cost, token and compaction measurement

No orchestration, planning, memory or context layer is implemented here: this module
starts native sessions, reads native history, and scores the sandbox with the fixture's
own ``grade.py``. It is an experiment driver, not a Clevin product component.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bundle  # noqa: E402

ARTIFACTS = HERE / "artifacts"
RUN_PREFIX = "clevin-swarm-B"
WORKDIR = bundle.WORKDIR
ALWAYS_ALLOW = {"type": "always_allow"}
BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True, "permission_policy": ALWAYS_ALLOW},
    }
]
TERMINAL = {"idle", "terminated", "failed", "stopped", "completed", "expired"}


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=5)


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def text_of(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in event.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


# --------------------------------------------------------------------- environment
SHARED_CLOUD_ENV = "env_01F4KCNxYngRzYKG5a1QLRZT"


def ensure_environment(c: anthropic.Anthropic, ledger: "Ledger") -> str:
    """The account's existing cloud environment, unless ``B_ENV_ID`` overrides it.

    A freshly created temporary cloud environment (``packages.pip: [pytest]``)
    accepted sessions but never executed a single tool call -- sessions sat in
    ``running`` with only ``agent.thinking`` (sesn_011bF2YLtCVW7zq1hFf55q2K, 35
    min, $0 list cost). The workload is therefore stdlib-only and runs on the
    known-good shared cloud environment; the temp-env behaviour is reported as a
    finding rather than worked around.
    """
    del ledger
    return os.environ.get("B_ENV_ID") or SHARED_CLOUD_ENV


# ------------------------------------------------------------------------- ledger
@dataclass
class Ledger:
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

    def save(self) -> Path:
        path = ARTIFACTS / "cleanup-ledger.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(path.read_text()) if path.exists() else []
        known = {e["id"] for e in existing}
        existing.extend(e for e in self.entries if e["id"] not in known)
        for entry in self.entries:
            for other in existing:
                if other["id"] == entry["id"] and entry["cleanup"] is not None:
                    other["cleanup"] = entry["cleanup"]
        path.write_text(json.dumps(existing, indent=2))
        return path


# --------------------------------------------------------------------------- arm
@dataclass
class Arm:
    """One configuration of the same workload."""

    name: str
    system: str
    model: str = "claude-sonnet-5"
    multiagent: dict[str, Any] | None = None
    roster: dict[str, str] = field(default_factory=dict)
    memory_store_id: str | None = None
    memory_instructions: str | None = None
    max_list_cost: str = "60"
    seed_file_id: str | None = None
    nudge_limit: int = 3
    notes: str = ""


class Run:
    """Drives one arm: create session, supervise to green, score, record."""

    def __init__(self, arm: Arm, *, environment_id: str, ledger: Ledger) -> None:
        self.arm = arm
        self.client = client()
        self.environment_id = environment_id
        self.ledger = ledger
        self.run_id = f"{utc_stamp()}-{secrets.token_hex(3)}"
        self.dir = ARTIFACTS / arm.name / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session_id: str | None = None
        self.timeline: list[dict[str, Any]] = []
        self.started = time.time()

    # ------------------------------------------------------------- setup
    def create_roster(self) -> list[str]:
        """Worker agents for a ``multiagent`` coordinator roster (native subagents).

        ``multiagent.agents`` requires concrete agent IDs whose own ``multiagent`` is
        unset (depth limit 1), so each role is a real agent version, not a prompt.
        """
        ids: list[str] = []
        for role, system in self.arm.roster.items():
            worker = self.client.beta.agents.create(
                name=f"{RUN_PREFIX}-{self.arm.name}-{role}-{self.run_id}",
                model=self.arm.model,
                system=system,
                description=f"workstream B subagent role {role}",
                tools=BUILTIN_TOOLS,
                metadata={"experiment": "clevin-swarm-B", "arm": self.arm.name, "role": role},
            )
            self.ledger.record("agent", worker.id, worker.name)
            ids.append(worker.id)
        return ids

    def create_agent(self) -> Any:
        params: dict[str, Any] = {
            "name": f"{RUN_PREFIX}-{self.arm.name}-{self.run_id}",
            "model": self.arm.model,
            "system": self.arm.system,
            "description": f"workstream B arm {self.arm.name}",
            "tools": BUILTIN_TOOLS,
            "metadata": {"experiment": "clevin-swarm-B", "arm": self.arm.name},
        }
        if self.arm.multiagent is not None:
            params["multiagent"] = {**self.arm.multiagent, "agents": self.create_roster()}
        agent = self.client.beta.agents.create(**params)
        self.ledger.record("agent", agent.id, agent.name)
        return agent

    def create_session(self, agent_id: str, prompt: str) -> str:
        params: dict[str, Any] = {
            "agent": {"type": "agent", "id": agent_id},
            "environment_id": self.environment_id,
            "budget": {
                "type": "limit",
                "max_list_cost": {"amount": self.arm.max_list_cost, "currency": "USD"},
            },
            "metadata": {"experiment": "clevin-swarm-B", "arm": self.arm.name},
            "title": f"B/{self.arm.name}/{self.run_id}",
            "initial_events": [
                {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
            ],
        }
        resources: list[dict[str, Any]] = []
        if self.arm.seed_file_id:
            resources.append(
                {
                    "type": "file",
                    "file_id": self.arm.seed_file_id,
                    "mount_path": bundle.MOUNT,
                }
            )
        if self.arm.memory_store_id:
            memory: dict[str, Any] = {
                "type": "memory_store",
                "memory_store_id": self.arm.memory_store_id,
                "access": "read_write",
            }
            if self.arm.memory_instructions:
                memory["instructions"] = self.arm.memory_instructions
            resources.append(memory)
        if resources:
            params["resources"] = resources
        session = self.client.beta.sessions.create(**params)
        self.session_id = session.id
        self.ledger.record("session", session.id, params["title"])
        self.log("session_created", session_id=session.id)
        return session.id

    # ---------------------------------------------------------- interaction
    def log(self, kind: str, **fields: Any) -> None:
        self.timeline.append(
            {"t": round(time.time() - self.started, 1), "kind": kind, **fields}
        )
        print(f"[{self.arm.name} +{self.timeline[-1]['t']}s] {kind} {fields}", flush=True)

    def send(self, text: str, *, interrupt: bool = False) -> None:
        assert self.session_id
        events: list[dict[str, Any]] = []
        if interrupt:
            events.append({"type": "user.interrupt"})
        events.append({"type": "user.message", "content": [{"type": "text", "text": text}]})
        self.client.beta.sessions.events.send(self.session_id, events=events)
        self.log("sent", interrupt=interrupt, text=text[:120])
        self.wait_busy()

    def wait_busy(self, *, timeout_s: float = 120, poll_s: float = 5) -> bool:
        """Wait for a sent message to actually start a turn.

        ``sessions.events.send`` returns before the session leaves ``idle``, so a
        naive ``wait_idle`` right after a send returns the *previous* idle state.
        """
        assert self.session_id
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.client.beta.sessions.retrieve(self.session_id).status not in TERMINAL:
                return True
            time.sleep(poll_s)
        self.log("never_became_busy")
        return False

    def wait_idle(self, *, timeout_s: float = 3600, poll_s: float = 15) -> str:
        assert self.session_id
        deadline = time.time() + timeout_s
        last = ""
        while time.time() < deadline:
            session = self.client.beta.sessions.retrieve(self.session_id)
            if session.status != last:
                self.log("status", status=session.status)
                last = session.status
            if session.status in TERMINAL:
                return session.status
            time.sleep(poll_s)
        self.log("timeout")
        return "timeout"

    def stop_reason(self) -> dict[str, Any]:
        events = self.events()
        for event in reversed(events):
            if event.get("type") == "session.status_idle":
                return event.get("stop_reason") or {}
        return {}

    def events(self) -> list[dict[str, Any]]:
        assert self.session_id
        return [jsonable(e) for e in self.client.beta.sessions.events.list(self.session_id, order="asc")]

    # -------------------------------------------------------------- grading
    def grade(self) -> dict[str, Any] | None:
        """Read the newest grade.py JSON out of a native tool_result event.

        The verdict is never taken from the agent's prose: only the sandbox's own
        stdout, as recorded server-side, counts.
        """
        best: dict[str, Any] | None = None
        for event in self.events():
            if not str(event.get("type")).endswith("tool_result"):
                continue
            text = text_of(event)
            if '"workload": "acme-billing' not in text:
                continue
            for match in re.finditer(r'\{\s*\n\s*"workload": "acme-billing', text):
                chunk = text[match.start() :]
                for end in range(len(chunk), 0, -1):
                    if chunk[end - 1] != "}":
                        continue
                    try:
                        best = json.loads(chunk[:end])
                    except Exception:  # noqa: BLE001 - truncated output is a real outcome
                        continue
                    break
        return best

    def force_grade(self) -> dict[str, Any] | None:
        self.send(
            f"Run exactly this and reply with nothing but the word DONE:\n"
            f"cd {WORKDIR} && python3 grade.py"
        )
        self.wait_idle(timeout_s=900)
        return self.grade()

    # -------------------------------------------------------------- metrics
    def metrics(self) -> dict[str, Any]:
        events = self.events()
        counts: dict[str, int] = {}
        for event in events:
            counts[str(event.get("type"))] = counts.get(str(event.get("type")), 0) + 1
        usage = [e for e in events if str(e.get("type")).startswith("session.usage")]
        last_usage = jsonable(usage[-1]) if usage else {}
        tool_names = [
            str(e.get("name"))
            for e in events
            if str(e.get("type")).endswith("tool_use")
        ]
        errors = [
            text_of(e)[:300]
            for e in events
            if e.get("is_error") is True
        ]
        return {
            "event_counts": counts,
            "compactions": counts.get("agent.thread_context_compacted", 0),
            "tool_calls": len(tool_names),
            "tool_histogram": {n: tool_names.count(n) for n in sorted(set(tool_names))},
            "error_tool_results": len(errors),
            "error_samples": errors[:5],
            "usage": last_usage.get("usage", last_usage),
            "agent_message_count": counts.get("agent.message", 0),
        }

    def finish(self, extra: dict[str, Any] | None = None) -> Path:
        report = {
            "arm": self.arm.name,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "model": self.arm.model,
            "notes": self.arm.notes,
            "environment_id": self.environment_id,
            "elapsed_s": round(time.time() - self.started, 1),
            "timeline": self.timeline,
            "stop_reason": self.stop_reason(),
            "metrics": self.metrics(),
            "grade": self.grade(),
            **(extra or {}),
        }
        (self.dir / "report.json").write_text(json.dumps(report, indent=2))
        (self.dir / "events.json").write_text(json.dumps(self.events(), indent=2))
        self.ledger.save()
        return self.dir / "report.json"


def supervise(
    run: Run,
    *,
    nudge: str,
    timeout_s: float = 3600,
    on_idle: Callable[[Run, int], str | None] | None = None,
) -> dict[str, Any]:
    """Run to green with as few interventions as possible; count every one.

    ``nudges`` is the workstream's "human interventions" metric: 0 means the arm
    finished the workload unattended.
    """
    nudges = 0
    while True:
        status = run.wait_idle(timeout_s=timeout_s)
        stop = run.stop_reason()
        run.log("idle", status=status, stop_reason=stop.get("type"))
        if status == "timeout" or stop.get("type") in {"budget_reached", "error"}:
            return {"nudges": nudges, "outcome": stop.get("type") or status}
        grade = run.grade()
        if grade is None:
            grade = run.force_grade()
            run.log("forced_grade", found=grade is not None)
        if grade and grade.get("verdict") == "PASS":
            return {"nudges": nudges, "outcome": "pass"}
        custom = on_idle(run, nudges) if on_idle else None
        if nudges >= run.arm.nudge_limit:
            return {"nudges": nudges, "outcome": "nudge_limit"}
        nudges += 1
        run.send(custom or nudge)


def in_parallel(tasks: Sequence[tuple[str, Callable[[], Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    lock = threading.Lock()

    def wrap(label: str, fn: Callable[[], Any]) -> None:
        try:
            value = fn()
        except Exception as error:  # recorded, never hidden
            value = {"error": f"{type(error).__name__}: {error}"}
        with lock:
            out[label] = value

    threads = [threading.Thread(target=wrap, args=t) for t in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return out
