# Workstream A: control plane and session semantics

## Executive finding

Managed Agents provides a strong Anthropic-owned session control plane: immutable
agent versions, version-pinned session snapshots, durable ordered event history,
native SSE, usage/cache accounting, interrupts, and model-dependent automatic
compaction. The self-hosted `EnvironmentWorker` supplies a separate execution
plane whose process and filesystem state are not transactionally coupled to that
history. The ceiling is therefore a recoverable event-sourced agent session with
a persistent workspace, not a single atomic session/workspace state machine.

Evidence artifacts:

- `experiments/A/results/control-plane.json`
- `experiments/A/results/compaction-opus-60.json`
- `experiments/A/results/compaction-haiku-terminal.json`
- `experiments/A/results/replay-after-idle.json`
- `experiments/A/results/tool-interrupt-details.json`
- `experiments/A/results/webhook-delivery.json`

## State model

| State | Owner | Lifetime and mutability | Recovery boundary |
| --- | --- | --- | --- |
| Agent versions: model, system prompt, tools, MCPs, Skills, subagent roster | Anthropic | Each update creates a monotonically increasing immutable version. A rollback is a new version copying an older configuration. | Any published version can be retrieved and used for a new session. Existing sessions do not move. |
| Session agent snapshot | Anthropic | Captured from the explicitly selected agent version at session creation. Model, system, Skills, and multiagent configuration remain pinned. Only tools and MCP servers are replaceable through the documented mid-session agent update. | Durable through idle, reconnect, and agent-version updates. |
| Session title, metadata, budget and status | Anthropic | Session-level state; title, metadata, and budget are mutable. | Recoverable by session retrieval. Budget exhaustion or a terminal model error may terminate further work. |
| Ordered events and usage | Anthropic | Append-only event IDs expose user/model/tool/lifecycle/usage/compaction activity. | Recoverable with `events.list`; SSE consumers must replay then deduplicate by event ID. |
| Model context | Anthropic | Derived from session history and compacted automatically on supported long-context paths. No session API exposes a custom compactor or explicit checkpoint. | Opus survived two native compactions and retained an early constraint. Haiku reached its 200k limit without a compaction and terminated. |
| Live shell processes and tool execution | Self-hosted environment sandbox | Ephemeral process state inside the current Modal Sandbox. | A `user.interrupt` produced an error `agent.tool_result` and returned the session to idle; the experiment did not establish whether every underlying process and side effect is synchronously cancelled. |
| Workspace files | Modal `clevin-sessions` volume | Stored below `/sessions/<session-id>` and mounted at `/workspace`; independent from Anthropic history. | Files survive independently of the event stream until the volume subtree is removed. History records tool requests/results, not an authoritative filesystem snapshot. |
| Sandbox identity | Clevin `SandboxRuntime` / Modal | A live sandbox is named by session ID; `get_or_create` reuses it or creates another sandbox mounting the same session volume. | Session ID joins Anthropic state to sandbox/volume state, but there is no cross-plane transaction or checkpoint. |
| Webhook delivery processing | Anthropic webhook plus Clevin handler | The handler verifies signatures, reacts only to `session.status_run_started`, and drains native environment work. | Duplicate notifications cause duplicate drain attempts; native queue leasing and session-named sandbox reuse provide practical convergence, not webhook-level exactly-once processing. |

### Version and session experiment

Run `clevin-swarm-A-20260828T021322Z-9dad4d` created temporary primary
agent `agent_01H3M5KhJPhJEnd12khFpfEK`, helper
`agent_018smwYx4AbYthpzmwpS3G8v`, and Skill
`skill_01QSt816QXwcE3nz8zcqYUQk`.

1. Version 1 used Haiku, the `VERSION_ONE` system prompt, the agent
   toolset, no Skills, and no subagents.
2. Session `sesn_01GxjLzu1AK3LxCGGQaxqSEv` was created against version 1
   and wrote `alpha-state` to `/workspace/control-plane-marker.txt`.
3. Version 2 changed the model to Sonnet, changed the system marker to
   `VERSION_TWO`, changed the tool configuration, attached the temporary
   Skill, and attached the helper agent.
