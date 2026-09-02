"""Workstream I probes: what native observability can and cannot see.

Each profile creates the minimum live traffic needed to observe one native
surface, then reduces it with ``observe.collect`` (native session APIs only).

    census        offline: SDK-declared event / webhook / usage surface
    econ          cloud env, coordinator + subagent roster: per-session vs
                  per-thread list_cost, span tokens, prompt-cache fields
    budget        tiny budget: does the stop show up natively, and where
    selfhosted    production self-hosted env: session -> work item ->
                  Modal sandbox -> clevin-sessions volume -> tool result
    watch         sample native surfaces on a fixed interval while a session
                  runs: measures how fresh a native-only monitor can be

Every temporary resource is named ``clevin-swarm-I-<UTC stamp>-<short id>`` and
recorded in the run's cleanup ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

import observe

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CLOUD_ENV_ID = "env_01F4KCNxYngRzYKG5a1QLRZT"
RUN_PREFIX = "clevin-swarm-I"
ALWAYS_ALLOW = {"type": "always_allow"}
BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True, "permission_policy": ALWAYS_ALLOW},
    }
]
TERMINAL = {"idle", "terminated"}


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=4)


class Run:
    def __init__(self, profile: str) -> None:
        self.profile = profile
        self.run_id = f"{utc_stamp()}-{secrets.token_hex(3)}"
        self.client = client()
        self.dir = ARTIFACTS / profile / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ledger: list[dict[str, Any]] = []
        self.results: dict[str, Any] = {
            "profile": profile,
            "run_id": self.run_id,
            "started_at": datetime.now(UTC).isoformat(),
            "observations": {},
        }

    def name(self, role: str) -> str:
        return f"{RUN_PREFIX}-{role}-{self.run_id}"

    def record(self, kind: str, resource_id: str, name: str) -> None:
        self.ledger.append(
            {"kind": kind, "id": resource_id, "name": name, "cleanup": None}
        )

    def create_agent(self, role: str, **params: Any) -> Any:
        body: dict[str, Any] = {
            "name": self.name(role),
            "model": "claude-haiku-4-5",
            "tools": BUILTIN_TOOLS,
            "metadata": {"experiment": RUN_PREFIX, "run_id": self.run_id, "role": role},
        }
        body.update(params)
        agent = self.client.beta.agents.create(**body)
        self.record("agent", agent.id, agent.name)
        return agent

    def create_session(
        self,
        *,
        agent_id: str,
        prompt: str,
        label: str,
        environment_id: str,
        max_list_cost: str = "200",
        agent_version: int | None = None,
        resources: list[dict[str, Any]] | None = None,
    ) -> Any:
        agent_ref: dict[str, Any] = {"type": "agent", "id": agent_id}
        if agent_version is not None:
            agent_ref["version"] = agent_version
        body: dict[str, Any] = {
            "agent": agent_ref,
            "environment_id": environment_id,
            "budget": {
                "type": "limit",
                "max_list_cost": {"amount": max_list_cost, "currency": "USD"},
            },
            "metadata": {
                "experiment": RUN_PREFIX,
                "run_id": self.run_id,
                "label": label,
            },
            "title": f"I/{self.profile}/{label}",
            "initial_events": [
                {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
            ],
        }
        if resources is not None:
            body["resources"] = resources
        session = self.client.beta.sessions.create(**body)
        self.record("session", session.id, f"{self.profile}/{label}")
        return session

    def wait(self, session_id: str, timeout_s: float, poll_s: float = 10.0) -> str:
        deadline = time.monotonic() + timeout_s
        status = "unknown"
        while time.monotonic() < deadline:
            status = self.client.beta.sessions.retrieve(session_id).status
            if status in TERMINAL:
                return status
            time.sleep(poll_s)
        return f"{status}:timeout"

    def note(self, key: str, value: Any) -> None:
        self.results["observations"][key] = value

    def finish(self, *, archive_agents: bool = True) -> Path:
        for entry in self.ledger:
            if entry["cleanup"] is not None:
                continue
            if entry["kind"] == "agent" and archive_agents:
                try:
                    self.client.beta.agents.archive(entry["id"])
                    entry["cleanup"] = "archived"
                except Exception as exc:  # noqa: BLE001 - never hide cleanup failures
                    entry["cleanup"] = f"FAILED: {type(exc).__name__}: {exc}"
            elif entry["kind"] == "session":
                entry["cleanup"] = "retained as evidence (idle, no live compute)"
            else:
                entry["cleanup"] = "no action required"
        self.results["cleanup_ledger"] = self.ledger
        self.results["finished_at"] = datetime.now(UTC).isoformat()
        path = self.dir / "result.json"
        path.write_text(json.dumps(self.results, indent=2, default=str))
        return path


# --------------------------------------------------------------------------
# census: what the SDK itself declares as observable
# --------------------------------------------------------------------------
def profile_census(_: argparse.Namespace) -> None:
    import typing

    from anthropic.types.beta import beta_managed_agents_session_usage
    from anthropic.types.beta.sessions import beta_managed_agents_session_event as sev
    from anthropic.types.beta.sessions import beta_managed_agents_span_model_usage

    run = Run("census")

    def variants(alias: Any) -> list[str]:
        """Member `type` literals of a (possibly Annotated) discriminated union."""
        args = typing.get_args(alias)
        members = typing.get_args(args[0]) if len(args) > 1 else args
        out: list[str] = []
        for member in members:
            field = member.model_fields.get("type")
            literal = typing.get_args(field.annotation) if field else ()
            out.append(str(literal[0]) if literal else member.__name__)
        return sorted(out)

    event_types = variants(sev.BetaManagedAgentsSessionEvent)
    webhook_types: list[str] = []
    try:
        from anthropic.types.beta import beta_webhook_event_data as wed

        webhook_types = variants(wed.BetaWebhookEventData)
    except Exception as exc:  # noqa: BLE001 - absence is itself a finding
        run.note("webhook_union_error", str(exc))

    run.note("session_event_types", event_types)
    run.note("session_event_type_count", len(event_types))
    run.note("webhook_event_types", webhook_types)
    run.note(
        "session_usage_fields",
        sorted(
            beta_managed_agents_session_usage.BetaManagedAgentsSessionUsage.model_fields
        ),
    )
    run.note(
        "span_model_usage_fields",
        sorted(
            beta_managed_agents_span_model_usage.BetaManagedAgentsSpanModelUsage.model_fields
        ),
    )
    print(json.dumps(run.results["observations"], indent=2)[:4000])
    print(run.finish())


# --------------------------------------------------------------------------
# econ: session vs thread cost attribution, span tokens, cache fields
# --------------------------------------------------------------------------
ECON_WORKER_SYSTEM = (
    "You are a measurement subagent. Do exactly what the coordinator asks using "
    "the bash tool inside the sandbox. Never touch the network or any external "
    "system. Reply with one short paragraph."
)
ECON_COORDINATOR_SYSTEM = (
    "You are running a harmless local instrumentation check. Never touch the "
    "network, git, or any external system. Use the bash tool in the sandbox and "
    "delegate to your subagents when the task says to."
)
ECON_PROMPT = (
    "CLEVIN_SMOKE_TEST observability probe. Do exactly this, then stop:\n"
    "1. Run `mkdir -p /tmp/obs && date -u +%s > /tmp/obs/start` with bash.\n"
    "2. Delegate to BOTH of your subagents in parallel: ask one to run "
    "`uname -a` and report the kernel string, and the other to run "
    "`nproc && free -m | head -2` and report cpu/memory.\n"
    "3. Run `printf 'obs-marker %s\\n' \"$(date -u +%FT%TZ)\" > "
    "/tmp/obs/marker.txt && cat /tmp/obs/marker.txt` with bash.\n"
    "4. Reply with a three-line summary: kernel, cpu/memory, marker contents.\n"
    "Do not do anything else."
)


def profile_econ(args: argparse.Namespace) -> None:
    run = Run("econ")
    workers = [
        run.create_agent(
            role,
            system=ECON_WORKER_SYSTEM,
            description=f"workstream I economics probe {role}",
        )
        for role in ("probe-a", "probe-b")
    ]
    coordinator = run.create_agent(
        "coordinator",
        system=ECON_COORDINATOR_SYSTEM,
        description="workstream I economics coordinator",
        multiagent={"type": "coordinator", "agents": [a.id for a in workers]},
    )
    session = run.create_session(
        agent_id=coordinator.id,
        prompt=ECON_PROMPT,
        label="coordinator",
        environment_id=CLOUD_ENV_ID,
        max_list_cost=args.budget,
    )
    print(f"session {session.id}")
    status = run.wait(session.id, args.timeout)
    run.note("final_status", status)
    report = observe.collect(session.id, sandbox=False, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    run.note("session_id", session.id)
    run.note("economics", report["economics"])
    run.note("threads", report["threads"])
    print(run.finish())


# --------------------------------------------------------------------------
# budget: is the stop natively visible, and how
# --------------------------------------------------------------------------
BUDGET_PROMPT = (
    "CLEVIN_SMOKE_TEST budget probe. Using bash only inside the sandbox, and "
    "without touching the network, count from 1 to 400 by running one `echo` "
    "per number as a separate bash call, printing a one-sentence comment about "
    "each number as you go. Never stop early."
)


def profile_budget(args: argparse.Namespace) -> None:
    run = Run("budget")
    agent = run.create_agent(
        "budget",
        system=(
            "You are a harmless local load generator for a cost-accounting probe. "
            "Use only the bash tool inside the sandbox; never touch the network."
        ),
        description="workstream I budget probe",
    )
    session = run.create_session(
        agent_id=agent.id,
        prompt=BUDGET_PROMPT,
        label="tiny-budget",
        environment_id=CLOUD_ENV_ID,
        max_list_cost=args.budget,
    )
    print(f"session {session.id} budget_minor={args.budget}")
    status = run.wait(session.id, args.timeout, poll_s=5.0)
    run.note("final_status", status)
    report = observe.collect(session.id, sandbox=False, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    run.note("session_id", session.id)
    run.note("economics", report["economics"])
    run.note("usage_snapshots", report["usage_snapshots"])
    run.note("errors", report["errors"])
    print(run.finish())


# --------------------------------------------------------------------------
# selfhosted: full chain through the EnvironmentWorker and Modal
# --------------------------------------------------------------------------
SELFHOSTED_PROMPT = (
    "CLEVIN_SMOKE_TEST observability chain probe. Do exactly this, then stop:\n"
    "1. `pwd && ls -la` in the workspace.\n"
    "2. Write a file `obs-marker-{run}.txt` in the current working directory "
    "containing the current UTC timestamp and the text `workstream-I`.\n"
    "3. `cat` the file back and `stat` it.\n"
    "4. Deliberately run one failing command: `cat /nonexistent-obs-probe`.\n"
    "5. Reply with the file path, its contents, and what the failing command "
    "printed.\n"
    "Do not use git, do not touch the network, do not change anything else."
)


def profile_selfhosted(args: argparse.Namespace) -> None:
    run = Run("selfhosted")
    agent_id = os.environ["CLEVIN_AGENT_ID"]
    environment_id = os.environ["CLEVIN_ENVIRONMENT_ID"]
    session = run.create_session(
        agent_id=agent_id,
        prompt=SELFHOSTED_PROMPT.replace("{run}", run.run_id),
        label="chain",
        environment_id=environment_id,
        max_list_cost=args.budget,
    )
    print(f"session {session.id}")
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + args.timeout
    status = "unknown"
    while time.monotonic() < deadline:
        session_now = run.client.beta.sessions.retrieve(session.id)
        status = session_now.status
        try:
            items, stats = observe._collect_work(run.client, environment_id, session.id)
        except Exception as exc:  # noqa: BLE001 - record and keep sampling
            items, stats = [], {"error": str(exc)}
        samples.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "status": status,
                "work_items": items,
                "work_stats": stats,
            }
        )
        if status in TERMINAL:
            break
        time.sleep(args.poll)
    run.note("final_status", status)
    run.note("work_samples", samples)
    report = observe.collect(session.id, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    run.note("session_id", session.id)
    run.note("economics", report["economics"])
    run.note("work_items", report["work_items"])
    run.note("sandbox", report["sandbox"])
    print(run.finish(archive_agents=False))


# --------------------------------------------------------------------------
# watch: freshness of a native-only monitor
# --------------------------------------------------------------------------
def profile_watch(args: argparse.Namespace) -> None:
    """Poll only native surfaces and record what each sample could have known."""
    run = Run("watch")
    session_id = args.session
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + args.timeout
    seen: set[str] = set()
    while time.monotonic() < deadline:
        t0 = time.monotonic()
        session = run.client.beta.sessions.retrieve(session_id)
        events = [
            observe._dump(e)
            for e in run.client.beta.sessions.events.list(session_id, order="asc")
        ]
        new = [e for e in events if str(e.get("id")) not in seen]
        seen.update(str(e.get("id")) for e in events)
        usage = observe._dump(session.usage) or {}
        samples.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "api_calls": 2,
                "wall_s": round(time.monotonic() - t0, 3),
                "status": session.status,
                "list_cost_usd": observe._cost(usage.get("list_cost")),
                "new_event_types": sorted({str(e.get("type")) for e in new}),
                "new_events": len(new),
                "newest_event_lag_s": (
                    round(
                        datetime.now(UTC).timestamp()
                        - (observe._ts(events[-1].get("processed_at")) or 0),
                        3,
                    )
                    if events
                    else None
                ),
            }
        )
        print(json.dumps(samples[-1]))
        if session.status in TERMINAL:
            break
        time.sleep(args.poll)
    run.note("session_id", session_id)
    run.note("samples", samples)
    print(run.finish())


def profile_sse(args: argparse.Namespace) -> None:
    """Compare native SSE freshness against native polling on one live session.

    Both consumers are native: `sessions.events.stream` and
    `sessions.events.list`. The measurement is per-event arrival lag relative to
    the server-assigned `processed_at`, which is the ceiling on how quickly any
    Managed-Agents-only monitor can react.
    """
    import threading

    run = Run("sse")
    session = run.create_session(
        agent_id=os.environ["CLEVIN_AGENT_ID"],
        prompt=(
            "CLEVIN_SMOKE_TEST freshness probe. Using bash only, no network and "
            "no git: run five separate commands, each `sleep 4 && date -u`, "
            "printing each result, then reply 'done'. Nothing else."
        ),
        label="sse-freshness",
        environment_id=os.environ["CLEVIN_ENVIRONMENT_ID"],
        max_list_cost=args.budget,
    )
    print(f"session {session.id}")
    sse: dict[str, dict[str, Any]] = {}
    stop = threading.Event()

    def consume() -> None:
        try:
            with run.client.beta.sessions.events.stream(session.id) as stream:
                for event in stream:
                    data = observe._dump(event)
                    inner = data.get("event") if isinstance(data, dict) else None
                    payload = inner if isinstance(inner, dict) else data
                    eid = str(payload.get("id"))
                    if eid in sse:
                        continue
                    sse[eid] = {
                        "type": payload.get("type"),
                        "processed_at": payload.get("processed_at"),
                        "received_at": datetime.now(UTC).timestamp(),
                    }
                    if stop.is_set():
                        break
        except Exception as exc:  # noqa: BLE001 - stream end/error is data
            run.note("sse_stream_error", f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()

    polled: dict[str, dict[str, Any]] = {}
    poll_calls = 0
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = run.client.beta.sessions.retrieve(session.id).status
        poll_calls += 1
        for event in run.client.beta.sessions.events.list(session.id, order="asc"):
            data = observe._dump(event)
            eid = str(data.get("id"))
            polled.setdefault(
                eid,
                {
                    "type": data.get("type"),
                    "processed_at": data.get("processed_at"),
                    "received_at": datetime.now(UTC).timestamp(),
                },
            )
        poll_calls += 1
        if status in TERMINAL and not stop.is_set():
            stop.set()
            break
        time.sleep(args.poll)
    stop.set()
    thread.join(timeout=10)

    def lags(source: dict[str, dict[str, Any]]) -> dict[str, Any]:
        values = [
            round(v["received_at"] - ts, 3)
            for v in source.values()
            if (ts := observe._ts(v.get("processed_at"))) is not None
        ]
        values.sort()
        return {
            "events": len(source),
            "measured": len(values),
            "min_s": values[0] if values else None,
            "median_s": values[len(values) // 2] if values else None,
            "max_s": values[-1] if values else None,
        }

    both = set(sse) & set(polled)
    advantage = sorted(
        round(polled[e]["received_at"] - sse[e]["received_at"], 3) for e in both
    )
    run.note("session_id", session.id)
    run.note("sse_lag", lags(sse))
    run.note("poll_lag", lags(polled))
    run.note("poll_interval_s", args.poll)
    run.note("poll_api_calls", poll_calls)
    run.note("events_seen_by_sse_only", sorted(set(sse) - set(polled)))
    run.note("events_seen_by_poll_only", sorted(set(polled) - set(sse)))
    run.note(
        "sse_advantage_s",
        {
            "n": len(advantage),
            "min": advantage[0] if advantage else None,
            "median": advantage[len(advantage) // 2] if advantage else None,
            "max": advantage[-1] if advantage else None,
        },
    )
    report = observe.collect(session.id, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    print(json.dumps(run.results["observations"]["sse_lag"], indent=2))
    print(json.dumps(run.results["observations"]["poll_lag"], indent=2))
    print(json.dumps(run.results["observations"]["sse_advantage_s"], indent=2))
    print(run.finish(archive_agents=False))


def profile_budget_turns(args: argparse.Namespace) -> None:
    """Drip small turns into a tiny-budget session: when does the budget bite?

    The single-turn `budget` profile overshot its cap inside one model request.
    This one measures the multi-turn case: the turn on which work stops, the
    overshoot ratio, and whether any native event announces the stop.
    """
    run = Run("budget_turns")
    agent = run.create_agent(
        "budgetturns",
        system="Answer in one short sentence. Never use tools.",
        tools=[],
        description="workstream I budget-enforcement probe",
    )
    session = run.create_session(
        agent_id=agent.id,
        prompt="CLEVIN_SMOKE_TEST budget probe turn 0. Reply 'ok 0'.",
        label="budget-turns",
        environment_id=CLOUD_ENV_ID,
        max_list_cost=args.budget,
    )
    print(f"session {session.id} budget_minor={args.budget}")
    turns: list[dict[str, Any]] = []
    for n in range(1, args.turns + 1):
        send_error: str | None = None
        if n > 1:
            try:
                run.client.beta.sessions.events.send(
                    session.id,
                    events=[
                        {
                            "type": "user.message",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"CLEVIN_SMOKE_TEST budget probe turn {n}. "
                                        f"Reply 'ok {n}'."
                                    ),
                                }
                            ],
                        }
                    ],
                )
            except anthropic.APIStatusError as exc:
                send_error = f"{exc.status_code}: {exc.message}"
        status = (
            run.wait(session.id, 300, poll_s=3.0)
            if send_error is None
            else run.client.beta.sessions.retrieve(session.id).status
        )
        report = observe.collect(
            session.id, sandbox=False, work=False, thread_events=False
        )
        turns.append(
            {
                "turn": n,
                "send_error": send_error,
                "status": status,
                "list_cost_usd": report["economics"]["session_list_cost_usd"],
                "budget_usd": report["economics"]["budget_usd"],
                "spans": len(report["model_spans"]),
                "event_counts": report["event_counts"],
                "errors": report["errors"],
            }
        )
        print(json.dumps({k: v for k, v in turns[-1].items() if k != "event_counts"}))
        if send_error or status in {"terminated", "archived"}:
            break
    final = observe.collect(session.id, sandbox=False, work=False, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(final, indent=2, default=str)
    )
    print(observe.summarize(final))
    cost = final["economics"]["session_list_cost_usd"] or 0.0
    budget = final["economics"]["budget_usd"] or 0.0
    run.note("session_id", session.id)
    run.note("turns", turns)
    run.note("final_status", final["correlation_keys"]["status"])
    run.note("budget_usd", budget)
    run.note("final_list_cost_usd", cost)
    run.note("overshoot_ratio", round(cost / budget, 3) if budget else None)
    run.note("event_counts", final["event_counts"])
    run.note("errors", final["errors"])
    print(run.finish())


# --------------------------------------------------------------------------
# compaction: what a compaction event tells you, and what it does not
# --------------------------------------------------------------------------
COMPACTION_SYSTEM = (
    "You are a context-pressure probe. Answer each message in at most two "
    "sentences. Never use tools, never touch the network."
)
FILLER = (
    "CLEVIN_SMOKE_TEST context filler turn {n}. Remember token ALPHA-{n}. "
    "Reference material follows; reply only 'ack {n}'.\n"
)


def profile_compaction(args: argparse.Namespace) -> None:
    run = Run("compaction")
    agent = run.create_agent(
        "compaction",
        system=COMPACTION_SYSTEM,
        tools=[],
        description="workstream I compaction observability probe",
    )
    session = run.create_session(
        agent_id=agent.id,
        prompt="CLEVIN_SMOKE_TEST compaction probe. Reply 'ready'.",
        label="compaction",
        environment_id=CLOUD_ENV_ID,
        max_list_cost=args.budget,
    )
    print(f"session {session.id}")
    run.wait(session.id, 300, poll_s=5.0)
    filler = "x" * args.filler_bytes
    turns: list[dict[str, Any]] = []
    for n in range(1, args.turns + 1):
        run.client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": FILLER.format(n=n) + filler}],
                }
            ],
        )
        status = run.wait(session.id, 600, poll_s=5.0)
        report = observe.collect(
            session.id, sandbox=False, work=False, thread_events=False
        )
        turns.append(
            {
                "turn": n,
                "status": status,
                "compactions": len(report["compactions"]),
                "spans": len(report["model_spans"]),
                "last_span": report["model_spans"][-1]
                if report["model_spans"]
                else None,
                "list_cost_usd": report["economics"]["session_list_cost_usd"],
            }
        )
        print(json.dumps(turns[-1], default=str))
        if turns[-1]["compactions"] >= args.stop_after_compactions:
            break
    # After compaction, ask for a fact from the earliest turn: does native
    # history still show what the model can and cannot recall?
    run.client.beta.sessions.events.send(
        session.id,
        events=[
            {
                "type": "user.message",
                "content": [
                    {
                        "type": "text",
                        "text": "Which ALPHA tokens do you still remember? List them.",
                    }
                ],
            }
        ],
    )
    run.wait(session.id, 600, poll_s=5.0)
    report = observe.collect(session.id, sandbox=False, work=False, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    run.note("session_id", session.id)
    run.note("turns", turns)
    run.note("compactions", report["compactions"])
    run.note("economics", report["economics"])
    print(run.finish())


# --------------------------------------------------------------------------
# terminate: how a stopped session looks on the execution plane
# --------------------------------------------------------------------------
def profile_terminate(args: argparse.Namespace) -> None:
    """Interrupt + stop a live self-hosted session, sampling the native work item.

    There is no `sessions.terminate`; the native stop levers are the
    `user.interrupt` session event and `environments.work.stop`. This measures
    what each of them makes visible, and how long visibility lags.
    """
    run = Run("terminate")
    agent_id = os.environ["CLEVIN_AGENT_ID"]
    environment_id = os.environ["CLEVIN_ENVIRONMENT_ID"]
    session = run.create_session(
        agent_id=agent_id,
        prompt=(
            "CLEVIN_SMOKE_TEST long local probe. Using bash only, and without "
            "touching the network or git, run `sleep 20 && date -u` twenty "
            "times in a row as separate tool calls, printing the date each "
            "time. Do nothing else."
        ),
        label="terminate",
        environment_id=environment_id,
        max_list_cost=args.budget,
    )
    print(f"session {session.id}")
    samples: list[dict[str, Any]] = []

    def sample(tag: str) -> None:
        status = run.client.beta.sessions.retrieve(session.id).status
        items, stats = observe._collect_work(run.client, environment_id, session.id)
        samples.append(
            {
                "tag": tag,
                "at": datetime.now(UTC).isoformat(),
                "session_status": status,
                "work_items": items,
                "work_stats": stats,
            }
        )
        print(json.dumps(samples[-1], default=str))

    time.sleep(args.warmup)
    sample("before-stop")
    run.client.beta.sessions.events.send(
        session.id, events=[{"type": "user.interrupt"}]
    )
    time.sleep(3.0)
    sample("after-user.interrupt")
    work = observe.work_client(run.client)
    stopped = observe._dump(
        work.beta.environments.work.stop(session.id, environment_id=environment_id)
    )
    run.note("work_stop_response", stopped)
    for tag, delay in (("t+2s", 2.0), ("t+15s", 13.0), ("t+60s", 45.0)):
        time.sleep(delay)
        sample(tag)
    report = observe.collect(session.id, raw_events=True)
    (run.dir / f"{session.id}.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    print(observe.summarize(report))
    run.note("session_id", session.id)
    run.note("samples", samples)
    run.note("final_report_work_items", report["work_items"])
    print(run.finish(archive_agents=False))


# --------------------------------------------------------------------------
# fleet: can cost be rolled up per agent version without an Admin API?
# --------------------------------------------------------------------------
def profile_fleet(args: argparse.Namespace) -> None:
    run = Run("fleet")
    calls = 0
    rows: list[dict[str, Any]] = []
    t0 = time.monotonic()
    for session in run.client.beta.sessions.list(
        limit=100, order="desc", include_archived=True
    ):
        data = observe._dump(session)
        usage = data.get("usage") or {}
        agent = data.get("agent") or {}
        rows.append(
            {
                "session_id": data.get("id"),
                "created_at": data.get("created_at"),
                "agent_id": agent.get("id"),
                "agent_version": agent.get("version"),
                "status": data.get("status"),
                "metadata": data.get("metadata"),
                "list_cost_usd": observe._cost(usage.get("list_cost")),
                "active_seconds": usage.get("active_seconds"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                "deployment_id": data.get("deployment_id"),
            }
        )
        if len(rows) >= args.limit:
            break
    calls = 1 + len(rows) // 100
    by_version: dict[str, dict[str, Any]] = {}
    by_experiment: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key, bucket in (
            (f"{row['agent_id']}@v{row['agent_version']}", by_version),
            (str((row["metadata"] or {}).get("experiment")), by_experiment),
        ):
            entry = bucket.setdefault(key, {"sessions": 0, "list_cost_usd": 0.0})
            entry["sessions"] += 1
            entry["list_cost_usd"] = round(
                entry["list_cost_usd"] + (row["list_cost_usd"] or 0.0), 6
            )
    run.note("rows", rows)
    run.note("session_count", len(rows))
    run.note("api_calls", calls)
    run.note("wall_seconds", round(time.monotonic() - t0, 2))
    run.note("cost_by_agent_version", by_version)
    run.note("cost_by_experiment_metadata", by_experiment)
    run.note(
        "total_list_cost_usd",
        round(sum(r["list_cost_usd"] or 0.0 for r in rows), 6),
    )
    print(json.dumps(run.results["observations"]["cost_by_agent_version"], indent=2))
    print(
        json.dumps(run.results["observations"]["cost_by_experiment_metadata"], indent=2)
    )
    print(
        f"sessions={len(rows)} api_calls={calls} "
        f"total=${run.results['observations']['total_list_cost_usd']}"
    )
    print(run.finish())


PROFILES = {
    "sse": profile_sse,
    "census": profile_census,
    "budget_turns": profile_budget_turns,
    "compaction": profile_compaction,
    "terminate": profile_terminate,
    "fleet": profile_fleet,
    "econ": profile_econ,
    "budget": profile_budget,
    "selfhosted": profile_selfhosted,
    "watch": profile_watch,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("--budget", default="200", help="max_list_cost minor units")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--poll", type=float, default=10.0)
    parser.add_argument("--session", default=None, help="watch profile: session id")
    parser.add_argument(
        "--turns", type=int, default=40, help="compaction: filler turns"
    )
    parser.add_argument("--filler-bytes", type=int, default=90_000)
    parser.add_argument("--stop-after-compactions", type=int, default=2)
    parser.add_argument("--warmup", type=float, default=45.0, help="terminate profile")
    parser.add_argument("--limit", type=int, default=500, help="fleet: max sessions")
    args = parser.parse_args()
    PROFILES[args.profile](args)


if __name__ == "__main__":
    main()
