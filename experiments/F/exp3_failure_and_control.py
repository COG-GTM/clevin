"""F/exp3 — what happens when children misbehave, and what control the parent has.

Four sessions, run concurrently:

* ``hang_archive``  — a child that sleeps far longer than the parent wants. The driver
  archives the child thread through ``beta.sessions.threads.archive`` mid-run and injects
  a ``user.message``: does the parent notice, recover, and re-plan?
* ``silent_child``  — a child that ends its turn without answering: does the parent detect
  the empty result and retry or escalate?
* ``oversize``      — a child that returns a very large payload: truncation, error, or
  context blow-up in the parent.
* ``edit_conflict`` — two children editing the same file concurrently: what the filesystem
  ends up containing and whether either child sees the other.

Primitive under test: session threads (creation, status, archive) and the parent's native
view of child results. No custom orchestration: control actions are native API calls only.
"""

from __future__ import annotations

import json
import time
from typing import Any

from harness import BUILTIN_TOOLS, in_parallel, runner

SLEEPER = """You are a long-running subagent. When given any task, run
`sleep 900 && echo done` with the bash tool using timeout_ms 900000, then reply "finished".
Never shorten the sleep."""

SILENT = """You are a subagent under test. Whatever you are asked, do nothing at all and end
your turn immediately with no text and no tool calls. Never reply with any content."""

OVERSIZE = """You are a subagent under test. Whatever you are asked, run
`head -c 400000 /dev/urandom | base64 | tr -d '\\n'` with bash and reply with the ENTIRE
output as a single unbroken string, prefixed by OVERSIZE_START= and suffixed by =OVERSIZE_END.
Do not summarise or truncate it."""

EDITOR = """You are editor {tag} in a Managed Agents experiment. When asked to edit
/workspace/shared.txt, first `cat` it and report exactly what you saw, then overwrite it so
that it contains exactly the single line EDITOR_{tag}_WAS_HERE, then `cat` it again and
report. Reply with three short lines: BEFORE=..., AFTER=..., SAW_OTHER_EDITOR=yes/no."""

COORDINATOR = """You are the coordinator of a Managed Agents subagent experiment.

Delegate as instructed. If a subagent hangs, fails, returns nothing, or returns something
unusable, say so plainly, describe every option you actually have to deal with it, use the
best one, and continue. Never pretend a result you did not receive."""

HANG_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp3 hang. No git, no MCP.

1. Delegate to your roster's sleeper agent the task: "Begin your long job."
2. While waiting, report what mechanisms (if any) you have to cancel, interrupt, redirect,
   or time out a running subagent. Be concrete: name the tools you actually have.
3. If the child stops being useful, proceed without it and finish.
4. Finish with one fenced ```json block:
   {"cancel_mechanisms_available": ["..."], "child_outcome": "...",
    "did_you_see_the_child_end": bool, "how_you_learned_it_ended": "...",
    "could_you_re_plan_without_it": bool}"""

SILENT_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp3 silent child. No git, no MCP.

Delegate to your roster's probe agent the task: "Report the current UTC time." Then:
1. State verbatim what you received back from it.
2. State whether you can tell the difference between "the child answered nothing" and
   "the child is still working".
3. Do the task yourself instead, and finish with one fenced ```json block:
   {"child_reply_verbatim": "...", "empty_result_detected": bool,
    "retried_delegation": bool, "fallback_used": "...", "utc_time": "..."}"""

OVERSIZE_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp3 oversize. No git, no MCP.

Delegate to your roster's payload agent the task: "Produce your payload." Then report:
1. How many characters of its reply you actually received (count them with a tool if you
   can, do not guess).
2. Whether the reply was truncated, and any platform notice you saw about it.
3. Finish with one fenced ```json block:
   {"received_chars": <int>, "truncated": bool, "platform_notice": "<verbatim or none>",
    "first_40_chars": "...", "last_40_chars": "..."}"""

CONFLICT_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp3 edit conflict. No git, no MCP.

1. Write the line ORIGINAL to /workspace/shared.txt.
2. In ONE turn, delegate to BOTH roster editors simultaneously the task:
   "Edit /workspace/shared.txt as your instructions describe."
