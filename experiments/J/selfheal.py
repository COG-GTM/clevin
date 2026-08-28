"""J-2 — is the Anthropic-side Managed Agents loop self-healing at all?

One question, three probes, all against native surfaces only. Nothing here
implements a watchdog, supervisor or re-dispatcher: the driver only *observes*
what the platform does on its own, and then exercises the two native triggers
(work-queue poll with reclaim, lifecycle webhook re-delivery) to see whether an
operator-free path exists.

Primitives observed / configured:
  * ``beta.environments.work.list|stats|retrieve``  — server-side work queue state
    machine (``queued|starting|active|stopping|stopped``), lease heartbeats
  * ``beta.environments.work.poll(reclaim_older_than_ms=…)`` — the only native
    re-offer mechanism for work whose worker died
  * ``session.status_run_started`` lifecycle webhook — the only native dispatch
    trigger, replayed after lease expiry (workstream C only replayed *within* the
    lease window)
  * ``beta.sessions`` / ``events`` — whether the session itself ever advances
    without an operator
  * a temporary Haiku agent + native bash toolset — the cheapest way to get one
    real in-flight tool call to strand

Probes:
  queue      (free) read the production environment's whole work-item history and
             extract the lease-expiry state machine from items siblings stranded
  retries    (free) scan existing session histories for platform-side retry
             evidence (``retry_status``) — what the loop *does* heal by itself
  strand     (~$1) create one cheap session, let the production webhook spawn a
             real worker, kill the Modal sandbox mid-command, then measure:
             detection latency, whether the item is re-offered to a reclaiming
             poll, and whether a post-expiry webhook replay re-dispatches it

Usage:
  uv run --project runtime python experiments/J/selfheal.py queue
  uv run --project runtime python experiments/J/selfheal.py retries
  uv run --project runtime python experiments/J/selfheal.py strand --budget 2
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import anthropic
import j_common as J

WORK_STATES = ("queued", "starting", "active", "stopping", "stopped")


def env_client() -> anthropic.Anthropic:
    """A client authenticated with the *environment* key (worker credentials)."""
    return anthropic.Anthropic(auth_token=J.env("ANTHROPIC_ENVIRONMENT_KEY"))


def now() -> str:
    return datetime.now(UTC).isoformat()


def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def gap(later: str | None, earlier: str | None) -> float | None:
    a, b = parse(later), parse(earlier)
    if a is None or b is None:
        return None
    return round((a - b).total_seconds(), 1)


def work_items(environment_id: str, limit: int = 100) -> list[dict[str, Any]]:
    client = env_client()
    return [
        J.jsonable(item)
        for item in client.beta.environments.work.list(environment_id, limit=limit)
    ]


# ------------------------------------------------------------------ probe: queue
def probe_queue(args: argparse.Namespace) -> int:
    """What the work queue's own history says about dead-worker handling."""
    environment_id = J.env("CLEVIN_ENVIRONMENT_ID")
    client = env_client()
    stats = J.jsonable(client.beta.environments.work.stats(environment_id))
    items = work_items(environment_id)

    rows = []
    for item in items:
        rows.append(
            {
                "work_id": item["id"],
                "session": (item.get("data") or {}).get("id"),
                "state": item["state"],
                "created_at": item["created_at"],
                "acknowledged_at": item.get("acknowledged_at"),
                "latest_heartbeat_at": item.get("latest_heartbeat_at"),
                "stopped_at": item.get("stopped_at"),
                "stop_requested_at": item.get("stop_requested_at"),
                "claim_delay_s": gap(item.get("acknowledged_at"), item["created_at"]),
                # The signature of a dead worker: the platform stops the item this
                # long after the last heartbeat it ever received.
                "stop_after_last_heartbeat_s": gap(
                    item.get("stopped_at"), item.get("latest_heartbeat_at")
                ),
                "lifetime_s": gap(item.get("stopped_at"), item["created_at"]),
            }
        )

    report = {
        "at": now(),
        "environment_id": environment_id,
        "stats": stats,
        "state_counts": {
            state: sum(1 for row in rows if row["state"] == state)
            for state in WORK_STATES
        },
        "total_items": len(rows),
        "items": rows,
        "one_item_per_session": len({row["session"] for row in rows}) == len(rows),
        "work_id_equals_session_id": all(
            row["work_id"] == row["session"] for row in rows
        ),
        "reclaimed_later": [
            row
            for row in rows
            if (row["claim_delay_s"] or 0) > 120  # late (re)claim
        ],
    }
    path = J.save("selfheal-queue.json", report)
    print(
        json.dumps({k: v for k, v in report.items() if k != "items"}, indent=2)[:3000]
    )
    print("evidence:", path, flush=True)
    return 0


