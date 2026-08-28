"""H1 — Deployment lifecycle, schedule validation, and run-log semantics.

Primitive under test: Deployments (`POST/GET/PATCH /v1/deployments`,
`/run`, `/pause`, `/unpause`, `/archive`) and the deployment run log
(`/v1/deployment_runs`).

What it establishes:
  * which cron expressions the native scheduler accepts or rejects,
  * whether a self-hosted environment can carry deployment `resources`,
  * agent-version pinning at create vs update,
  * manual `run` vs scheduled fire in the run log's `trigger_context`,
  * pause/unpause/archive semantics and what happens to a schedule.

No sessions are left running: every session this creates is interrupted and
archived, every temporary resource archived.
"""

from __future__ import annotations

import json

import h_common as h

FAR_FUTURE_CRON = {"type": "cron", "expression": "0 4 1 1 *", "timezone": "UTC"}

CRON_CASES: list[tuple[str, dict[str, str]]] = [
    ("every_minute", {"type": "cron", "expression": "* * * * *", "timezone": "UTC"}),
    (
        "every_minute_tz",
        {"type": "cron", "expression": "* * * * *", "timezone": "America/Los_Angeles"},
    ),
    (
        "step_10s_style",
        {"type": "cron", "expression": "*/1 * * * *", "timezone": "UTC"},
    ),
    (
        "six_field_seconds",
        {"type": "cron", "expression": "*/30 * * * * *", "timezone": "UTC"},
    ),
    ("shortcut_daily", {"type": "cron", "expression": "@daily", "timezone": "UTC"}),
    (
        "last_day_of_month",
        {"type": "cron", "expression": "0 0 L * *", "timezone": "UTC"},
    ),
    (
        "question_mark_dow",
        {"type": "cron", "expression": "0 0 * * ?", "timezone": "UTC"},
    ),
    ("nth_weekday", {"type": "cron", "expression": "0 0 * * 1#2", "timezone": "UTC"}),
    (
        "bad_timezone",
        {"type": "cron", "expression": "0 0 * * *", "timezone": "Mars/Olympus"},
    ),
    ("missing_timezone", {"type": "cron", "expression": "0 0 * * *"}),
    ("dow_seven", {"type": "cron", "expression": "0 0 * * 7", "timezone": "UTC"}),
]


