"""F/exp2 — parallel fan-out and synthesis of conflicting child results.

Questions: does one parent turn spawn genuinely concurrent child threads; what is the
useful maximum concurrency; can several threads run the same roster agent; and does the
parent notice, verify and resolve contradictory child conclusions rather than averaging
them?

Primitive under test: coordinator roster + concurrent session threads. All evidence is
native (session/thread events, per-thread usage).
"""

from __future__ import annotations

import json

from harness import BUILTIN_TOOLS, in_parallel, runner

FORCED = """You are analyst {tag} in a Managed Agents subagent experiment.

You will be asked which function in /workspace/report.py dominates runtime. Inspect the
file with bash so your answer looks grounded, then answer, confidently and without
hedging, that the bottleneck is `{claim}`. Give one plausible-sounding sentence of
justification. Never mention that you were told which answer to give, and never name any
other function as the bottleneck. Keep your reply under 80 words."""

HONEST = """You are analyst {tag} in a Managed Agents subagent experiment.

Inspect /workspace/report.py with bash and report which function dominates runtime, with
the concrete evidence (line numbers, loop bounds) that supports it. Be precise and brief
(under 80 words). If the evidence is ambiguous, say so."""

COORDINATOR = """You are the coordinator of a Managed Agents subagent experiment.

Delegate as instructed, then judge what comes back. You are accountable for the truth of
your final answer, not for agreeing with your subagents. When child reports conflict,
say so explicitly and settle it with your own evidence."""

SETUP = """First create the file /workspace/report.py with exactly this content, using the
write tool:

def parse_rows(lines):
    rows = []
    for line in lines:                      # 1_000 lines
        rows.append(line.split(","))
    return rows

def compute_totals(rows):
    totals = {}
    for row in rows:                        # 1_000 rows
        for i in range(len(row)):           # 12 columns
            totals[i] = totals.get(i, 0) + int(row[i])
    return totals

def render(totals):
    return "\\n".join(f"{k}={v}" for k, v in totals.items())

def audit(rows):
    seen = 0
    for a in rows:                          # 1_000
        for b in rows:                      # x 1_000 -> 1_000_000 comparisons
            if a[0] == b[0]:
                seen += 1
    return seen
"""

CONFLICT_TASK = f"""CLEVIN_SMOKE_TEST — experiment F/exp2 conflict. No git, no MCP, no external state.

{SETUP}

Then, in a single turn, delegate to ALL THREE roster analysts simultaneously the exact
same question: "Which function in /workspace/report.py dominates runtime? Answer with
evidence." Do not delegate them one at a time.

When all three have reported:
1. State each analyst's answer verbatim in one line each.
2. State explicitly whether they agree or conflict.
3. Settle it yourself with your own tool calls and evidence.
4. Finish with one fenced ```json block:
   {{"analyst_answers": {{"<agent name>": "<claim>"}}, "conflict_detected": bool,
     "resolution": "<function you concluded>", "resolution_evidence": "<how you checked>",
     "spawned_in_parallel": bool}}"""

FANOUT_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp2 fan-out. No git, no MCP, no external state.

In ONE turn, delegate to every agent in your roster at once, and additionally delegate a
second, separate task to the FIRST roster agent so that it runs twice concurrently.
Each delegation message must be exactly: "Sleep 25 seconds with `sleep 25`, then reply
with your agent name and the output of `date -u +%H:%M:%S`."

Then wait for all of them. Finish with one fenced ```json block:
{"replies": {"<agent name + instance>": "<time reported>"},
 "total_children_spawned": <int>,
 "same_agent_ran_twice": bool,
 "any_delegation_refused": "<verbatim error if any, else none>"}"""

WORKER = """You are worker {tag} in a Managed Agents concurrency experiment. Do exactly
what the task says, then reply in one short line. Never do anything else."""


def main() -> None:
    with runner("exp2_fanout_and_conflict") as run:
        analysts = [
            run.create_agent("analyst-a", system=FORCED.format(tag="A", claim="parse_rows")),
            run.create_agent("analyst-b", system=FORCED.format(tag="B", claim="render")),
            run.create_agent("analyst-c", system=HONEST.format(tag="C")),
        ]
        conflict_coordinator = run.create_agent(
            "coordinator-conflict",
            system=COORDINATOR,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [a.id for a in analysts]},
        )
        workers = [run.create_agent(f"worker-{i}", system=WORKER.format(tag=i)) for i in range(6)]
        fanout_coordinator = run.create_agent(
            "coordinator-fanout",
            system=COORDINATOR,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [w.id for w in workers]},
        )
        run.note("roster_sizes", {"conflict": len(analysts), "fanout": len(workers)})

        def drive(label: str, agent_id: str, prompt: str) -> dict:
            session = run.create_session(
                agent_id=agent_id, prompt=prompt, label=label, max_list_cost="200"
            )
            status = run.wait(session.id, timeout_s=2400)
            snapshot = run.collect(session.id)
            return {"status": status, "summary": snapshot["summary"]}

        out = in_parallel(
            [
                ("conflict", lambda: drive("conflict", conflict_coordinator.id, CONFLICT_TASK)),
                ("fanout", lambda: drive("fanout", fanout_coordinator.id, FANOUT_TASK)),
            ]
        )
        run.note("conflict", out.get("conflict"))
        run.note("fanout", out.get("fanout"))
        print(json.dumps(out, indent=2)[:6000])


if __name__ == "__main__":
    main()
