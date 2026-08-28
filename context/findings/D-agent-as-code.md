# Workstream D — Agent-as-code and configuration lifecycle

**Question.** Can a fleet of Clevin agent variants be managed through native Managed Agents versions
plus thin configuration-as-code, without standing up a second control plane?

**Answer.** Yes for the agent resource itself, and further than expected: the agent, its versions,
and the surrounding resource families are all fully expressible in code, drift against an
out-of-band edit is detectable and reconcilable with a native optimistic-concurrency guard, and
rollback is a first-class consequence of immutable version history. Two things do not reach Devin:
the version pointer is a single mutable "latest" with no named channels (so dev/staging/prod is
*separate agents*, not stages of one agent), and version pinning bounds configuration only — it does
not make a run reproducible.

All claims below come from six rerunnable drivers in `experiments/D/`; their raw output is committed
under `experiments/D/evidence/`. Every temporary resource was archived (§7).

---

## 1. How to reproduce

```bash
export PATH=$HOME/.nvm/versions/node/v22.23.2/bin:$PATH   # Node 22 + pnpm
pnpm install && uv sync --project runtime

# drift detector (no network, no secrets):
pnpm --filter @clevin/provision drift -- --desired-only

# drift detector against the live production agent (read-only):
pnpm --filter @clevin/provision drift

# experiments (each creates temporary resources named clevin-swarm-D-<UTC>-<id> and archives them):
uv run --project runtime python experiments/D/d1_reconstruct.py       # needs CLEVIN_AGENT_ID
uv run --project runtime python experiments/D/d2_drift.py
uv run --project runtime python experiments/D/d3_variants.py
uv run --project runtime python experiments/D/d4_canary_rollback.py
uv run --project runtime python experiments/D/d5_pinning.py
uv run --project runtime python experiments/D/sweep_cleanup.py       # archives anything a crash leaked
```

The provisioner (`pnpm --filter @clevin/provision provision`) was **not** run: it mutates the
production agent. Everything here uses either read-only calls against production or throwaway
agents.

---

## 2. Findings

### 2.1 The agent is fully reconstructible from code — class A

`packages/provision/src/agent-definition.ts` declares exactly nine fields (`name`, `description`,
`model`, `system`, `metadata`, `mcp_servers`, `tools`, `skills`, `multiagent`). Creating a fresh
agent from that definition and diffing the retrieved resource against it yields **zero drift**
(`d1_reconstruct.json` → `reconstruction.drift == []`, agent `agent_013nsbsxZFeNHNLnzNp4Bb9i`,
version 1). Nothing in the agent resource is Console-only: the code definition is a complete
specification.

The API *adds* fields it owns rather than altering what you sent —
`model.inference_geo`, `model.speed`, `model.effort` (when omitted), and a `configs` array per tool.
These appear consistently in every experiment as `server_added` and must be treated as server-owned,
not as drift; a naive round-trip comparison would report five to six false positives per agent.

Version history is append-only. `agents.update` always mints a new version
(production is at 7 with history `[7,6,5,4,3,2,1]`), and an attempt to write to a historical version
is rejected outright (`historical_write`: HTTP 400 `version: must be greater than or equal to 1`).
There is no in-place mutation of a published version.

### 2.2 Real production drift exists, and it is in the system prompt

Diffing the code definition against live production `agent_01Eef1xLtkWW2cDg1shFUpms` v7 reports a
single managed-field drift: `system` (code 4,539 chars vs. deployed 6,118 chars), and the v6→v7 delta
is *also* `system` only. So the currently deployed Clevin runs a system prompt that does not exist in
the repository — the exact failure mode configuration-as-code is supposed to prevent. Per the swarm
rules this was recorded, not reconciled: reconciling would mutate production.

### 2.3 Out-of-band drift is detectable, guarded, and reconcilable — class B

`d2_drift.py` simulates a Console edit with a direct API update (the Console has no separate
surface — it drives the same endpoints) on a production-shaped throwaway agent
`agent_01MtQf1PxW2dm7VKVwDSXjEh`:

| Step | Evidence |
| --- | --- |
| Baseline from code | v1, `drift == []` |
| Out-of-band edit (system + model.id + model.effort + `metadata.edited_by`) | v2 |
| Detection | 4 drift paths: `model.id`, `model.effort`, `system`, `metadata.source_of_truth` |
| Reapply code definition with a **stale** version guard (1) | rejected, HTTP 409 `Concurrent modification detected` |
| Reapply with the current guard (2) | v3, `residual_drift == []` |
| The out-of-band version afterwards | still retrievable — the edit is preserved as history, not erased |

