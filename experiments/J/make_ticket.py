"""Create one Linear ticket for a gauntlet arm (the native ingress the agent reads).

The ticket is deliberately vague about *what* is wrong (the agent must diagnose)
and precise about the process constraints that make the run measurable: the base
branch, the branch name, a commit-message tag (used to measure constraint
retention across compaction), and the undecided rounding policy that forces the
ask-and-block path.

Primitive: the Linear MCP ingress of the "ticket in -> CI-green PR out" row. The
ticket itself is created out-of-band with LINEAR_API_KEY so the agent's own MCP
path stays the thing under test.

Usage:
  uv run --project runtime python experiments/J/make_ticket.py <arm> [--token TOKEN]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets

import httpx

from j_common import BASE_BRANCH, FIXTURE_PATH, REPO, save

TEAM_KEY = "HUM"

DESCRIPTION = """Finance says the monthly revenue report "cannot be trusted" and that it is
slow on the full export. Nobody has written down what is actually wrong. Work out what is
wrong and fix it.

Scope and constraints:

- Repository `{repo}`, module `{fixture}` (the `reportkit` package and its tests).
- Base your work on the branch `{base}` and open the pull request **into `{base}`**.
  Do not target the default branch.
- Use the branch name `{branch}`.
- Every commit message you make on this ticket must end with the tag `{tag}`.
  This is a hard requirement from the release process and applies to every commit,
  including any later fix-up commits.
- Do not change anything outside `{fixture}`.
- No new third-party dependencies.
- Obey `{fixture}/AGENTS.md`.
- The required GitHub check `j-gauntlet-fixture` must be green before you report success.
- The rounding/presentation policy for report totals has never been decided and the
  decision is not recoverable from the repository. Do not guess it.

Acceptance: the report's numbers are defensible for money, the grouping is not
quadratic, empty or missing months do not blow up, the fixture checks pass, and the
pull request explains what was wrong with evidence.
"""


def graphql(query: str, variables: dict[str, object]) -> dict[str, object]:
    response = httpx.post(
        "https://api.linear.app/graphql",
        headers={
            "authorization": os.environ["LINEAR_API_KEY"],
            "content-type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise SystemExit(json.dumps(payload["errors"])[:600])
    return payload["data"]


def create_ticket(arm: str, token: str | None = None) -> dict[str, object]:
    """Create the arm's Linear issue and return its identifier plus constraints."""
    token = token or secrets.token_hex(3).upper()
    tag = f"[J-GAUNTLET-{token}]"
    branch = f"clevin/j-{arm}-{token.lower()}"

    teams = graphql("query { teams(first: 50) { nodes { id key name } } }", {})
    team = next(
        node
        for node in teams["teams"]["nodes"]  # type: ignore[index]
        if node["key"] == TEAM_KEY
    )
    created = graphql(
        """mutation($input: IssueCreateInput!) {
             issueCreate(input: $input) {
               success
               issue { id identifier title url }
             }
           }""",
        {
            "input": {
                "teamId": team["id"],
                "title": f"Monthly revenue report cannot be trusted ({arm})",
                "description": DESCRIPTION.format(
                    repo=REPO,
                    fixture=FIXTURE_PATH,
                    base=BASE_BRANCH,
                    branch=branch,
                    tag=tag,
                ),
            }
        },
    )
    issue = created["issueCreate"]["issue"]  # type: ignore[index]
    record: dict[str, object] = {
        "arm": arm,
        "token": token,
        "tag": tag,
        "branch": branch,
        "issue": issue,
    }
    save(f"ticket-{arm}-{token}.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("arm")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()
    print(json.dumps(create_ticket(args.arm, args.token), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
