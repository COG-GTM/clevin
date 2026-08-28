# I — Observability and economics

**Question.** Are native events, usage data, session history, Console views and Modal logs enough to
*operate* an advanced cloud agent — debug it, attribute its behaviour, and account for its cost?

**Answer.** Native telemetry is unusually good at *attribution* and unusually poor at *alerting*.
Everything that happened to a session is reconstructible after the fact from
`sessions.events.list` + `sessions.threads.list` + `environments.work.list`, and SSE delivers it in
~9 ms. But nothing native ever tells you something is wrong: there is no error rate, no stuck-session
signal, no "worker died" event, no cost push notification into the event stream, and no join key
between a tool call and the sandbox that ran it. Every alert an operator needs must be *derived by a
reader that is already polling*, and two of them (files changed, sandbox identity) cannot be derived
from native data at all.

All instrumentation here is read-only over native surfaces: `experiments/I/observe.py` (collector,
618 lines) and `experiments/I/probe.py` (experiment drivers, 886 lines). No event store, no metrics
backend, no dashboard — the blind spots below are left as findings, per §2.

---

## Class summary

| # | Capability | Class | Evidence |
| --- | --- | --- | --- |
| I-1 | Attribute a run: version → session → thread → model span → tool call → result → compaction → final state | **A** | `sesn_015ikak6ZguvnYVSo2fbBqqE`: 58 events, 6 spans, 5 tool calls, full chain reconstructed from `events.list` alone |
| I-2 | Per-session and per-thread cost accounting | **A** | `sesn_01CqH9wfNkgaHRYp7w1NtebR`: session $0.04 == sum of 3 thread costs ($0.02 + $0.01 + $0.01), delta $0.00 |
| I-3 | Prompt-cache visibility | **A** | Opus run: `cache_read` 24,221,989 / `cache_creation` 1,776,067 tokens, cache-read fraction 0.9316 |
| I-4 | Tool latency and tool errors | **A** | Self-hosted probe min/median/max 0.265 / 0.305 / 2.008 s; injected `cat /nonexistent-obs-probe` surfaced as `user.tool_result is_error=true` |
| I-5 | Real-time monitoring latency | **A** | SSE median lag **9 ms** (max 18 ms) vs 5 s polling median **3.571 s** — `sesn_01Wq14CHra5acxGheefF23UQ` |
| I-6 | Fleet-wide cost attribution by agent version and experiment | **A** (with a caveat) | 164 sessions, $104.42, **2 API calls, 0.96 s**; but 50 sessions ($15.79) carry no metadata and are unattributable |
| I-7 | Worker / execution-plane visibility (lease, heartbeat, stop) | **A** | Work item states `active → stopping → stopped` observed 3 s / 16 s after `work.stop`; Modal log lines carry `sesn_…` |
| I-8 | Compaction accounting (what it cost, what it dropped) | **C** | `agent.thread_context_compacted` carries only `{event_id, at}`; the 867,646 → 40,753 token drop and the 1,033-in/331-out summarisation cost are *inferred* from adjacent spans |
| I-9 | Budget/cost enforcement observability | **C** | Enforcement is a 400 at event admission; `session.budget_reached` exists **only as a webhook type**, never in the 35 session event types |
| I-10 | Detecting a stuck or stranded session | **C** | `requires_action` stop reason + unmatched `agent.tool_use` + frozen `latest_heartbeat_at` are derivable; no native alert, and no state change ever happens |
| I-11 | Subagent cost and activity from the session stream alone | **C** | Session-level spans see 48 input / 39,422 cache-read tokens; cumulative `session.usage` says 90 / 63,197 — subagent spans exist only on the child threads |
| I-12 | Tool call → sandbox identity | **D** | No native event, work item field, or Modal sandbox tag carries the pair; the only join is the runtime's own convention (sandbox name == session id) |
| I-13 | Files changed by a run | **D** | No native event names a path; recovered only by `modal.Volume.listdir` outside the native model |
| I-14 | Cost per *successful* task | **D** as configured / **C** available | Cost is exact; "successful" is not a native field unless outcome evaluation is configured (`user.define_outcome`, `span.outcome_evaluation_*` exist but the production agent does not use them) |
| I-15 | Aggregate/org-level metrics, error rates, alerting | **D** | Admin API unavailable (sibling A); no native aggregation endpoint — 164-session aggregate above was computed client-side |

---

## Evidence

### The native surface (census)

