# Workstream C experiments — runtime reliability, recovery, tool surface

Findings: `context/findings/C-runtime-reliability-and-tool-surface.md`.
Raw evidence from the original runs: `artifacts/`.

Everything here drives native Managed Agents primitives only: sessions, `sessions.events`,
`environments.work` (poll/ack/heartbeat), `EnvironmentWorker`, `SessionToolRunner`, the native
`agent_toolset_20260401` tools, and the `session.status_run_started` lifecycle webhook.

## Prerequisites

Run through `uv` so the runtime's dependencies (`anthropic`, `standardwebhooks`) are present, and
bind the secrets explicitly:

```bash
uv run --project runtime python experiments/C/<script>.py …
```

Required env: `ANTHROPIC_API_KEY`, `ANTHROPIC_ENVIRONMENT_KEY`, `ANTHROPIC_WEBHOOK_SECRET`,
`CLEVIN_ENVIRONMENT_ID`. Never pass secret values on the command line.

## One-time: temporary harness agent

Creates a throwaway native-tools-only agent (Haiku) so the production agent version is never
mutated. Writes `artifacts/harness-agent.json`.

```bash
uv run --project runtime python experiments/C/harness_agent.py
export CHAOS_AGENT_ID=$(python3 -c "import json;print(json.load(open('experiments/C/artifacts/harness-agent.json'))['agent_id'])")
```

Archive it when done (`beta.agents.archive`; there is no delete).

## Running one fault case

`run_case.py` starts a metadata-scoped fault worker, *then* creates the session (the worker has to be
polling before the session exists to win the claim race against the production Modal webhook path),
waits for a terminal stop reason, and dumps server-side history.

```bash
uv run --project runtime python experiments/C/run_case.py \
  --name c2-tool-hang --fault hang --prompt 'CLEVIN_SMOKE_TEST run: echo CHAOS-hang'
```

Fault modes (`chaos.py --fault`): `none`, `hang`, `kill-before`, `kill-after-side-effect`, `raise`,
`tool-error`, `oversized` (`--size-bytes`), `malformed`, `slow` (`--delay`). The fault arms on the
Nth tool call whose input contains `--trigger` (default `CHAOS`), so the prompt controls exactly
which call fails. `--budget` is in list-cost cents.

Artifacts per case: `artifacts/<name>.json` (full event history), `<name>-worker.log`, `<name>.out`.

## Recovering a stranded session (the C-3 result)

```bash
uv run --project runtime python experiments/C/exp_c3_recovery.py --session sesn_…
```

Phases: (1) find the `agent.tool_use` with no matching `user.tool_result`; (2) poll the environment
queue for 30 s to see whether the item is re-enqueued (it is not); (3) replay a signed
`session.status_run_started` webhook to the deployed handler (returns `spawned: []`); (4) attach a
bare `SessionToolRunner` — this is what actually re-dispatches the call.

## Staged but never run

`exp_c8_result_injection.py` posts malformed / oversized / duplicate / unknown-`tool_use_id`
`user.tool_result` events via `sessions.events.send`. It was one command short when the Anthropic
prepaid balance was exhausted; run it first when the balance is restored.