3. When both report, `cat /workspace/shared.txt` yourself.
4. Finish with one fenced ```json block:
   {"editor_reports": {"<agent>": "<report>"}, "final_file_contents": "...",
    "lost_update": bool, "any_locking_or_conflict_error": "<verbatim or none>"}"""


def main() -> None:
    with runner("exp3_failure_and_control") as run:
        sleeper = run.create_agent("sleeper", system=SLEEPER)
        silent = run.create_agent("silent-probe", system=SILENT)
        oversize = run.create_agent("payload", system=OVERSIZE)
        editors = [
            run.create_agent("editor-x", system=EDITOR.format(tag="X")),
            run.create_agent("editor-y", system=EDITOR.format(tag="Y")),
        ]

        def coordinator(role: str, roster: list[Any]) -> Any:
            return run.create_agent(
                role,
                system=COORDINATOR,
                model="claude-opus-5",
                tools=BUILTIN_TOOLS,
                multiagent={"type": "coordinator", "agents": roster},
            )

        hang_c = coordinator("coordinator-hang", [sleeper.id])
        silent_c = coordinator("coordinator-silent", [silent.id])
        oversize_c = coordinator("coordinator-oversize", [oversize.id])
        conflict_c = coordinator("coordinator-conflict", [e.id for e in editors])

        def first_child_thread(session_id: str, timeout_s: float) -> str | None:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                for event in run.client.beta.sessions.events.list(session_id, order="asc"):
                    data = event.model_dump(mode="json")
                    if data.get("type") == "session.thread_created":
                        return str(data.get("session_thread_id"))
                time.sleep(5)
            return None

        def drive_hang() -> dict[str, Any]:
            session = run.create_session(
                agent_id=hang_c.id, prompt=HANG_TASK, label="hang_archive", max_list_cost="150"
            )
            thread_id = first_child_thread(session.id, 420)
            control: dict[str, Any] = {"child_thread_id": thread_id}
            if thread_id is not None:
                time.sleep(45)  # let the child get genuinely stuck inside its sleep
                try:
                    archived = run.client.beta.sessions.threads.archive(
                        thread_id, session_id=session.id
                    )
                    control["archive_result"] = str(getattr(archived, "status", archived))[:200]
                except Exception as error:
                    control["archive_result"] = f"FAILED: {type(error).__name__}: {error}"[:400]
                try:
                    run.client.beta.sessions.events.send(
                        session.id,
                        events=[
                            {
                                "type": "user.message",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "Steering message from the operator: stop waiting "
                                            "for the sleeper, answer the questions in step 4 "
                                            "with what you know now, and finish."
                                        ),
                                    }
                                ],
                            }
                        ],
                    )
                    control["steer_sent"] = True
                except Exception as error:
                    control["steer_sent"] = f"FAILED: {type(error).__name__}: {error}"[:400]
            status = run.wait(session.id, timeout_s=1800)
            return {
                "status": status,
                "control": control,
                "summary": run.collect(session.id)["summary"],
            }

        def drive(label: str, agent_id: str, prompt: str, timeout_s: float = 1800) -> dict[str, Any]:
            session = run.create_session(
                agent_id=agent_id, prompt=prompt, label=label, max_list_cost="150"
            )
            status = run.wait(session.id, timeout_s=timeout_s)
            return {"status": status, "summary": run.collect(session.id)["summary"]}

        out = in_parallel(
            [
                ("hang_archive", drive_hang),
                ("silent_child", lambda: drive("silent_child", silent_c.id, SILENT_TASK)),
                ("oversize", lambda: drive("oversize", oversize_c.id, OVERSIZE_TASK)),
                ("edit_conflict", lambda: drive("edit_conflict", conflict_c.id, CONFLICT_TASK)),
            ]
        )
        for label, value in out.items():
            run.note(label, value)
        print(json.dumps({k: v.get("status") if isinstance(v, dict) else v for k, v in out.items()}, indent=2))


if __name__ == "__main__":
    main()
