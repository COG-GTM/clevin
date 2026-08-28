# Workstream H — Deployments and automation

**Question.** How much automation is expressible natively in the Managed Agents Deployment model? Can
polling plus persistent session/memory approximate event-driven behaviour? What concurrency controls
exist? Is the model merely limited, or genuinely unusable for advanced workflows?

**Verdict.** The Deployment model is a **stateless cron-to-session trigger with a supervisor that
auto-pauses on dependency failure**. Everything a run does — subagents, graded outcomes, Memory Store
writes, MCP calls, self-hosted Modal sandboxes — is the full session surface and works unchanged from
a deployment (all class A). Everything *about the trigger* is deliberately thin: minute granularity,
no concurrency control, no session continuation, no external event ingress, no retry. It is **limited
but usable**: recurring maintenance and one-minute polling are production-shaped today (`H-1`…`H-9`),
and only three things are genuinely out of reach — sub-minute reaction, event-driven wake, and
run-level continuity of a *session* (`H-10`…`H-12`).

Every claim below is backed by a JSON evidence file under `experiments/H/results/`, produced by the
rerunnable drivers in `experiments/H/`. All IDs are real; all temporary resources are archived
(§7).

---

## 1. Capability classification

| # | Capability | Class | Evidence |
| --- | --- | --- | --- |
| H-1 | Recurring schedule (5-field POSIX cron + IANA timezone), minute granularity | **A** | `h1_lifecycle.json` `cron.*` |
| H-2 | Manual/on-demand run of a deployment (`deployments.run`) | **A** | `h1` `run.manual` |
| H-3 | High-frequency (`* * * * *`) schedule sustained, no skipped fires | **A** | `h2_frequency_overlap.json` — 8 fires in 8 min |
| H-4 | Recurring maintenance that reads and writes a Memory Store | **A** | `h4_memory_continuity.json` |
| H-5 | Deployment run that delegates to built-in subagents | **A** | `h5…json` `subagents.*` |
| H-6 | Deployment run graded by a native outcome rubric (`user.define_outcome`) | **A** | `h5…json` `outcome.*`; `h5b` run `drun_01YR8JsoXVJNWBjo43j23tAg` |
| H-7 | Deployment run executing in the self-hosted Modal `EnvironmentWorker` | **A** | `h5…json` `selfhosted.*` + Modal logs |
| H-8 | Polling external systems (Linear MCP) instead of receiving a webhook | **A** | `h6_polling_wake.json` — 61.0 s detection |
| H-9 | Auto-pause on dependency failure, with typed reason + operator recovery | **A** | `h3_failures_autopause.json` |
| H-10 | Concurrency control (max-in-flight, overlap suppression, run queue) | **D** | `h2` — 3 concurrent sessions, no lock |
| H-11 | A run continuing a previous run's session (persistent worker/session) | **D** | `h4` `deployment.resume_previous_session.rejected` |
| H-12 | Event-driven ingress (GitHub/Linear → Anthropic) without polling | **D** | no event source in the SDK surface; §5 |
| H-13 | Sub-minute schedules / seconds granularity | **D** | `h1` `cron.six_field_seconds.rejected` |
| H-14 | Automatic retry of a failed run | **D** | `h3` — failure pauses, never retries |
| H-15 | Deployment-level observability of *run outcome* (vs. session creation) | **C** | §6 |
| H-16 | Version pinning and controlled roll-forward between runs | **A** | `h1` `pin.*` |
| H-17 | Deployments as declarative code in the provisioner | **C** (not built) | §8 |

---

## 2. The trigger surface, precisely

Reproduce: `uv run --project runtime python experiments/H/h1_lifecycle.py` (with
`PYTHONPATH=experiments/H`).

**Schedule** is `{"type": "cron", "expression": <5 fields>, "timezone": <IANA>}`. Accepted:
`* * * * *`, `*/1 * * * *` with `America/New_York`, `0 0 * * 7`. Rejected with precise messages:

