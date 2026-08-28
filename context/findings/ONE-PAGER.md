# How far can you push Claude Managed Agents? — findings one-pager

Ten evidence-backed workstreams (A–K, `context/findings/`) pushed Anthropic's Managed Agents to
their native ceiling on the question: *how close can pure Managed Agents configuration and its
intended extension points get to a Devin-like cloud coding agent?* Nothing was built from scratch to
paper over a missing primitive; where a primitive is absent, that absence is the finding.

## 1. What are Claude Managed Agents

Managed Agents is Anthropic's hosted agent platform: you define an **agent** (model + system prompt
+ tools + MCP connectors + Skills + subagent roster) as an immutable **version**, then run
**sessions** against it. Anthropic hosts the reasoning loop — the model, the conversation history,
context compaction, subagent threads, budgets, and a durable event log — while tool execution is
dispatched either to Anthropic's cloud sandbox or to a **self-hosted environment** you operate
(Clevin uses Modal). Around the loop sit native primitives: **Memory Stores** (a filesystem mounted
into sandboxes), **Deployments** (cron-scheduled runs), **Skills** (named procedure documents),
budget/usage accounting, lifecycle webhooks, and an SSE event stream.

## 2. Architecture: what happens where

```text
   ANTHROPIC (managed agent loop)                    SELF-HOSTED (Modal, "the sandbox")
  ┌──────────────────────────────────────┐          ┌──────────────────────────────────────┐
  │ agent versions (immutable config)    │          │ modal_app.py webhook endpoint        │
  │ session = durable event log:         │ webhook  │   session.status_run_started ───────►│──┐
  │   user.* / agent.* events, threads,  │─────────►│                                      │  │ spawns
  │   usage, budgets, compaction         │          │ Modal Sandbox (1 per session)        │◄─┘
  │                                      │          │  ┌────────────────────────────────┐  │
  │ MODEL LOOP (runs here):              │          │  │ EnvironmentWorker              │  │
  │  reasoning → agent.tool_use emitted  │  poll    │  │  polls environment work queue, │  │
  │  ── work item queued ───────────────►│◄─────────│  │  claims lease, heartbeats ~30s │  │
  │  waits in requires_action until…     │  claim   │  │  (fenced: 412 ⇒ lease lost)    │  │
  │                                      │          │  │  executes bash/edit/read/write │  │
  │  ◄── user.tool_result posted ────────│◄─────────│  │  in /workspace                 │  │
  │  loop continues                      │          │  └────────────────────────────────┘  │
  │                                      │          │                                      │
  │ MCP tool calls (GitHub/Linear) run   │          │ DATA THAT LIVES HERE:                │
  │ SERVER-SIDE — never reach sandbox    │          │  /workspace   ← repo checkout, edits │
  │                                      │          │    (clevin-sessions volume: survives │
  │ DATA THAT LIVES HERE:                │          │     sandbox teardown, not the procs) │
  │  conversation history, event log,    │          │  /workspace/skills/<name>/SKILL.md   │
  │  thread costs, compaction summaries  │          │  /mnt/memory  ← Memory Store mount,  │
  │  Memory Store contents (synced ⇄)    │◄────────►│    synced via session secret         │
  └──────────────────────────────────────┘   sync   └──────────────────────────────────────┘

  SSE event stream (9 ms median lag) and lifecycle webhooks flow OUT of Anthropic to any observer.
  Deployments fire ON Anthropic's side (cron ≥1 min) and each fire creates a brand-new session.
```

The critical asymmetry: the *loop* is durable (history survives anything), the *executor* is
**self-diagnosing but not self-healing** (J2). Anthropic natively detects an abandoned work item —
lease expiry ~360 s after the last heartbeat, no external actor — and retries narrow transient
error classes (`retry_status: retrying`), but it never re-dispatches: a reclaiming poll re-offers
nothing, a webhook replay spawns nothing, and a `billing_error`-exhausted session stays inert even
after the balance is restored. Forward progress after a real fault is always operator-initiated.

## 3. What can you build with Managed Agents?

**Out of the box (pure configuration):**
- Unattended multi-hour coding: a 27-file migration completed 4/4 on an objective grader, 0 human
  nudges, in 12 of 13 runs (B); constraints stated once were honoured ~35 tool calls later.
- Ticket → CI-green PR: an ambiguous Linear ticket was diagnosed, fixed, adversarially reviewed by
  a subagent, and landed as a green PR (J) — and a session took an existing PR from red CI to green
  while replying to review comments (K4).
- Depth-1 parallel delegation with per-role cost attribution; the parent detected and rejected a
  child's false conclusion and repaired the poisoned shared memory entry (F, J).
- Mid-run steering (`user.interrupt` accepted in 0.5 s; genuine re-planning) and mid-run
  requirement changes absorbed without a restart (K1, B).
- Agent-as-code: immutable versions, canary/rollback, per-session pinning — reproducible fleets (D, A).
- Scheduled runs (cron ≥1 min), full cost/attribution telemetry (SSE 9 ms lag; session cost equals
  the sum of thread costs; fleet roll-up of 164 sessions in 2 API calls) (H, I).

**How far we took them, and what it required:**
- **Ask-a-question-and-block** for 75 minutes at near-zero cost, resuming with the workspace intact
  — required declaring a custom `ask_human` tool and an operator loop watching `requires_action` (K2, J).