4. The original session still retrieved as version 1 and answered
   `VERSION_ONE`. Fresh session `sesn_01SgnPnJ4wjsH1TaT4Z2j543`
   retrieved the complete version-2 snapshot and answered `VERSION_TWO`.
5. Updating the original session with `agent={"tools": [], "mcp_servers": []}`
   replaced those arrays but preserved its version-1 model, system, Skills,
   and multiagent snapshot.
6. “Rollback” created version 3 from the version-1 configuration. Fresh
   session `sesn_012VsLLbd3rg3Z3SWjaDdaQS` used version 3 and answered
   `VERSION_ONE`; the version list remained `[3, 2, 1]`.

**Classification: A** for reproducible version-pinned creation and
roll-forward/new-version rollback. **Classification: C** for arbitrary
mid-session configuration change because only tools and MCP servers are
mutable; model, system, Skills, and subagents require a new session.

### Event replay and SSE reconnect

Ordering session `sesn_01FFMWrRAEwPVAXW28aMDSBH` preserved the order of a
two-message `events.send` batch. Two identical user messages received distinct
IDs (`sevt_0136nVS7i43xLjG8SrEncE7a` and
`sevt_01QirdxAsAimM3qZQi8a2T4p`). A read after the session settled returned the
same 40 unique event IDs in the same order twice, from
`sevt_01C92DyX1YBbEch6i13pLy7k` through
`sevt_01Pq9kHL5WQcnvXSMYbgSdDe`.

SSE session `sesn_0181u67MEuHnmc1DEY5herCq` disconnected after four
persistent event IDs. A new stream delivered later events with no duplicate IDs
across the two connections; every streamed persistent ID existed in
`events.list`. One event was missed by the deliberately early disconnect and
was recovered by history replay. Stream-only `event_start`/`event_delta`
previews are not durable event-history records.

**Classification: A.** Durable IDs plus ordered replay and SSE are sufficient
for an at-least-once consumer that replays history and deduplicates locally.
The stream is not itself a cursor/checkpoint API.

### Interrupts

Generation session `sesn_013nBj7ZsxVwtyRKiA1bbeBb` received
`user.interrupt` event `sevt_01J2ik42kdTPdrnp3aBEdYo1` after
`span.model_request_start`. The request ended with zero input/output tokens,
no `agent.message`, and an idle session.

Tool session `sesn_01A3GmGx4jBuEtP7oBAJcdSw` emitted bash tool use
`sevt_01LBFUyhNNTeZmCL9iidkby3`, then error tool result
`sevt_01RAqGxFMk76GWfntma7PsKd`:

> Tool execution was interrupted before completion. Please retry.

The interrupt event was `sevt_01RWWQ5wtkSHGKSJibhGvPrQ`, followed by terminal
idle event `sevt_01TQ4jcVmRLHx2DF1c5GkWrz`. Because the per-session volume was
cleaned after the run, this probe does not prove whether an arbitrary external
process or partial filesystem side effect can outlive the error result.

**Classification: A** for cancelling active model generation.
**Classification: C** for tool cancellation/recovery: the native control plane
reports an interruption and returns to idle, but atomic cancellation and
side-effect rollback are not provided.

### Long sessions, compaction, and prompt cache

Opus session `sesn_01HibtefzcKjxrNmPBYZ4iLv` processed 60 approximately
90 KB filler turns. It emitted two native compaction events:

- `sevt_01A1m9ZKKMgYGaZcUXCmWA9c`
- `sevt_0181MjDLZv4HfsAQ8RA7GFQu`

After both compactions it returned the exact early constraint
`EARLY_CONSTRAINT_ORANGE_7`. The event history remained available and contained
64 model-request spans for 62 user turns. Compaction requests were visible as
additional model spans around the compacted-context events.

The same pattern on Haiku session `sesn_013yxjUXrwvExMQRbCA2wt9u`
did not emit `agent.thread_context_compacted`; after six filler turns it emitted
terminal error `sevt_014tBBjBuTqdE3zEDz9i2y2P`:

> prompt is too long: 237081 tokens > 200000 maximum

Prompt caching is directly observable. The Opus run showed
`cache_read_input_tokens` growing from 40,099 to 828,239 before the first
compaction, then a compaction model request with 1,033 ordinary input tokens.
After compaction, cache creation restarted at 40,753 tokens and cache reads
again accumulated. The second compaction repeated that reset. Both
`span.model_request_end.model_usage` and `session.usage` expose cache fields.

