"""K3 — wake-on-event through Deployment polling.

Question: with no GitHub/Linear -> Anthropic event path, how close does a native
Deployment (cron schedule) plus a Memory Store get to reacting to a new ticket or
a new PR comment?

Primitive under test: `beta.deployments` (schedule, initial_events, resources,
vaults, deployment runs) plus `beta.deployment_runs` as the run ledger. The
driver only creates, inspects, and archives a temporary deployment; the polling
work itself is expressed entirely in the deployment's initial user message, i.e.
in configuration.

Usage:
  uv run --project runtime python experiments/K/k3_wake_on_event.py [--wait 420]
"""

from __future__ import annotations

import sys
import time
from typing import Any

from common import SMOKE_PREFIX, client, ids, budget, save, summarize_events

POLL_PROMPT = f"""{SMOKE_PREFIX}
You are a polling wake-up run. Do not touch Git and change no external state.
1. Read /mnt/memory to see whether a file named k3-poll-log.md exists, and note
   the last recorded poll timestamp if it does.
2. Report the current UTC time and whether this run found prior state (this is
   the only cross-run continuity a scheduled deployment has: each run is a NEW
   session).
3. Append one line `poll <UTC timestamp> saw-prior-state=<yes|no>` to
   /mnt/memory/k3-poll-log.md, creating the file if needed, then stop.
"""


def name() -> str:
    return f"clevin-swarm-K-k3-poll-{int(time.time())}"


def main() -> int:
    wait_seconds = 420.0
    if "--wait" in sys.argv:
        wait_seconds = float(sys.argv[sys.argv.index("--wait") + 1])
    config = ids()
    record: dict[str, Any] = {}

    # Every-minute cron is the finest native granularity: the schedule is a
    # 5-field POSIX cron expression, so sub-minute reaction is not expressible.
    deployment = client().beta.deployments.create(
        name=name(),
        agent={"type": "agent", "id": config["agent_id"]},
        environment_id=config["environment_id"],
        schedule={"type": "cron", "expression": "* * * * *", "timezone": "UTC"},
        budget=budget("25"),
        vault_ids=[config["vault_id"]],
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": config["memory_store_id"],
                "access": "read_write",
            }
        ],
        metadata={"experiment": "clevin-swarm-K", "probe": "k3-poll"},
        initial_events=[
            {"type": "user.message", "content": [{"type": "text", "text": POLL_PROMPT}]}
        ],
    )
    record["deployment"] = deployment.model_dump(mode="json")
    print("deployment:", deployment.id, flush=True)

    # Sub-minute reaction check: fire a manual run immediately and time it.
    manual_started = time.time()
    manual = client().beta.deployments.run(deployment.id)
    record["manual_run"] = manual.model_dump(mode="json")
    record["manual_run_latency_seconds"] = round(time.time() - manual_started, 2)
    print("manual run:", manual.id, manual.session_id, flush=True)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(60)
        runs = list(client().beta.deployment_runs.list(deployment_id=deployment.id))
        print("runs so far:", len(runs), flush=True)

    runs = list(client().beta.deployment_runs.list(deployment_id=deployment.id))
    record["runs"] = [run.model_dump(mode="json") for run in runs]
    record["run_count"] = len(runs)
    record["distinct_sessions"] = sorted(
        {run.session_id for run in runs if run.session_id}
    )
    record["triggers"] = sorted({run.trigger_context.type for run in runs})
    record["run_intervals_seconds"] = [
        round((b.created_at - a.created_at).total_seconds(), 1)
        for a, b in zip(
            sorted(runs, key=lambda r: r.created_at),
            sorted(runs, key=lambda r: r.created_at)[1:],
            strict=False,
        )
    ]

    # Does a run reuse a session, or is continuity only via the Memory Store?
    record["sessions"] = {}
    for session_id in record["distinct_sessions"][:4]:
        record["sessions"][session_id] = summarize_events(session_id)

    client().beta.deployments.pause(deployment.id)
    record["paused"] = client().beta.deployments.retrieve(deployment.id).model_dump(
        mode="json"
    )
    client().beta.deployments.archive(deployment.id)
    record["archived"] = client().beta.deployments.retrieve(deployment.id).model_dump(
        mode="json"
    )
    print(save("k3-deployment-polling.json", record))
    print(
        "runs:",
        record["run_count"],
        "distinct sessions:",
        len(record["distinct_sessions"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
