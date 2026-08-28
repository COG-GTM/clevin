# J follow-up — is the Managed Agents loop self-healing, or is recovery always operator-initiated?

One question only, funded by a ~$20 top-up after PR #16 merged. Everything else J left open
(`no-memory`, `no-subagents`, the wider chaos arm, compaction, mid-run steering) remains **untested**
— see §6.

**Answer.** The Anthropic-side loop is *self-diagnosing, not self-healing*. It natively (a) retries
error classes it marks `retry_status: retrying`, and (b) detects an abandoned work item and expires
its lease ~6 minutes after the last heartbeat, with no external actor. It never re-dispatches the
work: the abandoned item is never re-offered to a reclaiming poll, the lifecycle webhook will not
respawn it, and the session sits in `requires_action` forever. Forward progress after a dead worker
or an exhausted error always requires an external actor.

Driver: `experiments/J/selfheal.py` (`queue`, `retries`, `strand` subcommands). Evidence:
`experiments/J/evidence/selfheal-*.json`.

## Evidence 1 — native retry only inside one blast radius (`retry_status`)

Session errors carry a platform-side retry verdict, and the platform acts on it without an operator:

| `retry_status` | Observed error | Platform behaviour |
| --- | --- | --- |
| `retrying` | `MCP server 'github'/'linear' initialize failed` | re-attempted on its own; `sesn_015o4uWpg41z1YmzaWtNKi5E` shows the same pair of init errors at `04:33:29Z` and again at `04:34:23Z`, 54 s later, no operator in between (`experiments/I/artifacts/terminate/20260828T043328Z-637b75/`) |
| `terminal` | prompt too long | no retry |
| `exhausted` | `billing_error` | no retry, ever |

The `exhausted` case is the sharp one, and it is J's own dead session. `sesn_01EGsNu8uYt4SnS36Bk1JKvN`
died at `04:46:20Z` with `billing_error` / `retry_status: {"type": "exhausted"}` and
`stop_reason: retries_exhausted`. The balance was topped up ~25 minutes later. At `05:13:59Z` the
session was still at **666 events**, last event `04:46:19Z`, nothing new
(`evidence/selfheal-retries.json`). Restoring the precondition does not resume the run: a session that
stopped `retries_exhausted` is inert until someone sends it a new event.

## Evidence 2 — the work queue is a lease, not a scheduler

`evidence/selfheal-queue.json`, environment `env_0152FZKRpy9f8uVw38Guzosy`: 79 items, all `stopped`,
`depth: 0`, `pending: 0`; one item per session and `work_id == session_id`. That identity is the
structural reason there is no re-dispatch — a work item is not a retriable job, it is *the* lease on
one session activation, so once it is stopped there is nothing left to hand to another worker.

Historical items show the lease timeout directly, e.g. `sesn_01Mo2kMJ2c6m1J7G1WVPkgy5`:
`stop_after_last_heartbeat_s: 368.6`. Claim delay is unbounded on the other side — the same item
waited `4530.8 s` between `created_at` and `acknowledged_at` (no worker was polling), and Anthropic
did nothing about that either.

## Evidence 3 — dead worker, three fault modes (`selfheal.py strand`)

A throwaway Haiku agent runs one `sleep 90` bash call in a real self-hosted Modal worker; the fault is
applied while the call is in flight. `--kill-mode` matters, and conflating these modes is how the
earlier probe fooled itself.

| Run | Fault | Lease released | Session outcome |
| --- | --- | --- | --- |
| `sesn_01Xu42fdAkc7a22gCAjmXJxY` | kill missed (see §5); command outlived the worker's ~120 s bash dispatch timeout | `0.4 s` after last heartbeat | `user.tool_result` = `"bash: bash command timed out after 120.0s"`, agent reasoned about it, `end_turn` — **class A**, reconfirms C-1 |
| `sesn_01CX9iE183wRuii4XNUQmpU9` | `Sandbox.terminate` (runtime sets `enable_termination_grace_period`) | `5.5 s` | worker's shutdown path flushed a *partial* result (`"J2-STRAND-START"`), agent replied "it's still running", `end_turn` — an orderly worker exit is not a strand, and the partial result is silently wrong |
| `sesn_01G8EUvkZXmxieip1PfRBZB6` | `kill -9 -1` inside the sandbox — no shutdown path | **`360.7 s`** (`stopped_at 05:32:56Z`, last heartbeat `05:26:55Z`, `actor: null`) | stranded in `requires_action` on `sevt_01NEeJCzpVPau4pNeP3irrZo` forever |

For the real crash (row 3), after the lease expired:

- **No re-offer.** `work.poll(..., reclaim_older_than_ms=1000)` polled for `241 s` as a fresh worker:
  `offers: []`, `own_item_reoffered: false`. Workstream C polled without `reclaim_older_than_ms`; doing
  it *with* reclaim is the strongest native re-dispatch mechanism that exists, and it yields nothing.
- **No webhook respawn.** Replaying the signed `session.status_run_started` event: `200`
  `{"spawned": []}`, `events_before == events_after == 10`, `still_pending: true`, no new sandbox. The
  webhook keys off a session *starting a run*, and a stranded session is not starting one.
- **Workspace gone.** `final_modal_state.sandbox_id: null`, `status: "stopped"` — with a hard kill the
  session both stalls and loses its sandbox, so recovery is not just re-attachment.

Cost of the whole follow-up: `$1` list cost per strand run, three runs.