| Input | Error |
| --- | --- |
| `*/30 * * * * *` | `expected 5 fields, got 6` |
| `@daily` | `predefined shortcuts (@daily, @hourly, ...) are not supported` |
| `0 0 L * *` | `special character "L" is not supported (5-field POSIX cron only)` |
| `0 0 * * ?` | `special character "?" is not supported` |
| `0 0 * * 1#2` | `special character "#" is not supported` |
| `Mars/Olympus` | `is not a valid IANA timezone` |
| timezone omitted | `schedule.timezone: Field required` |

So: **minute granularity, POSIX cron only, no Quartz extensions, timezone mandatory** (DST is
therefore the platform's problem, not the caller's). `schedule` may be omitted entirely, giving a
manual-only deployment. The read model returns `last_run_at` and a five-deep `upcoming_runs_at`
preview, which is the only forward-looking scheduler introspection available.

**Payload** is `initial_events`: 1–50 events, validated (`must contain at least 1 item`,
`no more than 50 item(s)`, and ``a `system.message` event must be the last event in the list``). Any
session-openable event type is allowed — including `user.define_outcome` (H-6), which is how a
deployment gets a *graded* objective rather than a prompt.

**Version pinning** (`h1` `pin.*`) is snapshot-at-create and does not drift:

- created against agent v1 → `deployment.agent = {id, version: 1}`;
- publishing agent v2 leaves the deployment on v1 — **no silent roll-forward**;
- `update(agent=<agent_id>)` re-pins to the newest version (→ v2);
- `update(agent={"id":…, "version":1})` pins back to v1 (rollback);
- a nonexistent version is rejected `404 Referenced agent not found or not accessible.`

That is enough for canary/rollback of automated work by version, with the caveat that the re-pin is
an operator action; there is no "track latest" mode.

**Lifecycle.** `status` ∈ {`active`, `paused`}; `paused_reason` is `{"type":"manual"}` or
`{"type":"error", "error": {"type": …}}`. Two non-obvious behaviours:

1. **A manual run is accepted while the deployment is paused** (`h1` `run.while_paused.accepted`).
   Pause suppresses the *schedule*, not the API. Do not treat pause as a kill switch for on-demand
   invocation.
2. **`archived_at` is set while `status` still reads `active`** (`h1` `archive.after`,
   `h3` `depl_01HRdox1Me55L1iJ85Bsj2xe`). Archive is a separate axis; `status` alone is not a
   liveness check. After archive, `upcoming_runs_at` empties and `run`/`update`/`unpause` all fail
   `400 Cannot modify archived deployment`. `list` hides archived deployments unless
   `include_archived=True`.

---

## 3. High frequency, jitter, and the absence of concurrency control

Reproduce: `experiments/H/h2_frequency_overlap.py` (`H2_MINUTES`, `H2_SLEEP_SECONDS`).

An every-minute deployment whose run body deliberately occupies the session for ~150 s
(`sleep 150`), observed for 8 minutes — i.e. work strictly longer than the period.

- **8 scheduled fires in 8 minutes, zero skips, zero coalescing.**
- **Fire delay** (`created_at − trigger_context.scheduled_at`): 1.5, 7.1, 5.9, 3.7, 3.3, 9.6, 5.8,
  6.2 s (median ≈ 5.9 s, max 9.6 s). Cron fires are *approximately* on the minute; budget ~10 s of
  jitter, and never assume a run starts inside its own second.
- **Overlap is unrestricted**: up to **3 sessions `running` simultaneously**, 8 sessions total for
  one deployment in 8 minutes (`sesn_01XtNTuxBGZuwwC1bY4szHLr`, `sesn_01QRm23P968eA61GpGsnwp1C`,
  `sesn_015iNjNebjUMTaKB2PrkPC1E`, …). The deployment stayed `active`, `paused_reason: null`.

There is **no native `max_concurrency`, no overlap policy, no "skip if still running", no queue, and
no distributed lock** anywhere in the deployment surface (H-10, class **D**). The only native levers
are (a) choose a period longer than worst-case runtime, (b) make the run body idempotent and
self-serialising via a Memory Store lock file, (c) `pause`/`unpause` from outside. Nothing was built
to fix this: a run-queue would be a scheduler replacement, which is exactly the prohibited move.

