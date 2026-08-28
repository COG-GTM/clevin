"""H6 — Can Deployment polling substitute for an inbound event path?

Primitive under test: a scheduled Deployment whose session reads external state
through a hosted MCP server (Linear) — the only native way to "wake on a new
ticket", since there is no Linear/GitHub → Anthropic event path.

Method: arm an every-minute deployment whose initial event tells the session to
look for Linear issues carrying this run's unique marker and report them. Once a
baseline "nothing found" run exists, inject one real external event by creating a
Linear issue with that marker through the Linear API (the stimulus, not the
mechanism under test), then measure the wall-clock delay until a deployment run
detects it. The temporary issue is deleted afterwards.

Requires LINEAR_API_KEY in the environment (already injected) and the Clevin
vault for the agent's Linear MCP credential.

Env knobs: H6_TIMEOUT_SECONDS (default 900).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

import h_common as h

TIMEOUT_SECONDS = int(os.environ.get("H6_TIMEOUT_SECONDS", "900"))
LINEAR_API = "https://api.linear.app/graphql"
# Default: the "Humza Sandbox" team, the only team LINEAR_API_KEY can write to.
LINEAR_TEAM_ID = os.environ.get(
    "H6_LINEAR_TEAM_ID", "527775fc-571b-4d16-bb3b-f8d6e1975013"
)
LINEAR_MCP_URL = "https://mcp.linear.app/mcp"

SYSTEM = (
    "You are a polling agent for a deployment experiment. Use the Linear MCP server "
    "read-only: search or list issues as instructed and report what you find. Never create, "
    "update, comment on, or transition any Linear issue. Never use git or the network for "
    "anything else. Answer in one short message, then stop."
)


def linear(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        LINEAR_API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": os.environ["LINEAR_API_KEY"],
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def create_issue(marker: str) -> dict[str, Any]:
    result = linear(
        """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier title createdAt }
          }
        }
        """,
        {
            "input": {
                "teamId": LINEAR_TEAM_ID,
                "title": f"{marker} clevin-swarm-H polling probe (delete me)",
                "description": (
                    "Temporary issue created by the Clevin Managed Agents swarm, workstream H, "
                    "to measure deployment polling latency. Safe to delete."
                ),
            }
        },
    )
    return result["data"]["issueCreate"]["issue"]


def delete_issue(issue_id: str) -> dict[str, Any]:
    return linear(
        "mutation($id: String!) { issueDelete(id: $id) { success } }",
        {"id": issue_id},
    )


def main() -> None:
    api = h.client()
    rec = h.Recorder("h6_polling_wake")
    marker = f"H6-{h.utc_stamp()}"
    agent = h.create_probe_agent(
        api,
        rec,
        suffix="h6-poller",
        system=SYSTEM,
        tools=[
            h.AGENT_TOOLSET,
            {
                "type": "mcp_toolset",
                "mcp_server_name": "linear",
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            },
        ],
        mcp_servers=[{"type": "url", "name": "linear", "url": LINEAR_MCP_URL}],
    )
    deployment = api.beta.deployments.create(
        agent=agent.id,
        environment_id=h.CLOUD_ENVIRONMENT_ID,
        name=h.temp_name("h6-poller"),
        budget=h.small_budget("3"),
        metadata={"experiment": "clevin-swarm-H"},
        vault_ids=[h.VAULT_ID],
        schedule={"type": "cron", "expression": "* * * * *", "timezone": "UTC"},
        initial_events=[
            h.user_message(
                "Search Linear for issues whose title contains the exact string "
                f"`{marker}`. Reply with `ORG <linear organization or workspace name>` on the "
                "first line, then exactly `FOUND <identifier>` for each match, or the single "
                "word `NONE` if there are no matches. Read-only; change nothing."
            )
        ],
    )
    rec.note(
        "deployment.created",
        deployment_id=deployment.id,
        marker=marker,
        vault_ids=deployment.vault_ids,
    )

    issue: dict[str, Any] | None = None
    try:
        baseline = h.wait_for_runs(
            api, deployment.id, count=1, timeout_s=180, poll_s=15
        )
        rec.note("baseline.runs", count=len(baseline))
        for run in baseline:
            if run.session_id:
                for sid, session in h.iter_settled(
                    api, [run.session_id], timeout_s=300
                ):
                    rec.note(
                        "baseline.session",
                        session_id=sid,
                        status=session.status,
                        text=h.session_text(api, sid),
                    )
        before = set(r.id for r in h.list_runs(api, deployment.id))

        issue = create_issue(marker)
        injected_at = datetime.now(tz=UTC)
        rec.note(
            "stimulus.issue_created", issue=issue, injected_at=injected_at.isoformat()
        )

        detected = False
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while not detected and time.monotonic() < deadline:
            for run in sorted(
                h.list_runs(api, deployment.id), key=lambda r: r.created_at
            ):
                if run.id in before or not run.session_id:
                    continue
                sessions = list(h.iter_settled(api, [run.session_id], timeout_s=420))
                text = h.session_text(api, run.session_id)
                found = any(
                    "FOUND" in line and issue["identifier"] in line for line in text
                )
                rec.note(
                    "poll.run",
                    run_id=run.id,
                    session_id=run.session_id,
                    run_created_at=str(run.created_at),
                    seconds_after_stimulus=(
                        datetime.fromisoformat(
                            str(run.created_at).replace("Z", "+00:00")
                        )
                        - injected_at
                    ).total_seconds(),
                    session_status=sessions[0][1].status if sessions else None,
                    detected=found,
                    text=text,
                )
                before.add(run.id)
                if found:
                    detected = True
                    rec.note(
                        "detection.latency",
                        run_id=run.id,
                        seconds_from_issue_creation=(
                            datetime.now(tz=UTC) - injected_at
                        ).total_seconds(),
                        cron="* * * * *",
                    )
                    break
            if not detected:
                time.sleep(20)
        if not detected:
            rec.note("detection.timeout", timeout_s=TIMEOUT_SECONDS)
    finally:
        if issue is not None:
            try:
                result = delete_issue(issue["id"])
            except Exception as exc:  # noqa: BLE001
                rec.cleaned(
                    "linear_issue", issue["identifier"], "delete", f"failed: {exc}"
                )
            else:
                rec.cleaned(
                    "linear_issue", issue["identifier"], "delete", json.dumps(result)
                )
        h.archive_deployment(api, rec, deployment.id)
        for session in api.beta.sessions.list(deployment_id=deployment.id, limit=100):
            h.stop_session(api, rec, session.id)
        h.archive_agent(api, rec, agent.id)
        rec.write()


if __name__ == "__main__":
    main()
