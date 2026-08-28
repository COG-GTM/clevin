"""K5 — Skills as reusable named playbooks.

Question: can a user invoke a named, reusable procedure by name, and does the
agent actually follow it? Does a Skill attached through `agent_with_overrides`
behave the same as one attached to the published agent version, and does version
pinning work?

Primitive under test: `beta.skills` (create, `versions.create`, retrieve) plus the
agent `skills` configuration, exercised both per session (override) and as a
production agent version input. The archive layout requirement is native: all
files must live under one top-level directory containing `SKILL.md`.

Usage:
  uv run --project runtime python experiments/K/k5_skills_as_playbooks.py
"""

from __future__ import annotations

import io
from typing import Any

from common import (
    AGENT_TOOLSET,
    SMOKE_PREFIX,
    client,
    create_session,
    save,
    summarize_events,
    usage,
    wait_for_turn_end,
)

SKILL_NAME = "clevin-verification"

SKILL_V1 = """---
name: clevin-verification
description: Reusable playbook for verifying the Clevin repository. Use whenever
  asked to verify, lint, typecheck, or test the Clevin repo, or when asked to run
  the "clevin-verification" playbook.
---

# Clevin verification playbook

Run these commands from the repository root, in this exact order, and stop at the
first failure:

1. `pnpm install`
2. `uv sync --project runtime`
3. `pnpm verify`
4. `uv run --project runtime ruff format --check runtime`
5. `uv run --project runtime ruff check runtime`
6. `uv run --project runtime mypy runtime/src`
7. `uv run --project runtime pytest -c runtime/pyproject.toml`

Rules:

- Never skip step 2; the Python commands fail without the synced environment.
- Steps 3-7 are the gate for opening a pull request.
- Report each command's exit status; do not summarise a failure as a pass.
"""

SKILL_V2 = SKILL_V1.replace(
    "- Report each command's exit status; do not summarise a failure as a pass.",
    """- Report each command's exit status; do not summarise a failure as a pass.
- Commands that can exceed 120 s (`pnpm install`, `pytest`) need an explicit
  bash `timeout_ms`; a shell-level timeout does not extend the tool timeout.
- Version marker: v2.""",
)

RECALL_PROMPT = f"""{SMOKE_PREFIX}
Do not run any commands and change no state. Invoke the clevin-verification
playbook and report, verbatim and in order, the numbered command list it
prescribes, then quote its rules section exactly, then state the skill name you
used. Then stop.
"""


def archive(body: str) -> list[Any]:
    """Native layout: one top-level directory containing SKILL.md."""
    handle = io.BytesIO(body.encode("utf-8"))
    handle.name = f"{SKILL_NAME}/SKILL.md"
    return [(f"{SKILL_NAME}/SKILL.md", handle, "text/markdown")]


def run(label: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    session = create_session(
        title=f"clevin-swarm-K-k5-{label}",
        prompt=RECALL_PROMPT,
        overrides={"skills": skills, "tools": [AGENT_TOOLSET], "mcp_servers": []},
        max_cost="15",
        with_vault=False,
        metadata={"probe": f"k5-{label}"},
    )
    print(label, session.id, flush=True)
    settled = wait_for_turn_end(session.id, timeout=1200, poll=10)
    events = summarize_events(session.id)
    return {
        "session_id": session.id,
        "skills": skills,
        "settled": settled,
        "messages": [event for event in events if event["type"] == "agent.message"],
        "tool_calls": [
            event
            for event in events
            if event["type"] in {"agent.tool_use", "agent.custom_tool_use"}
        ],
        "errors": [event for event in events if "error" in event["type"]],
        "usage": usage(session.id),
    }


def main() -> int:
    record: dict[str, Any] = {}
    created = client().beta.skills.create(
        files=archive(SKILL_V1), display_title="Clevin verification playbook"
    )
    record["skill_created"] = created.model_dump(mode="json")
    skill_id = created.id
    version_1 = created.latest_version
    print("skill:", skill_id, "v:", version_1, flush=True)

    record["v1_by_override"] = run(
        "v1-pinned", [{"type": "custom", "skill_id": skill_id, "version": version_1}]
    )

    updated = client().beta.skills.versions.create(skill_id, files=archive(SKILL_V2))
    record["version_created"] = updated.model_dump(mode="json")
    version_2 = updated.version
    print("v2:", version_2, flush=True)

    record["v2_latest"] = run("v2-latest", [{"type": "custom", "skill_id": skill_id}])
    record["v1_still_pinned"] = run(
        "v1-repinned", [{"type": "custom", "skill_id": skill_id, "version": version_1}]
    )
    record["skill_after"] = client().beta.skills.retrieve(skill_id).model_dump(
        mode="json"
    )
    print(save("k5-skills-playbooks.json", record))
    for label in ("v1_by_override", "v2_latest", "v1_still_pinned"):
        texts = " ".join(m["text"] for m in record[label]["messages"])
        print(label, "mentions v2 marker:", "Version marker: v2" in texts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