`probe.py census` (`artifacts/census/20260828T042809Z-d65a4b/result.json`) enumerates the SDK, which
is the authoritative list of what can be observed:

- **35 session event types**, including `span.model_request_start|end`, `agent.tool_use`,
  `user.tool_result`, `agent.mcp_tool_use|result`, `agent.thread_context_compacted`,
  `session.usage`, `session.error`, and the outcome-evaluation trio
  `user.define_outcome` / `span.outcome_evaluation_start|ongoing|end`.
- **44 webhook types**, a *different* set — `session.budget_reached`, `session.requires_action`,
  `deployment_run.failed|started|succeeded`, `vault_credential.refresh_failed` exist as webhooks and
  have **no session-event equivalent**.
- `session.usage` fields: `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation`, `list_cost`, `active_seconds`, `server_tool_use`.
- Model-span usage fields: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `speed`.

The split matters operationally: **history is for reconstruction, webhooks are for alerting, and the
two are not interchangeable.** A monitor that only replays events can never learn that a budget was
reached; a monitor that only receives webhooks cannot reconstruct a run.

### I-1 / I-4 Full-chain attribution on a self-hosted session (class A)

`probe.py selfhosted` → `sesn_015ikak6ZguvnYVSo2fbBqqE`
(`artifacts/selfhosted/manual/sesn_015ikak6ZguvnYVSo2fbBqqE.json`):

```
agent=agent_01Eef1xLtkWW2cDg1shFUpms v7 env=env_0152FZKRpy9f8uVw38Guzosy
events=58 threads=1 spans=6 tools=5 compactions=0 errors=2
list_cost=$0.11 thread_sum=$0.11 delta=$0.0
cache_read_fraction=0.8233   active_seconds=17.751   budget=$3.0
tool_latency_s min/med/max=0.265/0.305/2.008
work_items=1  work_stats={'depth': 0, 'pending': 0, 'workers_polling': 0}
sandbox_by_name={'id': 'sb-BsGmEo98eQ9B0dmTssmUI8', 'joined_on': 'sandbox name == session id'}
volume_id=vo-uj5BUIG4br5MnRYO6cyHbV  volume_files=1
```

Every link except the last two is a native field: the session carries `agent.{id,version}` and
`environment_id`; each `span.model_request_start/end` pair gives per-request tokens and latency; each
`agent.tool_use` pairs with a `user.tool_result` by event id, giving per-tool latency and
`is_error`; `environments.work.list` gives the work item for the session. The deliberate failure
(`cat /nonexistent-obs-probe`) came back as a typed error result, and the two `errors=2` entries were
MCP initialisation failures for the GitHub and Linear credentials — recorded, not swallowed.

*Attribution is genuinely Devin-grade.* What is missing is everything below the tool boundary.

### I-5 SSE is effectively real-time; polling is not a substitute (class A)

`probe.py sse` (`artifacts/sse/20260828T043827Z-95979a/result.json`), one live self-hosted session,
both consumers native (`events.stream` vs `events.list`), lag measured against the server's own
`processed_at`:

| consumer | events | min | median | max |
| --- | --- | --- | --- | --- |
| SSE | 12 | 0.002 s | **0.009 s** | 0.018 s |
| polling @5 s | 12 | 0.413 s | **3.571 s** | 5.262 s |

SSE advantage median 3.562 s. One event arrived on each side that the other had not yet seen at
sample time (`events_seen_by_sse_only` / `_by_poll_only` are single-element), so neither consumer is
lossy — polling is merely late by ~half the interval. Practical ceiling for any
Managed-Agents-only monitor: **~10 ms if you hold a stream open, ~½ poll interval otherwise.**

### I-2 / I-11 Cost accounting and the subagent blind spot

`probe.py econ` → `sesn_01CqH9wfNkgaHRYp7w1NtebR`, one coordinator delegating to two subagents
(`artifacts/econ/manual/…json`):

```
session_list_cost_usd        0.04
thread_list_cost_usd         {coordinator: 0.02, probe-a: 0.01, probe-b: 0.01}
thread_list_cost_sum_usd     0.04     session_minus_thread_sum_usd  0.00
session_usage_tokens         input 90    cache_read 63,197
span_token_sums (session)    input 48    cache_read 39,422   spans 6
```

Two findings in one artifact. Cost reconciles exactly across the session/thread boundary — per-role
cost attribution is real (consistent with sibling F). But the **session-level event stream is not the
whole run**: 42 input and 23,775 cache-read tokens belong to subagent threads and appear only when you
list each thread's events. A monitor that watches the session stream and trusts span sums undercounts
by ~40 % on a delegating session. Cumulative `session.usage` is the only single-call figure that is
complete.