# ---------------------------------------------------------------- probe: retries
def probe_retries(args: argparse.Namespace) -> int:
    """Platform-side retry evidence: what the loop heals without any operator."""
    findings: list[dict[str, Any]] = []
    for session_id in args.sessions:
        events = J.events(session_id)
        interesting = []
        for event in events:
            blob = json.dumps(event, default=str)
            if (
                "retry_status" in blob
                or "billing_error" in blob
                or event.get("type", "").endswith("_error")
                or "error" in str(event.get("type"))
            ):
                interesting.append(
                    {
                        "processed_at": event.get("processed_at"),
                        "type": event.get("type"),
                        "excerpt": blob[:900],
                    }
                )
        session = J.jsonable(J.client().beta.sessions.retrieve(session_id))
        findings.append(
            {
                "session_id": session_id,
                "status": session.get("status"),
                "events": len(events),
                "last_event": {
                    "type": events[-1].get("type") if events else None,
                    "processed_at": events[-1].get("processed_at") if events else None,
                },
                "seconds_since_last_event": gap(
                    now(), events[-1].get("processed_at") if events else None
                ),
                "last_idle_stop": J.last_idle_stop(events),
                "error_events": interesting,
                "usage": (session.get("usage") or {}).get("list_cost"),
            }
        )
    path = J.save("selfheal-retries.json", {"at": now(), "sessions": findings})
    for entry in findings:
        print(
            json.dumps(
                {k: v for k, v in entry.items() if k != "error_events"}, indent=2
            )
        )
        for error in entry["error_events"]:
            print("  ", error["processed_at"], error["type"], error["excerpt"][:400])
    print("evidence:", path, flush=True)
    return 0


# ----------------------------------------------------------------- probe: strand
STRAND_SYSTEM = """You are a throwaway probe agent for a reliability experiment.
Do exactly what the user message asks with the bash tool, nothing else.
Never touch git, GitHub, Linear, or anything outside /workspace.
"""

# 90 s: long enough to kill the worker mid-command, short enough to stay inside
# the worker's own ~120 s bash dispatch timeout (workstream C's C-1), so a strand
# here can only be caused by the kill and not by the tool timing out.
STRAND_MESSAGE = """Run exactly this one command and report its output:

bash -lc 'echo J2-STRAND-START; sleep 90; echo J2-STRAND-END'

Do not run any other command. If it fails, say so and stop."""


def kill_session_sandbox(session_id: str, *, mode: str) -> dict[str, Any]:
    """Kill the worker running the session, resolved by sandbox id.

    ``j_common.kill_sandbox`` matches on sandbox name/tags and matched nothing
    for this session (``terminated: []``), so the fault must be applied to the
    sandbox id the runtime itself reports.

    Two fault modes, because they are not the same experiment:

    * ``terminate`` — ``Sandbox.terminate``. The runtime creates sandboxes with
      ``enable_termination_grace_period``, so the worker gets to run its
      shutdown path: it flushes a partial tool result and releases the lease.
      This is an orderly worker exit, not a crash.
    * ``sigkill`` — ``kill -9 -1`` inside the sandbox, so the worker dies with a
      tool call in flight and no chance to report anything. This is the actual
      dead-worker case the self-healing question is about.
    """
    import asyncio

    async def kill() -> dict[str, Any]:
        import modal

        from clevin_runtime.sandbox_runtime import SandboxRuntime

        os.environ.setdefault("MODAL_ENVIRONMENT", "clevin")
        snapshot = await SandboxRuntime().snapshot(session_id)
        if not snapshot.sandbox_id:
            return {"error": "no sandbox id for session", "status": snapshot.status}
        sandbox = await modal.Sandbox.from_id.aio(snapshot.sandbox_id)
        if mode == "sigkill":
            process = await sandbox.exec.aio(
                "bash", "-lc", "kill -9 -1 || true", timeout=20
            )
            try:
                await process.wait.aio()
            except Exception:  # noqa: BLE001 - the sandbox dies under us
                pass
        else:
            await sandbox.terminate.aio()
        after = await SandboxRuntime().snapshot(session_id)
        return {
            "mode": mode,
            "sandbox_id": snapshot.sandbox_id,
            "status_before": snapshot.status,
            "status_after": after.status,
        }

    try:
        return asyncio.run(kill())
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def make_probe_agent(run: dict[str, Any], ledger: J.Ledger) -> str:
    agent = J.client().beta.agents.create(
        name=f"{J.RUN_PREFIX}2-strand-{run['run_id']}",
        description="throwaway agent for the J-2 dead-worker self-healing probe",
        model={"id": "claude-haiku-4-5"},  # cheap: one tool call is all we need
        system=STRAND_SYSTEM,
        tools=[J.AGENT_TOOLSET],
        mcp_servers=[],
        skills=[],
        multiagent=None,
        metadata={"experiment": "clevin-swarm-J2", "run_id": run["run_id"]},
    )
    ledger.record("agent", agent.id, agent.name)
    return agent.id


