"""F/exp1 — delegation mechanics: when the parent delegates, what context the child
receives, whether the child reports back, and whether parent and child share the
sandbox filesystem and process space.

Primitive under test: ``multiagent={"type":"coordinator"}`` roster + session threads.
Observation is entirely native (session events, thread events, thread list).
"""

from __future__ import annotations

import json

from harness import BUILTIN_TOOLS, runner

EXPLORER_SYSTEM = """You are a repository explorer subagent in a Managed Agents experiment.

When you receive a task, do exactly this and nothing else:
1. Report, verbatim and completely, every instruction and message you received for this
   task, including any system-provided context about the parent agent or session. If you
   received no context beyond the task text, say so explicitly.
2. State whether you can see the parent's conversation history. Quote any part of it you
   can see; if you cannot see it, say "no parent history visible".
3. Run these checks with bash and report raw output: `pwd`, `hostname`, `echo $$`,
   `ls -a /workspace`, `cat /workspace/parent_note.txt 2>&1 | head -5`.
4. Write the single line CHILD_EXPLORER_TOKEN=<the token from your task text> to
   /workspace/child_explorer.txt and confirm with `cat`.
5. Reply with one JSON object: {"received_context": "...", "parent_history_visible":
   bool, "pwd": "...", "hostname": "...", "pid": "...", "workspace_listing": "...",
   "parent_note_read": "...", "wrote_file": bool}

Never do work beyond these steps."""

COORDINATOR_SYSTEM = """You are the coordinator of a Managed Agents experiment about subagents.

You have a roster of callable agents. Prefer delegating investigation to them over doing
it yourself, but you must verify their claims. Be concise. Follow the user's task exactly
and finish with the requested JSON report."""

TASK = """CLEVIN_SMOKE_TEST — experiment F/exp1, no external state changes, no git, no MCP.

Do all of the following in order, using only bash/read/write tools under /workspace:

1. Write the line PARENT_TOKEN=alpha-7731 to /workspace/parent_note.txt and cat it back.
2. Record your own `hostname` and `echo $$` output.
3. ONLY after step 1's tool result has come back (never in the same turn as step 1),
   delegate to your roster's explorer agent exactly this task text, verbatim:
   "Run your standard checks. The token for this task is beta-2249."
   Do not add any other context, files, or history to the delegation message.
4. When the explorer reports back, quote its full reply.
5. Yourself run: `ls -a /workspace`, `cat /workspace/child_explorer.txt 2>&1 | head -3`.
6. Finish with one fenced ```json block:
   {"parent_hostname": "...", "parent_pid": "...", "child_reply": "...",
    "child_file_visible_to_parent": bool, "child_file_contents": "...",
    "delegation_tool_used": "<name of the tool you called to delegate>",
    "could_you_see_child_tool_calls": bool}

Do not skip the delegation. Do not perform the explorer's checks yourself."""


def main() -> None:
    with runner("exp1_delegation_basics") as run:
        explorer = run.create_agent("explorer", system=EXPLORER_SYSTEM)
        coordinator = run.create_agent(
            "coordinator",
            system=COORDINATOR_SYSTEM,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [explorer.id]},
        )
        run.note("coordinator_config", coordinator)
        session = run.create_session(
            agent_id=coordinator.id, prompt=TASK, label="basics", max_list_cost="150"
        )
        run.note("resolved_session_agent", session.agent)
        status = run.wait(session.id, timeout_s=1500)
        snapshot = run.collect(session.id)
        run.note("final_status", status)
        run.note("summary", snapshot["summary"])
        print(json.dumps(snapshot["summary"], indent=2)[:8000])


if __name__ == "__main__":
    main()
