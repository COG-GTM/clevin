# Workstream A: control plane and session semantics

This experiment uses temporary Managed Agents resources to measure version
pinning, session snapshots, session-level mutable fields, event ordering and
replay, SSE reconnects, interrupts, compaction, prompt-cache usage, and the
boundary between Anthropic history and the Modal-backed workspace.

Run from the repository root:

```bash
uv run --project runtime python experiments/A/managed_agents_probe.py \
  --output experiments/A/results/<run>.json
```

Required environment variables:

- `ANTHROPIC_API_KEY`
- `CLEVIN_ENVIRONMENT_ID`
- `MODAL_TOKEN_ID`
- `MODAL_TOKEN_SECRET`
- `MODAL_ENVIRONMENT=clevin`

The probe creates only resources prefixed `clevin-swarm-A-`. By default it
archives temporary sessions and agents, deletes the temporary Skill, and removes
the per-session subtrees it created in the `clevin-sessions` volume. Pass
`--keep-resources` only when debugging cleanup itself.

Compaction is the expensive part. Use `--skip-compaction` for a short control
plane run. The defaults send 60 approximately 90 KB filler turns; adjust
`--compaction-turns`, `--filler-bytes`, and `--compaction-model` when testing a
changed context window. Use `--only-compaction` to isolate that probe.

The webhook probe is local and deterministic. It passes delayed, duplicated,
and reordered verified event types through the deployed handler logic without
calling Anthropic or Modal:

```bash
PYTHONPATH=runtime/src uv run --project runtime python \
  experiments/A/webhook_delivery_probe.py \
  --output experiments/A/results/webhook-delivery.json
```

It measures handler semantics, not provider delivery guarantees: duplicate
`session.status_run_started` notifications cause duplicate queue drains, while
non-run-started lifecycle events are ignored. Sequential safety therefore
depends on native queue leases and the session-named `SandboxRuntime` lookup.
