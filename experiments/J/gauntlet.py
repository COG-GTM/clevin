"""J — the integrated gauntlet: run one arm end to end and score it.

One arm = one temporary integrated agent + one session against the self-hosted
Modal environment, driven by a real Linear ticket, supervised only through native
session events. The supervisor here is an *experiment driver*, not a product
orchestrator: it answers the agent's blocking question, injects one round of
review feedback, optionally injects one infrastructure fault, and records native
evidence. It never decides what the agent should do next.

Arms:
  full          roster + memory + skill + ask-and-block   (the composed ceiling)
  no-memory     full minus the memory store and its prompt paragraph
  no-subagents  full minus the coordinator roster and delegation paragraph
  chaos         full, plus a Modal sandbox kill mid-run and a webhook replay

Usage:
  uv run --project runtime python experiments/J/gauntlet.py --arm full
  uv run --project runtime python experiments/J/gauntlet.py --arm chaos \
      --chaos-after 900
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from typing import Any

import j_common as J
from make_ticket import create_ticket

INITIAL_MESSAGE = """{ticket}

Work this ticket end to end. It is deliberately underspecified: diagnose the real
problems yourself before changing code, and keep a plan you revise as you learn.
"""

ANSWER = """Decision from the operator (finance and accounting agreed):

- Keep money exact internally: accumulate with decimal.Decimal, never float.
- Round only at presentation, to 2 decimal places, using banker's rounding
  (ROUND_HALF_EVEN).
- Report totals must therefore be exact sums of the exact inputs, formatted to
  2 decimal places only when rendered.

Record this decision in the code or its docstring so the next person does not
have to ask again, then continue.
"""

FEEDBACK = """Review feedback from the operator on your work so far. Revisit your
implementation before you call this done:

1. A month with no rows must not raise. Decide and document the defined
   behaviour, and cover it with a test.
2. The grouping must be a single pass over the rows. If any code path still
   rescans the rows once per month, fix it.
3. Every commit on this branch must carry the commit tag the ticket requires,
   including the commit that addresses this feedback.

Re-run the fixture checks after the changes and make sure the required GitHub
check ends green.
"""

SEED_MEMORIES: dict[str, str] = {
    "/repos/COG-GTM-clevin/setup.md": """# COG-GTM/clevin — verified setup facts

- The system `python3` has no pytest. Run repository Python through uv:
  `uv run --project runtime python -m pytest <paths> -q`.
- The reportkit fixture checks live at `experiments/J/fixture/tests` and are run by
  the required GitHub check `j-gauntlet-fixture`.
- `pnpm install` and `uv sync --project runtime` are only needed for the main
  runtime packages, not for the fixture.
""",
    "/repos/COG-GTM-clevin/conventions.md": """# COG-GTM/clevin — conventions learned on earlier tickets

- Every directory can carry its own AGENTS.md; the nearest one to the files you
  change wins. Read it before editing.
- Never change a test's expectation to make it pass; fix the implementation.
- The fixture package is standard-library only. Adding a dependency fails review.
""",
    "/repos/COG-GTM-clevin/failures.md": """# COG-GTM/clevin — past failure patterns

- A previous run "fixed" the CSV parser with a hand-written quote scanner and
  broke escaped quotes. The standard library `csv` module handles this correctly.
- A previous run reported success while the required GitHub check was still
  queued. Poll the check until it reports a conclusion.