### I-3 / I-8 Compaction: expensive, effective, and almost undocumented (class C)

Collector output for sibling A's Opus long run `sesn_01HibtefzcKjxrNmPBYZ4iLv`
(`artifacts/compaction/manual/opus-compaction.json`, 569 events, 64 spans, 2 compactions, $23.27):

```
compaction sevt_01A1m9ZKKMgYGaZcUXCmWA9c @02:30:28.857Z
  event payload fields        ['at', 'event_id']          <- the entire payload
  span before compaction      cache_read 828,239 + cache_creation 39,407  = 867,646 tokens
  summarisation span          input 1,033  output 331   latency 8.46 s
  first span after            cache_creation 40,753, cache_read 0
  => context 867,646 -> 40,753 tokens (95.3 % reduction), 1.15 s stall before resuming
```

The compaction event itself says only *that* it happened. Its cost, its input size, its output size
and the retained-context size are all **inferences from adjacent model spans** — reliable here
because the summarisation request is the next span at the identical timestamp and the resumed request
re-creates the whole cache. That inference is exactly what `observe.py::_compaction_windows`
implements, and `tokens_reported_by_event: null` records that nothing native asserts it. Cache
economics on the same run: 24.2 M cache-read vs 1.78 M cache-creation tokens, 93 % read fraction —
prompt caching is what makes an 867 K-token context affordable, and it is fully visible.

### I-9 Budget: enforced at the door, announced only by webhook (class C)

Two probes, both cloud, tiny budgets:

- `probe.py budget --budget 5` → `sesn_01NEMvd9jE27VwcyQMm7PDaS`: budget **$0.05**, final list cost
  **$0.11** — a single model turn overshot the cap **2.2×** before anything stopped it. A budget is
  not a spend limit within a turn.
- `probe.py budget_turns --budget 1 --turns 30` → `sesn_01PmsqzyDPSyTFgf2hZEShV9`
  (`artifacts/budget_turns/20260828T043913Z-b8e46a/result.json`): 11 turns ran; turn 12 was rejected
  at admission:

  ```
  400 invalid_request_error: session budget reached: consumed list cost has met
  `budget.max_list_cost`; only `user.tool_confirmation`, `user.tool_result`,
  `user.custom_tool_result`, and `user.interrupt` are accepted until the budget is raised
  ```

  Per-turn `event_counts` across all 12 turns contain **no budget event of any kind** — only
  `session.usage`. `session.budget_reached` appears in the 44 webhook types and in none of the 35
  session event types.

So: the multi-turn case is enforced exactly (overshoot ratio 1.0) and the single-turn case is not;
and a cost monitor built purely on session history *cannot see the stop* — it must either receive the
webhook or diff cumulative `session.usage` against `budget.max_list_cost` itself.

### I-7 / I-10 Execution plane: visible, but no alert exists

`probe.py terminate` → `sesn_015o4uWpg41z1YmzaWtNKi5E`
(`artifacts/terminate/20260828T043328Z-637b75/result.json`). There is no `sessions.terminate`; the
native levers are a `user.interrupt` event and `environments.work.stop`:

| sample | session status | work state | stopped_at |
| --- | --- | --- | --- |
| before stop | idle | active | — |
| after `user.interrupt` (+3 s) | idle | active | — |
| `work.stop` +2 s | idle | **stopping** | — |
| +16 s | idle | **stopped** | 04:34:41.061Z |

`user.interrupt` alone changed no work-item field; the work item is the only place the stop is
visible, and it lags the request by ~16 s. Modal's own logs carry the session id, which is what makes
the two planes joinable at all:

```
04:39:08 POST https://api.anthropic.com/v1/environments/env_0152…/work/sesn_01Wq14CHra5acxGheefF23UQ/heartbeat
04:41:07 POST https://api.anthropic.com/v1/environments/env_0152…/work/sesn_01Wq14CHra5acxGheefF23UQ/stop
```

Applying the collector to sibling C's *stranded* session `sesn_01YDmDJtbTgYkhXRisHri7XZ` (worker
killed mid-tool; `artifacts/stranded/manual/…json`) shows precisely which native fields expose the
failure — and how weak they are:

