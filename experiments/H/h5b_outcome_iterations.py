"""H5b — Does the native outcome eval→revision loop run to completion inside a deployment run?

H5 saw one `span.outcome_evaluation_end` with `result: "needs_revision"` and the
session then reached `idle`, which does not distinguish "the loop stopped after
one grade" from "the poll caught a transient idle". This re-runs the same graded
outcome and follows the event log until it stops growing, then reports every
evaluation span, its result, and the final session status.

Env knobs: H5B_QUIET_SECONDS (default 120), H5B_TIMEOUT_SECONDS (default 900).
"""

from __future__ import annotations

import os
import time
from typing import Any

import h_common as h

QUIET_SECONDS = int(os.environ.get("H5B_QUIET_SECONDS", "120"))
TIMEOUT_SECONDS = int(os.environ.get("H5B_TIMEOUT_SECONDS", "900"))

SYSTEM = (
    "You are a probe agent working toward a graded outcome. Use harmless local shell and file "
    "tools only. Follow the outcome specification and the grader's feedback literally. Never "
    "touch the network, git, or any external service."
)

OUTCOME = {
    "type": "user.define_outcome",
    "description": (
        "Produce /tmp/h5b-report.md containing exactly four lines in this order: "
        "cpus, memory, kernel, python — each line formatted `key: value` with a real measured "
        "value, then print the file."
    ),
    "rubric": {
        "type": "text",
        "content": (
            "- /tmp/h5b-report.md exists and was printed in the transcript.\n"
            "- It has exactly four lines, in order, with the exact lowercase keys "
            "`cpus`, `memory`, `kernel`, `python`.\n"
            "- Each value is a real measured value from this container.\n"
            "- No network access, git operations, or external state changes were attempted."
        ),
    },
    "max_iterations": 5,
}


def evaluation_spans(api: Any, session_id: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for event in api.beta.sessions.events.list(session_id, order="asc", limit=1000):
        payload = h.dump(event)
        if str(payload.get("type")).startswith("span.outcome_evaluation"):
            spans.append(
                {
                    "type": payload.get("type"),
                    "iteration": payload.get("iteration"),
                    "result": payload.get("result"),
                    "processed_at": payload.get("processed_at"),
                    "explanation": (payload.get("explanation") or "")[:600],
                }
            )
    return spans


def main() -> None:
    api = h.client()
    rec = h.Recorder("h5b_outcome_iterations")
    agent = h.create_probe_agent(
        api, rec, suffix="h5b-outcome", system=SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    deployment = api.beta.deployments.create(
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h5b-outcome"),
        budget=h.small_budget("4"),
        metadata={"experiment": "clevin-swarm-H"},
        initial_events=[OUTCOME],
    )
    rec.note("deployment.created", deployment_id=deployment.id)
    try:
        run = api.beta.deployments.run(deployment.id)
        rec.note("run.manual", run_id=run.id, session_id=run.session_id)
        session_id = run.session_id
        if session_id is None:
            return
        last_count = -1
        quiet_since: float | None = None
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            events = list(
                api.beta.sessions.events.list(session_id, order="asc", limit=1000)
            )
            session = api.beta.sessions.retrieve(session_id)
            if len(events) == last_count:
                quiet_since = quiet_since or time.monotonic()
                if time.monotonic() - quiet_since >= QUIET_SECONDS:
                    break
            else:
                quiet_since = None
                last_count = len(events)
                rec.note(
                    "progress",
                    events=len(events),
                    status=session.status,
                    spans=evaluation_spans(api, session_id),
                )
            time.sleep(20)
        session = api.beta.sessions.retrieve(session_id)
        rec.note(
            "final",
            session_id=session_id,
            status=session.status,
            usage=h.dump(session.usage),
            spans=evaluation_spans(api, session_id),
            transcript=h.session_text(api, session_id),
        )
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=50):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        rec.write()


if __name__ == "__main__":
    main()
