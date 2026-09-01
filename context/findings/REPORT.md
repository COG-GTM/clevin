# Claude Managed Agents: How Far Can You Push Them? — Final Report

**Program question:** build the most capable cloud agent possible *exclusively* by composing and
extending Claude Managed Agents paradigms, and determine how close that gets to a Devin-like
product. Where a capability cannot be reached that way, the finding *is* the deliverable.

**Method:** ten evidence workstreams (A control plane, B long-horizon quality, C runtime
reliability, D agent-as-code, E Memory Store, F built-in subagents, H deployments, I observability
& economics, J integrated gauntlet + self-healing, K Devin-parity interaction), each run as live
experiments against the production Anthropic workspace and the self-hosted Modal environment.
Every claim below is tied to a captured artifact (session IDs, event IDs, request IDs, Modal logs)
in `context/findings/<workstream>.md` and `experiments/<workstream>/`. Roughly 200+ live sessions
were run; ~$1,000+ of list-cost API spend was measured. Capabilities carry a class letter:

- **A** — achievable entirely through Managed Agents configuration.
- **B** — achievable through a native, intended extension point (e.g. a client servicing an event).
- **C** — partially achievable through a native extension point.
- **D** — not achievable within the Managed Agents model (would require building a parallel system).

Per the program constraint, **no class-D capability was rebuilt**. Findings marked *untested* were
blocked by prepaid-credit exhaustion (which itself became evidence — see §4.2) and are listed in
§8 rather than guessed at.

---

## 1. What Claude Managed Agents are

Claude Managed Agents (Anthropic beta) is a hosted **agent loop as a service**. Anthropic runs the
reasoning loop; you supply configuration and, optionally, a place for tools to execute:

- **Agents** — a declarative resource of exactly nine fields (`name, description, model, system,
  metadata, mcp_servers, tools, skills, multiagent`). Every change mints an immutable, append-only
  **version**; nothing about an agent is Console-only (D).
- **Sessions** — a durable conversation + event history bound at creation to one agent version and
  one model. The event log (35 event types) records every model span, tool call, thinking block,
  usage tick, status transition, and compaction, and is exactly replayable after the fact (A, B).
- **Tool execution** — either Anthropic-hosted tools, server-side **MCP** connectors
  (GitHub/Linear tested), or **self-hosted environments**: your worker polls a work queue, claims a
  leased work item, executes shell/file tools in your sandbox (Modal here), and posts results back.
- **Memory Stores** — versioned key/value stores FUSE-mounted into the sandbox at `/mnt/memory`,
  surviving across sessions (E).
- **Skills** — versioned file bundles materialized at `/workspace/skills/<name>/SKILL.md` (K).
- **Built-in subagents** — meaning the delegation *mechanism* is native, not that any subagents
  ship prewritten: a `multiagent` coordinator roster giving depth-1 parallel delegation
  with per-thread cost attribution (F).
- **Deployments** — cron-scheduled session creation (5-field POSIX cron, minute floor) with a
  typed auto-pausing dependency supervisor (H).
- **Cross-cutting**: lifecycle **webhooks** (44 types), **SSE** event streaming (~9 ms median lag),
  per-request **usage/cost** accounting, per-session **budgets**, and native context
  **compaction** on large-window models (A, I).

What it is *not*: it is not a scheduler, not a supervisor, not a knowledge system, and not a fleet
product. The loop is **self-diagnosing but not self-healing** (§4.1) — that distinction is the
spine of this report.

---

## 2. Architecture: what happens where, and where data lives

```
┌────────────────────────── ANTHROPIC-MANAGED (the agent loop) ──────────────────────────┐
│                                                                                        │
│  Agent resource (9 fields) ──publish──► immutable version history [vN … v1]            │
│        │  "latest" resolved ONCE at session creation; sessions are frozen snapshots    │
│        ▼                                                                               │
│  SESSION  = durable ordered event log (source of truth for everything the agent did)   │
│   ├─ model reasoning spans (span.model_request_start/end, agent.thinking)              │
│   ├─ agent.tool_use / agent.custom_tool_use  ──────────────┐                           │
│   ├─ agent.mcp_tool_use ──► MCP servers (GitHub, Linear)   │  executed SERVER-SIDE,    │
│   │                         never dispatched to your worker│  immune to worker death   │
│   ├─ session.usage (per model request, cumulative, cache-read/creation split)          │
│   ├─ budgets (admission control), compaction (agent.thread_context_compacted)          │
│   └─ subagent threads (sthr_…, per-thread cost; child events NOT in parent stream)     │
│                                                            │                           │
│  Consumers:  SSE stream (~9 ms lag; live only, no replay — pair with events.list)      │
│              webhooks (44 types; 5 overlap with the 35 session event types —           │
│              budget_reached / requires_action / idled are WEBHOOK-ONLY)                │
│                                                            │                           │
│  WORK QUEUE per environment: 1 work item == 1 session (79/79 observed).                │
│  No routing/filtering — any worker with the env key can claim any item.                │
└────────────────────────────────────────────────────────────┼───────────────────────────┘
                    lifecycle webhook (single-shot) ─┐       │ work/poll (block_ms ≤ 999)
                                                     ▼       ▼ claim → lease (fencing token)
┌────────────────────────── SELF-HOSTED (Modal sandbox) ─────────────────────────────────┐
│  EnvironmentWorker: polls, claims, heartbeats every ~30 s                              │
│   └─ lease expires ~360 s after last heartbeat if worker dies → item `stopped`,        │
│      `actor: null` — DETECTED natively, NEVER re-dispatched (§4.1)                     │
│  Sandbox (gVisor kernel, 1 vCPU): bash / edit / read / write / glob / grep             │
│   ├─ /workspace            ── repo, processes, shell state — DIES with sandbox (~1 h)  │
│   ├─ clevin-sessions volume ─ /sessions/<id>/ — SURVIVES sandbox teardown (75-min test)│
│   ├─ /mnt/memory (FUSE)    ── Memory Store mount, ~100–200 ms/op, 100 KiB/file,        │
│   │                           no CAS at the mount (API has expected_content_sha256)    │
│   └─ /workspace/skills/<name>/SKILL.md ── Skill files (invisible without a prompt)     │
└────────────────────────────────────────────────────────────────────────────────────────┘

WHERE DATA LIVES (three independent durability planes — no atomic join between them):
  1. Anthropic: agent versions, session history/events, usage, threads   — forever (no delete)
  2. Modal volume: files under /sessions/<id>/                           — until you delete
  3. Memory Store: versioned memories (+ full version lineage)           — redactable, never deletable
The session ID is only a JOIN KEY between planes. There is no atomic history+filesystem
commit, checkpoint, rollback, or fork (A-15).
```

