"""H2 — High-frequency schedules, fire jitter, and overlapping runs.

Primitive under test: the Deployment cron scheduler and its run log.

An every-minute deployment is armed with an agent that deliberately occupies its
session for longer than the schedule period (a `sleep` through the native bash
tool). Over the observation window we record, per fire: `scheduled_at`, the run
record's `created_at`, whether a session was created or an error recorded, and
the status of every session at each poll. That answers whether the native
scheduler serialises, skips, or freely overlaps runs, and what the wake-up
latency actually is.

Env knobs: H2_MINUTES (default 8), H2_SLEEP_SECONDS (default 150).
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import h_common as h

MINUTES = int(os.environ.get("H2_MINUTES", "8"))
SLEEP_SECONDS = int(os.environ.get("H2_SLEEP_SECONDS", "150"))

SYSTEM = (
    "You are a temporary probe agent measuring deployment scheduling. "
    "Do exactly what the user message says with the bash tool, nothing else. "
    "Never touch the network, git, or any external service."
)


def parse(ts: str | datetime | None) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> None:
    api = h.client()
    rec = h.Recorder("h2_frequency_overlap")
    agent = h.create_probe_agent(
        api,
        rec,
        suffix="h2",
        system=SYSTEM,
        tools=[h.AGENT_TOOLSET],
    )
    deployment = api.beta.deployments.create(
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h2-every-minute"),
        schedule={"type": "cron", "expression": "* * * * *", "timezone": "UTC"},
        budget=h.small_budget("1"),
        metadata={"experiment": "clevin-swarm-H"},
        initial_events=[
            h.user_message(
                "Record the wall-clock time with `date -u`, then occupy this session by "
                f"running `sleep {SLEEP_SECONDS}` with an explicit bash timeout_ms of "
                f"{(SLEEP_SECONDS + 60) * 1000}. Then run `date -u` again and report both "
                "timestamps in one short message. Do nothing else."
            )
        ],
    )
    rec.note(
        "deployment.created",
        deployment_id=deployment.id,
        schedule=h.dump(deployment.schedule),
        upcoming=[str(t) for t in (deployment.schedule.upcoming_runs_at or [])],
    )

    seen_runs: dict[str, dict[str, object]] = {}
    try:
        deadline = time.monotonic() + MINUTES * 60
        while time.monotonic() < deadline:
            runs = h.list_runs(api, deployment.id)
            for run in runs:
                if run.id in seen_runs:
                    continue
                trigger = h.dump(run.trigger_context)
                scheduled_at = parse(trigger.get("scheduled_at"))
                created_at = parse(run.created_at)
                delay = (
                    (created_at - scheduled_at).total_seconds()
                    if scheduled_at and created_at
                    else None
                )
                seen_runs[run.id] = {
                    "run_id": run.id,
                    "scheduled_at": str(scheduled_at),
                    "created_at": str(created_at),
                    "fire_delay_s": delay,
                    "session_id": run.session_id,
                    "error": h.dump(run.error),
                    "agent_version": h.dump(run.agent).get("version"),
                }
                rec.note("run.observed", **seen_runs[run.id])
            sessions = list(
                api.beta.sessions.list(deployment_id=deployment.id, limit=100)
            )
            statuses = [s.status for s in sessions]
            rec.note(
                "concurrency.poll",
                total_sessions=len(sessions),
                running=statuses.count("running"),
                idle=statuses.count("idle"),
                terminated=statuses.count("terminated"),
                rescheduling=statuses.count("rescheduling"),
                runs=len(seen_runs),
            )
            time.sleep(20)

        deployment_after = api.beta.deployments.retrieve(deployment.id)
        rec.note(
            "deployment.after_window",
            status=deployment_after.status,
            paused_reason=h.dump(deployment_after.paused_reason),
            schedule=h.dump(deployment_after.schedule),
        )
        sessions = list(api.beta.sessions.list(deployment_id=deployment.id, limit=100))
        rec.note(
            "sessions.final",
            count=len(sessions),
            detail=[
                {
                    "id": s.status and s.id,
                    "status": s.status,
                    "created_at": str(s.created_at),
                    "usage": h.dump(s.usage),
                }
                for s in sessions
            ],
        )
        # One session's transcript is enough to show the agent really ran the sleep.
        if sessions:
            rec.note(
                "session.sample_text",
                session_id=sessions[0].id,
                text=h.session_text(api, sessions[0].id)[:20],
            )
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=100):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        rec.write()


if __name__ == "__main__":
    main()
