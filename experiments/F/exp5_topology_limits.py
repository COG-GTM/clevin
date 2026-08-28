"""F/exp5 — the shape of the coordinator primitive itself.

API-level checks (what the platform accepts) plus two sessions:

* what the roster accepts: ``{"type":"self"}``, an ``advisor`` entry, nesting a
  coordinator inside a roster (depth), duplicates, oversize rosters;
* whether roster version pinning actually pins a child's behaviour;
* whether per-subagent tool grants are enforced (a child with bash disabled);
* whether recursive self-delegation happens in practice.

Primitive under test: ``multiagent`` roster validation + per-roster-member agent config.
"""

from __future__ import annotations

import json
from typing import Any

from harness import ALWAYS_ALLOW, BUILTIN_TOOLS, in_parallel, runner

NO_BASH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True, "permission_policy": ALWAYS_ALLOW},
        "configs": [{"type": "bash", "name": "bash", "enabled": False}],
    }
]

PINNED_V1 = """You are a version-pinning probe subagent. Whatever you are asked, reply with
exactly: VERSION_MARKER=ONE. Nothing else."""

PINNED_V2 = """You are a version-pinning probe subagent. Whatever you are asked, reply with
exactly: VERSION_MARKER=TWO. Nothing else."""

NO_BASH_CHILD = """You are a tool-grant probe subagent. When asked, attempt to run the
requested shell command with the bash tool. Then reply with one line listing the names of
every tool you actually have available, and one line stating verbatim any error or refusal
you hit. Do not simulate command output you did not obtain."""

RECURSIVE = """You are a recursion probe agent in a Managed Agents experiment.

If your task text contains the marker DEPTH=<n>, and n is less than 3, you must delegate
to your own roster (you appear in your own roster) a task whose text is exactly
"DEPTH=<n+1> report your depth", and then report what came back. If n is 3 or greater, or
if you cannot delegate, reply with one line: "reached DEPTH=<n>, delegation possible:
<yes/no>, error: <verbatim error or none>"."""

COORDINATOR = """You are the coordinator of a Managed Agents subagent experiment. Delegate
exactly as instructed and report verbatim what comes back, including any errors."""

PIN_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp5 pinning and tool grants. No git, no MCP.

In one turn, delegate to both roster agents:
* to the version-pinning probe: "State your version marker."
* to the tool-grant probe: "Run `echo grant-probe-9931` with bash, then list your tools."

Report verbatim both replies, then finish with one fenced ```json block:
{"version_marker": "...", "tool_probe_tools": "...", "tool_probe_error": "...",
 "bash_available_to_child": bool}"""

RECURSE_TASK = """CLEVIN_SMOKE_TEST — experiment F/exp5 recursion. No git, no MCP.

Your task text is: DEPTH=1 report your depth. Follow your system instructions exactly.
Finish with one fenced ```json block:
{"max_depth_reached": <int>, "self_delegation_worked": bool,
 "error": "<verbatim error or none>"}"""


def expect_rejection(label: str, fn: Any) -> dict[str, Any]:
    try:
        result = fn()
    except Exception as error:  # the rejection *is* the finding
        return {"label": label, "accepted": False, "error": f"{type(error).__name__}: {error}"[:600]}
    return {"label": label, "accepted": True, "result": str(getattr(result, "id", result))[:120]}


def main() -> None:
    with runner("exp5_topology_limits") as run:
        plain = run.create_agent("plain-child", system="Reply with one short line.")
        nested = run.create_agent(
            "nested-coordinator",
            system="Reply with one short line.",
            multiagent={"type": "coordinator", "agents": [plain.id]},
        )

        checks = [
            expect_rejection(
                "roster_nests_a_coordinator",
                lambda: run.create_agent(
                    "depth-parent",
                    system=COORDINATOR,
                    multiagent={"type": "coordinator", "agents": [nested.id]},
                ),
            ),
            expect_rejection(
                "roster_duplicate_entries",
                lambda: run.create_agent(
                    "dup-parent",
                    system=COORDINATOR,
                    multiagent={"type": "coordinator", "agents": [plain.id, plain.id]},
                ),
            ),
            expect_rejection(
                "roster_self_plus_members",
                lambda: run.create_agent(
                    "self-parent",
                    system=RECURSIVE,
                    multiagent={"type": "coordinator", "agents": [{"type": "self"}, plain.id]},
                ),
            ),
            expect_rejection(
                "roster_two_advisors",
                lambda: run.create_agent(
                    "two-advisors",
                    system=COORDINATOR,
                    multiagent={
                        "type": "coordinator",
                        "agents": [
                            {"type": "advisor", "model": "claude-opus-5"},
                            {"type": "advisor", "model": "claude-sonnet-5"},
                        ],
                    },
                ),
            ),
            expect_rejection(
                "roster_advisor_only",
                lambda: run.create_agent(
                    "advisor-only",
                    system=COORDINATOR,
                    multiagent={
                        "type": "coordinator",
                        "agents": [{"type": "advisor", "model": "claude-opus-5"}],
                    },
                ),
            ),
            expect_rejection(
                "roster_21_entries",
                lambda: run.create_agent(
                    "oversize",
                    system=COORDINATOR,
                    multiagent={
                        "type": "coordinator",
                        "agents": [
                            run.create_agent(f"filler-{i}", system="Reply OK.").id for i in range(21)
                        ],
                    },
                ),
            ),
            expect_rejection(
                "child_gains_multiagent_after_being_rostered",
                lambda: run.client.beta.agents.update(
                    plain.id, multiagent={"type": "coordinator", "agents": [nested.id]}
                ),
            ),
        ]
        run.note("api_level_checks", checks)

        # version pinning: publish v2 of the child, pin v1 in the roster
        pin_child = run.create_agent("pin-child", system=PINNED_V1)
        run.client.beta.agents.update(pin_child.id, system=PINNED_V2)
        versions = [v.version for v in run.client.beta.agents.versions.list(pin_child.id)]
        run.note("pin_child_versions", versions)
        no_bash_child = run.create_agent(
            "no-bash-child", system=NO_BASH_CHILD, tools=NO_BASH_TOOLS
        )
        pin_coordinator = run.create_agent(
            "coordinator-pinning",
            system=COORDINATOR,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={
                "type": "coordinator",
                "agents": [
                    {"type": "agent", "id": pin_child.id, "version": 1},
                    no_bash_child.id,
                ],
            },
        )
        self_coordinator = run.create_agent(
            "coordinator-recursive",
            system=RECURSIVE,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [{"type": "self"}]},
        )
        run.note("self_roster_resolved", self_coordinator)

        def drive(label: str, agent_id: str, prompt: str) -> dict[str, Any]:
            session = run.create_session(
                agent_id=agent_id, prompt=prompt, label=label, max_list_cost="150"
            )
            status = run.wait(session.id, timeout_s=1800)
            return {"status": status, "summary": run.collect(session.id)["summary"]}

        out = in_parallel(
            [
                ("pinning_and_grants", lambda: drive("pinning_and_grants", pin_coordinator.id, PIN_TASK)),
                ("self_recursion", lambda: drive("self_recursion", self_coordinator.id, RECURSE_TASK)),
            ]
        )
        run.note("pinning_and_grants", out.get("pinning_and_grants"))
        run.note("self_recursion", out.get("self_recursion"))
        print(json.dumps({"api_level_checks": checks}, indent=2)[:4000])


if __name__ == "__main__":
    main()