Key measured properties of the boundary:

- **Tool dispatch is at-least-once, with no server-side deadline.** If nobody posts a result, the
  session sits `idle`/`requires_action` **indefinitely** (C: 150.6 s observed timeout was the
  SDK's *client-side* limit). `agent.tool_use.processed_at` marks emission, not execution; there
  is no idempotency key and no "dispatch started" event (C-7).
- **The lease authorizes claiming, not answering.** A bare `SessionToolRunner` with only the
  environment key — no work item, no lease — can re-attach and answer a pending tool call (C-5).
  This is the one native recovery mechanism (class C), and also a security property: a leaked
  environment key can answer tool calls in *any* session in that environment (C).
- **Lease safety is real**: heartbeats carry a fencing token (`expected_last_heartbeat`); a 412
  means lease lost and the worker stops serving. Two workers cannot both answer one call (C-6).
- **No work routing exists.** `work/poll` has no session/agent/label filter; C's harness
  accidentally claimed four sibling workstreams' sessions in three minutes (blast radius: a
  stalled turn). Per-tenant isolation requires separate environments — whose keys are
  **Console-only** (API-created environments reject existing keys with 403; no key-mint endpoint) (C-12).
- **MCP inverts reliability intuition**: `agent.mcp_tool_use` is never dispatched locally, so your
  *most remote* tools are your *most reliable* ones — immune to worker death (C-14).

---

## 3. What you CAN build with Managed Agents

### 3.1 Summary table — capability by capability