**Cost note.** Overlap is not free: the 8-session window burned 8 sessions × ~$1 list cost each with
~150 s `active_seconds` apiece. A minute-cron deployment is a standing spend commitment
(≈1440 sessions/day), which is the practical, not technical, ceiling on polling frequency.

---

## 4. Failure, auto-pause, and recovery

Reproduce: `experiments/H/h3_failures_autopause.py`.

**Validated at create time** (fail fast):

| Case | Result |
| --- | --- |
| missing environment | `404 Referenced environment not found or not accessible.` |
| missing vault | `404 Referenced vault not found or not accessible.` |
| archived agent | `400 Cannot modify archived agent` |
| **missing Memory Store** | **accepted** — `depl_01HpDnb3Z7m4zKkAwyMJY8P1` created `active` with a bogus `memstore_01AAA…` resource |

So resource references are *not* uniformly validated; a Memory Store typo survives creation and only
surfaces at run time.

**Failure at run time is a first-class, typed, self-pausing event.** Archiving an attached Memory
Store under a live every-minute deployment (`depl_01UMHaoA8z9kHSoqnDzVTzro`) produced:

```json
{"label": "archived_store.run", "run_id": "drun_01MgVpGBRrgNdxCAmrtUQCUM",
 "trigger": {"scheduled_at": "2026-08-28T02:19:00Z", "type": "schedule"},
 "session_id": null,
 "error": {"type": "memory_store_archived_error",
           "message": "session creation rejected: a referenced memory store is archived; check deployment resources"}}
{"label": "archived_store.auto_paused",
 "paused_reason": {"type": "error", "error": {"type": "memory_store_archived_error"}}}
```

**One bad fire pauses the deployment** (runs_seen = 1). Recovery is fully native and worked
end-to-end: `update(resources=[])` to remove the broken reference, then `unpause`, after which
scheduled runs resumed (`runs_before: 1 → runs_after: 2`, status `active`). There is **no retry and
no backoff** (H-14, class D) — the platform stops and waits for an operator, which for unattended
automation means an unnoticed pause is an outage. The SDK's pause-reason union enumerates the whole
supervisor: manual, archived agent/environment/memory store/vault, missing environment/resource/
vault/skill, unsupported self-hosted resources, workspace-or-organisation disabled, MCP egress
blocked, unknown.

**Archiving the agent is worse than a pause: it cascades.** Archiving the agent behind a live
deployment archived the *deployment* in the same instant (`archived_at 02:13:55.615202Z`, agent
archived 02:13:55.79), `upcoming_runs_at` emptied, **no failed run was ever recorded** (run list is
empty), and every recovery call returned `400 Cannot modify archived deployment`. A deployment cannot
outlive its agent, and this failure mode is invisible in the run log — you must watch `archived_at`.

**Self-hosted + resources is fine.** A self-hosted (`env_0152FZKRpy9f8uVw38Guzosy`) every-minute
deployment carrying a live Memory Store resource produced 4 consecutive clean scheduled runs and no
auto-pause, contradicting any assumption that `unsupported_self_hosted_resources_error` applies to
Memory Stores.

**Delayed runs.** The only delay the platform exposes is the fire jitter of §3 (≤9.6 s observed);
there is no visible queue depth, no backlog replay after a pause, and a pause window's missed fires
are **dropped, not caught up** (`upcoming_runs_at` moves forward while paused). Deployments are
therefore "at-most-once per minute", not "exactly-once eventually".

---

## 5. Memory, continuity, and the class-D core

Reproduce: `experiments/H/h4_memory_continuity.py`.