Two sharp edges:

- **`version` is an optimistic-concurrency guard, not a target.** Reconciling means "write the
  desired state as a *new* version, guarded by the version you diffed". A code-managed pipeline that
  diffs and then writes without the guard will silently clobber a concurrent Console edit; with the
  guard it fails loudly, which is the behaviour a fleet needs. This is the primitive that makes
  agent-as-code safe, and it is native.
- **Metadata is a patch, not a replacement.** Reapplying the full code definition does *not* remove a
  stray key an operator added: `metadata.edited_by` survived reconciliation
  (`stray_key_survives_reapplied_definition: true`) and had to be deleted with an explicit
  `metadata={"edited_by": None}` (v4). Any drift reconciler must diff metadata keys and null the
  extras; "write the desired state" alone does not converge.

The API also enforces referential integrity across fields: removing `mcp_servers` while
`mcp_toolset` entries still reference them is rejected with
`mcp_toolset references [github linear] but no matching entry in mcp_servers`. Reconciliation is
therefore not field-by-field — the desired state must be internally consistent in a single update.

### 2.4 Variants, sharing, and fleet scale — class A, with a structural caveat

`d3_variants.py`:

- **dev/staging/prod**: three variants created from one shared code definition with per-stage
  overrides; all three reconstruct with `drift == []`
  (`agent_01VDZEoyxbGBppWk3TwaWEZA`, `agent_01D85Tm6ntpiWRM2uQhPfXeZ`, `agent_01XYmL1SCGyPnzbKyr6qDA2X`).
- **Sharing**: the shared MCP-server, tool, skill and multiagent blocks are byte-identical across
  variants after round-tripping (`shared_blocks_identical_across_variants: true`). Composition in
  code is sufficient; there is no native "include"/inheritance primitive, and none is needed.
- **Scale**: 12 variants created in 4.8 s with zero errors. No rate-limit or quota wall at fleet
  scale.
- **Names are not identities**: creating a second agent with an identical name succeeds and returns a
  distinct ID. Code-managed naming conventions are the *only* thing preventing duplicate fleets, and
  the API will not stop a double-apply from doubling the fleet.
- **The list API cannot select a fleet server-side.** The only documented filters are
  `created_at[gte]`, `created_at[lte]`, `include_archived`, `limit`, `page` — there is no metadata
  filter, so "list my workstream-D agents" is client-side filtering over a full listing (42 agents on
  the first page, 15 matching metadata). Fine at this scale; the pagination burden grows linearly
  with the fleet.

**Structural caveat (the real gap).** A variant is a *separate agent resource*. Within one agent
there is exactly one mutable "latest" pointer and an integer history; there are no named channels,
tags, or aliases, so nothing expresses "v5 is prod, v7 is staging" on one agent. Promotion is
therefore "copy the desired state into the other agent and update it", and consumers must be pointed
at a different agent ID per stage. Class A for *managing* the fleet; the aliasing Devin-style
promotion would want is absent.

### 2.5 Session-level overrides are a real variant mechanism

A session can override the agent's configuration inline (`d3_variants.json` →
`session_overrides`): a session created with an inline agent block honoured a marker from an override
system prompt (`[[OVERRIDE-VARIANT]] ok`), while the underlying agent resource was left untouched
(`agent_resource_unchanged: true`, still v1). So a one-off experiment does not need to burn a version
— which matters because version numbers are the audit trail, and probe versions pollute it.

### 2.6 Canary and rollback — class A

`d4_canary_rollback.py` builds one throwaway agent with three versions (v1 canary-A, v2 canary-B,
v3 deliberately broken) and runs the *same* arithmetic benchmark against explicit version pins:

| Pin | Session | Marker | Answer correct | Output tokens | Active s |
| --- | --- | --- | --- | --- | --- |
| v1 | `sesn_011TWm28iQQsgzH5frvNQYXt` | `[[CANARY-A]]` | yes | 147 | 2.07 |
| v2 | `sesn_01DYEauxkXLz4SMtFzgqjvbu` | `[[CANARY-B]]` | yes | 208 | 2.65 |
| v3 (broken) | `sesn_01VY1FDzEpawmuE7w2hDS7wh` | `[[BROKEN]]` | **no** (`ANSWER=unavailable`) | 148 | 2.19 |

