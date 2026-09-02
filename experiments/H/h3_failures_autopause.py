"""H3 — Failed runs, auto-pause, and recovery of a broken deployment.

Primitive under test: deployment run error reporting and the native auto-pause
path (`paused_reason.type == "error"`).

Each case builds a deployment on an every-minute schedule, then breaks one of its
dependencies (archived agent, archived memory store, resources on a self-hosted
environment). We then watch the native run log for a run with a non-null `error`
and the deployment for an auto-pause, and we test recovery: unpausing while the
cause persists, and unpausing after fixing it.

Env knobs: H3_WAIT_SECONDS (default 240) per case.
"""

from __future__ import annotations

import os
import time
from typing import Any

import h_common as h

WAIT_SECONDS = int(os.environ.get("H3_WAIT_SECONDS", "240"))
EVERY_MINUTE = {"type": "cron", "expression": "* * * * *", "timezone": "UTC"}
SYSTEM = "You are a temporary probe agent. Reply with the single word ACK and stop."


def create_deployment(api: Any, rec: h.Recorder, **kwargs: Any) -> Any:
    deployment = api.beta.deployments.create(
        budget=h.small_budget("1"),
        metadata={"experiment": "clevin-swarm-H"},
        initial_events=[h.user_message(f"{h.SMOKE_PREFIX} reply ACK and stop.")],
        schedule=EVERY_MINUTE,
        **kwargs,
    )
    rec.note(
        "deployment.created",
        name=kwargs.get("name"),
        deployment_id=deployment.id,
        environment_id=deployment.environment_id,
        resources=h.dump(deployment.resources),
        status=deployment.status,
    )
    return deployment


def watch(
    api: Any, rec: h.Recorder, case: str, deployment_id: str, *, wait_s: int
) -> Any:
    """Poll the run log and deployment until a failed run or auto-pause appears."""
    deadline = time.monotonic() + wait_s
    seen: set[str] = set()
    while time.monotonic() < deadline:
        for run in h.list_runs(api, deployment_id):
            if run.id in seen:
                continue
            seen.add(run.id)
            rec.note(
                f"{case}.run",
                run_id=run.id,
                trigger=h.dump(run.trigger_context),
                session_id=run.session_id,
                error=h.dump(run.error),
            )
        deployment = api.beta.deployments.retrieve(deployment_id)
        if deployment.status == "paused":
            rec.note(
                f"{case}.auto_paused",
                paused_reason=h.dump(deployment.paused_reason),
                schedule=h.dump(deployment.schedule),
                runs_seen=len(seen),
            )
            return deployment
        time.sleep(15)
    deployment = api.beta.deployments.retrieve(deployment_id)
    rec.note(
        f"{case}.no_autopause_within_window",
        status=deployment.status,
        runs_seen=len(seen),
        schedule=h.dump(deployment.schedule),
    )
    return deployment


def case_archived_agent(api: Any, rec: h.Recorder) -> None:
    agent = h.create_probe_agent(api, rec, suffix="h3-archived-agent", system=SYSTEM)
    deployment = create_deployment(
        api,
        rec,
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h3-archived-agent"),
    )
    try:
        api.beta.agents.archive(agent.id)
        rec.note("archived_agent.agent_archived", agent_id=agent.id)
        watch(api, rec, "archived_agent", deployment.id, wait_s=WAIT_SECONDS)
        h.attempt(
            rec,
            "archived_agent.manual_run_after_pause",
            lambda: api.beta.deployments.run(deployment.id),
        )
        h.attempt(
            rec,
            "archived_agent.unpause_with_cause_present",
            lambda: api.beta.deployments.unpause(deployment.id),
        )
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)