A read/write Memory Store was attached with `access: "read_write"` and instructions ("Recurring
maintenance log. Append one line per run."), pre-seeded through the API, then driven by three manual
runs of the same deployment.

**What works (H-4, class A):** cross-run state accumulates. Run 1 created `run-log.md`; run 3 read
run 2's line back and appended (`run 1 …` → `run 1 …\nrun 2 … previous=1`, 50 → 97 bytes). Every
write is attributed: `memory_versions.list` returns `created_by: {"type": "session_actor",
"session_id": …}` with `operation: created|modified`, and filtering by `session_id` returned exactly
one version per run session (`sesn_01V43…` created, `sesn_01KwE…` modified, `sesn_01RTb…` modified).
That is a genuine, native, auditable "recurring job with state".

**Two sharp edges:**

1. **The mount namespace is the store name, and it is not writable above that.** A memory seeded via
   the API at path `/maintenance/run-log.md` was not visible at `/mnt/memory/maintenance/run-log.md`,
   and `mkdir /mnt/memory/maintenance` failed `Read-only file system`. The only writable directory
   was `/mnt/memory/<store-name-slug>/`. API paths and sandbox paths are **not** the same namespace;
   an automation that seeds via API and consumes via mount must agree on the store-slug prefix or the
   run silently starts from scratch (runs 1 and 2 both believed the log was empty — see below).
2. **Concurrent runs lose writes.** Runs 1 and 2 overlapped conceptually (run 2 started 17 s after
   run 1) and both wrote "run 1 …" — run 2's `modified` version overwrote run 1's content rather than
   appending to it. With no concurrency control (§3) and last-writer-wins memory, **recurring
   maintenance over a shared Memory Store is only safe if the period exceeds the runtime.**

**Continuity is class D (H-11).** Every run creates a *new* session (`sessions.list(deployment_id=…)`
→ 3 sessions for 3 runs). There is no `session_id` on `deployments.create/update/run`; the SDK itself
refuses it before any HTTP call:

```
TypeError: Deployments.update() got an unexpected keyword argument 'session_id'
```

A deployment cannot resume, attach to, or continue an existing session, and it cannot hand a run a
warm sandbox. The only native continuity is **the Memory Store plus whatever the sandbox image and
`clevin-sessions` volume already contain** — i.e. continuity of *artifacts*, never of *context*. A
deployment "continuing prior work" therefore costs a full re-read of state and re-establishment of
plan every fire. Nothing was built to bridge this; a session-resuming trigger would be a scheduler
replacement.

**Event ingress is class D (H-12).** There is no inbound event path from GitHub or Linear into
Managed Agents. Native webhook event types (SDK `beta.webhooks`) are Anthropic → you, and are purely
about the automation's own lifecycle: `deployment.created|updated|paused|unpaused|archived`,
`deployment_run.started|failed|succeeded`. They can drive an operator alert on auto-pause — which is
the single most valuable use given §4 — but they cannot *cause* a run. `deployments.run` is the only
external trigger, so any real event-driven behaviour is "someone else's process calls
`deployments.run`", which is out of scope for a natively-configured agent.

---

## 6. Polling as a webhook substitute — it actually works

Reproduce: `experiments/H/h6_polling_wake.py` (needs `LINEAR_API_KEY` for the stimulus only; the
agent reaches Linear exclusively through the native MCP + vault).

Setup: temporary agent with `agent_toolset_20260401` + `mcp_toolset(linear)`, deployment on
`* * * * *` with `vault_ids=[vlt_011CeLyihmq1GNjHGxvtWw1q]`, and an initial message instructing a
read-only search for a unique marker string, answering `FOUND <identifier>` or `NONE`.

1. Baseline fire — MCP reached Linear, correct negative: `ORG Humza Sandbox` / `NONE`.
2. Stimulus injected outside the platform at `02:22:18.416Z` — Linear issue `HUM-15`.
3. Next scheduled fire found it: `agent.mcp_tool_result` returned the issue, and the agent replied
   `ORG Humza Sandbox` / `FOUND HUM-15`.
4. **Detection latency: 61.05 s** from issue creation to the detecting run (`drun_01RbxfYy56obzfPxzmJcz5gy`).

So the parity row "sleeps, then wakes on a new ticket or comment" is reachable at **~1 period +
jitter ≈ 60–70 s worst case ~2 min** with a one-minute cron (H-8, class **A** for the mechanism).
The gaps versus a real webhook are: (a) a floor of one minute, (b) the agent must re-derive
"what's new" every fire because runs share no context (§5) — dedupe has to live in the Memory Store
or in the external system's state (e.g. a label/assignee transition), and (c) 1440 sessions/day of
standing cost. For Devin-shaped work (ticket assigned, PR review comment posted) a one-minute
detection floor is operationally indistinguishable from an event, so **polling is a sufficient
substitute for wake-on-event; it is not a substitute for low-latency interaction.**

---

## 7. Everything a run can do is the full session surface

The most important positive finding: **the deployment trigger does not restrict session capability.**

**Subagents (H-5, class A).** A `multiagent: {"type": "coordinator", "agents": [a, b]}` agent, invoked
by `deployments.run` (`drun_01RLR7v4D4UWVA29NVUybcfP` → `sesn_013tGtyi7Zoq1srX8pzzmRAf`), delegated
two questions in parallel and synthesised: the event log shows `session.thread_created` ×2,
`agent.thread_message_sent` ×2 (one per worker, each with `to_agent_name`),
`agent.thread_message_received`, `session.thread_status_running|idle` ×4. Usage:
11.6 `active_seconds`, 1015 output tokens, 19 253 cache-read tokens. Delegation from a schedule is
real and observable per-thread.

**Graded outcomes (H-6, class A).** `user.define_outcome` as a deployment `initial_events` entry
yielded `span.outcome_evaluation_start` → `span.outcome_evaluation_end` with
`result: "needs_revision"` and a substantive grader explanation (it caught that the agent used
`CPU count:` where the rubric demanded the lowercase key `cpus:`), plus its own usage block
(1023 output tokens). A scheduled deployment can therefore carry *acceptance criteria*, not just a
prompt — the strongest available native answer to "unattended work that self-checks". `h5b` re-ran
this to watch the loop: iteration 0 graded `needs_revision`, the agent revised, and iteration 1's
evaluation was still `span.outcome_evaluation_ongoing` when the observation window closed under heavy
concurrent load from sibling workstreams — **grading is slow (tens of seconds to minutes) and its
completion under load is not something I confirmed to a `passed` verdict** (§9).

**Self-hosted Modal execution (H-7, class A).** A deployment on `env_0152FZKRpy9f8uVw38Guzosy`
(`drun_01KJiHaWUkrw6cf167jnbSA9` → `sesn_01QY3grdDmWqaWGPcGXba49j`) ran inside the Modal sandbox:
transcript shows `Linux modal 4.19.0-gvisor … x86_64`, `nproc` = 1, and `/` containing `__modal` and
`workspace`. Modal app logs corroborate the end-to-end path 1 s after the run:

```
02:18:06 INFO:anthropic.lib.environments._poller:claimed work work_id=sesn_01QY3grdDmWqaWGPcGXba49j work_type=session
02:18:06 POST …/environments/env_0152FZKRpy9f8uVw38Guzosy/work/sesn_01QY3grdDmWqaWGPcGXba49j/ack → 200
02:18:10 anthropic.lib.tools._beta_session_runner: session tool runner starting session_id=sesn_01QY3grdDmWqaWGPcGXba49j
```

Note the contrast with the cloud environment (4 CPUs, 16 GB, kernel `6.18.44-fc-v22` per the H5
outcome run): scheduled work inherits whichever environment the deployment names, and the self-hosted
one is the smaller machine.

**Observability of runs (H-15, class C).** A `DeploymentRun` reports `id`, `deployment_id`, resolved
`agent` + version, `created_at`, `trigger_context` (`{"type":"manual"}` or
`{"type":"schedule","scheduled_at":…}`), and either `session_id` **or** a typed `error`. Filters
exist for trigger type and for `has_error`. What it does **not** report is what happened *after*
session creation: there is no run status, no completion time, no success/failure of the work, no
usage roll-up. `drun.error` means "the session could not be created", not "the run failed". To know
whether last night's maintenance actually worked you must join `sessions.list(deployment_id=…)` →
`sessions.events.list` → `session.usage` yourself, or subscribe to the
`deployment_run.succeeded|failed` webhooks. Run-level truth is assembleable but not served.

---

## 8. Distance to Devin

| Parity row | Class | Distance |
| --- | --- | --- |
| Runs on a schedule | **A** | Closed for anything ≥1 minute; POSIX cron + timezone + version pinning is enough for real maintenance jobs. Devin-side sub-minute or "@hourly"-style ergonomics are missing but cosmetic. |
| Sleeps, then wakes on a new ticket or comment | **A** (mechanism) / **C** (behaviour) | Deployment polling detects an external change in ~61 s. Missing: event ingress (D), and per-fire amnesia means "what's new" must be recomputed from the Memory Store or the external system each time. |
| Responds to PR review comments and fixes CI failures | **C** | The trigger and the MCP reach are proven (§6, and the run body is a full session). The gap is not the schedule — it is that each fire is a cold session with no memory of the PR it was already fixing, so the loop must be re-derived from GitHub state every minute. Owned by K/J to demonstrate end-to-end. |
| Recovers from a crashed sandbox or failed tool | **C** (deployment half) | Deployment-level recovery is *supervisory*: typed `paused_reason`, operator `update` + `unpause`, verified working. There is no retry, no backoff, and an archived agent silently archives the deployment with no failed-run record. Unattended fleets need an external watcher on `deployment.paused` webhooks. |
| Learns across tasks | **A** (deployment half) | Recurring runs read and write a Memory Store with per-session provenance. Caveat: last-writer-wins under overlap, and the mount namespace is `/mnt/memory/<store-slug>/`, not the API path. |
| Parallel investigation, then synthesis | **A** | Subagent delegation works identically from a scheduled run; thread events are fully visible. |
| Ticket in → CI-green PR out, unattended | **C** | A deployment can *start* one unattended and can run in the Modal sandbox, but it cannot continue one across fires (H-11 D). Long jobs must fit a single session or be resumable purely from repo + Memory Store state. |
| Observable, attributable run history | **C** | Deployment→run→session→version attribution is complete; run *outcome* is not exposed (H-15). |
| Per-task cost accounting | **A** (via session) | `session.usage` per run session is the instrument; there is no deployment-level cost roll-up, so cost per automation is a client-side sum. |
| Fleet of agent variants managed as code | **C** for deployments specifically | Deployments are fully API-manageable and version-pinnable, but `packages/provision` does not reconcile them (§9); today they are imperative resources next to declarative agents. |
| Session forking | **D** | Confirmed from this side too: a deployment run cannot branch or inherit a session; the only "fork" is N independent cold sessions. |

---

## 9. What I did not test, and why

- **Deployment reconciliation in `packages/provision` (H-17).** The provisioner reconciles agents,
  environments, memory stores and vaults but not deployments. Adding that is a *product* change to a
  shared file with no new information about the primitive, and sibling workstream D owns the
  agent-as-code question — so it is documented as a class C gap and left unbuilt. Nothing in this
  workstream modifies `agent-definition.ts`.
- **Outcome-evaluation loop to a `passed` verdict.** `h5b` observed `needs_revision` → revision →
  iteration 1 evaluation `ongoing` for >13 minutes (28 `span.outcome_evaluation_ongoing` events on
  `sesn_01LwMuvVs3d5EkP4Hrsf7LvR`, no terminal span) while five sibling swarm sessions saturated the
  workspace, and the observation window closed first. The *mechanism* inside a deployment is proven;
  the end-to-end latency and terminal verdict of a graded deployment run are not. Rerun `h5b` on a
  quiet workspace with `H5B_TIMEOUT_SECONDS=2400`.
- **Multi-hour and multi-day schedules, DST transitions.** `upcoming_runs_at` is the only evidence
  cited (`0 4 1 1 *` → 2027-01-01 … 2031-01-01). Verifying an actual DST-boundary fire requires
  wall-clock waiting beyond this session.
- **`deployment_run.*` / `deployment.paused` webhook delivery.** The event types exist in the SDK, but
  registering a webhook endpoint is a Console action against a shared Modal URL that other
  workstreams depend on; changing the account's webhook registration would have disturbed siblings.
  Delivery semantics of deployment webhooks are therefore untested — the run log and `paused_reason`
  polling are what I verified.
- **Concurrency ceiling.** I observed 3 simultaneous run sessions because the run body lasted ~150 s
  against a 60 s period; I did not search for a platform maximum in-flight count, and a
  higher-concurrency probe would mostly measure workspace limits shared with siblings.
- **Retry/backoff and catch-up after a long pause.** Confirmed absent from the surface; I did not
  leave a deployment paused for hours to prove missed fires are never replayed beyond the
  `upcoming_runs_at` movement observed during the H3 pause window.
- **A replacement scheduler, run queue, concurrency lock, or session-resuming trigger** — deliberately
  not built. H-10, H-11, H-12, H-13, H-14 are class D findings; building around them is the failure
  mode this program exists to avoid.

---

## 10. Provenance ledger

Every file added by this workstream, the primitive it exercises, how Managed Agents consumes it, and
why configuration alone was insufficient. No file is a product component: all six drivers are
throwaway probes that create, drive, observe and archive temporary native resources through the
Anthropic SDK, and none is imported by the runtime or the provisioner.

| File | Primitive | Invocation path | Why not configuration alone |
| --- | --- | --- | --- |
| `experiments/H/h_common.py` | Shared harness for `beta.agents`, `beta.deployments`, `beta.deployment_runs`, `beta.sessions(.events)`, `beta.memory_stores(.memories/.memory_versions)` | SDK calls only; writes JSON evidence to `experiments/H/results/` | Observing scheduler behaviour requires timestamped polling of run/session/version state over minutes; the Console shows no fire delay, no overlap count, and no per-version actor |
| `experiments/H/h1_lifecycle.py` | Deployment CRUD, cron validation, `initial_events` validation, version pinning, pause/unpause/archive, run + session filters | `deployments.create/retrieve/update/run/pause/unpause/archive/list`, `deployment_runs.list`, `sessions.list` | The accepted cron grammar and the pause/archive semantics are only discoverable by submitting rejected inputs and reading typed errors |
| `experiments/H/h2_frequency_overlap.py` | Cron scheduler under load; native concurrency behaviour | every-minute deployment + `sessions.list(deployment_id=…)` polling | Fire jitter and simultaneous-run counts are emergent runtime facts, absent from any configuration surface |
| `experiments/H/h3_failures_autopause.py` | Deployment supervisor: reference validation, typed run errors, `paused_reason`, native recovery, agent-archive cascade | `deployments.create/update/unpause`, `memory_stores.archive`, `agents.archive`, `deployment_runs.list` | Auto-pause and its recovery path only exist at run time; they must be provoked by breaking a real dependency under a live schedule |
| `experiments/H/h4_memory_continuity.py` | Memory Store as deployment resource (`access: read_write`), memory version provenance, absence of session continuation | `memory_stores.memories.create`, `memory_versions.list(session_id=…)`, `deployments.run` ×3, attempted `update(session_id=…)` | Whether recurring runs share state, whose session wrote which version, and whether a run can resume a session are all runtime/API-shape questions |
| `experiments/H/h5_subagents_outcome_selfhosted.py` | `multiagent` coordinator, `user.define_outcome` rubric, self-hosted `EnvironmentWorker` — all reached *through* a deployment | `deployments.run` → `sessions.events.list` (thread + outcome spans) → Modal app logs | The open question was whether the deployment trigger restricts session capability; only executing each primitive from a deployment answers it |
| `experiments/H/h5b_outcome_iterations.py` | Native outcome evaluation loop (`span.outcome_evaluation_*`) inside a deployment run | `deployments.run` + event-log tailing until quiescence | H5's single poll could not distinguish "loop stopped after one grade" from "caught a transient idle" |
| `experiments/H/h6_polling_wake.py` | Cron schedule + `mcp_toolset(linear)` + vault as a substitute for event ingress | `deployments.create(schedule, vault_ids)`; external Linear GraphQL stimulus; detection read from `sessions.events.list` | Polling latency is only measurable by injecting a real external change at a known instant and timing the fire that notices it |
| `experiments/H/results/*.json` | Evidence, not code | — | — |
| `context/findings/H-deployments-automation.md` | This report | — | — |

The one non-SDK dependency is `urllib` against Linear's GraphQL API in `h6`, used **only** to create
and delete the external stimulus issue — the deliberate out-of-band actor that a webhook would
otherwise represent. The agent under test reaches Linear exclusively through the native MCP + vault.

---

## 11. Cleanup ledger

Every temporary resource is named `clevin-swarm-H-<UTC>-<id>-<case>` and archived by the driver's
`finally` block; the per-experiment `cleanup` array in each results JSON is the authoritative record.
Aggregate:

| Kind | Created | Cleanup action | Result |
| --- | --- | --- | --- |
| Probe agents | 15 | `agents.archive` | all archived |
| Deployments | 18 | `deployments.archive` | all archived (2 already archived by agent-archive cascade — recorded, not hidden) |
| Run sessions | 24 | `user.interrupt` if running, then `sessions.archive` | 21 archived by the driver; **2 failed** — see below — then archived manually; 1 (`h5b`) archived manually |
| Memory Stores | 3 | `memory_stores.archive` | all archived |
| Linear issue `HUM-15` | 1 | `issueDelete` (Humza Sandbox workspace) | `{"issueDelete": {"success": true}}` |
| Modal resources | 0 | — | read-only log inspection only |

**Two cleanup failures, both fixed.** In `h2`, `sesn_01XtNTuxBGZuwwC1bY4szHLr` and
`sesn_01QRm23P968eA61GpGsnwp1C` returned `BadRequestError` on `sessions.archive`: both were still
`running` a 150 s `sleep`, and the harness archives immediately after sending `user.interrupt` rather
than waiting for the interrupt to land, so the archive raced the still-running session. Both were
verified `idle`/`archived_at: null` afterwards and archived by hand. The lesson for any rerun is that
`user.interrupt` is asynchronous — poll to a non-`running` status before archiving; `h_common.stop_session`
now does exactly that.

`h5b` also exceeded its observation window and exited before its own cleanup phase, so it wrote no
results JSON and left `depl_01LtbAcgnhGk5Q6J9oYUmmPP`, `agent_01QBLYa2kgEWdMQNt1p7yDRK` and
`sesn_01LwMuvVs3d5EkP4Hrsf7LvR` live. All three were interrupted and archived by hand, and a final
sweep of `deployments.list`, `agents.list`, `memory_stores.list` and `sessions.list` shows no
remaining `clevin-swarm-H-*` resource.

Production resources were not mutated: no new version of
`agent_01Eef1xLtkWW2cDg1shFUpms` was published, `memstore_01JCboyFNzqNzucVq3xFpnYZ` was never
written to (all memory experiments used temporary stores), and `vlt_011CeLyihmq1GNjHGxvtWw1q` was
only *referenced* by the H6 deployment, never modified. The one deployment created with an
intentionally bogus Memory Store reference (`depl_01HpDnb3Z7m4zKkAwyMJY8P1`) is archived. Two
deployments were archived indirectly by their agent's archive; both are listed in `h3`'s cleanup
array with their cascade noted in §4.

---

## 12. Answers to the brief

**How much automation is expressible natively?** A cron-triggered cold session, per minute at best,
carrying any payload the session API accepts — including subagent rosters, graded outcomes, Memory
Store read/write, MCP reach, and self-hosted sandboxes — pinned to a chosen agent version, with a
supervisor that pauses on dependency failure and a run log that attributes each fire. That covers
recurring maintenance, scheduled audits, and polling for external change.

**Can polling plus persistent session/memory approximate event-driven behaviour?** Polling: yes, at
~61 s. Persistent session: no — there is no persistent session (H-11 D), so the "persistent" half is
carried entirely by the Memory Store, and the approximation is *stateless* wake-up plus reconstructed
context, not a live agent noticing something.

**What concurrency controls exist?** None (H-10 D). Overlap is unbounded and observed; the only
levers are period choice, an idempotent run body, and an operator pause.

**Limited, or unusable?** **Limited, not unusable.** Nothing about the trigger degrades what a run can
do, and the failure model is typed and recoverable. It is unusable only for the three things it
declines to model: sub-minute reaction, event ingress, and continuity of a session across fires —
and the correct response to those is the class D above, not a scheduler of our own.