## Classification

| # | Capability | Class | Evidence |
| --- | --- | --- | --- |
| J2-1 | Platform detects an abandoned work item with no external actor | **A** | lease expiry `360.7 s` after last heartbeat, `stop_requested_at == stopped_at`, `actor: null` |
| J2-2 | Platform retries a transient error class on its own | **A** (narrow) | `retry_status: retrying` on MCP init, re-attempted 54 s later |
| J2-3 | Platform re-dispatches the abandoned work | **D** | reclaiming poll `offers: []`; webhook replay `spawned: []`; item/session identity leaves nothing to re-dispatch |
| J2-4 | Automatic recovery from `billing_error` once the balance returns | **D** | `retries_exhausted` session unchanged 25 min after top-up |
| J2-5 | Recovery of a stranded session using native APIs, once an actor triggers it | **C** | C-3's `SessionToolRunner` reattachment; here the external actor is unavoidable, and after a hard kill the sandbox is gone too |
| J2-6 | Tool timeout produces an error the agent can reason about | **A** | row 1 above |
| J2-7 | Worker shutdown reports truthfully | **C** | graceful exit posted a partial result that reads as success; nothing native distinguishes "finished" from "died mid-command" |

## Distance to Devin

*Recovers from a crashed sandbox or failed tool.* Native covers detection and honest tool-level errors;
it does not cover resumption. Devin's supervisor restarts the box and continues the task; here a
`kill -9` costs the workspace and the run halts until an operator re-attaches a worker or sends an
event. The gap is one component — a re-dispatcher — and per §2 it must stay unbuilt: the queue offers
no primitive to build it on, since the only lease for a session is already stopped.

Practical consequence for anyone operating this: the *only* native signal that a run needs help is a
work item whose `stopped_at` arrives with a `requires_action` session still pending. That is a
sufficient condition to page on, and it is available through `work.list` alone.

Related, from the timeout question raised while running this: the 120 s bash cap is worker-side (ours,
in `runtime/src/clevin_runtime`) and freely raisable, but it sits under two ceilings that are not —
the ~360 s heartbeat lease and Modal's `APP_SANDBOX_TIMEOUT_SECONDS`. Long commands must be
backgrounded and polled, not given a longer tool timeout.

## Provenance ledger

| Code | Primitive | Invocation path | Why configuration was insufficient |
| --- | --- | --- | --- |
| `experiments/J/selfheal.py::probe_queue` | environment work queue | `beta.environments.work.list/stats` with the environment key | lease timing is not documented or visible in the Console |
| `experiments/J/selfheal.py::probe_retries` | `session.error` / `retry_status`, `session.usage` | `beta.sessions.events.list` | only the event stream states whether the platform retried |
| `experiments/J/selfheal.py::probe_strand` | `EnvironmentWorker` lease + lifecycle webhook + session state | creates a temporary agent/session, lets the production webhook spawn the worker, kills it, then polls `work.poll(reclaim_older_than_ms=...)` and replays the signed webhook | the question is behavioural: no configuration reveals whether abandoned work is re-offered |
| `experiments/J/selfheal.py::kill_session_sandbox` | self-hosted worker fault injection | `SandboxRuntime().snapshot()` → `modal.Sandbox` terminate / in-sandbox `kill -9` | `j_common.kill_sandbox` matches on sandbox name/tags and terminated nothing (`terminated: []`); the fault must target the sandbox id the runtime reports, and graceful vs. hard kill are different experiments |

No production resource was modified. Nothing was built to work around J2-3 or J2-4.

## What this corrects in earlier findings

- C-2 ("platform notices a dead worker and re-dispatches") is two claims. **Detection is native and
  timed at ~360 s** (class A); only re-dispatch is class D. J's own earlier chaos probe reported
  `terminated: []` — it never actually killed a live worker, and its conclusion rested on the webhook
  replay of an already-idle session.
- A `Sandbox.terminate` is *not* a worker crash in this runtime: the grace period lets the worker post
  a partial tool result and release the lease cleanly.

## Cleanup ledger

| Resource | Action | Result |
| --- | --- | --- |
| `agent_015m7MhdV2VEZU3sYUFar2ww`, `agent_01Hj4dcUHbb5jkJ7D3ctWqxo`, `agent_01Nc1jQzmQhB9h1bXzJaYfjC` (temp probe agents) | archive | archived |
| Sessions `sesn_01Xu42fdAkc7a22gCAjmXJxY`, `sesn_01CX9iE183wRuii4XNUQmpU9`, `sesn_01G8EUvkZXmxieip1PfRBZB6` | retained as evidence | idle, no live compute |
| Modal sandboxes `sb-vtjKwjcPn6VmGMQl2TpNpw`, `sb-e0CzArcu8zrP0blLYAP0cH` | terminated by the experiment | `status: stopped`, `sandbox_id: null` afterwards |

## What was not tested, and why

- `no-memory` / `no-subagents` reduced arms, the wider chaos matrix, forced compaction, and mid-run
  steering under the full roster: **untested** — deliberately skipped, the top-up funded one question.
- Whether an operator-triggered `SessionToolRunner` can recover *this* stranded session with the
  sandbox already destroyed: not run (C-3 covers the reattachment case with the sandbox intact).
- Whether the ~360 s lease is configurable per environment: no such field is exposed on the
  environment or work resources in the SDK.
- Volume/disk/network faults and duplicated tool responses: still C's territory, untested here.