""",
}


def arm_config(arm: str) -> dict[str, bool]:
    return {
        "memory": arm != "no-memory",
        "subagents": arm != "no-subagents",
        "skill": True,
        "chaos": arm == "chaos",
    }


def build(run: dict[str, Any], arm: str, cfg: dict[str, bool], ledger: J.Ledger) -> str:
    """Create the temporary integrated agent for this arm; return its id."""
    client = J.client()
    roster: list[str] = []
    if cfg["subagents"]:
        for spec in J.SUBAGENTS:
            agent = client.beta.agents.create(
                name=f"{J.RUN_PREFIX}-{spec['role']}-{run['run_id']}",
                description=str(spec["description"]),
                model={"id": "claude-opus-5", "effort": "medium"},
                system=str(spec["system"]),
                tools=list(spec["tools"]),  # type: ignore[arg-type]
                mcp_servers=[],
                skills=[],
                multiagent=None,
                metadata={"experiment": "clevin-swarm-J", "run_id": run["run_id"]},
            )
            ledger.record("agent", agent.id, agent.name)
            roster.append(agent.id)
        run["roster"] = roster

    skills: list[dict[str, Any]] = []
    if cfg["skill"]:
        skill = client.beta.skills.create(
            files=J.skill_archive(),
            display_title=f"{J.RUN_PREFIX} revenue report hardening {run['run_id']}",
        )
        ledger.record("skill", skill.id, str(skill.display_title))
        skills = [
            {"type": "custom", "skill_id": skill.id, "version": skill.latest_version}
        ]
        run["skill"] = {"id": skill.id, "version": skill.latest_version}

    params: dict[str, Any] = {
        "name": f"{J.RUN_PREFIX}-integrated-{arm}-{run['run_id']}",
        "description": f"workstream J integrated gauntlet arm {arm}",
        "model": {"id": "claude-opus-5", "effort": "medium"},
        "system": J.system_prompt(memory=cfg["memory"], subagents=cfg["subagents"]),
        "mcp_servers": [J.LINEAR_MCP, J.GITHUB_MCP],
        "tools": [J.AGENT_TOOLSET, *J.MCP_TOOLSETS, J.ASK_TOOL],
        "skills": skills,
        "metadata": {
            "experiment": "clevin-swarm-J",
            "run_id": run["run_id"],
            "arm": arm,
        },
    }
    if roster:
        params["multiagent"] = {"type": "coordinator", "agents": roster}
    agent = client.beta.agents.create(**params)
    ledger.record("agent", agent.id, agent.name)
    run["agent"] = {"id": agent.id, "version": agent.version}
    return agent.id


def seed_memory(run: dict[str, Any], ledger: J.Ledger) -> str:
    """Create and seed a temporary Memory Store with prior-task knowledge."""
    client = J.client()
    store = client.beta.memory_stores.create(
        name=f"{J.RUN_PREFIX}-memory-{run['run_id']}",
        description="Temporary store: prior-task knowledge for the J gauntlet.",
        metadata={"experiment": "clevin-swarm-J", "run_id": run["run_id"]},
    )
    ledger.record("memory_store", store.id, store.name)
    for path, content in SEED_MEMORIES.items():
        client.beta.memory_stores.memories.create(store.id, path=path, content=content)
    run["memory_store"] = {
        "id": store.id,
        "seeded": sorted(SEED_MEMORIES),
        "before": [entry.get("path") for entry in J.memory_entries(store.id)],
    }
    return store.id


def start_session(
    run: dict[str, Any],
    arm: str,
    agent_id: str,
    store_id: str | None,
    ticket: str,
    budget: str,
) -> Any:
    params: dict[str, Any] = {
        "agent": {"type": "agent", "id": agent_id},
        "environment_id": J.env("CLEVIN_ENVIRONMENT_ID"),
        "vault_ids": [J.env("CLEVIN_VAULT_ID")],
        "budget": {
            "type": "limit",
            "max_list_cost": {"amount": budget, "currency": "USD"},
        },
        "title": f"{J.RUN_PREFIX}-{arm}-{run['run_id']}",
        "metadata": {
            "experiment": "clevin-swarm-J",
            "arm": arm,
            "run_id": run["run_id"],
        },
        "initial_events": [
            {
                "type": "user.message",
                "content": [
                    {"type": "text", "text": INITIAL_MESSAGE.format(ticket=ticket)}
                ],
            }
        ],
    }
    if store_id:
        params["resources"] = [
            {
                "type": "memory_store",
                "memory_store_id": store_id,
                "access": "read_write",
                "instructions": (
                    "Prior verified setup, convention and failure facts for this "
                    "repository, under /repos/<owner>-<repo>/. Confirm before use; "
                    "write back only verified reusable facts."
                ),
            }
        ]
    return J.client().beta.sessions.create(**params)


def supervise(
    run: dict[str, Any],
    session_id: str,
    *,
    cfg: dict[str, bool],
    timeout: float,
    steer_after: float,
    chaos_after: float,
    poll: float,
) -> None:
    """Poll native events; answer the block, steer once, inject one fault once."""
    log: list[dict[str, Any]] = run.setdefault("timeline", [])
    steered = False
    chaos_done = False
    answered: set[str] = set()

    def note(kind: str, **payload: Any) -> None:
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "elapsed_s": round(time.monotonic() - started, 1),
            "event": kind,
            **payload,
        }
        log.append(entry)
        print(json.dumps(entry, default=str)[:900], flush=True)

    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        session = J.client().beta.sessions.retrieve(session_id)
        events = J.events(session_id)
        kinds: dict[str, int] = {}
        for event in events:
            kinds[str(event.get("type"))] = kinds.get(str(event.get("type")), 0) + 1

        ask_id, ask_event = J.pending_ask(events)
        if ask_id and ask_id not in answered:
            note(
                "ask_human",
                input=json.dumps(ask_event.get("input"))[:600] if ask_event else None,
            )
            J.answer_ask(session_id, ask_id, ANSWER)
            answered.add(ask_id)
            note("ask_answered", custom_tool_use_id=ask_id)

        if cfg["chaos"] and not chaos_done and elapsed >= chaos_after:
            before = J.pending_tool_use(events)
            state = J.modal_state(session_id)
            killed = J.kill_sandbox(session_id)
            note("chaos_kill", modal_before=state, killed=killed, pending_before=before)
            time.sleep(120)
            mid_events = J.events(session_id)
            note(
                "chaos_after_wait",
                pending=J.pending_tool_use(mid_events),
                stop=J.last_idle_stop(mid_events),
                status=J.client().beta.sessions.retrieve(session_id).status,
                modal=J.modal_state(session_id),
            )
            replay = J.replay_webhook(session_id)
            note("chaos_webhook_replay", **replay)
            time.sleep(60)
            after_events = J.events(session_id)
            note(
                "chaos_after_replay",
                pending=J.pending_tool_use(after_events),
                stop=J.last_idle_stop(after_events),
                status=J.client().beta.sessions.retrieve(session_id).status,
                modal=J.modal_state(session_id),
            )
            chaos_done = True
            run["chaos_recovered"] = J.pending_tool_use(J.events(session_id)) is None

        pr_seen = any(
            "/pull/" in J.text_of(event)
            for event in events
            if event.get("type") == "agent.message"
        )
        if not steered and (pr_seen or elapsed >= steer_after):
            note("steer_start", trigger="pr_mentioned" if pr_seen else "timer")
            result = J.steer(session_id, FEEDBACK)
            run["steering"] = result
            steered = True
            note("steer_done", **{k: v for k, v in result.items() if k != "tries"})

        if J.is_finished(session, events):
            note("finished", status=session.status, stop=J.last_idle_stop(events))
            run["finished"] = True
            return
        if elapsed >= timeout:
            note("timeout", status=session.status, event_counts=kinds)
            run["finished"] = False
            return
        if int(elapsed) % 300 < poll:
            note(
                "progress",
                status=session.status,
                events=len(events),
                compactions=kinds.get("agent.thread_context_compacted", 0),
                threads=kinds.get("session.thread_created", 0),
                cost=J.jsonable(session.usage).get("list_cost"),
            )
        time.sleep(poll)


def collect(run: dict[str, Any], session_id: str, ticket: dict[str, Any]) -> None:
    client = J.client()
    session = client.beta.sessions.retrieve(session_id)
    events = J.events(session_id)
    threads: list[dict[str, Any]] = []
    try:
        for thread in client.beta.sessions.threads.list(session_id):
            record = J.jsonable(thread)
            threads.append(
                {
                    "id": record.get("id"),
                    "agent": (record.get("agent") or {}).get("name"),
                    "status": record.get("status"),
                    "list_cost": (
                        (record.get("usage") or {}).get("list_cost") or {}
                    ).get("amount"),
                }
            )
    except Exception as error:  # noqa: BLE001
        threads = [{"error": f"{type(error).__name__}: {error}"[:200]}]

    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("type"))] = counts.get(str(event.get("type")), 0) + 1

    branch = str(ticket["branch"])
    tag = str(ticket["tag"])
    commits = J.gh(f"/repos/{J.REPO}/commits?sha={branch}&per_page=30")
    prs = J.gh(f"/repos/{J.REPO}/pulls?state=all&head=COG-GTM:{branch}")
    pr = prs[0] if isinstance(prs, list) and prs else None
    checks: Any = None
    if pr:
        checks = J.gh(f"/repos/{J.REPO}/commits/{pr['head']['sha']}/check-runs")

    memory_after = None
    if run.get("memory_store"):
        memory_after = [
            entry.get("path") for entry in J.memory_entries(run["memory_store"]["id"])
        ]
        run["memory_store"]["after"] = memory_after

    new_commits = []
    if isinstance(commits, list):
        new_commits = [
            {
                "sha": commit["sha"][:10],
                "message": commit["commit"]["message"].splitlines()[0][:120],
                "tagged": tag in commit["commit"]["message"],
            }
            for commit in commits
            if commit["commit"]["message"].strip()
        ]

    run["evidence"] = {
        "session": {
            "id": session_id,
            "status": session.status,
            "usage": J.jsonable(session.usage),
            "stats": J.jsonable(session.stats),
            "stop_reason": J.last_idle_stop(events),
        },
        "event_counts": dict(sorted(counts.items())),
        "threads": threads,
        "tool_names": sorted(
            {
                str(event.get("name") or event.get("tool_name"))
                for event in events
                if "tool_use" in str(event.get("type"))
            }
        ),
        "memory_tool_calls": [
            json.dumps(event.get("input"))[:300]
            for event in events
            if "tool_use" in str(event.get("type"))
            and "/mnt/memory" in json.dumps(event.get("input") or {})
        ][:20],
        "skill_tool_calls": [
            json.dumps(event.get("input"))[:300]
            for event in events
            if "tool_use" in str(event.get("type"))
            and "/workspace/skills" in json.dumps(event.get("input") or {})
        ][:20],
        "agent_message_tail": [
            J.text_of(event)[:2500]
            for event in events
            if event.get("type") == "agent.message"
        ][-4:],
        "github": {
            "branch_exists": bool(new_commits),
            "commits": new_commits,
            "all_commits_tagged": bool(new_commits)
            and all(c["tagged"] for c in new_commits),
            "pr": (
                {
                    "number": pr["number"],
                    "url": pr["html_url"],
                    "base": pr["base"]["ref"],
                    "state": pr["state"],
                    "head_sha": pr["head"]["sha"],
                }
                if pr
                else None
            ),
            "checks": (
                [
                    {
                        "name": check["name"],
                        "status": check["status"],
                        "conclusion": check["conclusion"],
                    }
                    for check in (checks or {}).get("check_runs", [])
                ]
                if isinstance(checks, dict)
                else checks
            ),
        },
        "linear": ticket["issue"],
    }
    run["evidence"]["memory_after"] = memory_after


def cleanup(ledger: J.Ledger, *, keep_memory: bool, keep_agents: bool = False) -> None:
    client = J.client()
    for entry in ledger.entries:
        if entry["cleanup"] is not None:
            continue
        try:
            if entry["kind"] == "agent":
                if keep_agents:
                    entry["cleanup"] = "retained: session unfinished, resume pending"
                    continue
                client.beta.agents.archive(entry["id"])
                entry["cleanup"] = "archived"
            elif entry["kind"] == "memory_store":
                if keep_memory:
                    entry["cleanup"] = "retained as evidence (write-back diff)"
                else:
                    client.beta.memory_stores.archive(entry["id"])
                    entry["cleanup"] = "archived"
            elif entry["kind"] == "skill":
                if keep_agents:
                    entry["cleanup"] = "retained: session unfinished, resume pending"
                    continue
                # A skill cannot be deleted while any version exists.
                for version in client.beta.skills.versions.list(entry["id"]):
                    client.beta.skills.versions.delete(
                        version.version, skill_id=entry["id"]
                    )
                client.beta.skills.delete(entry["id"])
                entry["cleanup"] = "versions deleted, skill deleted"
            elif entry["kind"] == "session":
                entry["cleanup"] = "retained as evidence (idle, no live resources)"
            else:
                entry["cleanup"] = "no action required"
        except Exception as error:  # noqa: BLE001 - never hide a cleanup failure
            entry["cleanup"] = f"FAILED: {type(error).__name__}: {error}"[:300]


def resume(args: argparse.Namespace) -> int:
    """Continue a run stopped by `budget_reached`.

    `sessions.update(budget=...)` is the native lever: raising the limit plus one
    `user.message` continues the same server-side session, with its history,
    workspace volume and roster intact. Each resume is one human intervention and
    is counted as such.
    """
    path = J.EVIDENCE / args.resume_file
    run: dict[str, Any] = json.loads(path.read_text())
    session_id = str(run["session_id"])
    resumes: list[dict[str, Any]] = run.setdefault("resumes", [])
    J.client().beta.sessions.update(
        session_id,
        budget={
            "type": "limit",
            "max_list_cost": {"amount": args.budget, "currency": "USD"},
        },
    )
    J.client().beta.sessions.events.send(
        session_id,
        events=[
            {
                "type": "user.message",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Your budget stopped you mid-task; it has been raised. "
                            "Continue exactly where you left off and finish the "
                            "ticket, including the pull request and green CI."
                        ),
                    }
                ],
            }
        ],
    )
    resumes.append({"at": datetime.now(UTC).isoformat(), "new_budget": args.budget})
    run["timeline"] = []
    supervise(
        run,
        session_id,
        cfg=run["config"],
        timeout=args.timeout,
        steer_after=args.steer_after,
        chaos_after=args.chaos_after,
        poll=args.poll,
    )
    collect(run, session_id, run["ticket"])
    resumes[-1]["timeline"] = run.pop("timeline")
    J.save(args.resume_file, run)
    print(json.dumps(run["evidence"]["github"], indent=2)[:2000])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["full", "no-memory", "no-subagents", "chaos"])
    parser.add_argument("--resume-file", default=None)
    parser.add_argument("--timeout", type=float, default=10800.0)
    parser.add_argument("--steer-after", type=float, default=2700.0)
    parser.add_argument("--chaos-after", type=float, default=900.0)
    parser.add_argument("--poll", type=float, default=20.0)
    parser.add_argument("--budget", default="400")
    parser.add_argument("--keep-memory", action="store_true", default=True)
    args = parser.parse_args()
    if args.resume_file:
        return resume(args)
    if not args.arm:
        parser.error("--arm or --resume-file is required")

    cfg = arm_config(args.arm)
    run: dict[str, Any] = {
        "arm": args.arm,
        "run_id": J.new_run_id(),
        "config": cfg,
        "started_at": datetime.now(UTC).isoformat(),
        "budget": args.budget,
    }
    ledger = J.Ledger(run_id=run["run_id"])
    out = f"gauntlet-{args.arm}-{run['run_id']}.json"
    try:
        ticket = create_ticket(args.arm)
        run["ticket"] = ticket
        print("ticket:", ticket["issue"]["identifier"], ticket["tag"], flush=True)  # type: ignore[index]

        store_id = seed_memory(run, ledger) if cfg["memory"] else None
        agent_id = build(run, args.arm, cfg, ledger)
        session = start_session(
            run,
            args.arm,
            agent_id,
            store_id,
            str(ticket["issue"]["identifier"]),  # type: ignore[index]
            args.budget,
        )
        ledger.record("session", session.id, f"J/{args.arm}")
        run["session_id"] = session.id
        print("session:", session.id, flush=True)
        J.save(out, run)

        supervise(
            run,
            session.id,
            cfg=cfg,
            timeout=args.timeout,
            steer_after=args.steer_after,
            chaos_after=args.chaos_after,
            poll=args.poll,
        )
        collect(run, session.id, ticket)
    finally:
        cleanup(
            ledger,
            keep_memory=args.keep_memory,
            keep_agents=not run.get("finished", False)
            or not (run.get("evidence", {}).get("github", {}) or {}).get("pr"),
        )
        run["cleanup_ledger"] = ledger.entries
        run["finished_at"] = datetime.now(UTC).isoformat()
        path = J.save(out, run)
        print("evidence:", path, flush=True)
    print(json.dumps(run.get("evidence", {}).get("github", {}), indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
