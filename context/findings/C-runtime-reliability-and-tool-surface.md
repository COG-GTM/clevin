# Workstream C — Runtime reliability, recovery, and the tool surface

Status: **partial — halted by prepaid-balance exhaustion** at 2026-08-28 02:43 UTC.
`POST /v1/sessions` returns `400 invalid_request_error: Your credit balance is too low to access
the Anthropic API` (`request_id req_011CeUNqsWchmFxV8fGeqvAM`). Per §7 of the master prompt no
credits were purchased and no further sessions were created. Everything below is either backed by a
live session/log excerpt or explicitly labelled as SDK-source-derived or as inference.

All experiments used a temporary, native-tools-only harness agent
(`agent_01CDtid4b87nEjESmYBPAE9Y`, Haiku 4.5) against the shared self-hosted environment
`env_0152FZKRpy9f8uVw38Guzosy`. The production agent version was never mutated. All prompts begin
with `CLEVIN_SMOKE_TEST`.

Reproduction drivers: `experiments/C/` (`chaos.py`, `run_case.py`, `harness_agent.py`,
`exp_c0_baseline.py`, `exp_c3_recovery.py`, `exp_c8_result_injection.py`); raw evidence in
`experiments/C/artifacts/`.

---

## 1. Summary table

| # | Capability | Class | One-line basis |
| --- | --- | --- | --- |
| C-1 | Tool timeout produces a usable error the agent can reason about | **A** | 150.6 s dispatch timeout → `user.tool_result is_error=true "tool 'bash' timed out"`; agent explained and stopped cleanly |
| C-2 | Platform notices a dead worker and re-dispatches the work | **D** | Session sat in `requires_action` forever; 270 s of continuous `work/poll` returned nothing; webhook replay reported `spawned: []` |
| C-3 | Recovery of a stranded session without custom orchestration | **C** | A bare `SessionToolRunner` re-attached, reconciled history, re-dispatched the pending call and the session ran to `end_turn` — native mechanism, but no native trigger |
| C-4 | Safe retry (no split-brain between two workers) | **A** | Heartbeats every ~30 s carry `expected_last_heartbeat` fencing; 412 = lease lost → worker stops serving |
| C-5 | Exactly-once tool side effects across a worker crash | **D** (mitigable to **C** by prompt/tool design) | Recovery re-dispatches any `agent.tool_use` lacking a `user.tool_result`; nothing records that the side effect already happened |
| C-6 | Per-session / per-owner work routing in a shared environment | **C** | Work items are environment-scoped with no filter; ownership can only be decided *after* claiming, by retrieving the session and reading its metadata |
| C-7 | Concurrent sessions on the self-hosted fleet | **A** (at observed scale) | ≥6 concurrent session sandboxes heartbeating in the Modal app log; one sandbox per session, no queueing observed |
| C-8 | Very large tool output does not blow up the session | **A** (SDK-source) | Native bash caps output at 100 KiB, keeps the tail, prefixes `[output truncated]` |
| C-9 | Where tools execute | **A** | Native tool events execute wherever the session's tool runner lives (the Modal sandbox); MCP tool calls are never dispatched to the runner — they execute server-side |
| C-10 | Malformed / oversized / duplicate `user.tool_result` handling | **untested** | Driver written (`exp_c8_result_injection.py`), not run — balance exhausted |

---

## 2. Evidence

### C-1 Tool timeout (class A)

Session `sesn_01LP1pjukhvc8V5FPw3QA44w` (`artifacts/c2-tool-hang.json`), fault mode `hang`:

```
02:22:59.220708Z agent.tool_use   bash {"command": "echo CHAOS-hang"}
02:25:29.822821Z user.tool_result is_error=true [{"text": "tool 'bash' timed out", "type": "text"}]
02:25:31.118650Z agent.message    'The bash tool timed out ... Stopping as requested'
02:25:31.228647Z session.status_idle stop_reason={"type": "end_turn"}
```