**Classification: C.** Native compaction and constraint retention are strong on
the tested Opus path, but they are model/context-window dependent and not a
universal session guarantee or user-configurable checkpoint mechanism.
Prompt-cache indicators are **class A**.

### Anthropic history versus sandbox state

Session `sesn_01GxjLzu1AK3LxCGGQaxqSEv` had 30 Anthropic events, including
the user request, bash tool use/result, model responses, usage, and a
`session.updated` event. Independently, its Modal volume contained
`control-plane-marker.txt` with `alpha-state`. Updating session tools did not
modify the file. The volume subtree required a separate cleanup action after
the Anthropic session was archived.

**Classification: B** for persistent workspace state through the native
self-hosted `EnvironmentWorker` extension point. **Classification: C** for a
unified recoverable state model because event history and physical files are
separate, joined by session ID, and not atomically snapshotted together.

### Delayed, duplicated, and reordered webhooks

The deterministic handler probe passed verified lifecycle event objects through
the production handler logic:

- Two duplicate `session.status_run_started` notifications invoked the native
  work-queue drain twice.
- `session.updated` was ignored whether delivered before or after
  `session.status_run_started`.
- Delivery order therefore did not change the number of relevant drains, but
  there is no webhook-event-ID deduplication in the handler.

`_drain_work(..., drain=True)` polls Anthropic's environment queue, whose lease
is reclaimed after 2,000 ms, and `SandboxRuntime.get_or_create` looks up a live
sandbox named by session ID before creating one. These native-extension
boundaries make duplicate drain attempts convergent in the normal sequential
case, but they do not constitute an exactly-once guarantee.

**Classification: C.** Lifecycle webhooks and native work leases support
recovery, but provider delivery timing/redelivery and concurrent duplicate
handler execution were not established.

## Capability classifications

| Capability | Class | Evidence and limit |
| --- | --- | --- |
| Agent-version-pinned model/system/tools/Skills/subagents | A | Version-1 session retained its full snapshot after versions 2 and 3 were published. |
| Roll forward and roll back configuration | A | Updates created versions 2 and 3; rollback was a new immutable version, not destructive history rewrite. |
| Change tools/MCPs mid-session | A | Session update replaced tools with `[]` while retaining version 1. |
| Change model/system/Skills/subagents mid-session | D | The SDK/API exposes only tools and MCPs in the mid-session agent update. Creating a replacement orchestrator or state migration layer would violate the Managed Agents boundary. |
| Ordered duplicate message ingestion | A | Batch order preserved; identical messages received distinct durable event IDs. |
| SSE disconnect/reconnect recovery | A | Replay recovered the disconnect gap; stable event IDs support deduplication. |
| Cancel model generation | A | Native interrupt stopped the request with zero output and no partial durable agent message. |
| Cancel tool execution atomically | C | Native error result and idle recovery exist; process/side-effect rollback was not proved and no transaction primitive exists. |
| Automatic long-context compaction | C | Two Opus compactions retained the constraint; Haiku terminated at 200k without compaction. |
| Prompt-cache observability | A | Cache creation/read token fields were present on model spans and cumulative usage. |
| Persistent sandbox filesystem | B | Native self-hosted worker plus Modal volume persisted a file independently of the session. |
| Atomic Anthropic-history/filesystem checkpoint | D | No Managed Agents primitive couples event history to a filesystem snapshot; no custom parallel state system was built. |
| Duplicate/delayed/reordered lifecycle webhook recovery | C | Handler is order-insensitive for ignored types but duplicates queue drains and has no event-ID deduplication. |
| Session fork from a checkpoint | D | No session clone/fork/checkpoint primitive appeared in the session API inspected for this workstream. No fork mechanism was built. |

## Distance to Devin