def case_archived_memory_store(api: Any, rec: h.Recorder) -> None:
    agent = h.create_probe_agent(api, rec, suffix="h3-archived-store", system=SYSTEM)
    store = api.beta.memory_stores.create(name=h.temp_name("h3-store"))
    rec.note("memory_store.created", store_id=store.id, name=store.name)
    deployment = create_deployment(
        api,
        rec,
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h3-archived-store"),
        resources=[{"type": "memory_store", "memory_store_id": store.id}],
    )
    try:
        api.beta.memory_stores.archive(store.id)
        rec.note("archived_store.store_archived", store_id=store.id)
        deployment = watch(
            api, rec, "archived_store", deployment.id, wait_s=WAIT_SECONDS
        )
        if deployment.status == "paused":
            # Recovery: clear the broken resource, then unpause.
            h.attempt(
                rec,
                "archived_store.clear_resources",
                lambda: api.beta.deployments.update(deployment.id, resources=[]),
            )
            h.attempt(
                rec,
                "archived_store.unpause_after_fix",
                lambda: api.beta.deployments.unpause(deployment.id),
            )
            runs_before = len(h.list_runs(api, deployment.id))
            time.sleep(90)
            rec.note(
                "archived_store.after_recovery",
                runs_before=runs_before,
                runs_after=len(h.list_runs(api, deployment.id)),
                status=api.beta.deployments.retrieve(deployment.id).status,
            )
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        h.archive_memory_store(api, rec, store.id)


def case_self_hosted_resources(api: Any, rec: h.Recorder) -> None:
    """Resources on a self-hosted environment are accepted at create time (H1)."""
    agent = h.create_probe_agent(api, rec, suffix="h3-selfhosted-res", system=SYSTEM)
    store = api.beta.memory_stores.create(name=h.temp_name("h3-sh-store"))
    rec.note("memory_store.created", store_id=store.id, name=store.name)
    deployment = create_deployment(
        api,
        rec,
        agent=agent.id,
        environment_id=h.SELF_HOSTED_ENVIRONMENT_ID,
        name=h.temp_name("h3-selfhosted-res"),
        resources=[{"type": "memory_store", "memory_store_id": store.id}],
    )
    try:
        watch(api, rec, "selfhosted_resources", deployment.id, wait_s=WAIT_SECONDS)
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        h.archive_memory_store(api, rec, store.id)


def case_bad_references(api: Any, rec: h.Recorder, archived_agent_id: str) -> None:
    """Do broken references fail closed at create time or only at run time?"""
    agent = h.create_probe_agent(api, rec, suffix="h3-badrefs", system=SYSTEM)
    try:
        h.attempt(
            rec,
            "create.missing_memory_store",
            lambda: api.beta.deployments.create(
                agent=agent.id,
                environment_id=h.CLOUD_ENVIRONMENT_ID,
                name=h.temp_name("h3-missing-store"),
                initial_events=[h.user_message(f"{h.SMOKE_PREFIX} ACK")],
                resources=[
                    {
                        "type": "memory_store",
                        "memory_store_id": "memstore_01" + "A" * 22,
                    }
                ],
            ),
        )
        h.attempt(
            rec,
            "create.missing_environment",
            lambda: api.beta.deployments.create(
                agent=agent.id,
                environment_id="env_01" + "A" * 22,
                name=h.temp_name("h3-missing-env"),
                initial_events=[h.user_message(f"{h.SMOKE_PREFIX} ACK")],
            ),
        )
        h.attempt(
            rec,
            "create.missing_vault",
            lambda: api.beta.deployments.create(
                agent=agent.id,
                environment_id=h.CLOUD_ENVIRONMENT_ID,
                name=h.temp_name("h3-missing-vault"),
                initial_events=[h.user_message(f"{h.SMOKE_PREFIX} ACK")],
                vault_ids=["vlt_01" + "A" * 22],
            ),
        )
        h.attempt(
            rec,
            "create.archived_agent",
            lambda: api.beta.deployments.create(
                agent=archived_agent_id,
                environment_id=h.CLOUD_ENVIRONMENT_ID,
                name=h.temp_name("h3-archived-agent-create"),
                initial_events=[h.user_message(f"{h.SMOKE_PREFIX} ACK")],
            ),
        )
    finally:
        h.archive_agent(api, rec, agent.id)


def main() -> None:
    api = h.client()
    rec = h.Recorder("h3_failures_autopause")
    archived = h.create_probe_agent(api, rec, suffix="h3-prearchived", system=SYSTEM)
    api.beta.agents.archive(archived.id)
    rec.cleaned("agent", archived.id, "archive", "archived")
    try:
        case_bad_references(api, rec, archived.id)
        case_archived_agent(api, rec)
        case_archived_memory_store(api, rec)
        case_self_hosted_resources(api, rec)
    finally:
        rec.write()


if __name__ == "__main__":
    main()