def wait_for(
    predicate: Any, *, timeout: float, poll: float = 10.0, label: str = ""
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            print(f"timeout waiting for {label}", flush=True)
            return None
        time.sleep(poll)


def item_for(environment_id: str, session_id: str) -> dict[str, Any] | None:
    for item in work_items(environment_id, limit=20):
        if (item.get("data") or {}).get("id") == session_id:
            return item
    return None


def probe_strand(args: argparse.Namespace) -> int:
    environment_id = J.env("CLEVIN_ENVIRONMENT_ID")
    run: dict[str, Any] = {
        "probe": "strand",
        "run_id": J.new_run_id(),
        "started_at": now(),
        "environment_id": environment_id,
        "timeline": [],
    }
    ledger = J.Ledger(run_id=run["run_id"])
    started = time.monotonic()

    def note(kind: str, **payload: Any) -> None:
        entry = {
            "at": now(),
            "elapsed_s": round(time.monotonic() - started, 1),
            "event": kind,
            **payload,
        }
        run["timeline"].append(entry)
        print(json.dumps(entry, default=str)[:1200], flush=True)

    try:
        agent_id = make_probe_agent(run, ledger)
        session = J.client().beta.sessions.create(
            agent={"type": "agent", "id": agent_id},
            environment_id=environment_id,
            budget={
                "type": "limit",
                "max_list_cost": {"amount": args.budget, "currency": "USD"},
            },
            title=f"{J.RUN_PREFIX}2-strand-{run['run_id']}",
            metadata={"experiment": "clevin-swarm-J2", "run_id": run["run_id"]},
            initial_events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": STRAND_MESSAGE}],
                }
            ],
        )
        ledger.record("session", session.id, "J2/strand")
        run["session_id"] = session.id
        note("session_created", session_id=session.id)

        # Phase 1: the production webhook spawns a Modal worker, which claims the
        # work item and starts the long command.
        active = wait_for(
            lambda: (item_for(environment_id, session.id) or {}).get("state")
            == "active"
            and (item_for(environment_id, session.id) or {}).get("latest_heartbeat_at"),
            timeout=args.spawn_timeout,
            label="work item active + heartbeating",
        )
        item = item_for(environment_id, session.id)
        note("work_active", item=item, modal=J.modal_state(session.id))
        if not active:
            note("aborted", reason="no worker ever claimed the item")
            return 1

        pending = wait_for(
            lambda: J.pending_tool_use(J.events(session.id)),
            timeout=args.tool_timeout,
            label="in-flight tool call",
        )
        note("tool_in_flight", pending=pending)

        # Phase 2: the fault — kill the worker while the command is running.
        killed = kill_session_sandbox(session.id, mode=args.kill_mode)
        run["killed"] = killed
        note("sandbox_killed", killed=killed)

        # Phase 3: does the platform notice on its own, and how fast?
        detect_started = time.monotonic()
        stopped_item = None
        while time.monotonic() - detect_started < args.detect_timeout:
            item = item_for(environment_id, session.id)
            state = (item or {}).get("state")
            note(
                "work_state",
                state=state,
                latest_heartbeat_at=(item or {}).get("latest_heartbeat_at"),
                stopped_at=(item or {}).get("stopped_at"),
                session_status=J.client().beta.sessions.retrieve(session.id).status,
                pending=bool(J.pending_tool_use(J.events(session.id))),
                queue=J.jsonable(
                    env_client().beta.environments.work.stats(environment_id)
                ),
            )
            if state == "stopped":
                stopped_item = item
                break
            time.sleep(args.poll)
        run["detection"] = {
            "detected": stopped_item is not None,
            "seconds_from_kill_to_stopped": round(time.monotonic() - detect_started, 1)
            if stopped_item
            else None,
            "stop_after_last_heartbeat_s": gap(
                (stopped_item or {}).get("stopped_at"),
                (stopped_item or {}).get("latest_heartbeat_at"),
            ),
            "item": stopped_item,
        }
        note("detection", **{k: v for k, v in run["detection"].items() if k != "item"})

        # Phase 4: is the abandoned work re-offered to a *reclaiming* poll? This is
        # the native mechanism a persistent worker fleet would rely on; workstream
        # C polled without `reclaim_older_than_ms`.
        client = env_client()
        offers: list[dict[str, Any]] = []
        reclaim_started = time.monotonic()
        while time.monotonic() - reclaim_started < args.reclaim_seconds:
            try:
                offered = client.beta.environments.work.poll(
                    environment_id,
                    block_ms=999,
                    reclaim_older_than_ms=args.reclaim_older_than_ms,
                    anthropic_worker_id=f"j2-probe-{run['run_id']}",
                )
            except anthropic.APIStatusError as error:
                offers.append({"error": error.status_code, "body": str(error)[:200]})
                break
            if offered is not None:
                record = J.jsonable(offered)
                mine = (record.get("data") or {}).get("id") == session.id
                offers.append(
                    {
                        "work_id": record["id"],
                        "session": (record.get("data") or {}).get("id"),
                        "state": record.get("state"),
                        "mine": mine,
                    }
                )
                note("work_offered", **offers[-1])
                # Foreign items are deliberately left un-ack'd so they fall back
                # to the production path; ours is not served either — the question
                # is only whether the platform *offers* it again.
                if mine:
                    break
            time.sleep(2.0)
        run["reclaim"] = {
            "reclaim_older_than_ms": args.reclaim_older_than_ms,
            "seconds_polled": round(time.monotonic() - reclaim_started, 1),
            "offers": offers,
            "own_item_reoffered": any(offer.get("mine") for offer in offers),
        }
        note("reclaim", **run["reclaim"])

        # Phase 5: the other native trigger — re-deliver the lifecycle webhook
        # *after* lease expiry, with the tool call still unanswered.
        before_events = len(J.events(session.id))
        replay = J.replay_webhook(session.id)
        note("webhook_replay", **replay)
        time.sleep(args.replay_wait)
        after = J.events(session.id)
        run["webhook_replay"] = {
            "response": replay,
            "events_before": before_events,
            "events_after": len(after),
            "still_pending": bool(J.pending_tool_use(after)),
            "session_status": J.client().beta.sessions.retrieve(session.id).status,
            "work_item": item_for(environment_id, session.id),
            "modal": J.modal_state(session.id),
        }
        note(
            "webhook_replay_result",
            **{k: v for k, v in run["webhook_replay"].items() if k != "work_item"},
        )

        events = J.events(session.id)
        run["conclusion"] = {
            "platform_detects_dead_worker": run["detection"]["detected"],
            "platform_reoffers_work": run["reclaim"]["own_item_reoffered"],
            "webhook_replay_redispatches": run["webhook_replay"]["events_after"]
            > run["webhook_replay"]["events_before"],
            "session_still_stranded": bool(J.pending_tool_use(events)),
            "last_idle_stop": J.last_idle_stop(events),
            "final_status": J.client().beta.sessions.retrieve(session.id).status,
            "final_usage": J.jsonable(
                J.client().beta.sessions.retrieve(session.id).usage
            ).get("list_cost"),
        }
        note("conclusion", **run["conclusion"])
    finally:
        for entry in ledger.entries:
            if entry["cleanup"] is not None:
                continue
            try:
                if entry["kind"] == "agent":
                    J.client().beta.agents.archive(entry["id"])
                    entry["cleanup"] = "archived"
                elif entry["kind"] == "session":
                    entry["cleanup"] = (
                        "retained as evidence (stranded, no live compute)"
                    )
            except Exception as error:  # noqa: BLE001 - never hide a cleanup failure
                entry["cleanup"] = f"FAILED: {type(error).__name__}: {error}"[:300]
        sandbox = (
            J.modal_state(run.get("session_id", "")) if run.get("session_id") else {}
        )
        run["final_modal_state"] = sandbox
        run["cleanup_ledger"] = ledger.entries
        run["finished_at"] = now()
        path = J.save(f"selfheal-strand-{run['run_id']}.json", run)
        print("evidence:", path, flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="probe", required=True)

    sub.add_parser("queue")

    retries = sub.add_parser("retries")
    retries.add_argument(
        "--sessions",
        nargs="+",
        default=["sesn_01EGsNu8uYt4SnS36Bk1JKvN"],
        help="sessions to scan for platform-side retry / error events",
    )

    strand = sub.add_parser("strand")
    strand.add_argument("--budget", default="2")
    strand.add_argument(
        "--kill-mode",
        choices=("sigkill", "terminate"),
        default="sigkill",
        help="sigkill: crash the worker with no shutdown path (the real "
        "dead-worker case). terminate: orderly Modal terminate, which the "
        "runtime's grace period turns into a clean worker exit.",
    )
    strand.add_argument("--spawn-timeout", type=float, default=300.0)
    strand.add_argument("--tool-timeout", type=float, default=300.0)
    strand.add_argument("--detect-timeout", type=float, default=900.0)
    strand.add_argument("--reclaim-seconds", type=float, default=240.0)
    strand.add_argument("--reclaim-older-than-ms", type=int, default=1000)
    strand.add_argument("--replay-wait", type=float, default=180.0)
    strand.add_argument("--poll", type=float, default=20.0)

    args = parser.parse_args()
    os.environ.setdefault("MODAL_ENVIRONMENT", "clevin")
    return {
        "queue": probe_queue,
        "retries": probe_retries,
        "strand": probe_strand,
    }[args.probe](args)


if __name__ == "__main__":
    raise SystemExit(main())