150.6 s elapsed — the SDK's client-side outer dispatch timeout
(`_beta_session_runner.py`; native bash separately defaults to a 120 s per-command timeout). The
server does **not** impose a tool deadline: the session simply stays in
`status_idle / requires_action` until somebody posts a result. So "tool timeout" is a *worker-side*
guarantee, configurable through the worker, and the resulting error event is structured well enough
that the model diagnosed it unaided.

*Distance to Devin (recovers from a failed tool):* equal for the timeout case — the agent sees a
typed error and re-plans.

### C-2 Worker killed mid-command → session stranded (class D)

`experiments/C/run_case.py --name c3-kill-before ... --fault kill-before` kills the worker process
with `os._exit(97)` inside the tool dispatch, *before* the command runs. Session
`sesn_01YDmDJtbTgYkhXRisHri7XZ` (`artifacts/c3-kill-before.json`):

```
02:35:19.271682Z agent.tool_use bash {"command": "echo CHAOS-kill-before"}
02:35:19.342…Z  session.status_idle stop_reason={"event_ids": ["sevt_0197CcGssxZMPjyPBCmrjKjC"],
                                                "type": "requires_action"}
```

Worker log (`artifacts/c3-kill-before-worker.log`): claimed → acked → heartbeat → `executing tool
tool=bash` → `FAULT kill-before: exiting worker process`.

A replacement worker was started 5.2 s later and polled the same environment continuously for
~4 minutes (`artifacts/c3-kill-before-restart-worker.log`, hundreds of
`GET /v1/environments/.../work/poll` 200s) and received **zero** work items — `scoped worker exiting
served=0`. A further 30 s poll and a signed `session.status_run_started` webhook replay to the
production Modal handler (`artifacts/c3-recovery-sesn_01YDmDJtbTgYkhXRisHri7XZ.json`) gave:

```json
"queue_redispatch": {"polled_seconds": 30.0, "items_seen": []},
"webhook_replay_status": 200,   // handler body: {"status":"ok","spawned":[]}
"pending_after_webhook": {"id": "sevt_0197CcGssxZMPjyPBCmrjKjC", "name": "bash", ...}
```

So: the work item is consumed by the ack and is never re-created; the session's own lifecycle event
(`status_run_started`) has already fired, so nothing re-triggers the webhook; the session waits
indefinitely. **The platform does not detect or repair a worker that dies while owning a session.**
This is class D as stated ("does Anthropic notice, retry?") — and per §2 no watchdog/orchestrator was
built to cover it.

*Distance to Devin (recovers from a crashed sandbox):* large. Devin's runtime restarts the machine
and continues; here a crashed worker leaves a live session parked forever unless an external actor
re-attaches.

### C-3 The one native recovery path (class C)

Constructing `anthropic.lib.tools.SessionToolRunner(client, session_id, tools=…,
environment_key=…)` — no work item, no lease, no `EnvironmentWorker` — recovered the stranded
session (`exp_c3_recovery.py` phase 3, same artifact):

```
02:42:25 session tool runner starting session_id=sesn_01YDmDJtbTgYkhXRisHri7XZ
02:42:26 executing tool tool=bash tool_use_id=sevt_0197CcGssxZMPjyPBCmrjKjC
...
"bare_runner": {"dispatched": [{"name": "bash", "tool_use_id": "sevt_0197CcGssxZMPjyPBCmrjKjC"}]},
"pending_after_runner": null
```

The runner's full-history reconciliation is what does the work: it re-dispatches any `agent.tool_use`
with no matching result, so re-attachment is idempotent with respect to *events*. That is a native
extension point, which is why this is C and not D — but the trigger is missing: something outside
Managed Agents must notice the strand and hold the environment key. The lifecycle-event surface
offers `session.status_run_started` only, which has already fired by then.

Minimal improvement available within the rules: the worker itself is the only native place to add a
"finish what you claimed" guarantee, and it cannot cover its own process death. A prompt-level
mitigation is available for the *next* turn, not for the stranded turn.

