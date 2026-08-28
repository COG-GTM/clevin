"""D2 — out-of-band change detection and reconciliation.

Primitive: agent update semantics (new version per update, `version` as an
optimistic-concurrency guard) plus the read-only drift report added in
`packages/provision/src/drift.ts`.

Production must never be reconciled, so the driver builds a throwaway
code-managed agent, mutates it out of band the way a Console edit would
(system prompt, model effort, metadata, MCP server list), detects the drift
structurally, reconciles it back to the code-declared state, and proves the
reconciled version matches the desired state again. It also checks whether a
stale `version` guard blocks a lost-update.

    ANTHROPIC_API_KEY=... uv run --project runtime python experiments/D/d2_drift.py
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from _common import (
    Ledger,
    agent_projection,
    api_error,
    archive_agents,
    client,
    desired_agent_state,
    diff_state,
    temp_name,
    write_evidence,
)


def main() -> None:
    api = client()
    ledger = Ledger()
    result: dict[str, Any] = {"experiment": "D2 drift detection and reconciliation"}
    try:
        run(api, ledger, result)
    finally:
        archive_agents(api, ledger, ledger.ids("agent"))
        result["cleanup"] = ledger.entries
        path = write_evidence("d2_drift", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print(f"evidence: {path}")


def run(api: anthropic.Anthropic, ledger: Ledger, result: dict[str, Any]) -> None:
    desired = desired_agent_state()
    name = temp_name("drift")
    code_managed = {
        **desired,
        "name": name,
        "description": "workstream D drift and reconciliation probe",
        "metadata": {
            **dict(desired["metadata"] or {}),
            "swarm_workstream": "D",
            "swarm_experiment": "d2",
        },
    }

    created = api.beta.agents.create(**code_managed)  # type: ignore[arg-type]
    ledger.created("agent", created.id, name)
    baseline_drift, baseline_added = diff_state(
        code_managed, agent_projection(api.beta.agents.retrieve(created.id))
    )
    result["baseline"] = {
        "agent_id": created.id,
        "version": 1,
        "drift": baseline_drift,
        "server_added": baseline_added,
    }

    # --- referential integrity: can an out-of-band edit leave a dangling reference?
    try:
        api.beta.agents.update(created.id, mcp_servers=[], version=1)
        result["dangling_reference_edit"] = {"accepted": True}
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["dangling_reference_edit"] = {"accepted": False, **api_error(error)}

    # --- out-of-band edit: what a Console operator can change without the repo.
    out_of_band = api.beta.agents.update(
        created.id,
        system="Out-of-band edited prompt. Ignore the repository definition.",
        model={"id": "claude-haiku-4-5-20251001"},
        metadata={
            **dict(code_managed["metadata"] or {}),
            "source_of_truth": "console",
            "edited_by": "out-of-band",
        },
        version=1,
    )
    result["out_of_band"] = {"new_version": out_of_band.version}

    detected_drift, detected_added = diff_state(
        code_managed, agent_projection(out_of_band)
    )
    result["detection"] = {
        "version": out_of_band.version,
        "drift_paths": [entry["path"] for entry in detected_drift],
        "server_added": detected_added,
        "drift_count": len(detected_drift),
    }

    # --- stale-guard check: does the API reject a lost update?
    try:
        api.beta.agents.update(created.id, name=f"{name}-stale", version=1)
        result["stale_guard"] = {"rejected": False}
    except Exception as error:  # noqa: BLE001 - the rejection is the evidence
        result["stale_guard"] = {"rejected": True, **api_error(error)}

    # --- reconcile back to the code-declared state.
    reconciled = api.beta.agents.update(
        created.id,
        version=out_of_band.version,
        **code_managed,  # type: ignore[arg-type]
    )
    residual_drift, residual_added = diff_state(
        code_managed, agent_projection(reconciled)
    )
    result["reconciliation"] = {
        "new_version": reconciled.version,
        "residual_drift": residual_drift,
        "server_added": residual_added,
        "clean": not residual_drift,
    }

    # --- metadata is a patch, so can a re-applied definition delete a stray key?
    stray_key_survives = "edited_by" in (reconciled.metadata or {})
    explicit_delete = api.beta.agents.update(
        created.id,
        metadata={"edited_by": None},
        version=reconciled.version,
    )
    result["metadata_patch_semantics"] = {
        "stray_key_survives_reapplied_definition": stray_key_survives,
        "stray_key_removed_by_explicit_null": "edited_by"
        not in (explicit_delete.metadata or {}),
        "version_after_explicit_null": explicit_delete.version,
    }

    # --- can the out-of-band version be erased, or does history keep it?
    result["versions_after_reconcile"] = [
        version.version
        for version in api.beta.agents.versions.list(created.id, limit=100)
    ]
    result["out_of_band_version_still_retrievable"] = (
        api.beta.agents.retrieve(created.id, version=out_of_band.version).system
        == "Out-of-band edited prompt. Ignore the repository definition."
    )


if __name__ == "__main__":
    main()
