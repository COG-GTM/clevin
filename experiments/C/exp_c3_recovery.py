"""Recovery paths for a session stranded by a dead worker (workstream C).

Primitive under test: self-hosted ``EnvironmentWorker`` work queue + the native
``SessionToolRunner``. ``exp`` C3 leaves a session in
``session.status_idle / requires_action`` with an unanswered ``agent.tool_use``
because the worker process died mid-dispatch. This driver asks, in order:

1. does the queue re-enqueue the work item on its own (native re-dispatch)?
2. does replaying the signed ``session.status_run_started`` webhook to the
   production Modal handler recover it?
3. does attaching a bare ``SessionToolRunner`` (no work item, no lease) recover
   it -- i.e. is reconciliation alone enough?

Usage:
    uv run --project ../../runtime python exp_c3_recovery.py --session sesn_... \
        [--skip-poll] [--skip-webhook]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

import anthropic
import chaos

log = logging.getLogger("c3recovery")

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"


def pending_tool_use(session_id: str) -> dict[str, object] | None:
    """Return the tool_use event that still has no matching tool_result."""
    c = chaos.client()
    events = [
        e.model_dump(mode="json")
        for e in c.beta.sessions.events.list(session_id, order="asc")
    ]
    answered = {
        e.get("tool_use_id")
        for e in events
        if e.get("type") in ("user.tool_result", "user.custom_tool_result")
    }
    for e in events:
        if (
            e.get("type") in ("agent.tool_use", "agent.custom_tool_use")
            and e.get("id") not in answered
        ):
            return e
    return None


async def phase_poll(seconds: float) -> dict[str, object]:
    """Poll the shared environment queue: is the stranded item re-enqueued?"""
    key = chaos.env("ANTHROPIC_ENVIRONMENT_KEY")
    environment_id = chaos.env("CLEVIN_ENVIRONMENT_ID")
    seen: list[str] = []
    async with anthropic.AsyncAnthropic(auth_token=key) as c:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            item = await c.beta.environments.work.poll(environment_id, block_ms=999)
            if item is not None:
                seen.append(str(getattr(item.data, "id", item.id)))
                # Do not ack: leave foreign work for the production handler.
            await asyncio.sleep(0.3)
    return {"polled_seconds": seconds, "items_seen": seen}


async def phase_runner(
    session_id: str, workdir: str, seconds: float
) -> dict[str, object]:
    """Attach a bare SessionToolRunner with no work item and no lease."""
    from anthropic.lib.tools._beta_session_runner import SessionToolRunner
    from anthropic.lib.tools.agent_toolset import AgentToolContext

    key = chaos.env("ANTHROPIC_ENVIRONMENT_KEY")
    dispatched: list[dict[str, object]] = []
    Path(workdir).mkdir(parents=True, exist_ok=True)
    ctx = AgentToolContext(workdir=Path(workdir))
    async with anthropic.AsyncAnthropic(auth_token=key) as c:
        runner = SessionToolRunner(
            c,
            session_id,
            tools=chaos.tools_factory(chaos.FaultConfig(mode="none"))(ctx),
            max_idle=20.0,
            environment_key=key,
        )

        async def drive() -> None:
            async for call in runner:
                dispatched.append({"name": call.name, "tool_use_id": call.event.id})

        try:
            await asyncio.wait_for(drive(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        except Exception as exc:  # noqa: BLE001 - the failure mode is the finding
            return {"error": f"{type(exc).__name__}: {exc}", "dispatched": dispatched}
    return {"dispatched": dispatched}


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--workdir", default="/tmp/chaos-workspace")
    p.add_argument("--poll-seconds", type=float, default=45.0)
    p.add_argument("--runner-seconds", type=float, default=90.0)
    p.add_argument("--skip-poll", action="store_true")
    p.add_argument("--skip-webhook", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    session_id = args.session
    report: dict[str, object] = {"session_id": session_id}
    report["pending_before"] = pending_tool_use(session_id)

    if not args.skip_poll:
        report["queue_redispatch"] = await phase_poll(args.poll_seconds)
        report["pending_after_poll"] = pending_tool_use(session_id)

    if not args.skip_webhook:
        try:
            status = chaos.nudge_webhook(session_id)
            report["webhook_replay_status"] = status
        except Exception as exc:  # noqa: BLE001
            report["webhook_replay_status"] = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(25)
        report["pending_after_webhook"] = pending_tool_use(session_id)

    if pending_tool_use(session_id) is not None:
        report["bare_runner"] = await phase_runner(
            session_id, args.workdir, args.runner_seconds
        )
        report["pending_after_runner"] = pending_tool_use(session_id)

    out = ARTIFACTS / f"c3-recovery-{session_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str)[:4000])
    chaos.dump_events(session_id, f"c3-recovery-{session_id}")


if __name__ == "__main__":
    asyncio.run(main())