```
session.status_idle @02:35:19.420Z stop_reason={'type': 'requires_action',
                                                'event_ids': ['sevt_0197CcGssxZMPjyPBCmrjKjC']}
tool_calls[0] bash  requested 02:35:19.271Z  latency 427.5 s  is_error true
work item: state active, latest_heartbeat_at frozen at 02:35:18.073Z for ~6 min
```

Three derivable signals — a `requires_action` stop reason naming the orphaned `tool_use`, an
`agent.tool_use` with no result, and a frozen heartbeat — and **zero** native notifications:
`staleness.native_alert_exists: false`. Nothing transitions, nothing retries, no webhook fires
(`session.requires_action` fires for the *state*, not for its staleness). Detection latency is
whatever threshold the reader invents. This is the operational core of sibling C's class-D
"platform does not notice a dead worker": the platform does not notice because **nothing in the model
is watching**.

### I-6 Fleet economics (class A, with a discipline caveat)

`probe.py fleet` (`artifacts/fleet/20260828T043112Z-0de729/result.json`) over the whole swarm's
history: **164 sessions, $104.42 list cost, 2 API calls, 0.96 s** — `sessions.list` returns cost and
agent version inline, so fleet accounting is essentially free.

| grouping | top rows |
| --- | --- |
| by agent@version | `agent_01M2mpYh…@v1` $25.81 (1 session) · `agent_01XoUwd1…@v1` $23.27 (1) · production `agent_01Eef1xL…@v7` $6.10 (33) |
| by `metadata.experiment` | `workstream-A` $68.25 (24) · `clevin-swarm-F` $13.88 (23) · **`None` $15.79 (50)** · `clevin-swarm-K` $5.65 (31) · `clevin-swarm-H` $0.31 (25) |

Session metadata is the *only* tagging mechanism, it is free-text, and it is unset on 50 sessions
worth $15.79 (15 % of spend). Cost attribution is a configuration discipline, not a platform
guarantee.

### I-12 / I-13 The two hard blind spots (class D)

- **No tool call names its sandbox.** No session event, work-item field (`data` carries only
  `{id, type}`), or Modal sandbox tag contains the other side's identifier. The only join is the
  Clevin runtime's own naming convention (`sandbox_runtime.py` names each sandbox after the session
  id), which `observe.py::_collect_sandbox` exploits and labels
  `"joined_on": "sandbox name == session id"`. If a future worker changed that convention, native
  telemetry would lose the execution plane entirely. Recorded as class D and **not** fixed: fixing it
  means emitting our own correlation records, which is the standalone observability product §2
  forbids.
- **No native event names a changed file.** Tool inputs contain commands, not effects; there is no
  diff, no path list, no fs event. Files changed are recoverable only by `modal.Volume.listdir`
  (`volume_files`, `vo-uj5BUIG4br5MnRYO6cyHbV`), i.e. from outside the Managed Agents model — and
  even then only as a post-hoc tree with mtimes, never attributed to the tool call that caused it.
  Native history and sandbox filesystem state remain transactionally uncoupled (consistent with
  sibling A).

### I-14 Cost per successful task (class D as configured)

Cost is exact per session and per thread; **success is not a native field.** `sessions.list` exposes
`status` (`idle`/`terminated`/…), which says the run stopped, not that it worked. The one native
success primitive is outcome evaluation — `user.define_outcome` plus
`span.outcome_evaluation_start|ongoing|end` exist in the event-type census — but the production agent
never emits them, so across all 164 sessions no native predicate distinguishes a green PR from a
stranded worker. Cost-per-successful-task is therefore only obtainable by *configuring outcomes
first* (class C, untested here), and no amount of reading history recovers it retroactively.

---

## Distance to Devin

| Parity row | Class | Distance |
| --- | --- | --- |
| Observable, attributable run history | **A** | At parity, and arguably ahead on model-level detail (per-request tokens, cache, speed, thinking); behind on *effects* — Devin's history shows files changed, this one shows commands issued. |
| Per-task cost accounting | **A** for cost, **D** for "per successful task" | Session/thread list cost is exact and free to aggregate; there is no native notion of task success to divide by, and no org-level rollup (Admin API unavailable). |
| Recovers from a crashed sandbox or failed tool | observability half **C** | A monitor can *tell* a session is stranded (requires_action + orphaned tool_use + frozen heartbeat) within one poll, but nothing native raises it, so the "recover" trigger must come from outside the model. |
| Fleet of agent variants managed as code | **A** (economics half) | Cost by `agent@version` in one call; only weakness is that experiment tagging is unenforced free-text metadata. |