| Devin-like capability touched | Class | Distance to Devin |
| --- | --- | --- |
| Long-horizon work across hours and compactions | C | Opus preserves an early constraint through repeated native compaction, but this is model-dependent and lacks an inspectable/restorable checkpoint; Memory Store quality remains workstream E. |
| Recovers from a crashed sandbox or failed tool | C | Session history and a session-scoped volume give the ingredients for recovery, but the control plane cannot atomically reconcile an interrupted tool's side effects; destructive crash cases remain workstream C. |
| Mid-run steering | C | A native interrupt reliably stops current generation/tool work and returns idle; whether injected replacement instructions produce robust replanning remains workstream K. |
| Ask-a-question-and-block, resume later with workspace intact | B | Anthropic idle history and the self-hosted session volume are independently durable, but the maximum worker/volume wait and lease behavior remain workstream K. |
| Observable, attributable run history | C | Agent version, session, model/tool/usage/compaction events are attributable; sandbox process and filesystem transitions are not fully represented in Anthropic history. |
| Per-task cost accounting | A | Session usage and per-request cache/token fields provide native task-level accounting without the unavailable Admin API. |
| Fleet of agent variants managed as code | A | Immutable versions and explicit session pinning provide the control-plane substrate; declarative fleet reconciliation remains workstream D. |
| Warm environment (blueprint/snapshot equivalent) | B | The sandbox image and session volume are native environment-extension mechanisms, but they are separate from the Anthropic session snapshot. |
| Session forking from a checkpoint | D | No native fork/checkpoint API was found; building one would require the prohibited parallel session/filesystem state layer. |

## Reproduction

From the repository root, with the documented personal secrets bound as
environment variables:

```bash
uv run --project runtime python experiments/A/managed_agents_probe.py \
  --output experiments/A/results/<run>.json
```

For a cheaper control-plane-only run:

```bash
uv run --project runtime python experiments/A/managed_agents_probe.py \
  --skip-compaction --output experiments/A/results/<run>.json
```

For the long-context probe:

```bash
uv run --project runtime python experiments/A/managed_agents_probe.py \
  --only-compaction --compaction-model claude-opus-4-6 \
  --compaction-turns 60 --filler-bytes 90000 \
  --output experiments/A/results/<run>.json
```

For deterministic handler behavior:

```bash
PYTHONPATH=runtime/src uv run --project runtime python \
  experiments/A/webhook_delivery_probe.py \
  --output experiments/A/results/webhook-delivery.json
```

The live probes cannot currently be rerun because the prepaid Anthropic balance
was exhausted while attempting a third compaction. Do not enable auto-recharge
or purchase credits solely for this workstream.

## Provenance ledger

Every executable line added by this workstream is covered below. The files are
experiment drivers, not Clevin product components.

| Lines | Managed Agents primitive | Invocation path | Why configuration alone was insufficient |
| --- | --- | --- | --- |
| `experiments/A/managed_agents_probe.py:1-69` | Agent definitions, tools, Skills, subagents | Test driver → `beta.agents` / `beta.skills` | Distinct markers and harmless bounded behavior were required to identify which immutable snapshot executed. |
| `experiments/A/managed_agents_probe.py:70-198` | Events, usage, compaction and Skill upload | Test driver → SDK model/event serialization | Evidence needed stable event IDs, usage/cache fields, compacted-event counts, and a valid temporary Skill archive. |
| `experiments/A/managed_agents_probe.py:199-274` | Session create/update and event injection | Test driver → `beta.sessions` / `sessions.events` | Repeated live transitions and terminal-state synchronization cannot be established from static configuration. |
| `experiments/A/managed_agents_probe.py:275-417` | Agent versions and session snapshots | Agent create/update/list → pinned and fresh sessions | Comparing active and fresh sessions required controlled version mutations and retrieval. |
| `experiments/A/managed_agents_probe.py:418-509` | Ordered events, duplicates, replay | `events.send` / `events.list` | Delivery order and ID behavior are empirical control-plane properties. |
| `experiments/A/managed_agents_probe.py:510-567` | SSE event stream | `events.stream` → disconnect → stream/list replay | Reconnect gaps and persistent-versus-preview event shapes require a real stream. |
| `experiments/A/managed_agents_probe.py:568-651` | `user.interrupt`, model and tool lifecycle | Active session → interrupt event → event replay | Cancellation timing and error semantics cannot be inferred from schemas alone. |
| `experiments/A/managed_agents_probe.py:652-736` | Native context compaction and cache usage | Long Opus/Haiku sessions → usage/compaction events | Trigger behavior and constraint retention require crossing live context thresholds. |
| `experiments/A/managed_agents_probe.py:737-856` | Self-hosted `EnvironmentWorker` volume and resource lifecycle | Session ID → Modal volume; SDK archive/delete | Comparing planes and proving cleanup require direct inspection/removal of experiment-owned state. |
| `experiments/A/managed_agents_probe.py:857-end` | Experiment orchestration only | CLI → the primitive probes above | A rerunnable, bounded harness was required to reproduce and clean all native-resource experiments. |
| `experiments/A/webhook_delivery_probe.py:1-22` | Lifecycle webhook extension point | Fake verified request boundary → production handler | A harmless local request stand-in avoids external mutations while exercising handler control flow. |
| `experiments/A/webhook_delivery_probe.py:23-52` | Environment work polling triggered by webhooks | Handler → patched verifier/native drain boundary | Duplicate and reordered invocation counts require deterministic delivery sequences. |
| `experiments/A/webhook_delivery_probe.py:53-end` | Lifecycle event filtering | CLI → production `handle_webhook` | Configuration cannot demonstrate which event types trigger queue drains. |