Two versions of one agent can therefore be canaried side by side over one benchmark with no
orchestration whatsoever: each session pins `{type: "agent", id, version}`, and `session.usage` gives
per-variant token and list-cost attribution. The pin is honoured exactly — the resolved session
snapshot reports the pinned version, never latest.

Rollback works, with one semantic to internalise: **rollback is roll-forward.** You cannot move the
pointer back to v1; you re-apply v1's state as a *new* version. Re-applying v1's full state produced
v4, byte-identical to v1 on every managed field (`matches_v1: true`, `server_added_vs_v1: []`),
history `[4,3,2,1]`, and a post-rollback benchmark run scoring correct again with the v1 marker. The
broken v3 remains in history for forensics. Attempting the rollback with only a stale version guard
is rejected (409), so a rollback racing an operator's edit fails loudly rather than resurrecting the
broken config.

### 2.7 Version pinning bounds configuration, not behaviour — class C

`d5_pinning.py`, on one throwaway agent whose v1 and v2 differ only in a marker in the system prompt:

- **A live session does not follow the agent forward.** Session created by bare agent ID resolved
  v1; v2 was then published; the session's *next* turn still answered with the v1 marker
  (`[[VERSION-1]] Goodbye.`, 20 events, `follows_agent_forward: false`), and its resolved agent
  snapshot stayed at version 1. A session's configuration is frozen at creation. Deploying a new
  version is therefore safe for in-flight work — and equally means a fix does not reach running
  sessions.
- **A fresh bare-ID session resolves latest** (`sesn_01BDXcdPrBbRdbBXsVk8c6XH`, version 2, v2 marker).
  Publishing changes the next session, not the current one.
- **Mid-session, only `tools` and `mcp_servers` are updatable.** `sessions.update(agent={...})`
  accepted a `tools` replacement, and rejected `system` and `skills` with
  `` `agent.system`: only `tools` and `mcp_servers` are updatable. To change other fields, create a new session. ``
  `model`, `multiagent` and `version` are not even fields of the update body
  (`unknown field "model"`). You cannot re-point a live session at another version.
- **A nonexistent pin fails closed**: pinning version 99 → HTTP 404 `agent.version: 99 not found`.
  No silent fallback to latest.
- **Pinning is not reproducibility.** Two sessions with an identical pin, identical prompt and
  identical environment produced the same event-type sequence and the same resolved version, but
  different text ("The capital of France is Paris." vs. "Paris is the capital of France.") and
  different output-token counts. There is no seed, temperature, or determinism control on the agent
  resource. Configuration is reproducible; runs are not.

### 2.8 Declarative coverage of the wider resource surface

`d3_variants.json` → `declarative_coverage` enumerates the CRUD verbs the SDK actually exposes per
resource family:

| Family | create | retrieve | update | list | archive | delete |
| --- | --- | --- | --- | --- | --- | --- |
| agents | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| agents.versions | — | — | — | ✅ | — | — |
| sessions | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| environments | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| memory_stores | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| vaults | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| deployments | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| skills | ✅ | ✅ | — | ✅ | — | ✅ |
| files | — | — | — | ✅ | — | ✅ |

Consequences for agent-as-code:

- **Versions are read-only as a family**: no create/retrieve/update/archive verb of their own. They
  exist only as a by-product of `agents.update` and are listable. You cannot delete a bad version,
  prune history, or tag one.
- **Skills have no update verb** — a Skill change is a new Skill resource (create + delete), which is
  why a Skill reference in the agent definition is a moving target that code must pin by ID.
- **Nothing is Console-only.** Every resource family Clevin uses is reachable from the API, so a
  second control plane is unnecessary; the drift detector plus the provisioner is the whole
  requirement. There is no server-side "desired state" concept though: convergence is entirely the
  client's job, which is why the reconciler must handle metadata patch semantics (§2.3) and
  server-owned fields (§2.1) itself.

---

## 3. Class summary