| Primitive / capability | What Managed Agents offers | Upper bound |
|---|---|---|
| **Autonomous execution** — sessions, agent loop, `bash`/file tools, compaction, prompt caching, `user.define_outcome` | A hosted, durable reasoning loop that plans, edits files and runs commands unattended for hours, compacts its own context, and can be graded against a declared outcome rubric. Measured: 12/13 graded runs 4/4 on a 27-file `float`→`Decimal` migration, 0 nudges across all 10 completed runs, $41–62, 2–6.5 min; a 5.4 MB / 60-turn Opus run compacted twice (867,646→40,753 tokens, 95.3%) with exact early-constraint recall; 93% cache-read fraction. | Goes all the way to real unattended multi-file work — but there is no notion of a *job* around the run: a budget stop is a mid-edit guillotine and every failure collapses into `retries_exhausted`. Compaction is a property of the model, not the platform (Haiku never compacts and dies at its 200 K limit). No verification primitive: testing exists only because bash does, and the model's own success claim can't be trusted (every arm claimed success; the SHA-protected grader failed one). Outcome rubrics are slow (>13 min under load) and used by 0 of 164 observed sessions. |
| **Session management** — `sessions.create/retrieve/list/update`, resume, archive, thread listing, metadata, `retry_status` | A session is a durable, addressable, long-lived object: created against an agent version plus resources, resumable at any time with history, roster, sandbox and git HEAD intact, listable/filterable fleet-wide, taggable with metadata, and archivable. Measured: a session parked for 4501 s resumed in 0.2 s; a budget-stopped run resumed one tool call before `git push` and finished; 164 sessions enumerated with usage in 2 calls / 0.96 s. | The session is durable but **immutable and un-branchable**. Config is frozen at creation — a session is a snapshot of its agent version, so rollback means rolling forward into a new session, and the only mid-session mutations are `tools` and `mcp_servers` (model, prompt, Skills, roster and resources are fixed for life). There is no fork, checkpoint, clone or branch: every such endpoint 404s, so "try two approaches from this state" is impossible and A/B means starting cold. Status is coarse and overloaded (`requires_action` covers machine-wait, human-wait and stranded alike), sessions archive rather than delete, and there is no session→version reverse index. |
| **Memory & learning** — Memory Stores (mount + API), CAS writes, provenance, redaction; Dreams (research preview) | A durable, versioned, attributable knowledge store shared across sessions and mounted into the sandbox, with compare-and-swap writes and full provenance. Measured: 260 writes / 12 workers / 4.7 s zero failures; CAS via `expected_content_sha256` (1 winner, 5×409); learning is real when rediscovery is expensive (3 install attempts / 69 s / $17 → 1 attempt / 45 s / $12). | Storage is enterprise-grade; **retrieval does not exist**. Nothing is ever injected — retrieval is the model choosing to `rg` a mount, under an Anthropic-authored memory prompt you can only append to, so a 200-entry store costs the same as a 2-entry one. Memory is a permanent injection surface (redact, never rewrite history) and remembering a cheap fact costs more than rediscovering it (2.7× the cold baseline). Curation has a native answer — Dreams, an async job that reorganizes a store from 1–100 past transcripts — but it is gated behind `dreaming-2026-04-21` and untested here. |
| **Subagents / delegation** — `multiagent` coordinator roster, implicit `create_agent`/`send_to_agent`/`list_agents`/`send_to_parent`, `{type:"self"}`, `{type:"advisor"}` | Real in-session parallelism with static safety: fan out to rostered child agents in isolated threads, per-thread cost attribution, per-entry tool grants enforced at the tool layer, and child version pinning. Measured: ≥7 concurrent children; deliberately conflicting child reports synthesised on evidence. | Delegation is configuration-time only and one level deep: depth is capped at 1 and enforced twice (a rostered agent may not have a roster; a child never receives `create_agent`), and nothing can delegate to an agent that isn't already on its roster. Zero runtime control — no cancel, timeout or heartbeat from inside; ~533 KB child replies silently vanish 3/3; concurrent child edits are silent last-writer-wins. Costs 3–4× solo with **no measured quality gain** against either a visible or a hidden oracle. |
| **Skills** — Skill resources attached to an agent version | Versioned, reusable playbooks delivered into the session that the agent follows verbatim — the native way to encode org procedure as a first-class resource. | Attachment alone is a **silent no-op**: 3/3 sessions denied the playbook existed until a system-prompt paragraph named the path. There is no listing or loading tool, no agent-side publishing, and no scope-based auto-selection — the "which playbook applies to this task" layer, which is most of the value, is absent. |
| **Human interaction & steering** — `user.interrupt`, `user.message`, event stream, custom tools + `requires_action` | Interrupt a working session and redirect it mid-flight, and let the agent block on a human via a custom tool. Measured: interrupt accepted in 0.5 s with genuine re-planning and a correct progress audit; a custom `ask_human` parked a session for 900 s and 4501 s and resumed in 0.2 s, at $18 for 945 s wall (20.3 s active) — parking is essentially free. | Steering is at parity in substance, but the ergonomics are yours to build: there is no "queue my message" — a bare `user.message` to a working session is HTTP 400, so interrupt-then-message is client logic. There is no native ask-a-human state either: `requires_action` is overloaded (machine-wait and human-wait look identical, disambiguation needs event correlation), and something you operate has to notice the park and answer it. |
| **Integrations** — `mcp_servers` on the agent version, vaults + vault credentials | First-class MCP wiring with secrets in a managed vault, version-pinned per agent and grant-enforced per tool. Measured: one session took a PR red→green — read diff/comments/checks, fixed, tested locally, pushed, polled checks to `success`, replied inline in 10 MCP calls / 91 s / $58; MCP tool calls never dispatch locally, so they're immune to worker death. | Integration depth is exactly the MCP server's depth: no native PR, CI, review or repository model, and no user-level attribution — identity is the vault PAT, so every action is "the agent". Browser/computer-use toolsets are rejected outright, so end-to-end work can run headless but can never be *shown* or recorded. And nothing external can start a session: the mechanics are at parity, the trigger is missing. |
| **Configuration & release** — nine-field agent resource, immutable versions, 409 concurrency, per-version pinning | Agents as code, completely: 100% of an agent is code-expressible, versions are immutable, stale writes fail with 409, pinning fails closed (404 on a bad version). Measured: drift == [] round-trips, byte-exact roll-forward rollback, 12 agents provisioned in 4.8 s, and free canarying — three pinned versions over one benchmark with zero orchestration and per-variant cost from `session.usage`. | Excellent as a definition store, thin as a release system: no aliases or channels ("prod" cannot be a pointer), no version delete/tag/prune, no server-side desired state (convergence is your client code, and 5–6 server-added fields per agent make naive diffing worthless — hence `drift.ts`), and no reverse index from version→sessions. Experiments aren't replayable either: identical pin and prompt gave 164 vs 248 output tokens. |
| **Scheduling & automation** — deployments (cron), lifecycle webhooks | The one native way an agent starts without a human, and a fully capable one: each fire is a complete session (subagents, MCP, memory, outcome rubric). Measured: 8/8 punctual fires, median 5.9 s jitter, ~61 s to notice a new Linear issue by polling. | A stateless trigger with a one-minute floor, not a lifecycle: every fire is a cold new session with no dedupe (3 concurrent overlaps observed), no retry, no catch-up and no in-flight cap, and one bad dependency silently pauses the whole deployment (an archived agent is an invisible outage). Because polling is the only ingress, "react to this PR comment" becomes ~1,440 cold sessions/day and your own deduplication. |
| **Observability** — ordered event history, SSE, `session.usage`, metadata, budgets, `retry_status` | Best-in-class forensics and cost truth: every session is an exactly replayable event log, streamable live, with usage that reconciles to the cent across subagent threads. Measured: SSE median 9 ms / max 18 ms, a 164-session / $104.42 fleet rollup in 2 calls / 0.96 s, `retry_status` as the recoverable-vs-fatal discriminator, and hard budget ceilings enforced at event admission (turn 12 rejected after 11 ran). | You can reconstruct anything after the fact and operate nothing in the moment: no aggregate or error-rate API, no stuck-session signal, no alerting, no changed-files record, no tool-call→effect join. History and webhooks are disjoint surfaces (5 of 35+44 types overlap; budget, `requires_action` and idled are webhook-only). Budgets are a guillotine, not a brake — one turn blew a $0.05 cap to $0.11 (2.2×) with no wrap-up turn — and attribution is pure discipline: 50 sessions / $15.79 were permanently unattributable. |
| **Reliability & recovery** — work-item leases, heartbeats, `SessionToolRunner` re-attach, native retries | Correct primitives where they exist: leases are properly fenced (a resurrected worker is shut out), lease expiry is detected natively ~360 s after the last heartbeat, transient tool errors retry, and any session can be recovered by re-attaching a bare `SessionToolRunner` with nothing but the environment key. | The loop is **self-diagnosing but never self-healing**. Nothing re-dispatches: `work_id == session_id`, so reclaiming polls re-offer nothing and a dead worker strands the session in `requires_action` forever; webhook replay spawns nothing; a billing-dead session stayed inert even after credits were restored. There is no server-side tool deadline, and native recovery re-executes any tool call lacking a result — it cannot distinguish "never ran" from "ran, then the worker died", so idempotent side effects are your responsibility. Every recovery is operator-initiated. |
| **Model selection / routing** — `model` on the agent version, per-roster-entry models, one `advisor` | Model choice as code: pin a model per agent version, give each subagent a different model, and consult one advisor model — all expressed declaratively and reproducibly. | Choice, not routing. A session is pinned to one model at creation and immutable for life (`model` isn't even a session-update field), so there is no per-step routing, no fallback on failure, no cost-based downgrade and no "cheap model to explore, Opus to fix". The only lever is per-subagent, decided before the run. Worse, the choice silently changes capability class: Opus compacts at ~868 K tokens while Haiku never compacts and terminates at 200 K. |
| **Execution environments** — environment resource, environment key, work queue, `SessionToolRunner`, your own sandbox | A genuinely private execution plane: the loop is hosted, but every command, file edit and repo operation runs on infrastructure you own, reached by outbound-only workers. Measured here as one gVisor Modal sandbox per session (named by session ID) with a persistent volume workspace, the Memory Store mounted at `/mnt/memory`, heartbeats and split-brain fencing. | The plane is solid; the fleet layer is absent. The work queue has **zero routing** — any worker holding the environment key can claim any session's work item (one worker accidentally claimed 4 sibling sessions) — so hardware affinity, admission control and concurrency caps are yours to build. Access is one all-powerful, Console-only environment key: no API minting, no per-engineer or per-repo scoping, no user object anywhere. And there is no snapshot or known-good re-entry primitive: only the volume survives a sandbox's ~1 h life, with tool edges at 100 KiB output / 120 s / unconfined bash. |

### 3.2 What the headline results actually required

None of the class-A rows are "it just works out of the box with a bare prompt". The recurring cost
was *client-side glue and prompt engineering at the edges of the primitives*:

- **Skill and memory discovery paragraphs in the system prompt.** Skills and Memory Stores are
  file deliveries; both are dead weight until the prompt says where to look (K-10/11, J-5/6). The
  platform ships its own memory policy prompt ("check memory first… write early, write often") that
  you can only append to and that can change under you without a version bump (E).
- **A custom `ask_human` tool + an operator servicing `requires_action`.** There is no native
  ask-a-human state; the custom-tool mechanism *is* the primitive (K-4), and something you run
  must notice the park and answer it.
- **Interrupt-then-message client logic** for steering (K-1) — a bare `user.message` to a working
  session is HTTP 400.
- **Correct settle detection.** `events.send` returns while the session still reports the previous
  turn's `idle`; three workstreams independently hit this trap (B, D, H). The reliable condition is
  terminal status *and* last event being a terminal status event; naive status polling reports a
  busy session as done.
- **In-sandbox graders** (SHA-protected `grade.py`) — because the model's own completion summary
  is not trustworthy: every B arm claimed success; the grader failed one of them (B).
- **Provisioner extensions** (class B, in `packages/provision`): client-side drift detection that
  splits real drift from the 5–6 server-added fields per agent (which otherwise make a fleet's
  drift signal worthless), subagent/Skill ID plumbing, and metadata-key deletion handling
  (metadata is a PATCH — a stray key survives full reconciliation, D-6).
- **Budget raises + nudges by an operator.** J's integrated run stopped at $414 one tool call
  before `git push`; `sessions.update(budget=…)` + one message resumed the same session with
  history, roster, sandbox, and git HEAD intact (J-3).
- **Idempotent side-effecting tools by design.** Native recovery re-executes any tool call lacking
  a result and cannot distinguish "never ran" from "ran, then the worker died" (C-7).

### 3.3 The integrated ceiling (workstream J)

The best single configuration this program achieved, composed entirely of native primitives —
coordinator + 3 subagents, Memory Store, Skill, `ask_human`, GitHub/Linear MCP, self-hosted Modal
worker — took an ambiguous Linear ticket ("revenue report can't be trusted, and it's slow", naming
none of four planted defects) to a **CI-green PR on the first attempt**: 666 events, 3 threads,
675 s active, 462 insertions (273 test lines), one `ask_human` call raised only after a repo-wide
grep proved the rounding policy was genuinely undecidable, self-benchmarked its quadratic fix
(0.316 s → 0.055 s), polled checks to `success`, and detected and corrected a Memory Store entry
poisoned by its own explorer subagent (which had accused the parent of fabricating the policy it
couldn't see the `ask_human` answer for).

Two human touches were needed (the intended `ask_human` answer; an unintended budget raise), and
the price was the finding: **~$716 list cost for one ~15-minute ticket** (parent $413, explorer
$255, reviewer $47; 6.4 M cache-read tokens). Capability composes; economics is the ceiling. At
this configuration a Devin-scale fleet (dozens of tickets/day) is not economically expressible —
and there is no model routing to bring the price down within a session (§4.5).

---

## 4. What you can NOT build with Managed Agents

These are capabilities for which **no primitive exists** — building them means building the thing
from scratch, outside the Managed Agents model. Each was probed, not assumed.

### 4.1 A self-healing loop (the program's sharpest answer)

The loop is **self-diagnosing, not self-healing**. Measured decomposition (J2, C):

- *Detection is native (A).* A SIGKILL'd worker's lease expires **360.7 s** after the last
  heartbeat (corroborated at 368.6 s); the item flips to `stopped` with `actor: null`. A stranded
  session is detectable from three fields (`requires_action` stop reason, unmatched tool-use,
  frozen heartbeat) — **and announced by none**: no alert, no webhook, no event fires.
- *Narrow transient retry is native (A).* `retry_status: retrying` errors (e.g. MCP `initialize`
  failure) are re-attempted unprompted (54 s later, same session, no operator).
- *Re-dispatch does not exist (D), structurally.* A reclaiming poll re-offers nothing (241 s,
  `offers: []`); a signed webhook replay returns `200 {"spawned":[]}`; `work_id == session_id`
  (79/79 items) — a work item is *the lease on one activation*, not a retriable job, so once
  stopped there is nothing left to hand anyone. One item sat unclaimed **4,530 s** with no
  timeout, alert, or failure.
- *Billing exhaustion is permanently inert (D).* `retry_status: exhausted` sessions never resume
  after the balance is restored (verified 25+ min after top-up: zero new events).
- *Recovery after an external trigger is native (C).* A bare `SessionToolRunner` re-attach with
  just the environment key re-drives the pending tool call — but *deciding when* is entirely
  yours, and a hard kill also destroyed `/workspace`, so recovery is not mere re-attachment.

### 4.2 A durable notion of a job

- **No session fork/checkpoint/clone** — exhaustively probed: `/fork`, `/branch`, `/copy`,
  `/checkpoints` → 404; `fork_from_session_id` → 400. `initial_events` accepts only
  `user.message`/`user.define_outcome`, which closes the whole workaround family: no history
  transplant, no seeding a cheap model with an expensive model's reasoning (K-15).
- **No continuation across Deployment fires** — 8 fires, 8 cold sessions; no `session_id`
  parameter exists anywhere on the deployment surface (H-1). Continuity is only what you rebuild
  from the volume/Memory Store by prompt, paying full context cost per wake.
- **No exactly-once side effects** — at-least-once dispatch, no idempotency key, no transactional
  join between event history and sandbox state (three independent durability planes, §2).
- **Failure states collapse**: "finished", "waiting for a human", "mid-tool-call", "billing dead",
  and "retries exhausted" all present as `status: idle` — a healthy 5-tool run cycles
  running→idle **6 times** (I-11). Only `stop_reason` + `session.error` scans disambiguate.

### 4.3 Event-driven lifecycle

No GitHub/Linear→Anthropic ingress exists. The only wake primitives are cron (≥60 s floor,
hard-rejected below it) and manual `deployments.run` (1.23 s). Wake-on-ticket is a ~61 s poll, at
~1,440 sessions/day standing cost per minute-cron poller, with no deployment-level cost roll-up.
No sub-minute schedules, no retries, no concurrency control (`sleep 150` body → 3 concurrent
sessions), no catch-up of missed fires, and auto-pause failure modes that are invisible to naive
monitoring (an archived agent archives the deployment with **no failed-run record and
`status` still reading `active`**) (H).

### 4.4 A knowledge/retrieval system

Memory Store has **no scope predicate, no ranking, no embedding, no injection** — a wide (200
entries) and narrow (2 entries) store cost the same because nothing enters context until the model
greps (E-3). Naming hierarchy (`repos/<owner>/<repo>/…`) substitutes for scoping at small scale,
wired by whatever creates sessions — a store **cannot be bound to an agent version** (agents take
no `resources` param), so memory wiring is not agent-as-code (E). Memory is also a permanent,
cross-session **prompt-injection surface with no trust boundary**: hostile entries are ignored but
never flagged, and content can be redacted but never deleted (E-8/14). Subagent writes are
attributed to the parent — per-subagent memory provenance is class D (E-6).

### 4.5 Model routing

A session is **pinned to one model at creation and immutable for life** — `model` is not even a
field of the session-update body (400 `unknown field "model"`); the mid-session surface is
`tools`/`mcp_servers` only (D-9). Therefore: no per-step routing, no fallback on failure, no
cost-based downgrade, no "cheap model for exploration, Opus for the fix". The only native levers
are (a) different models per agent version, chosen before the session, and (b) per-roster-entry
model pinning for subagents plus one `advisor` (F). The model choice also silently changes
capability class: Opus compacts at ~868 K tokens; **Haiku never compacts and terminates at its
200 K limit** — picking a small model removes long-horizon capability with no warning (I-7).
Devin's multi-model routing has no counterpart here; recreating it would mean orchestrating
sessions from outside, i.e. building the loop this program forbids.

### 4.6 Fleet operations and the long tail

The long tail of Devin behaviors with no primitive behind them, each verified absent:

- **Alerting/aggregation**: no error-rate API, no stuck-session alert, no in-stream budget event
  (webhook-only), no org rollups beyond client-side sums; metadata is free-form and unenforced
  (50 sessions / $15.79 were unattributable) (I).
- **Attribution below the tool boundary**: no tool-call→sandbox join (only our own
  sandbox-name==session-id convention), no changed-files record of any kind (I-14).
- **Nested delegation** (>depth 1, enforced at create/update *and* dynamically), coordinator-side
  child cancel/timeout/heartbeat, child oversize-reply diagnostics (F).
- **Release management**: no version aliases/channels/tags/deletion; dev/staging/prod is separate
  agent IDs every consumer must be repointed at (D).
- **Deterministic replay**: no seed; identical config+prompt varies ±25% on cost/time (B-12, D-11).
- **Programmatic environment isolation**: environment keys are Console-only, blocking per-tenant
  or per-CI-job worker fleets (C-12).
- **Browser / Computer Use tool types**: rejected by the API (earlier program evidence).
- **Agent-side Skill publishing**: no tool can create a Skill version from inside a session;
  self-improvement of *knowledge* is native, of *procedures* is not (K-14).
- **Retention controls**: agents/deployments archive-only; sessions have neither delete nor
  archive-independent cleanup path at scale (~90 unarchived sessions accumulated with no TTL);
  memory versions are redactable, never deletable — a GDPR/secret-leak consideration (C, E, H).

Per the program rule, none of these were rebuilt. The nearest natives are documented instead:
`SessionToolRunner` re-attach for recovery (C), `work.list` stopped-item×pending-session pairs as
the one cheap native paging signal (J2), `retry_status` as the recoverable/fatal discriminator (I).

---

## 5. Execution Is Not the Product: Devin's Lifecycle and Control-Plane Moat

```
            EXECUTION PLANE                        LIFECYCLE / CONTROL PLANE
   (what a run does while it's alive)         (what makes runs into a product)
 ┌────────────────────────────────────┐   ┌──────────────────────────────────────┐
 │ multi-hour coding      ✅ parity    │   │ event wake (webhooks→session)  ❌ D  │
 │ steering / interrupts  ✅ parity    │   │ crash detect → re-dispatch     ❌ D  │
 │ ask-and-block          ✅ B         │   │ resume after billing/idle      ❌ D  │
 │ CI/PR repair loop      ✅ parity    │   │ session fork / checkpoint      ❌ D  │
 │ parallel subagents     ✅ depth 1   │   │ scoped knowledge injection     ❌ D  │
 │ agent-as-code          ✅ arguably  │   │ per-step model routing         ❌ D  │
 │                           better    │   │ fleet concurrency/alerting     ❌ D  │
 │ cost attribution       ✅ parity+   │   │ org operability / retention    ❌ D  │
 └────────────────────────────────────┘   └──────────────────────────────────────┘
        Managed Agents ceiling ≈ here            Devin's differentiation is here
```

**Could an enterprise realistically build a production-grade cloud coding agent on Managed
Agents?** For *attended, single-run* work — yes, today, credibly: the 27-file migration
(12/13 at 4/4, zero nudges) and the red→green PR loop ($58) genuinely rival Devin's execution, and
the configuration story (immutable versions, 409 guards, byte-exact rollback) is arguably better
than most agent products.

For *unattended fleet* operation — no, not on the primitives alone. The enterprise would have to
own, from scratch: a supervisor watching `work.list`/`stop_reason` and re-driving stranded
sessions (the irreducible piece — nothing native re-dispatches); event ingress from
GitHub/Linear; a knowledge-selection layer above the grep-a-mount memory model; model/cost
routing across sessions; concurrency locks and retries around deployments; alerting derived from
a self-run event-stream consumer; idempotency discipline on every side-effecting tool; and
retention/archival hygiene. Each is precisely a system the program was forbidden to build —
because each *is* the product layer. That work is not a weekend: it is the majority of a
Devin-like product's engineering, sitting on top of an excellent engine. And the measured
economics (~$716/ticket on an Opus roster, with no routing lever to cheapen it mid-run) mean the
fleet must also be re-priced before it can exist.

---

## 6. Custom-build audit

The full `swarm/integration` diff was reviewed against the class-D prohibition. **Verdict: clean —
no forbidden capability replacement was built.** What landed:

- `packages/provision/` (drift.ts + config/resources/agent-definition extensions): client-side
  desired-vs-live comparison, subagent/Skill ID plumbing, discovery prompt paragraphs. These are
  class-B *provisioner extension points* — configuration of native primitives, needed because the
  platform has no desired-state/drift endpoint. Not a runtime, scheduler, or loop.
- `experiments/A..K/`: drivers, harnesses, in-sandbox graders, evidence JSON, cleanup and rescue
  scripts — instruments that configure or observe native primitives, not product capabilities.
- Explicitly **not** built anywhere in the diff: an agent loop, session orchestrator, watchdog or
  re-dispatch engine, memory database/vector store, external event system, scheduler/job queue,
  or observability product. Every class-D gap in §4 is documented, not patched.

One honest near-miss to disclose: J's first two fault injections targeted the wrong sandbox and
silently produced clean runs; the corrected methodology (verify the fault landed via the
runtime-reported sandbox ID) is itself recorded as a finding — un-verified fault injection will
confidently produce wrong conclusions.

---

## 7. Where the numbers come from (evidence index)

| Workstream | Findings file | Headline evidence |
|---|---|---|
| A | `A-control-plane.md` | version snapshots, event durability (40/40 replay), SSE non-replay, interrupt semantics (285 ms synthetic error), 2× Opus compaction (95.3% reduction), Haiku non-compaction |
| B | `B-long-horizon-quality.md` | 13 graded runs, 12/13 at 4/4, 0 nudges, $26–62, ±25% variance, budget guillotine, 13/13 uncheated SHAs |
| C | `C-runtime-reliability-and-tool-surface.md` | no tool deadline, dead-worker strand, lease fencing, `SessionToolRunner` recovery, queue non-routing, gVisor/100 KiB/120 s tool edges |
| D | `D-agent-as-code.md` | 9-field resource, 409 guard, roll-forward rollback, server-added fields, metadata PATCH trap, 12 agents/4.8 s, no aliases |
| E | `E-native-memory-store.md` | CAS 1-of-6, wide==narrow cost, provenance/redaction, injection surface, $3/$8/$5/$3 learning curve |
| F | `F-builtin-subagents.md` | depth-1 double enforcement, 7 concurrent children, 533 KB silent drop 3/3, tool-layer grants, 3–4× cost / no quality gain |
| H | `H-deployments-automation.md` | 8/8 fires (1.5–9.6 s jitter), 3 concurrent overlaps, typed auto-pause + native recovery, archived-agent invisible outage, outcome grading |
| I | `I-observability-and-economics.md` | 35 vs 44 disjoint surfaces, 9 ms SSE, exact cost reconciliation, 2.2× budget overshoot, compaction reconstruction, fleet rollup |
| J | `J-integrated-gauntlet.md`, `J-self-healing.md` | $716 CI-green ticket, poisoned-memory correction, budget-stop resume, 360.7 s lease expiry, `{"spawned":[]}`, inert billing death |
| K | `K-parity-interaction.md` | 0.5 s interrupt steering, $18/945 s ask-and-block, 75-min volume-vs-sandbox lifetimes, $58 PR loop, Skill invisibility, fork 404s |

## 8. Explicitly untested (credit exhaustion or scope), stated so the report cannot overclaim

- B: idle-gap resumption (confounded by simultaneous billing death — recorded untested, not
  broken); a fixture large enough to force compaction (peak observed context 59 K vs ~200 K+
  threshold — real compaction pressure needs 10× more than a 27-file refactor generates).
- C: malformed/oversized/duplicate/unknown `tool_use_id` result injection (staged in
  `exp_c8_result_injection.py`); Modal-side faults (volume detach, disk full, network cut,
  redeploy, sandbox expiry); the direct kill-after-side-effect duplication run (inferred from
  mechanisms 5–7, flagged as inference).
- F: 64/256 KB child-reply rungs (failed on balance, not size — ceiling is ">16 KB, unmeasured");
  `user.interrupt` with `session_thread_id` child cancellation (named by a platform error message,
  class B by that evidence only); parent compaction while children run.
- H: deployment webhook delivery semantics; max in-flight runs; DST-boundary fires; a terminal
  `passed` outcome verdict.
- I: webhook alerting end-to-end (typed in the SDK union, not exercised); Console-only views;
  SSE sample was 12 events.
- J: no-memory / no-subagents ablations and the wider chaos matrix (only the focused hard-kill
  self-healing arm was funded); compaction and mid-run steering at integrated scale.
- Statistical breadth: most arms are n=1–2; conclusions are existence proofs and mechanism
  measurements, not distributions.

---

## 9. Conclusion

**What makes a cloud agent valuable beyond its intelligence?** The model is table stakes; the
product is: (1) *always-there* — wakes on events in seconds, not on a cron poll; (2) *self-healing*
— crashes, spend stops, and transient failures recover without a human noticing; (3)
*already-primed* — the right org knowledge is selected and injected, not hopefully grepped; (4)
*continuable* — work survives interruption as a durable job that can resume, fork, and be handed
off; (5) *economical at fleet scale* — routed across models, cached, budgeted with grace, priced
per ticket not per token; (6) *operable* — alertable, attributable, governable, with retention and
isolation an org can sign off on.

**Of these, what does Managed Agents actually offer?** Genuinely: durable, attributable,
steerable *execution* — the run itself is excellent, honest about cost to the cent, replayable
after the fact, and safely configurable as code — plus raw materials for the rest (volumes,
memory mounts, webhooks, cron, leases, `retry_status`). Partially: continuation (a parked session
resumes in 0.2 s for ~free — but only if nothing killed it) and scheduling (cron, with no
execution guarantees around it). Not at all: event wake, self-healing, knowledge injection,
model routing, fork/checkpoint, fleet control, and org operability — the entire lifecycle plane,
which is exactly where Devin is structurally differentiated.

Managed Agents is a superb engine. Devin is the vehicle. The ceiling of "Clevin" built purely from
Managed Agents paradigms is a **credible attended coding agent with best-in-class auditability and
an unattended-operation gap that no amount of configuration closes** — every path to closing it
runs through building the supervisor, ingress, routing, and knowledge layers that are themselves
the product.

---

## Appendix: Core findings by workstream

The 3–4 findings from each experiment that matter most for understanding the platform's
capabilities and limits. Full detail is in each workstream's findings file (§7).

### A — Control plane & session semantics
1. **Sessions are frozen config snapshots.** An agent version is immutable; a session pins one at
   creation and nothing mid-run can change model or prompt (only tools/MCPs). Rollback is
   roll-forward to a new version.
2. **The event log is the source of truth and exactly replayable** (40/40 events byte-identical on
   re-list) — but SSE is live-only: a late subscriber sees nothing, so any monitor must pair SSE
   with `events.list`.
3. **Interrupts cleanly cancel generation, but tool cancellation is not transactional** — an
   in-flight tool call on the worker keeps running; the loop and the sandbox have no atomic join.
4. **Compaction is real but model-dependent:** Opus compacted twice (867 K→41 K tokens, 95.3%
   reduction, exact constraint recall afterward); Haiku never compacted and died at its 200 K
   prompt limit.

### B — Long-horizon quality
1. **Long-horizon autonomy is natively excellent:** 12/13 graded 27-file migration runs scored
   4/4 with zero human nudges, and 13/13 left the SHA-protected contract tests untouched — the
   model never cheated the grader.
2. **Cost is honest but noisy:** $41–62 per wide migration with ±25% runtime/cost variance across
   identical runs.
3. **Budgets are a guillotine, not a governor:** a budget stop lands mid-edit with no wind-down
   turn, leaving the workspace in whatever state the last tool call produced.
4. **Quality interventions bought nothing:** plan-prompting, a subagent roster, and memory priming
   produced no measurable quality gain over the plain baseline on this workload.

### C — Runtime reliability & tool surface
1. **There is no server-side tool deadline:** a tool call the worker never answers leaves the
   session in `requires_action` forever — the loop waits indefinitely by design.
2. **A dead worker strands the session permanently** — nothing native detects or re-dispatches it;
   the one native recovery path is a bare `SessionToolRunner` re-attach needing only the
   environment key.
3. **The work queue has zero routing:** any worker on the environment can claim any session's work
   item (one run accidentally claimed 4 sibling sessions); leases are properly fenced, so
   split-brain is prevented, but affinity is your problem.
4. **The tool surface has hard edges:** ~100 KiB bash output cap, 120 s default timeout, gVisor
   kernel, and bash runs unconfined inside the sandbox — isolation comes from the sandbox
   boundary, not the tool.

### D — Agent-as-code
1. **The entire agent is nine code-expressible fields** — `name`, `description`, `model`,
   `system`, `metadata`, `mcp_servers`, `tools`, `skills`, `multiagent` — with immutable versions
   and 409 concurrency guards; this is arguably better than Devin's config story.
2. **Rollback is byte-exact roll-forward:** re-submitting an old version's config produces an
   identical new version; canary = pointing a fraction of sessions at it.
3. **There is no server-side desired state:** the API returns live objects with 5–6 server-added
   fields per agent, so naive diffing always reports drift — client-side normalization
   (`drift.ts`) is mandatory.
4. **No release layer:** no aliases, channels, labels, or deletion — "prod" vs "staging" is a
   convention you maintain in your own manifest.

### E — Native Memory Store
1. **Storage is enterprise-grade:** 260 memories from 12 concurrent writers in 4.7 s with zero
   failures; CAS works exactly (1 winner, 5 HTTP 409s of 6 racers); provenance and redaction are
   native.
2. **Retrieval does not exist:** nothing is injected into context — a 200-entry store and a
   2-entry store cost the same because the model must actively grep the `/mnt/memory` mount.
3. **Cross-session learning is real when the model looks:** a primed memory cut a repeat task from
   ~$17/69 s to ~$12/45 s.
4. **Memory is a permanent injection surface:** entries can be redacted but never deleted, so a
   poisoned memory is a standing prompt-injection risk that only curation mitigates.

### F — Built-in subagents
1. **Delegation is real parallelism with hard static guarantees:** 7 concurrent children observed;
   depth 1 is doubly enforced; tool grants and version pins are enforced at the platform layer.
2. **There is zero runtime control:** no cancel, timeout, or heartbeat for a running child — once
   spawned, you wait.
3. **Large child replies vanish silently:** a ~533 KB response produced no text in the parent 3/3
   times, with no error event.
4. **Cost 3–4× for no measured quality gain** on the tested tasks; concurrent children editing the
   same file resolve by silent last-writer-wins.

### H — Deployments & automation
1. **Cron is punctual and complete:** 8/8 fires in 8 minutes, 1.5–9.6 s jitter (median 5.9 s),
   zero skips.
2. **Every fire is a cold new session** with no dedupe or concurrency control — a 150 s body on a
   1-minute cron produced 3 concurrent sessions; a minute-poller costs ~1,440 sessions/day.
3. **Dependency failures fail quietly:** one archived Memory Store auto-paused the deployment
   (typed, recoverable); an archived agent killed it with *zero* run-log evidence.
4. **This is the only native wake mechanism** — ≥1-minute polling is the floor for reacting to
   external events (Linear issue detected in ~61 s).

### I — Observability & economics
1. **Forensics are superb:** 9 ms median SSE latency, per-thread cost reconciles to the cent, and
   a 164-session/$104 fleet rollup took 2 API calls and 0.96 s.
2. **There is no operations layer:** event history and webhooks are disjoint surfaces (35 vs 44
   event types); a stranded session is detectable from three fields but announced by nothing.
3. **Budgets overshoot:** a $0.05 single-turn budget spent $0.11 (2.2×) — enforcement is
   per-turn-boundary, not per-token.
4. **Attribution requires discipline you supply:** 50 sessions worth $15.79 had no metadata and
   were unattributable; there is no native tool-call→sandbox or changed-files join.

### J — Integrated gauntlet & self-healing
1. **The composed ceiling is a real ticket→PR agent:** ambiguous Linear ticket → CI-green PR on
   the first attempt (666 events, 3 threads, one justified human question, self-corrected a
   poisoned memory) — at ~$716 list cost.
2. **The loop is self-diagnosing, never self-healing:** lease expiry natively detects an abandoned
   work item 360.7 s after the last heartbeat, and narrow transient errors retry.
3. **Re-dispatch structurally does not exist:** `work_id == session_id`, reclaiming polls re-offer
   nothing, webhook replay spawns nothing — an abandoned run needs an operator.
4. **Billing death is permanently inert:** a session killed by credit exhaustion never resumed
   after credits were restored.

### K — Devin-parity interaction
1. **Steering is at parity:** `user.interrupt` accepted in ~0.5 s with genuine re-planning; a
   direct message during an outstanding tool call is rejected — interrupt-first is mandatory.
2. **Ask-and-block is nearly free:** a 75-minute human wait resumed in 0.2 s; the parked session
   cost ~$18 for 945 s wall / ~20 s active.
3. **The PR loop works natively:** red CI → green PR while replying to review comments, via 10
   GitHub MCP calls, ~91 active seconds, ~$58.
4. **Skills are invisible and forking is absent:** an attached Skill goes unused unless the system
   prompt says where it lives; every fork/checkpoint/clone endpoint 404s; only the volume outlives
   the sandbox's ~1 h life.