---

## What I could not test, and why

- **Deliberately not built (class D):** an event store, a metrics pipeline, alert rules, a
  sandbox↔session correlation emitter, or a file-change tracker. All four would have converted I-10,
  I-12 and I-13 into "solved" by building the standalone observability product §2 prohibits. The
  blind spots are reported instead.
- **Console views:** not evaluated — no browser workstream (§4 dead end), and Console-only surfaces
  cannot be read from the API. Any Console-only metric is therefore unverified either way.
- **Outcome-evaluation instrumentation (I-14 → class C):** `user.define_outcome` /
  `span.outcome_evaluation_*` were enumerated but never exercised; testing them means shipping an
  outcome definition on an agent version, which belongs with D/J rather than a read-only workstream.
- **Webhook-side alerting end to end:** `session.budget_reached` and `session.requires_action` were
  identified from the SDK's webhook union, not received. The production webhook endpoint is
  registered for `session.status_run_started` only, and re-registering webhook subscriptions on the
  shared Modal deployment would have mutated a shared resource other workstreams depend on.
- **Compaction was measured on a sibling's session, not a fresh one.** My own compaction probe
  (`sesn_017ytXdtsi51N4vGbsT81DYd`, Haiku) hit `prompt is too long: 203,959 > 200,000` and terminated
  *without* compacting, so it produced a model-dependent boundary rather than compaction telemetry; I
  reused A's Opus run rather than re-spending ~$23 to reproduce a known result.
- **Worker-restart telemetry** was read from sibling C's chaos session rather than re-injected: the
  fault harness is theirs, and re-running it would duplicate their spend. All conclusions above are
  from my own collection against their session ids, not from their prose.
- **SSE run truncated:** the freshness sample is 12 events on a session still `running` when the
  driver's deadline hit; the lag distribution is tight but small-n.

---

## Provenance ledger

Every line added lives in `experiments/I/` and exists to *read* a native surface. Nothing was added
to the runtime, the provisioner, or the agent definition.

| File / unit | Primitive it observes | How it is invoked | Why configuration alone was insufficient |
| --- | --- | --- | --- |
| `observe.py::list_events`, `_reduce_events` | Session event stream (`sessions.events.list`), 35 event types | `python observe.py <session-id>` | The API returns an undifferentiated event list; correlating `span.model_request_start/end` and `agent.tool_use`/`user.tool_result` pairs into spans and latencies is arithmetic no configuration performs. |
| `observe.py::_collect_threads` | `sessions.threads.list` + per-thread events/usage | same | Per-role cost and the subagent token gap (I-11) require one call per thread; nothing aggregates them. |
| `observe.py::work_client`, `_collect_work` | `environments.work.list` / `.stats` (self-hosted execution plane) | same | Work reads reject the workspace key (401) and need `ANTHROPIC_ENVIRONMENT_KEY`; discovering and recording that boundary *is* a finding. |
| `observe.py::_collect_sandbox` | Modal sandbox + `clevin-sessions` volume (the sandbox side of the `EnvironmentWorker` primitive) | same | Tests whether the native↔sandbox join exists; it does not (I-12) — the code documents the convention it has to rely on. |
| `observe.py::_economics` | `session.usage`, thread `usage`, `budget.max_list_cost` | same | Reconciling session cost against thread sums and span sums is the measurement that exposed I-11. |
| `observe.py::_staleness` | `stop_reason` on `session.status_idle`, unmatched `agent.tool_use`, `latest_heartbeat_at` | same | Directly tests I-10: whether a native-only reader can detect a stranded session. Records `native_alert_exists: false` rather than raising an alert. |
| `observe.py::_compaction_windows` | `agent.thread_context_compacted` + adjacent model spans | same | Tests I-8: the event's payload is `{event_id, at}`, so compaction cost/size can only be inferred. The inference is the experiment. |
| `probe.py::profile_census` | SDK event-type and webhook unions | `python probe.py census` | Establishes the observable surface from the SDK (the source of truth per §4b) instead of guessing. |
| `probe.py::profile_selfhosted` | Session + `EnvironmentWorker` + sandbox, with an intentional tool failure | `python probe.py selfhosted` | Produces the I-1/I-4 chain, including a real `is_error` tool result. Uses `CLEVIN_SMOKE_TEST`. |
| `probe.py::profile_econ` | Native subagent delegation + per-thread usage | `python probe.py econ` | I-2/I-11 need a genuinely delegating session; a single-thread session cannot show the gap. |
| `probe.py::profile_budget`, `profile_budget_turns` | `budget.max_list_cost`, event admission | `python probe.py budget[_turns] --budget N` | I-9: single-turn overshoot vs multi-turn admission refusal, and the absence of an in-stream budget event. |
| `probe.py::profile_sse` | `sessions.events.stream` vs `.list` | `python probe.py sse` | I-5 is a latency measurement; both consumers are native, `threading` only lets them run against one session. |
| `probe.py::profile_terminate` | `user.interrupt` event + `environments.work.stop` | `python probe.py terminate` | I-7: measures which native lever moves which field, and the ~16 s lag. |
| `probe.py::profile_fleet` | `sessions.list` (cost + agent version + metadata inline) | `python probe.py fleet` | I-6: tests whether fleet economics is obtainable natively; it is, in 2 calls. |
| `probe.py::profile_compaction`, `profile_watch` | Context compaction under pressure; live session tailing | `python probe.py compaction|watch` | Compaction telemetry under a controlled filler load; the run's terminal prompt-length failure is itself reported. |
| `probe.py::Run` helpers (temp agent create/archive, artifact + cleanup ledger writing) | Agent versions (temporary, `clevin-swarm-I-*`), session create/archive | used by every profile | §7 requires named temporary resources and a cleanup ledger; the helper enforces both. |