| Capability | Class | Evidence |
| --- | --- | --- |
| Agent fully reconstructible from code | **A** | `d1_reconstruct.json` — zero drift on reconstruction |
| Code-managed vs. Console-managed configuration | **A** | one API surface; no Console-only fields (`declarative_coverage`) |
| Out-of-band drift detection | **B** | `drift.ts` + `d2_drift.json` — 4 drift paths found; needs a client-side detector because there is no server-side desired state |
| Drift reconciliation | **B** | `d2_drift.json` — v3 clean, 409 on stale guard; metadata needs explicit nulls |
| dev/staging/prod variants | **A** (separate agents) / **C** (as stages of one agent) | `d3_variants.json` — 3 variants clean; no channels/aliases on one agent |
| Sharing skills/tools/subagents across variants | **A** | `shared_blocks_identical_across_variants: true`; composition in code, no native inheritance |
| Fleet of many variants managed as code | **A** | 12 variants in 4.8 s, 0 errors; but no server-side metadata filter and names are not unique |
| Canary two versions over one benchmark | **A** | `d4_canary_rollback.json` — 3 pinned runs, per-run usage |
| Roll back a broken version | **A** (as roll-forward) | v4 byte-identical to v1; v3 retained |
| Active sessions during version changes | **A** | `d5_pinning.json` — live session frozen at v1; only `tools`/`mcp_servers` mutable mid-session |
| Version pinning ⇒ reproducibility | **C** | identical pin, divergent text and token counts |
| Deleting/tagging/aliasing a version | **D** | `agents.versions` exposes `list` only |
| Named release channels / promotion aliases | **D** | no alias primitive; promotion = copy state into another agent |
| Deterministic (seeded) runs | **D** | no seed/temperature on the agent resource |

## 4. Distance to Devin

- **Fleet of agent variants managed as code** (parity row: *agent versions + the provisioner*) —
  **A, essentially at parity.** Nine declarative fields cover the agent completely, drift is
  detectable, history is immutable and auditable, canary and rollback need no extra machinery. The
  gaps are ergonomic rather than structural: no version aliases (so "prod" is a separate agent, not a
  channel), no server-side fleet selection, and no uniqueness constraint to stop a double-apply.
  Devin's blueprint/snapshot promotion model would be built as *convention in the provisioner*, not
  as a platform feature.
- **Observable, attributable run history** (touched only where versions are concerned) — version
  history plus the per-session resolved agent snapshot means every session is attributable to an
  exact configuration. What is missing is the reverse index: there is no way to list the sessions
  that ran a given version.
- **Reproducibility** — pinning a version reproduces the *configuration* exactly and nothing more.
  Any Devin-like "rerun this task identically" expectation is unmet at the platform level.

## 5. Provenance ledger

Every added line configures, extends, observes, or tests a Managed Agents primitive. No line would
be meaningful without Managed Agents.

| Code | Lines | Primitive | Managed Agents invocation path | Why configuration alone was insufficient |
| --- | --- | --- | --- | --- |
| `packages/provision/src/drift.ts` | 279 | Agent configuration + versions | Reads `agentDefinition` (the same object the provisioner sends to `agents.create/update`) and compares it to `client.beta.agents.retrieve()`; run as `pnpm --filter @clevin/provision drift` | The API has no desired-state or drift endpoint: it returns the current agent and nothing else. Detecting drift requires a client-side comparison that knows which nine fields are declaratively managed and which fields (`model.speed`, `model.inference_geo`, `model.effort`, tool `configs`) the server owns. |
| `packages/provision/test/drift.test.ts` | 187 | Same | Unit tests over the comparison, incl. server-added-field and normalization cases | Guards the detector against false positives, which would otherwise make the fleet's drift signal useless. |
| `packages/provision/package.json` | 1 | Same | `drift` script entry point | Makes the detector runnable the same way as `provision`. |
| `experiments/D/_common.py` | 417 | Agents, versions, sessions, session events, usage | `beta.agents.{create,retrieve,update,archive,list}`, `beta.agents.versions.list`, `beta.sessions.{create,retrieve,update,archive}`, `beta.sessions.events.{list,send}`; extracts the TS desired state via `drift --desired-only` so experiments test the *same* definition the provisioner ships | Shared harness for the five probes: temp-resource naming, cleanup ledger, structural diff, and turn settlement. Nothing here is a product component. |
| `experiments/D/d1_reconstruct.py` | 140 | Agent configuration + version immutability | Creates an agent from the code definition, diffs it, diffs production, walks `versions.list`, attempts a historical write | Only an empirical round-trip can show whether the code definition is complete and whether history is immutable. |
| `experiments/D/d2_drift.py` | 156 | Version optimistic concurrency + drift | Out-of-band `agents.update`, detection via the diff, stale-guard update, guarded reconcile, metadata null-patch | Documented semantics do not state whether reconciliation clobbers concurrent edits or whether metadata replaces or patches. |
| `experiments/D/d3_variants.py` | 234 | Agent fleet, list API, session-level agent overrides, resource families | `agents.create` ×16, `agents.list`, session with inline agent override, SDK verb introspection per family | Fleet scale, selector limits, name non-uniqueness and declarative coverage are only observable against the live API. |
| `experiments/D/d4_canary_rollback.py` | 207 | Version pinning + `session.usage` | Three versions; sessions pinned via `{type:"agent",id,version}`; scored on marker + answer; guarded roll-forward | Canary/rollback semantics (especially "no pointer move") cannot be inferred from configuration. |
| `experiments/D/d5_pinning.py` | 247 | Session/agent binding + mid-session updates | Publishes a version under a live session, sends a further turn, probes `sessions.update(agent=…)` per field, runs one pin twice | Establishes what is pinned to the session vs. the agent, and the reproducibility ceiling. |
| `experiments/D/sweep_cleanup.py` | 65 | Agent/session archive | `agents.list` + `sessions.list` filtered by the temp-resource convention, then `archive` | A driver that dies mid-run leaks a resource; this makes the cleanup ledger regenerable rather than aspirational. It found and archived exactly one leaked agent and one leaked session. |

