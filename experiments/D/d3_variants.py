"""D3 — variant fleets, shared definitions, and declarative coverage.

Primitives: agent create/list/archive, per-session `agent_with_overrides`,
and the resource families the Managed Agents API exposes at all.

The driver builds dev/staging/prod variants from one shared code-declared
definition (so tools, MCP servers and skills are shared by construction),
scales the fleet to `D3_FLEET_SIZE` agents to see whether many variants are
manageable, checks what selectors the list API offers, and tests whether a
variant can exist *without* an agent resource at all via
`agent_with_overrides`. Finally it records which resource families expose
create/update/archive in the SDK, i.e. what can be managed as code.

    ANTHROPIC_API_KEY=... uv run --project runtime python experiments/D/d3_variants.py
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic
from _common import (
    Ledger,
    agent_projection,
    api_error,
    archive_agents,
    archive_sessions,
    client,
    desired_agent_state,
    diff_state,
    run_probe,
    temp_name,
    write_evidence,
)

STAGES = ("dev", "staging", "prod")
FLEET_SIZE = int(os.environ.get("D3_FLEET_SIZE", "12"))
PROBE_MODEL = "claude-haiku-4-5-20251001"


def variant_body(
    desired: dict[str, Any], name: str, stage: str, experiment: str
) -> dict[str, Any]:
    """One shared definition, per-stage deltas only."""
    return {
        **desired,
        "name": name,
        "description": f"workstream D {stage} variant",
        # Stage variance is deliberately narrow: everything else (tools,
        # mcp_servers, skills, multiagent) comes from the shared definition.
        "model": {"id": PROBE_MODEL},
        "system": f"{desired['system']}\n\nDEPLOY_STAGE={stage}",
        "metadata": {
            **dict(desired["metadata"] or {}),
            "swarm_workstream": "D",
            "swarm_experiment": experiment,
            "stage": stage,
        },
    }


def main() -> None:
    api = client()
    ledger = Ledger()
    result: dict[str, Any] = {"experiment": "D3 variants and declarative coverage"}
    try:
        run(api, ledger, result)
    finally:
        archive_sessions(api, ledger, ledger.ids("session"))
        archive_agents(api, ledger, ledger.ids("agent"))
        result["cleanup"] = ledger.entries
        path = write_evidence("d3_variants", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"evidence: {path}")


def run(api: anthropic.Anthropic, ledger: Ledger, result: dict[str, Any]) -> None:
    desired = desired_agent_state()

    # --- dev/staging/prod variants from one shared definition.
    variants: list[dict[str, Any]] = []
    shared_paths_equal = True
    for stage in STAGES:
        name = temp_name(f"variant-{stage}")
        body = variant_body(desired, name, stage, "d3-stage")
        agent = api.beta.agents.create(**body)  # type: ignore[arg-type]
        ledger.created("agent", agent.id, name)
        actual = agent_projection(agent)
        drift, added = diff_state(body, actual)
        # shared blocks must be byte-identical across variants
        shared_drift, _ = diff_state(
            {
                key: body[key]
                for key in ("tools", "mcp_servers", "skills", "multiagent")
            },
            {
                key: actual[key]
                for key in ("tools", "mcp_servers", "skills", "multiagent")
            },
        )
        shared_paths_equal = shared_paths_equal and not shared_drift
        variants.append(
            {
                "stage": stage,
                "agent_id": agent.id,
                "version": agent.version,
                "drift": drift,
                "server_added": added,
            }
        )
    result["stage_variants"] = variants
    result["shared_blocks_identical_across_variants"] = shared_paths_equal

    # --- duplicate names: is `name` a unique key or free-form?
    duplicate_name = variants[0]["agent_id"]
    first = api.beta.agents.retrieve(duplicate_name)
    try:
        clone = api.beta.agents.create(
            name=first.name,
            description="duplicate-name probe",
            model={"id": PROBE_MODEL},
        )
        ledger.created("agent", clone.id, f"duplicate of {first.name}")
        result["duplicate_name"] = {"accepted": True, "agent_id": clone.id}
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["duplicate_name"] = {"accepted": False, **api_error(error)}

    # --- fleet scale.
    fleet_started = time.monotonic()
    fleet_errors: list[dict[str, Any]] = []
    for index in range(FLEET_SIZE):
        name = temp_name(f"fleet-{index:02d}")
        body = variant_body(desired, name, "fleet", "d3-fleet")
        try:
            agent = api.beta.agents.create(**body)  # type: ignore[arg-type]
        except Exception as error:  # noqa: BLE001 - a ceiling is the evidence
            fleet_errors.append({"index": index, **api_error(error)})
            break
        ledger.created("agent", agent.id, name)
    result["fleet"] = {
        "requested": FLEET_SIZE,
        "created": len(
            [entry for entry in ledger.entries if "fleet-" in entry["note"]]
        ),
        "elapsed_s": round(time.monotonic() - fleet_started, 1),
        "errors": fleet_errors,
    }

    # --- what selectors does the list API offer for fleet management?
    listed = list(api.beta.agents.list(limit=100))
    swarm_agents = [
        agent
        for agent in listed
        if (agent.metadata or {}).get("swarm_experiment") in {"d3-stage", "d3-fleet"}
    ]
    result["list_api"] = {
        "total_listed_first_page": len(listed),
        "client_side_metadata_matches": len(swarm_agents),
        "server_side_metadata_filter": False,
        "documented_list_filters": [
            "created_at[gte]",
            "created_at[lte]",
            "include_archived",
            "limit",
            "page",
        ],
    }

    # --- variant without a resource: per-session overrides.
    base = variants[0]
    override_marker = "[[OVERRIDE-VARIANT]]"
    try:
        outcome = run_probe(
            api,
            ledger,
            agent={
                "type": "agent_with_overrides",
                "id": base["agent_id"],
                "version": 1,
                "system": (
                    "You are a variant defined entirely by session overrides. "
                    f"Begin your final message with the marker {override_marker}. Be terse."
                ),
                "model": PROBE_MODEL,
                "tools": [],
                "mcp_servers": [],
                "skills": [],
            },
            prompt="CLEVIN_SMOKE_TEST reply with your marker and the word ok only.",
            title="D3 override variant",
            metadata={"swarm_experiment": "d3-override"},
            timeout_s=420.0,
        )
        after = api.beta.agents.retrieve(base["agent_id"])
        result["session_overrides"] = {
            "outcome": outcome.to_json(),
            "marker_honoured": override_marker in outcome.final_text,
            "agent_version_after": after.version,
            "agent_resource_unchanged": after.version == 1,
        }
    except Exception as error:  # noqa: BLE001 - an unsupported path is the evidence
        result["session_overrides"] = {"failed": True, **api_error(error)}

    # --- which resource families are API-manageable (i.e. codifiable)?
    result["declarative_coverage"] = declarative_coverage(api)


def declarative_coverage(api: anthropic.Anthropic) -> dict[str, dict[str, bool]]:
    families = {
        "agents": api.beta.agents,
        "agent_versions": api.beta.agents.versions,
        "sessions": api.beta.sessions,
        "skills": api.beta.skills,
        "memory_stores": api.beta.memory_stores,
        "environments": api.beta.environments,
        "vaults": api.beta.vaults,
        "vault_credentials": api.beta.vaults.credentials,
        "deployments": api.beta.deployments,
        "files": api.beta.files,
    }
    return {
        family: {
            verb: callable(getattr(resource, verb, None))
            for verb in ("create", "retrieve", "update", "list", "archive", "delete")
        }
        for family, resource in families.items()
    }


if __name__ == "__main__":
    main()
