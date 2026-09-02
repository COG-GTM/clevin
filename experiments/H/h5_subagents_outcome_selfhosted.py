"""H5 — Deployment-triggered workflows: subagent delegation, graded outcomes, self-hosted runs.

Primitives under test:
- deployment `initial_events` of type `user.define_outcome` (native rubric-graded
  eval→revision loop) as the way to express *what* recurring automation must achieve;
- `multiagent` coordinator topology reached from a deployment-created session;
- a deployment bound to the self-hosted (Modal `EnvironmentWorker`) environment.

Each case runs the deployment manually once and inspects the resulting session's
native event log for delegation, evaluation, and worker activity.

Env knobs: H5_SETTLE_SECONDS (default 900).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

import h_common as h

SETTLE_SECONDS = int(os.environ.get("H5_SETTLE_SECONDS", "900"))

WORKER_SYSTEM = (
    "You are a specialist subagent in a deployment experiment. Answer the question you are "
    "given using only harmless local shell commands and your own reasoning, then report back "
    "in one short message. Never touch the network, git, or any external service."
)
COORDINATOR_SYSTEM = (
    "You are a coordinator agent for a deployment experiment. Delegate independent questions "
    "to your roster of subagents in parallel rather than answering them yourself, then "
    "synthesise their answers into one final message. Never touch the network, git, or any "
    "external service."
)

DELEGATION_TASK = (
    "Two independent questions, one per subagent, delegated in parallel:\n"
    "(a) how many CPUs and how much memory does this container report?\n"
    "(b) what is the Python version and the current UTC time in this container?\n"
    "Delegate both, wait for both, then reply with a single synthesis message that labels "
    "which subagent answered which question. Then stop."
)


def event_summary(api: Any, session_id: str) -> dict[str, Any]:
    types: Counter[str] = Counter()
    interesting: list[dict[str, Any]] = []
    for event in api.beta.sessions.events.list(session_id, order="asc", limit=1000):
        payload = h.dump(event)
        kind = str(payload.get("type"))
        types[kind] += 1
        if any(
            token in kind
            for token in (
                "thread",
                "subagent",
                "outcome",
                "evaluation",
                "budget",
                "error",
                "status",
            )
        ):
            interesting.append({k: v for k, v in payload.items() if k != "content"})
    return {"event_types": dict(types), "interesting": interesting[:60]}


def observe(
    api: Any, rec: h.Recorder, case: str, deployment_id: str, session_id: str
) -> None:
    for sid, session in h.iter_settled(api, [session_id], timeout_s=SETTLE_SECONDS):
        rec.note(
            f"{case}.settled",
            session_id=sid,
            status=session.status,
            usage=h.dump(session.usage),
            multiagent=h.dump(getattr(session, "multiagent", None)),
        )
    rec.note(f"{case}.events", session_id=session_id, **event_summary(api, session_id))
    rec.note(
        f"{case}.transcript",
        session_id=session_id,
        text=h.session_text(api, session_id),
    )
    rec.note(
        f"{case}.threads",
        sessions_for_deployment=[
            {"id": s.id, "status": s.status}
            for s in api.beta.sessions.list(deployment_id=deployment_id, limit=50)
        ],
    )


def case_subagents(api: Any, rec: h.Recorder) -> None:
    worker_a = h.create_probe_agent(
        api, rec, suffix="h5-worker-a", system=WORKER_SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    worker_b = h.create_probe_agent(
        api, rec, suffix="h5-worker-b", system=WORKER_SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    coordinator = h.create_probe_agent(
        api,
        rec,
        suffix="h5-coordinator",
        system=COORDINATOR_SYSTEM,
        tools=[h.AGENT_TOOLSET],
        multiagent={"type": "coordinator", "agents": [worker_a.id, worker_b.id]},
    )
    deployment = api.beta.deployments.create(
        agent=coordinator.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h5-subagents"),
        budget=h.small_budget("3"),
        metadata={"experiment": "clevin-swarm-H"},
        initial_events=[h.user_message(DELEGATION_TASK)],
    )
    rec.note(
        "subagents.deployment",
        deployment_id=deployment.id,
        agent=h.dump(deployment.agent),
    )
    try:
        run = api.beta.deployments.run(deployment.id)
        rec.note(
            "subagents.run",
            run_id=run.id,
            session_id=run.session_id,
            error=h.dump(run.error),
        )
        if run.session_id:
            observe(api, rec, "subagents", deployment.id, run.session_id)
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        for agent_id in (coordinator.id, worker_a.id, worker_b.id):
            h.archive_agent(api, rec, agent_id)


def case_graded_outcome(api: Any, rec: h.Recorder) -> None:
    agent = h.create_probe_agent(
        api, rec, suffix="h5-outcome", system=WORKER_SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    outcome_event = {
        "type": "user.define_outcome",
        "description": (
            "Produce /tmp/h5-report.md: a short report listing the container's CPU count, "
            "total memory, kernel version and Python version, one per line, each line "
            "formatted as `key: value`, and print the file at the end."
        ),
        "rubric": {
            "type": "text",
            "content": (
                "- The file /tmp/h5-report.md exists and was printed in the transcript.\n"
                "- It contains exactly four lines, in the order: cpus, memory, kernel, python.\n"
                "- Every line uses the exact `key: value` form with a real measured value.\n"
                "- No network access, git operations, or external state changes were attempted."
            ),
        },
        "max_iterations": 3,
    }
    deployment = h.attempt(
        rec,
        "outcome.deployment_create",
        lambda: api.beta.deployments.create(
            agent=agent.id,
            environment_id=h.CLOUD_ENVIRONMENT_ID,
            name=h.temp_name("h5-outcome"),
            budget=h.small_budget("3"),
            metadata={"experiment": "clevin-swarm-H"},
            initial_events=[outcome_event],
        ),
    )
    if deployment is None:
        h.archive_agent(api, rec, agent.id)
        return
    try:
        run = api.beta.deployments.run(deployment.id)
        rec.note(
            "outcome.run",
            run_id=run.id,
            session_id=run.session_id,
            error=h.dump(run.error),
        )
        if run.session_id:
            observe(api, rec, "outcome", deployment.id, run.session_id)
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)


def case_self_hosted(api: Any, rec: h.Recorder) -> None:
    """A deployment on the self-hosted Modal environment, no resources attached."""
    agent = h.create_probe_agent(
        api, rec, suffix="h5-selfhosted", system=WORKER_SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    deployment = api.beta.deployments.create(
        agent=agent.id,
        environment_id=h.SELF_HOSTED_ENVIRONMENT_ID,
        name=h.temp_name("h5-selfhosted"),
        budget=h.small_budget("2"),
        metadata={"experiment": "clevin-swarm-H"},
        initial_events=[
            h.user_message(
                f"{h.SMOKE_PREFIX} run `uname -a`, `nproc` and `ls /` and report the output "
                "in one short message, then stop."
            )
        ],
    )
    rec.note(
        "selfhosted.deployment",
        deployment_id=deployment.id,
        environment_id=deployment.environment_id,
    )
    try:
        run = api.beta.deployments.run(deployment.id)
        rec.note(
            "selfhosted.run",
            run_id=run.id,
            session_id=run.session_id,
            error=h.dump(run.error),
        )
        if run.session_id:
            observe(api, rec, "selfhosted", deployment.id, run.session_id)
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)


def main() -> None:
    api = h.client()
    rec = h.Recorder("h5_subagents_outcome_selfhosted")
    try:
        case_subagents(api, rec)
        case_graded_outcome(api, rec)
        case_self_hosted(api, rec)
    finally:
        rec.write()


if __name__ == "__main__":
    main()