- **Skills as playbooks** — required discovering that attached Skills are *invisible* to the model
  (files on a volume, no listing tool, no prompt injection) and adding one system-prompt paragraph
  telling it where to look; after that, exact adherence (K5).
- **Memory across tasks** — write-back works and measurably helps the next session (1 install
  attempt vs 3); retrieval required prompting the model to `ls -R /mnt/memory` (E, K6, J).
- **Budget recovery** — `budget_reached` is not terminal: raising the budget plus one message
  resumes the same session, history, and workspace (J).
- **Drift detection** — a thin provisioner extension diffs live agent config against code (D).
- **Stranded-session recovery** — a bare `SessionToolRunner` re-attach reconciles history and
  finishes the run; but *you* must notice and trigger it (C-3).

The recurring price: every "how far" item is an **extension point plus prompt discipline** — a
custom tool, an operator loop, or a paragraph of system prompt. None of it is a platform feature
you switch on.

## 4. What can you NOT build with Managed Agents?

These have **no supporting primitive**; replicating them means building your own infrastructure
from scratch (which this program deliberately did not do):

- **Self-healing.** The loop diagnoses but never heals: it detects a dead worker (lease expiry,
  ~360 s) and retries narrow transient errors, but nothing ever re-dispatches abandoned work —
  structurally, a work item *is* the session's one lease, so once stopped there is nothing left to
  hand to another worker (J2). A hard kill also loses the sandbox. You would build the entire
  supervisor/re-dispatch layer yourself; the only native page-worthy signal is a `stopped` work
  item whose session is still `requires_action`.
- **Event-driven wake.** No GitHub/Linear/webhook → session path exists. Wake-on-event means your
  own event bus; natively you get ≥1-minute cron polling where every fire is a cold new session.
- **Session continuity.** No fork, clone, checkpoint, or seeding a session with history; no
  continuation across deployment fires; no atomic history+filesystem snapshot.
- **Model routing.** A session is pinned to one model at creation, immutable mid-session (the
  update surface is tools/MCPs only). No fallback, no per-step routing, no cost-based downgrade;
  the only lever is pinning different models per subagent roster entry. Devin-style multi-model
  routing (e.g. Fusion) would be a from-scratch proxy layer.
- **Knowledge push.** Memory Stores have no scoping, selection, or ranking — nothing injects
  relevant knowledge into a session; the model must grep the mount.
- **Browser / Computer Use.** The tool types are rejected outright.
- **Org-level observability.** No aggregation, error rates, alerting, cost-per-successful-task, or
  tool-call→sandbox/file-change join; the Admin API is unavailable.

**The long tail Devin ships that you would re-create yourself:** repo blueprints and warm VM
snapshots; scoped knowledge auto-injection; playbook discovery; PR creation/review/CI feedback
loops that wake on events; secrets management UX; session sharing, steering and mobile surfaces;
concurrency controls; usage-based routing; org policy enforcement; and the operational muscle of
noticing and repairing every failure mode above, automatically.

## 5. Where the moat actually is: the lifecycle layer

Managed Agents reproduces Devin's **execution layer** essentially at parity — sandboxed multi-hour
coding with steering, delegation, playbooks, memory, scheduling, and better-than-Devin cost
attribution. What it does not provide is the **lifecycle layer**: the always-on, event-woken,
crash-recovering, knowledge-primed, model-routed loop *around* execution. Every capability in §4 is
in that layer, and each is real distributed-systems work (supervision, event ingress, state
snapshotting, routing) — not prompt engineering.

**Could an enterprise build a production-grade cloud coding agent on Managed Agents?** Yes — the
reasoning, tool reach, and single-run reliability are there today, and a team comfortable operating
one custom tool-runner (as Clevin does) gets a credible single-tenant agent. But *production-grade*
means the lifecycle layer, and that is a platform-engineering program, not a configuration
exercise: a supervisor service, an event bus, a knowledge service, a routing proxy, and an
observability stack — roughly the parts of Devin that are not the model. Economics compound the
gap: one 15-minute ticket on an Opus coordinator with a 3-agent roster cost $716 list; the cost
curve, not the capability curve, is the nearest ceiling.

## 6. Conclusion

**What makes a cloud agent valuable beyond its intelligence?** That it is *always there* (wakes on
events, survives crashes, resumes across gaps), *already primed* (knows your repos, conventions,
and playbooks without being told where to look), *economical at fleet scale* (routing, caching,
concurrency, budget policy), and *operable* (observable, alertable, steerable by anyone in the
org). Intelligence gets a ticket fixed; these properties are what let hundreds of tickets get fixed
unattended.

**Of these, what does Managed Agents actually offer?** Roughly half of one: it offers *durable,
attributable execution* — the run itself is excellent, honest about costs, and steerable — plus raw
materials (volumes, memory mounts, webhooks, cron) for the rest. Always-there, already-primed, and
fleet-economical are precisely the properties with no primitive behind them. Managed Agents is a
superb engine; Devin is the vehicle.

---
*Audit note: the full `swarm/integration` diff was reviewed for capabilities built outside Managed
Agents paradigms. All product code is provisioner configuration (agent/subagent definitions, skill
ID plumbing, drift detection — classes A/B); everything else is experiment harnesses and evidence.
No violations: no custom agent loop, memory layer, scheduler, or orchestrator was built, and every
class-D gap above remains unbuilt by design.*