No runtime, provisioner, agent definition, scheduler, custom agent loop, custom
compactor, or parallel session-state system was added.

## Untested and deliberately omitted

- A third Opus compaction was attempted in
  `sesn_01P5Lp1vYb35AuQJ8KvCSRWR`; after two compactions the account emitted
  terminal `billing_error` events because the prepaid balance was exhausted.
  The two-compaction run is the completed retained-constraint result.
- Provider-side webhook retry intervals, concurrent duplicate HTTP deliveries,
  and signature replay were not observable after balance exhaustion. The
  committed webhook artifact tests production handler semantics only.
- The tool-interrupt run proves the Anthropic-visible error result and idle
  transition, not kernel-level process termination or rollback of partial file
  writes. Workstream C owns destructive sandbox/tool fault injection.
- Model/system/Skill/subagent mutation of an existing session is deliberately
  class D. No replacement loop, session migration orchestrator, or hidden
  prompt overlay was built.
- Atomic event-history/filesystem snapshots and session forks are deliberately
  class D. Implementing them would create the prohibited parallel state and
  orchestration layers.
- Memory Store retention, subagent behavior across compaction, and steering
  quality are inputs owned by E, F, and K respectively and were not duplicated.

## Cleanup ledger

All successful live runs used `clevin-swarm-A-<timestamp>-<id>` names.

| Resource | Cleanup | Result |
| --- | --- | --- |
| Sessions `sesn_01GxjLzu1AK3LxCGGQaxqSEv`, `sesn_01SgnPnJ4wjsH1TaT4Z2j543`, `sesn_012VsLLbd3rg3Z3SWjaDdaQS`, `sesn_01FFMWrRAEwPVAXW28aMDSBH`, `sesn_0181u67MEuHnmc1DEY5herCq`, `sesn_013nBj7ZsxVwtyRKiA1bbeBb`, `sesn_01A3GmGx4jBuEtP7oBAJcdSw` | Archived; each `/sessions/<id>` volume subtree removed | Success |
| Agents `agent_01H3M5KhJPhJEnd12khFpfEK`, `agent_018smwYx4AbYthpzmwpS3G8v` | Archived | Success |
| Skill `skill_01QSt816QXwcE3nz8zcqYUQk` | All versions deleted, then Skill deleted | Success |
| Opus compaction session `sesn_01HibtefzcKjxrNmPBYZ4iLv` and agent `agent_01XoUwd12Zf1CmLvck4UVy5D` | Session archived, volume subtree removed, agent archived | Success |
| Haiku terminal session `sesn_013yxjUXrwvExMQRbCA2wt9u` and agent `agent_01MSv56acvp1e95AG9cjMstS` | Terminal session cleanup attempted, volume subtree removed, agent archived | Success; no cleanup failures recorded |
| Credit-exhausted session `sesn_01P5Lp1vYb35AuQJ8KvCSRWR` and agent `agent_01M2mpYh2zVJmzXkAqmsD8Eo` | Terminal session cleanup attempted, volume subtree removed, agent archived | Success; no cleanup failures recorded |

No production agent version, environment, Memory Store, vault, Modal app,
shared volume root, image, deployment, or credential was mutated.
