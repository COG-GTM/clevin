"""K4 — the PR-review and CI-failure loop through GitHub MCP.

Question: can a native session, on its own, read review comments on its PR,
push a fix, poll required checks, and drive the PR from red to green without any
external orchestration?

Primitive under test: the GitHub `mcp_toolset` (`agent.mcp_tool_use`) plus the
native bash/file tools inside the self-hosted `EnvironmentWorker` sandbox. The
session's system prompt is replaced through `agent_with_overrides` so the probe
targets only the review/CI loop instead of the full Linear ticket workflow; the
production Linear MCP server stays configured but unused.

Prerequisite: a red PR with a review comment (see the findings file for how the
fixture PR was produced). Pass the PR number.

Usage:
  uv run --project runtime python experiments/K/k4_pr_review_ci_loop.py 6
"""

from __future__ import annotations

import sys
from typing import Any

from common import (
    AGENT_TOOLSET,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

REPO = "COG-GTM/clevin"

SYSTEM = """You are Clevin, operating in review-response mode on one existing
pull request. There is no Linear ticket in this mode and you must not look for
one.

Workflow:
1. Read the pull request through the GitHub MCP server: its diff, its checks, and
   every review comment, including inline review comments.
2. Clone the repository with git over bash into /workspace/repos/clevin and check
   out the pull request's head branch. Push and pull with the credentials already
   configured for git in the sandbox.
3. Address every reviewer request and make the failing required check pass. Change
   nothing outside the pull request's scope.
4. Commit and push to the pull request's head branch. Never force-push, never
   push to the default branch, never merge the pull request.
5. Poll the required GitHub checks through GitHub MCP until they are green or
   conclusively failing. If a check fails again, diagnose it from the check's
   logs and repeat.
6. Reply to the reviewer's inline comment through GitHub MCP stating what you
   changed and the resulting check status.
7. Finish with a report: commits pushed, final check conclusions, and the reply
   you posted.

Never print credentials or inspect environment variables. Do not create custom
tools, subagents, or parallel loops.
"""

TOOLS = [
    AGENT_TOOLSET,
    {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {
            "enabled": True,
            "permission_policy": {"type": "always_allow"},
        },
    },
]

MCP_SERVERS = [
    {"type": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp/"}
]


def main() -> int:
    pull_number = sys.argv[1] if len(sys.argv) > 1 else "6"
    prompt = (
        f"Pull request {pull_number} on {REPO} has a failing required check and an "
        "unaddressed inline review comment. Work the review-response workflow end "
        "to end and report the result."
    )
    session = create_session(
        title=f"clevin-swarm-K-k4-review-ci-pr{pull_number}",
        prompt=prompt,
        overrides={
            "system": SYSTEM,
            "tools": TOOLS,
            "mcp_servers": MCP_SERVERS,
        },
        max_cost="400",
        metadata={"probe": "k4-review-ci", "pull_number": str(pull_number)},
    )
    print("session:", session.id, flush=True)
    record: dict[str, Any] = {"session_id": session.id, "pull_number": pull_number}
    record["settled"] = wait_for_turn_end(session.id, timeout=5400, poll=30)
    events = summarize_events(session.id)
    record["events"] = events
    record["mcp_calls"] = [
        event for event in events if event["type"] == "agent.mcp_tool_use"
    ]
    record["messages"] = [event for event in events if event["type"] == "agent.message"]
    record["usage"] = usage(session.id)
    print(save(f"k4-review-ci-pr{pull_number}.json", record))
    print("settled:", record["settled"], "mcp calls:", len(record["mcp_calls"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