### C-4 Lease fencing is real and safe (class A)

Production Modal app log (`modal app logs clevin -e clevin`), 02:44–02:46 UTC:

```
POST /v1/environments/env_0152.../work/sesn_01Mo2kMJ2c6m1J7G1WVPkgy5/heartbeat
     ?expected_last_heartbeat=2026-08-28T02%3A44%3A09.653497Z  200 OK      (every ~30 s)
POST /v1/environments/env_0152.../work/sesn_01P5Lp1vYb35AuQJ8KvCSRWR/stop   200 OK
```

`expected_last_heartbeat` is an optimistic-concurrency (fencing) token: per the SDK, a 412 means the
lease was lost, and the worker then stops serving the item rather than continuing to post results,
and a worker that cannot heartbeat for the TTL assumes lease loss for the same reason. Two workers
therefore cannot both answer one tool call. Retry safety at the *transport* level is native and
sound; retry safety at the *side-effect* level is C-5.

### C-5 Tool side effects are at-least-once (class D for exactly-once)

Composition of two measured facts — recovery re-dispatches any unanswered `agent.tool_use` (C-3),
and the tool-result send is retried until the lease window expires (SDK) — means a crash *after* a
command has taken effect but *before* its result is posted results in the command running twice on
re-attachment. There is no native idempotency key, no "tool already executed" marker, and no
event recording that a dispatch was started (`agent.tool_use.processed_at` is stamped when the
platform emitted the call, not when the worker ran it).

The direct experiment (`--fault kill-after-side-effect`, appending to `counter.txt`, then recovering
and counting lines) is written and staged but **was not run** — it was the command that hit the
credit wall. The claim above is therefore *inference from measured mechanisms*, not a measured
duplication.

Mitigations that stay inside the rules (prompt + tool design, class C): instruct the agent to make
shell side effects idempotent (`mkdir -p`, write-then-rename, `grep -q … || echo …`), and prefer the
native file tools (`write`/`edit`, path-confined and idempotent for identical content) over `bash`
append/`>>` patterns for state that matters.

*Distance to Devin:* Devin has the same fundamental at-least-once exposure; the gap is that Devin's
agent is told about the restart, whereas here the re-dispatched call is indistinguishable from a
first attempt, so the model cannot notice the duplication either.

### C-6 A shared self-hosted environment has no work routing (class C)

Work items are environment-scoped: `GET /v1/environments/{env}/work/poll` takes `block_ms`,
`reclaim_older_than_ms` and a worker id — no session, agent, label, or metadata filter. Any worker
polling the environment can claim any session's item, and the item's identity is only visible *after*
the claim.

Measured: an unscoped local experiment worker claimed four sibling workstreams' sessions inside three
minutes (`artifacts/c3-kill-before-restart-worker.log`, first generation):

```
claimed work_id=sesn_01DgUi228gLuUsRWb7eRrSPa  (clevin-swarm-K-k6-replay-memory)
claimed work_id=sesn_01EC7pdWNdtPewNAg6NomHRh  (clevin-swarm-K-k5c-skill-discovery-prompt)
claimed work_id=sesn_01HibtefzcKjxrNmPBYZ4iLv  (clevin-swarm-A-…-compaction)
claimed work_id=sesn_01Mo2kMJ2c6m1J7G1WVPkgy5  (clevin-swarm-K-k2-ask-and-block)
```