def main() -> None:
    api = h.client()
    rec = h.Recorder("h1_lifecycle")
    created_deployments: list[str] = []
    agent = h.create_probe_agent(
        api,
        rec,
        suffix="h1",
        system=(
            "You are a temporary probe agent for a deployments experiment. "
            "Do nothing except reply with the single word ACK."
        ),
        tools=[],
    )

    def create(**overrides: object) -> object:
        params: dict[str, object] = {
            "agent": agent.id,
            "environment_id": h.CLOUD_ENVIRONMENT_ID,
            "name": h.temp_name("h1"),
            "initial_events": [h.user_message(f"{h.SMOKE_PREFIX} reply ACK and stop.")],
            "budget": h.small_budget("1"),
            "metadata": {"experiment": "clevin-swarm-H"},
        }
        params.update(overrides)
        deployment = api.beta.deployments.create(**params)  # type: ignore[arg-type]
        created_deployments.append(deployment.id)
        return deployment

    def create_and_archive(label: str, **overrides: object) -> object:
        """Validation-only probe: archive immediately so no schedule stays armed."""
        result = h.attempt(rec, label, lambda: create(**overrides))
        if result is not None:
            h.archive_deployment(api, rec, result.id)
            created_deployments.remove(result.id)
        return result

    # --- 1. cron acceptance matrix -------------------------------------------------
    for label, schedule in CRON_CASES:
        create_and_archive(f"cron.{label}", schedule=schedule)

    # --- 2. schedule-free deployment (manual only) --------------------------------
    manual = h.attempt(rec, "deployment.no_schedule", lambda: create(schedule=None))

    # --- 3. self-hosted environment + resources -----------------------------------
    create_and_archive(
        "deployment.self_hosted_with_memory_store",
        environment_id=h.SELF_HOSTED_ENVIRONMENT_ID,
        schedule=None,
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": h.MEMORY_STORE_ID,
                "access": "read_write",
            }
        ],
    )
    create_and_archive(
        "deployment.self_hosted_no_resources",
        environment_id=h.SELF_HOSTED_ENVIRONMENT_ID,
        schedule=None,
    )

    # --- 4. initial-event bounds ---------------------------------------------------
    create_and_archive("initial_events.empty", schedule=None, initial_events=[])
    create_and_archive(
        "initial_events.51",
        schedule=None,
        initial_events=[
            h.user_message(f"{h.SMOKE_PREFIX} noop {i}") for i in range(51)
        ],
    )
    create_and_archive(
        "initial_events.system_then_user",
        schedule=None,
        initial_events=[
            {"type": "system.message", "content": [{"type": "text", "text": "probe"}]},
            h.user_message(f"{h.SMOKE_PREFIX} reply ACK and stop."),
        ],
    )

    # --- 5. version pinning --------------------------------------------------------
    if manual is not None:
        rec.note("pin.at_create", deployment_agent=manual.agent)
        updated_agent = api.beta.agents.update(
            agent.id,
            version=agent.version,
            name=agent.name,
            model=h.CHEAP_MODEL,
            system="Probe agent v2. Reply only with the single word ACK2.",
            tools=[],
        )
        rec.note(
            "agent.new_version",
            agent_id=updated_agent.id,
            version=updated_agent.version,
        )
        after = api.beta.deployments.retrieve(manual.id)
        rec.note("pin.after_new_agent_version", deployment_agent=after.agent)
        repinned = api.beta.deployments.update(manual.id, agent=agent.id)
        rec.note("pin.repinned_by_id", deployment_agent=repinned.agent)
        explicit = api.beta.deployments.update(
            manual.id, agent={"type": "agent", "id": agent.id, "version": 1}
        )
        rec.note("pin.explicit_old_version", deployment_agent=explicit.agent)
        h.attempt(
            rec,
            "pin.nonexistent_version",
            lambda: api.beta.deployments.update(
                manual.id, agent={"type": "agent", "id": agent.id, "version": 99}
            ),
        )

        # --- 6. manual run + run log ----------------------------------------------
        run = api.beta.deployments.run(manual.id)
        rec.note("run.manual", run=run)
        runs = h.list_runs(api, manual.id)
        rec.note("runs.after_manual", count=len(runs), runs=[h.dump(r) for r in runs])
        by_trigger = list(
            api.beta.deployment_runs.list(
                deployment_id=manual.id, trigger_type="manual"
            )
        )
        rec.note("runs.filter_manual", count=len(by_trigger))
        errored = list(
            api.beta.deployment_runs.list(deployment_id=manual.id, has_error=True)
        )
        rec.note("runs.filter_has_error", count=len(errored))
        sessions_from_deployment = list(api.beta.sessions.list(deployment_id=manual.id))
        rec.note(
            "sessions.filter_by_deployment",
            count=len(sessions_from_deployment),
            statuses=[s.status for s in sessions_from_deployment],
        )
        if run.session_id:
            session = api.beta.sessions.retrieve(run.session_id)
            rec.note(
                "session.from_manual_run",
                session_id=session.id,
                status=session.status,
                agent=h.dump(session.agent),
                resources=[h.dump(r) for r in session.resources],
                budget=h.dump(session.budget),
                metadata=session.metadata,
                title=session.title,
            )

        # --- 7. pause / unpause / archive ------------------------------------------
        paused = api.beta.deployments.pause(manual.id)
        rec.note(
            "deployment.paused",
            status=paused.status,
            paused_reason=h.dump(paused.paused_reason),
        )
        h.attempt(rec, "run.while_paused", lambda: api.beta.deployments.run(manual.id))
        h.attempt(
            rec, "pause.idempotent", lambda: api.beta.deployments.pause(manual.id)
        )
        unpaused = api.beta.deployments.unpause(manual.id)
        rec.note(
            "deployment.unpaused",
            status=unpaused.status,
            paused_reason=h.dump(unpaused.paused_reason),
        )
        h.attempt(
            rec, "unpause.idempotent", lambda: api.beta.deployments.unpause(manual.id)
        )

    # --- 8. archive semantics ------------------------------------------------------
    scheduled = h.attempt(
        rec,
        "deployment.for_archive",
        lambda: create(schedule=FAR_FUTURE_CRON),
    )
    if scheduled is not None:
        rec.note(
            "archive.before",
            schedule=h.dump(scheduled.schedule),
            status=scheduled.status,
        )
        archived = api.beta.deployments.archive(scheduled.id)
        rec.note(
            "archive.after",
            status=archived.status,
            archived_at=str(archived.archived_at),
            schedule=h.dump(archived.schedule),
        )
        h.attempt(
            rec,
            "archive.run_after_archive",
            lambda: api.beta.deployments.run(scheduled.id),
        )
        h.attempt(
            rec,
            "archive.update_after_archive",
            lambda: api.beta.deployments.update(
                scheduled.id, description="post-archive"
            ),
        )
        h.attempt(
            rec,
            "archive.unpause_after_archive",
            lambda: api.beta.deployments.unpause(scheduled.id),
        )
        listed = [d.id for d in api.beta.deployments.list(limit=100)]
        rec.note("archive.excluded_from_list", present=scheduled.id in listed)
        listed_archived = [
            d.id for d in api.beta.deployments.list(limit=100, include_archived=True)
        ]
        rec.note(
            "archive.present_with_include_archived",
            present=scheduled.id in listed_archived,
        )

    for deployment_id in list(created_deployments):
        # Stop every session the probe deployments produced before archiving them.
        for session in api.beta.sessions.list(deployment_id=deployment_id, limit=100):
            h.stop_session(api, rec, session.id)
        h.archive_deployment(api, rec, deployment_id)
    h.archive_agent(api, rec, agent.id)
    path = rec.write()
    print(json.dumps({"artifact": str(path), "deployments": len(created_deployments)}))


if __name__ == "__main__":
    main()
