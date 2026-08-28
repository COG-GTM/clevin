"""H4 — Recurring maintenance: can a deployment continue prior work through a Memory Store?

Primitives under test: deployment `resources` of type `memory_store`, the Memory
Store mount inside the deployment-created session, and the native memory version
log (`memory_stores.memory_versions.list`, filterable by `session_id`) as the
provenance instrument.

Each deployment run gets a *fresh* session — there is no native "resume this
deployment's previous session". So the only native continuity channel is the
Memory Store. We run the same maintenance deployment three times manually,
seeded with one memory written through the API, and check per run whether the
session (a) read what earlier runs wrote and (b) appended its own entry, using
the memory version log to attribute each write to its session.

Env knobs: H4_RUNS (default 3), H4_SETTLE_SECONDS (default 420) per run.
"""

from __future__ import annotations

import os

import h_common as h

RUNS = int(os.environ.get("H4_RUNS", "3"))
SETTLE_SECONDS = int(os.environ.get("H4_SETTLE_SECONDS", "420"))
LOG_PATH = "/maintenance/run-log.md"

SYSTEM = (
    "You are a recurring maintenance agent for a memory-continuity experiment. "
    "Your memory store is mounted read-write at /mnt/memory. "
    "Never touch the network, git, or any external service; only read and write files "
    "under /mnt/memory and run harmless local shell commands."
)

TASK = (
    "Recurring maintenance run.\n"
    f"1. Read the file /mnt/memory{LOG_PATH}.\n"
    "2. Report, verbatim, the last line that is already in that file.\n"
    "3. Append exactly one new line of the form "
    "`run <N>: <UTC timestamp from `date -u`> previous=<the previous run number>` "
    "where <N> is one greater than the highest run number already present.\n"
    "4. Reply with a single message containing the full file contents after your append. "
    "Then stop."
)


def main() -> None:
    api = h.client()
    rec = h.Recorder("h4_memory_continuity")
    store = api.beta.memory_stores.create(name=h.temp_name("h4-store"))
    rec.note("memory_store.created", store_id=store.id, name=store.name)
    seed = api.beta.memory_stores.memories.create(
        store.id,
        path=LOG_PATH,
        content="run 0: seeded through the memory API before any deployment run\n",
    )
    rec.note("memory.seeded", memory_id=seed.id, path=seed.path, detail=h.dump(seed))

    agent = h.create_probe_agent(
        api, rec, suffix="h4-maintenance", system=SYSTEM, tools=[h.AGENT_TOOLSET]
    )
    deployment = api.beta.deployments.create(
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h4-maintenance"),
        budget=h.small_budget("2"),
        metadata={"experiment": "clevin-swarm-H"},
        resources=[
            {
                "type": "memory_store",
                "memory_store_id": store.id,
                "access": "read_write",
                "instructions": "Recurring maintenance log. Append one line per run.",
            }
        ],
        initial_events=[h.user_message(TASK)],
    )
    rec.note(
        "deployment.created",
        deployment_id=deployment.id,
        resources=h.dump(deployment.resources),
    )

    session_ids: list[str] = []
    try:
        for index in range(1, RUNS + 1):
            run = api.beta.deployments.run(deployment.id)
            rec.note(
                "run.manual", index=index, run_id=run.id, session_id=run.session_id
            )
            if run.session_id is None:
                continue
            session_ids.append(run.session_id)
            for session_id, session in h.iter_settled(
                api, [run.session_id], timeout_s=SETTLE_SECONDS
            ):
                rec.note(
                    "run.settled",
                    index=index,
                    session_id=session_id,
                    status=session.status,
                    usage=h.dump(session.usage),
                )
                rec.note(
                    "run.transcript",
                    index=index,
                    session_id=session_id,
                    text=h.session_text(api, session_id),
                )
            memory = api.beta.memory_stores.memories.list(store.id)
            entries = [h.dump(m) for m in memory]
            rec.note("memory.after_run", index=index, entries=entries)
            for entry in entries:
                if entry.get("path") == LOG_PATH:
                    detail = api.beta.memory_stores.memories.retrieve(
                        entry["id"], memory_store_id=store.id
                    )
                    rec.note(
                        "memory.log_contents",
                        index=index,
                        content=h.dump(detail).get("content"),
                    )
            versions = [
                h.dump(v)
                for v in api.beta.memory_stores.memory_versions.list(store.id, limit=50)
            ]
            rec.note(
                "memory.versions", index=index, count=len(versions), versions=versions
            )

        for session_id in session_ids:
            attributed = [
                h.dump(v)
                for v in api.beta.memory_stores.memory_versions.list(
                    store.id, session_id=session_id, limit=50
                )
            ]
            rec.note(
                "memory.versions_by_session",
                session_id=session_id,
                count=len(attributed),
                operations=[v.get("operation") for v in attributed],
            )

        # Is there any native way to make a deployment continue a previous session?
        h.attempt(
            rec,
            "deployment.resume_previous_session",
            lambda: api.beta.deployments.update(
                deployment.id,
                initial_events=[h.user_message("continue")],
                session_id=session_ids[0],
            )
            if session_ids
            else None,
        )
        rec.note(
            "sessions.per_deployment",
            count=len(
                list(api.beta.sessions.list(deployment_id=deployment.id, limit=100))
            ),
            note="every deployment run creates a new session; no resume parameter exists",
        )
    finally:
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=100):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        h.archive_memory_store(api, rec, store.id)
        rec.write()


if __name__ == "__main__":
    main()