## Reproduction

```bash
uv sync --project runtime
cd experiments/I
uv run --project ../../runtime python probe.py census        # native surface
uv run --project ../../runtime python probe.py selfhosted    # I-1, I-4
uv run --project ../../runtime python probe.py econ          # I-2, I-11
uv run --project ../../runtime python probe.py sse           # I-5
uv run --project ../../runtime python probe.py fleet         # I-6
uv run --project ../../runtime python probe.py terminate     # I-7
uv run --project ../../runtime python probe.py budget_turns --budget 1 --turns 30   # I-9
uv run --project ../../runtime python observe.py <session-id> [--raw-events]        # any session, incl. a sibling's
```

Requires `ANTHROPIC_API_KEY`; `ANTHROPIC_ENVIRONMENT_KEY` for the work plane, `MODAL_TOKEN_ID` /
`MODAL_TOKEN_SECRET` + `MODAL_ENVIRONMENT=clevin` for the sandbox/volume half. Artifacts land in
`experiments/I/artifacts/<profile>/<run-id>/`.

## Cleanup ledger

`experiments/I/artifacts/cleanup-ledger.json` (machine-readable).

| Resource | Action | Result |
| --- | --- | --- |
| `agent_01TszZNjSDehu9E4r5boemvq` (`…-budgetturns-20260828T043750Z-67e5e2`) | agent archive | archived |
| `agent_01J3cHbLtBpwRkws7LQKq6XD` (`…-compaction-20260828T043128Z-c1e94e`) | agent archive | archived |
| `agent_01RuXj6Dy5vPWPfjGsjwtf7Y` (`…-coordinator-20260828T042833Z-3ccc18`) | agent archive | archived |
| `agent_015xGFfCS5YjwgwZ5sAnHyQd` (`…-probe-b-20260828T042833Z-3ccc18`) | agent archive | archived |
| `agent_01Eik2J4PLyQooprwornu3Da` (`…-probe-a-20260828T042833Z-3ccc18`) | agent archive | archived |
| `agent_01DxT5gsex9BziaE3oKmC8Nr` (`…-budgetturns-20260828T043913Z-b8e46a`) | agent archive | already archived by the probe |
| Probe sessions (`sesn_01NEMvd9…`, `sesn_01PmsqzyD…`, `sesn_01CqH9wf…`, `sesn_015o4uWp…`, `sesn_01Wq14CH…`, `sesn_015ikak6…`, `sesn_017ytXdt…`) | left in place, unarchived | **deliberate** — they are the evidence for every claim above and cost nothing at rest; a re-verifying session needs them readable |
| Modal sandboxes / `clevin-sessions` volume subtrees | none | **deliberate** — read-only observation; the sandboxes belong to the shared production app and the volume entries are sibling workstreams' evidence |
| Production agent `agent_01Eef1xLtkWW2cDg1shFUpms`, environments, memory store, vault, Modal app/webhook | none | never mutated; all reads only |

No cleanup failures. No temporary environments, memory stores, vaults, deployments or Modal
resources were created by this workstream.
