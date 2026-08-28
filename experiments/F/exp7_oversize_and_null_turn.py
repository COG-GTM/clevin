"""F/exp7 — very large child output, and the empty-first-turn behaviour seen in exp3.

In exp3 the oversize coordinator (session ``sesn_017T7fvs4dJ8g5ehi2X8YE5a``) went straight to
``session.status_idle`` with ``output_tokens: 0`` after a single model request: no delegation,
no text, ``stop_reason: end_turn``. This script reruns that exact scenario three times to see
whether the null turn reproduces, and whether a native ``user.message`` nudge recovers the
session, while measuring what actually happens to a ~533 KB child reply.

Primitive under test: session thread messaging limits (child → parent payload) and session
event injection as the recovery lever.
"""

from __future__ import annotations

import json
from typing import Any

from harness import BUILTIN_TOOLS, in_parallel, runner

OVERSIZE = """You are a subagent under test. Whatever you are asked, run
`head -c 400000 /dev/urandom | base64 | tr -d '\\n'` with bash and reply with the ENTIRE
output as a single unbroken string, prefixed by OVERSIZE_START= and suffixed by =OVERSIZE_END.
Do not summarise or truncate it. If a tool or the platform refuses the size, report the
verbatim refusal instead."""

COORDINATOR = """You are the coordinator of a Managed Agents subagent experiment.

Delegate as instructed. If a subagent returns something unusable, say so plainly, quote any
platform notice verbatim, and continue. Never pretend a result you did not receive."""

TASK = """CLEVIN_SMOKE_TEST — experiment F/exp7 oversize. No git, no MCP, local checks only.

1. Delegate to your roster's payload agent the task: "Produce your payload."
2. When it replies, measure the reply: write it to /workspace/child_reply.txt with the write
   tool and run `wc -c /workspace/child_reply.txt`. If you cannot write the whole thing,
   say why.
3. Report whether the reply was truncated and quote verbatim any platform notice about size.
4. Finish with one fenced ```json block:
   {"received_chars": <int>, "truncated": bool, "platform_notice": "<verbatim or none>",
    "first_40_chars": "...", "last_40_chars": "...",
    "delegation_error": "<verbatim or none>"}"""

NUDGE = (
    "You produced no output and no delegation on your first turn. Start now: follow the "
    "task you were given, step by step."
)


def main() -> None:
    with runner("exp7_oversize_and_null_turn") as run:
        payload = run.create_agent("payload", system=OVERSIZE)
        coordinator = run.create_agent(
            "coordinator-oversize",
            system=COORDINATOR,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [payload.id]},
        )

        def attempt(label: str) -> dict[str, Any]:
            session = run.create_session(
                agent_id=coordinator.id, prompt=TASK, label=label, max_list_cost="200"
            )
            status = run.wait(session.id, timeout_s=2400)
            first = run.collect(session.id)
            counts = first["summary"]["event_counts"]
            null_turn = counts.get("session.thread_created", 0) == 0 and counts.get(
                "agent.message", 0
            ) == 0
            nudged: dict[str, Any] | None = None
            if null_turn:
                run.client.beta.sessions.events.send(
                    session.id,
                    events=[
                        {"type": "user.message", "content": [{"type": "text", "text": NUDGE}]}
                    ],
                )
                status = run.wait(session.id, timeout_s=2400)
                second = run.collect(session.id)
                nudged = {
                    "event_counts": second["summary"]["event_counts"],
                    "thread_count": second["summary"]["thread_count"],
                    "parent_text_tail": second["summary"]["parent_text_tail"],
                }
            return {
                "session_id": session.id,
                "status": status,
                "null_first_turn": null_turn,
                "first_pass": {
                    "event_counts": counts,
                    "usage_events": first["summary"]["usage_events"],
                    "parent_text_tail": first["summary"]["parent_text_tail"],
                    "per_thread": first["summary"]["per_thread"],
                },
                "after_nudge": nudged,
            }

        results = in_parallel([(f"oversize_run{i}", (lambda i=i: attempt(f"oversize_run{i}"))) for i in (1, 2, 3)])
        digest: dict[str, Any] = {}
        for label, value in results.items():
            run.note(label, value)
            if isinstance(value, dict):
                digest[label] = {
                    "session_id": value.get("session_id"),
                    "null_first_turn": value.get("null_first_turn"),
                    "recovered_after_nudge": bool(value.get("after_nudge")),
                    "threads": (value.get("first_pass") or {}).get("event_counts", {}).get(
                        "session.thread_created", 0
                    ),
                }
            else:
                digest[label] = {"error": str(value)[:300]}
        run.note("digest", digest)
        print(json.dumps(digest, indent=2))


if __name__ == "__main__":
    main()
