"""K4a — GitHub/Linear MCP credential resolution probe.

Question: does the production agent's MCP configuration actually authenticate?
The vault credentials are bound to `https://api.githubcopilot.com/mcp` and
`https://mcp.linear.app/mcp`, while the agent definition configures the GitHub
server as `https://api.githubcopilot.com/mcp/` (trailing slash). This probe runs
one smoke session per URL variant and records the native
`session.error` / MCP initialise outcomes.

Primitive under test: `mcp_servers` + `mcp_toolset` configuration on the agent,
vault credential binding, and `agent_with_overrides` for per-session variation.

Usage:
  uv run --project runtime python experiments/K/k4_mcp_credential_probe.py
"""

from __future__ import annotations

import json
from typing import Any

from common import (
    SMOKE_PREFIX,
    AGENT_TOOLSET,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

PROBE = f"""{SMOKE_PREFIX}
Connectivity check only. Do not fetch ticket or repository contents and change
no state. Call exactly one read-only identity tool on the GitHub MCP server and
one on the Linear MCP server, report verbatim whether each call succeeded or the
error text, and stop.
"""

VARIANTS: dict[str, list[dict[str, Any]]] = {
    "as-configured": [
        {"type": "url", "name": "linear", "url": "https://mcp.linear.app/mcp"},
        {"type": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp/"},
    ],
    "github-no-trailing-slash": [
        {"type": "url", "name": "linear", "url": "https://mcp.linear.app/mcp"},
        {"type": "url", "name": "github", "url": "https://api.githubcopilot.com/mcp"},
    ],
}

TOOLS = [
    AGENT_TOOLSET,
    {
        "type": "mcp_toolset",
        "mcp_server_name": "linear",
        "default_config": {
            "enabled": True,
            "permission_policy": {"type": "always_allow"},
        },
    },
    {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {
            "enabled": True,
            "permission_policy": {"type": "always_allow"},
        },
    },
]


def main() -> int:
    record: dict[str, Any] = {}
    for label, servers in VARIANTS.items():
        session = create_session(
            title=f"clevin-swarm-K-k4-mcp-{label}",
            prompt=PROBE,
            overrides={"mcp_servers": servers, "tools": TOOLS},
            max_cost="10",
            metadata={"probe": f"k4-mcp-{label}"},
        )
        print(label, session.id, flush=True)
        settled = wait_for_turn_end(session.id, timeout=900, poll=10)
        events = summarize_events(session.id)
        record[label] = {
            "session_id": session.id,
            "settled": settled,
            "errors": [event for event in events if "error" in event["type"]],
            "mcp_events": [event for event in events if "mcp" in event["type"]],
            "messages": [
                event for event in events if event["type"] == "agent.message"
            ],
            "usage": usage(session.id),
        }
        print(json.dumps(record[label]["errors"])[:500], flush=True)
    print(save("k4-mcp-credential-probe.json", record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