Nothing was built that would survive without Managed Agents: no second control plane, no state
store, no scheduler, no orchestrator. The drift detector is deliberately read-only — reconciliation
is performed by the existing provisioner, not by new code.

## 6. What was not tested, and what was deliberately not built

**Not built (class D — left unbuilt on purpose):**

- A version alias/channel layer (a "prod pointer" table mapping stages to version numbers). This
  would be a second control plane holding the platform's own release state; the finding is that the
  primitive is absent.
- A version-deletion/pruning mechanism, since `agents.versions` exposes only `list`.
- A determinism harness (seed/replay) to make pinned runs reproducible.
- A server-side-equivalent fleet index to work around the missing metadata filter on `agents.list`.

**Not tested:**

- Drift on *non-agent* resources. The detector covers the agent's nine fields only; environments,
  memory stores, vaults and deployments were enumerated for declarative coverage but their drift
  semantics were not exercised.
- Skills and `multiagent` in a variant. Production is `skills: []` / `multiagent: null`, so the
  "shared across variants" result is about shared *definition blocks*, verified byte-identical after
  round-trip, not about a live Skill or subagent roster. Workstreams F and K own the populated case;
  once they land a roster in `agent-definition.ts`, `drift.ts` covers it with no change.
- Console UI behaviour. There is no Console access in this session, so "a Console change" was
  simulated with a direct API update. This is faithful in that the Console drives the same endpoints,
  but a Console-only field, if one existed, would not have been observed. §2.8 argues against that
  from the SDK surface, not from the UI.
- Concurrent reconciliation at scale (many writers racing one agent). The 409 guard was verified with
  a single deliberate stale write.
- Production reconciliation. The system-prompt drift in §2.2 was left in place; verifying that a
  reconcile of production behaves as the throwaway agent did would require mutating production.
- Self-hosted environment interaction. All probe sessions ran on the cloud environment
  `env_01F4KCNxYngRzYKG5a1QLRZT`; version changes were not tested against a self-hosted
  `EnvironmentWorker` session (workstream C's surface).

## 7. Cleanup ledger

Machine-readable form: the `cleanup` array in each `experiments/D/evidence/*.json`.

| Driver | Temporary resources | Cleanup action | Result |
| --- | --- | --- | --- |
| `d1_reconstruct.py` | 1 agent | `beta.agents.archive` | archived |
| `d2_drift.py` | 1 agent | `beta.agents.archive` | archived |
| `d3_variants.py` | 16 agents, 1 session | `beta.agents.archive`, `beta.sessions.archive` | all archived |
| `d4_canary_rollback.py` | 1 agent, 4 sessions | as above | all archived |
| `d5_pinning.py` | 1 agent, 4 sessions | as above | all archived |
| `sweep_cleanup.py` | — (sweeper) | archived 1 leaked agent + 1 leaked session from an earlier crashed run | archived |

No cleanup failures. Nothing in production was mutated: the production agent, environments, memory
stores, vaults, and Modal resources were read-only throughout, and the provisioner was never run.
Earlier driver runs (before crash-safe `finally` cleanup was added) left the one agent and one
session that `sweep_cleanup.py` then archived; rerunning any driver now cleans up even on failure.