Each was released un-ack'd and a signed webhook replay was sent; the handler answered `spawned: []`
each time (an un-ack'd claim is not immediately reclaimable while its lease is alive), yet all four
sessions later reached `end_turn` — recovery happened on subsequent turns, not from the replay, so the
cost of a mis-claim is a stall of the current turn, not permanent loss.

Because a worker must be polling *before* a session exists in order to win the claim race against the
production webhook path, the only native way to scope a worker to its own sessions is: claim, then
`sessions.retrieve(id)` and match `metadata` (implemented as `chaos.py --allow-metadata`). That is a
mitigation, not isolation. Full isolation would need a second environment with its own environment
key, and per `AGENTS.md` **environment keys can only be minted in the Console, not via the API** —
confirmed empirically: a temporary environment created via the API
(`env_01U5YAys6ccLrqfGq4uMrZqA`) rejected the existing key with `403 Token not authorized for this
environment`, and there is no key-creation endpoint in the SDK.

### C-7 Concurrency (class A at observed scale)

The same Modal log window shows six distinct sessions
(`sesn_01Mo2k…`, `sesn_012k3L…`, `sesn_01P5Lp…`, `sesn_01KRHY…`, `sesn_01HbVS…`, `sesn_01HLvc…`)
heartbeating concurrently, one sandbox each, with no throttling or queue-depth errors. The webhook
handler drains with `block_ms=None, reclaim_older_than_ms=2000, drain=True`, so bursts are handled by
spawning more sandboxes. Constrained-compute behaviour (Modal concurrency limits, cold-start storms)
was not pushed — see §4.

### C-8 / C-9 Tool surface, from the SDK as installed (`anthropic 0.125.0`)

- Native bash: 120 s default command timeout; output truncated to 100 KiB keeping the **tail**, with
  `[output truncated]` prepended. Persistent bash sessions are not safe to share concurrently.
- Native dispatch: 150 s outer timeout per tool call (measured in C-1); execution is shielded from
  consumer cancellation so an in-flight call still posts its result.
- File tools resolve and confine every path to `workdir` + `allowed_roots` (memory-store mounts),
  following symlinks before the check; **bash is unconfined** and relies on the sandbox for
  containment. Practical consequence for Clevin: the sandbox is the only security boundary for
  `bash`, so "where tools execute" must stay Modal-side.
- `SessionToolRunner` dispatches `agent.tool_use` and `agent.custom_tool_use` only, and explicitly
  **not** `agent.mcp_tool_use` — hosted MCP calls (Linear, GitHub) execute server-side. Inference
  (untested): MCP-only turns should survive a dead worker, since no local dispatch is required.
- SSE reconnects with backoff capped at 10 s and reconciles against full event history, so a dropped
  stream is not a lost turn (source-level; the network-interruption experiment was not run).

---

## 3. Provenance ledger

Every added file exists to configure, drive, or observe a Managed Agents primitive; none of it is a
product component.

| File | Primitive | How Managed Agents invokes/consumes it | Why configuration was insufficient |
| --- | --- | --- | --- |
| `experiments/C/chaos.py` (`FaultTool`, `tools_factory`) | Native `agent_toolset_20260401` tools + `EnvironmentWorker` | The worker calls the tool factory per session; each tool is the native tool wrapped to fail on a trigger | There is no native way to inject a tool fault; the wrapper is the smallest object that makes the platform's failure path observable |
| `experiments/C/chaos.py` (`run_scoped_worker`) | `work.poll` / `work.ack` / `worker.handle_item` | Uses the native work-queue endpoints directly | The stock `EnvironmentWorker.run()` serves *any* session in a shared environment; scoping is needed to avoid stealing sibling swarm sessions (C-6) |
| `experiments/C/chaos.py` (`nudge_webhook`) | `session.status_run_started` lifecycle webhook + `standardwebhooks` signing | Replays a signed delivery to the deployed production handler | Tests whether the lifecycle-event surface can re-trigger recovery; there is no API to re-deliver a webhook |
| `experiments/C/harness_agent.py` | Agent create + versions | Creates a temporary native-tools-only agent version | The production agent version must not be mutated (§7) |
| `experiments/C/run_case.py` | Sessions create + `sessions.events.list` + worker lifecycle | Drives one fault case end to end and dumps server-side history | Repeatability across sessions; every claim in this file is regenerable |
| `experiments/C/exp_c3_recovery.py` | `work.poll`, lifecycle webhook, `SessionToolRunner` | Probes the three candidate recovery paths against a stranded session | The question ("is recovery native?") can only be answered by exercising each native path |
| `experiments/C/exp_c8_result_injection.py` | `sessions.events.send` (`user.tool_result`) | Posts malformed/oversized/duplicate result events to the server | Server-side validation of result events is not documented; staged but unrun |
| `experiments/C/exp_c0_baseline.py` | Sessions + worker happy path | Control run | Needed to separate fault effects from baseline behaviour |

No agent-loop, orchestrator, scheduler, memory layer, or observability product was built. The
"missing watchdog" of C-2/C-3 was deliberately **not** implemented: it would be exactly the
top-level orchestrator §2 forbids.

---

## 4. What could not be tested, and why

Balance exhaustion (02:43 UTC) stopped anything needing a new session:

- kill-after-side-effect duplication (C-5) — driver staged, one command short.
- malformed / oversized / duplicate / unknown-`tool_use_id` `user.tool_result` handling (C-10).
- oversized tool output *in a live session* (the 100 KiB truncation is source-level only) and its
  interaction with compaction.
- duplicated tool response (two workers answering one call) — C-4's fencing predicts the second is
  rejected; unverified.
- delayed worker startup beyond the ~15 s window observed, and worker restart *during model
  reasoning* rather than during a tool call.
- Modal-side faults: volume detached, disk filled, network interrupted, app redeployed mid-session,
  sandbox expiry. These need a live session in flight to be meaningful; read-only Modal inspection
  alone cannot establish recovery semantics. (Note: these are also the cases most likely to be
  class C/D, since C-2 shows there is no re-dispatch to recover *any* of them.)
- hosted MCP behaviour under long sessions; per-subagent tool grants; tool-configuration changes
  between agent versions with a session in flight. All require sessions.
- Isolated fault-injection environment: blocked by the Console-only environment-key mint (C-6), not
  by balance.

---

## 5. Cleanup ledger

| Resource | Action | Result |
| --- | --- | --- |
| Temp environment `env_01U5YAys6ccLrqfGq4uMrZqA` (`clevin-swarm-C-20260828T020720Z-0a890f`) | `beta.environments.delete` | Deleted; `retrieve` now returns 404 |
| Temp harness agent `agent_01CDtid4b87nEjESmYBPAE9Y` (`clevin-swarm-C-20260828T021326Z-63059d`) | `beta.agents.archive` (no `delete` exists in the SDK) | Archived, `archived_at=2026-08-28T02:46:11.890324Z`. **Cannot be deleted** — the API exposes archive only |
| Experiment sessions (`sesn_01…` listed above) | none available | Sessions have no delete/archive endpoint; they remain as evidence |
| Local worker processes / `/tmp/chaos-workspace` | killed / left empty | All chaos workers terminated (verified `0` matching processes); workspace holds no residue |
| Production agent, environment, Memory Store, Vault, Modal app/volume/image | not modified | Unchanged; the only Modal interaction was read-only (`container list`, `app logs`) |

---

## 6. Distance-to-Devin notes (rows this workstream touched)

- **Recovers from a crashed sandbox or failed tool** — failed tool: parity (C-1). Crashed
  worker/sandbox: far from parity (C-2 class D); the pieces for a fix exist natively (C-3) but the
  trigger does not, so unattended recovery is not reachable inside the model.
- **Ticket in → CI-green PR out, unattended** — the unattended claim is capped by C-2: any worker
  death mid-run silently parks the session, and nothing in the native surface notices.
- **Observable, attributable run history** — lease/heartbeat/stop traffic and per-tool events are
  visible end to end (C-4, C-7 excerpts), which is enough to *detect* a strand externally, just not
  to fix it natively.
- **Ask-a-question-and-block / resume later** — partially informed here: the lease is per work item
  and heartbeat-fenced, and a session with no worker simply idles indefinitely without losing
  server-side state (C-2 is the same mechanism seen as a failure). Workspace survival across a long
  block is workstream K's row and was not measured.
