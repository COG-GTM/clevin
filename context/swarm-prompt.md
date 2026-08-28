# Clevin Managed Agents Swarm — Session Prompt

This file is the complete prompt for a swarm research session. The master session hands the whole
file to each child session verbatim and adds exactly one line:

```
YOUR ASSIGNMENT: workstream <ID> — <title>
```

Everything a session needs is below: objective, hard constraints, verified environment facts, the
workstream briefs with their dependency order, the output contract, and the operating rules.

---

## 1. Objective

Build the most capable cloud agent possible **exclusively by composing and extending Claude Managed
Agents paradigms**, and determine how close that gets to a Devin-like product.

The target is not "the best Clevin by any means necessary". It is the **absolute ceiling of Clevin
achievable through Managed Agents configuration and its intended extension points**. Where a
capability cannot be reached that way, the finding *is* the deliverable.

Primitives in scope: agent configuration and versions; sessions and server-side state; context
compaction; Memory Stores; built-in subagents; built-in tools and MCPs; Skills; self-hosted
`EnvironmentWorker` sandboxes; Deployments; SSE event streams, usage events, and lifecycle webhooks;
Console and API management surfaces.

Out of scope: repository/credential lifecycle, security and enterprise-readiness testing, and
browser/Computer Use (see §4).

## 2. The hard rule (read this twice)

Custom code is in scope **only** when it directly configures, implements, extends, observes, or
empirically tests a Managed Agents primitive. Before writing any code you must be able to state: the
primitive, how Managed Agents invokes or consumes the code, why configuration alone was
insufficient, what the experiment teaches about the primitive, and why it is no larger than needed.

> If the code would still be a meaningful product component without Claude Managed Agents, it is out
> of scope.

**If a capability is not buildable on top of a Managed Agents primitive, DO NOT build it from
scratch. Mark it "not covered — class D", record why, and move on.** This is the single most
important instruction in this prompt. A class D result is a success, not a failure, and it must never
be converted into a class B by building unrelated infrastructure.

Never build: a replacement agent loop; a separate context/compaction layer; a top-level session
orchestrator; a planning or delegation engine; a custom memory database, vector store, or RAG layer;
a scheduler or job queue replacing Deployments; a parallel session-state system; a repository or
credential platform; an independent automation platform; a standalone observability product; or any
feature whose purpose is to make Clevin look more complete without testing a primitive.

Classify every capability you investigate as exactly one of:

- **A** — achievable entirely through Managed Agents configuration.
- **B** — achievable through a native Managed Agents extension point.
- **C** — partially achievable through a native extension point.
- **D** — not achievable within the Managed Agents model.

## 3. Devin-parity bar

"Devin-like" means these capabilities. Each row names the primitive that must carry it. Your findings
must end with an A/B/C/D class and a one-line "distance to Devin" note for every row your workstream
touches.

| Devin-like capability | Primitive that must carry it |
| --- | --- |
| Ticket in → CI-green PR out, unattended | Session + self-hosted `EnvironmentWorker` + GitHub/Linear MCP |
| Long-horizon work across hours and compactions | Session state + compaction + Memory Store |
| Builds a plan, then revises it as facts change | System prompt + built-in subagents |
| Parallel investigation, then synthesis | Built-in subagents |
| Learns across tasks (repo conventions, past failures) | Memory Store |
| Recovers from a crashed sandbox or failed tool | `EnvironmentWorker` + session APIs + lifecycle webhooks |
| Asks for help only when genuinely blocked | System prompt + tool design |
| Mid-run steering: user messages a live session and it re-plans | Session events (`user.interrupt`, user message injection) |
| Ask-a-question-and-block, resume later with workspace intact | Session idle state + worker lease/idle timeout |
| Sleeps, then wakes on a new ticket or comment | Deployments (polling) + lifecycle webhooks |
| Responds to PR review comments and fixes CI failures | GitHub MCP + Deployment-driven polling |
| Playbooks: reusable named procedures | Skills |
| Knowledge auto-selected by repo/task scope | Memory Store (scoping is the known weak point) |
| Warm environment (blueprint/snapshot equivalent) | Sandbox image + `clevin-sessions` volume |
| Self-improvement: writes back learnings after a task | Memory Store writes + Skill upload |
| Session forking from a checkpoint to try two approaches | No known primitive — confirm, likely class D |
| Attachments / multimodal input | Session message content |
| Runs on a schedule | Deployments |
| Observable, attributable run history | SSE + usage events + Console |
| Per-task cost accounting | `session.usage` + budget events |
| Fleet of agent variants managed as code | Agent versions + the provisioner |

## 4. Verified starting facts — do not re-derive these

Established by a readiness smoke test against the live account. Trust these; spend your time past
them.

**Existing production resources**

- Agent `agent_01Eef1xLtkWW2cDg1shFUpms` ("Clevin Native Ticket-to-Green-PR Agent"), versions 5/6/7.
- Self-hosted environment `env_0152FZKRpy9f8uVw38Guzosy` ("Clevin Modal Self-Hosted Environment");
  cloud environment `env_01F4KCNxYngRzYKG5a1QLRZT`.
- Memory Store `memstore_01JCboyFNzqNzucVq3xFpnYZ` ("Clevin Repository Learnings"), mounted at
  `/mnt/memory`.
- Vault `vlt_011CeLyihmq1GNjHGxvtWw1q` with GitHub (`vcrd_01Mb2JPaGPWAvZiZKkPZ7mNJ`) and Linear
  (`vcrd_011FfcwAegBzfCYWxvn1T77G`) credentials.
- Modal: environment `clevin`, app `clevin`, volume `clevin-sessions`, deployed webhook
  `https://hrabbani-clevin--clevin-webhook.modal.run`, sandbox image `im-q6zSu8EtUeq4bEqbtkltJ1`.
- The production agent is currently `skills: []` and `multiagent: null`. Any Skill or subagent
  finding only counts once it lands in `packages/provision/src/agent-definition.ts`.

**Confirmed working** — agent create/retrieve/update and versioning; session creation and
server-side persistence; event replay; native SSE streaming with stable event IDs; usage and budget
events; `user.interrupt` against an actively working session; small-scale concurrent sessions;
Modal auth, sandbox create/write/terminate, volume mounting; the end-to-end webhook →
`EnvironmentWorker` → Modal sandbox path; Memory Store mount at `/mnt/memory`; native subagent
delegation; Skill upload/attach/invoke (the archive must contain `<name>/SKILL.md`, not a root-level
`SKILL.md`); custom tool declaration (`type: "custom"`, not `"custom_tool"`); MCP configuration;
deployment create/pause/inspect/archive.

**Known dead ends — do not spend time here**

- Browser and Computer Use: `browser_toolset_20260801` and `computer_toolset_20260801` are rejected
  as invalid Managed Agents tool types. **Recorded as class D. The browser workstream is cancelled.**
  Do not attempt browser automation, and do not substitute a browser MCP or sandbox-side browser
  tool unless your assignment explicitly says to evaluate one.
- Admin API: unavailable (no admin key). Org spend/usage/workspace/API-key endpoints return 401/403,
  and `/v1/organizations/webhooks` does not exist. Per-session `session.usage` and budget events are
  the cost instrument.
- Model config: Haiku rejects `effort: "low"`; omit `effort` for cheap models.
- Runtime config now reads injected environment variables as well as a root `.env`, with the process
  environment taking precedence. No `.env` file is required.

**Repo setup**: `pnpm install` and `uv sync --project runtime`. Verification, all of which must pass
before you open a PR:

```bash
pnpm verify
uv run --project runtime ruff format --check runtime
uv run --project runtime ruff check runtime
uv run --project runtime mypy runtime/src
uv run --project runtime pytest -c runtime/pyproject.toml
```

## 5. Workstreams

Read the dependency notes: **parallelize wherever possible, and gate only where a real input is
missing.** If a dependency is not ready, do the independent part of your workstream first and say so
in your findings rather than idling or duplicating another session's work.

### A. Control plane and session semantics *(no dependencies — start immediately)*

Produce a precise state and lifecycle model: what is Anthropic-owned vs sandbox-owned, what is
pinned to the agent version vs the session, what is recoverable vs irrecoverable. Change model,
system prompt, tools, skills, and subagent config after a session starts and compare against a fresh
session. Roll versions forward and back. Disconnect/reconnect SSE; delay, duplicate, and reorder
webhook handling. Cancel during generation and during tool execution. Run long enough for several
compactions and test whether early constraints survive. Look for prompt-caching indicators in usage
events. Compare Anthropic-side history against sandbox filesystem state.

*A's state model is an input to B, C, F and J — publish partial findings early rather than holding
everything until the end.*

### B. Long-horizon agent quality *(best after A's compaction findings; the workloads can be built in parallel)*

Determine whether Managed Agents supports long-running, minimally supervised work. Workloads: large
multi-file refactor, dependency upgrade, framework migration, test-suite debugging, repeated
review/revision, changing requirements, a task needing information introduced hours earlier, a task
with multiple false starts, a task spanning several compactions. Vary: one session vs several
resumptions, Memory Store on/off, subagents on/off, different system-prompt strategies, planned
worker interruption, injected tool failure. Measure completion, human interventions, constraint
retention, repeated mistakes, plan stability, regression rate, elapsed time, token and compute usage,
and variance across runs.

### C. Runtime reliability, recovery, and the tool surface *(no dependencies — start immediately)*

Push the Anthropic-managed loop plus self-hosted Modal sandboxes until recovery semantics are clear.
Inject: worker killed mid-command; worker restarted during reasoning; restart after files are written
but before the tool reports success; volume detached; disk filled; network interrupted; tool timeout;
malformed tool output; very large tool output; Modal app redeployed; sandbox expired; delayed worker
startup; duplicated tool response; several sessions against constrained compute. For each: does
Anthropic notice, retry, retry *safely*; does the session stay usable; does sandbox state survive;
does the agent understand what happened; can it recover without custom orchestration; what minimal
prompt/tool change improves recovery?

This workstream also owns the **tool and MCP surface** (the non-browser half of the cancelled browser
workstream): hosted MCP behaviour under long sessions, tool retries, malformed and oversized tool
responses interacting with compaction, tool configuration changes between versions, per-subagent tool
grants, generalist vs role-specific tool sets, and where tools execute relative to the sandbox.

Recovery improvements must come from prompts, tool design, tool schemas, `EnvironmentWorker`
behaviour, session APIs, and lifecycle events only.

### D. Agent-as-code and configuration lifecycle *(no dependencies — start immediately)*

Can an agent fleet be managed through native versions plus thin configuration-as-code, without a
second control plane? Reconstruct the agent fully from `packages/provision`. Compare code-managed
against Console-managed configuration; make a Console change, detect the drift, reconcile it back.
Create dev/staging/prod-style versions; canary two versions over one benchmark; roll back a broken
version. Share Skills, tools, and subagent definitions across variants; manage many variants;
identify which resources cannot be managed declaratively; test active-session behaviour during
version changes; test whether version pinning alone gives reproducibility.

### E. Native Memory Store *(no dependencies — start immediately)*

How far can the native Memory Store go as the long-term memory layer? Create stores through
supported flows for operating knowledge, coding conventions, user preferences, prior task learnings,
known failure patterns, and environment knowledge; attach different combinations to versions and
sessions. Have the agent maintain a store over many sessions; test whether it recognises what is
worth remembering, and whether it corrects or supersedes stale entries. Introduce contradictory and
subtly incorrect entries. Store lessons from failed runs and check whether later sessions improve.
Compare repeated tasks with and without the store. Test behaviour across versions, across
compaction, and across several subagents sharing one store (including conflicting writes). Determine
how retrieval is triggered, whether provenance is visible, what lifecycle actions still need the
Console, and the practical size/structure ceiling. Explicitly answer: can naming and structure
approximate the missing dynamic scoping?

*E's store structure is an input to B and J.*

### F. Built-in subagents *(no dependencies — start immediately)*

Push built-in subagents toward real planning, delegation, parallelism, specialisation, and review.
Test rosters: repository explorer, planner, implementer, test debugger, reviewer, documentation
writer, performance investigator, skeptical verifier, failure-recovery specialist. Test patterns:
planner → implementer, implementer → reviewer, parallel exploration then synthesis, one subagent per
failing test, one per hypothesis, competing proposals, implementer plus adversarial reviewer,
hierarchical delegation if supported, repeated delegation over a long session. Answer: when does the
parent delegate; how much context does the child get; how reliably does it report back; can the
parent synthesise conflicting results; do children share filesystem and process state correctly; what
happens when one hangs or fails; can the parent cancel or redirect; do results survive compaction;
does adding subagents improve success rate or only cost; what is the useful maximum concurrency; can
role prompts produce consistently specialised behaviour? Stress it: overlapping file edits,
conflicting conclusions, a deliberately poor result, resource contention, very large child outputs,
child tool failure, parent compaction while children run, recursive delegation.

Do not work around subagent limits by spawning top-level sessions inside Clevin or building an
external delegation engine. Subagent definitions, prompts, and tool grants are the only levers.

*F's winning roster is an input to J.*

### H. Deployments and automation *(no dependencies — start immediately)*

Push the native Deployment model. Run high-frequency schedules; use deployments for recurring
maintenance; have them inspect and update Memory Stores; trigger workflows that delegate to
subagents; test overlapping runs, failed and delayed runs, and version changes between runs;
determine whether a deployment can continue prior work; test whether external activity can be polled
instead of received through a custom webhook. Answer: how much automation is expressible natively;
can polling plus persistent session/memory approximate event-driven behaviour; what concurrency
controls exist; is the model merely limited or genuinely unusable for advanced workflows?

### I. Observability and economics *(depends on A for the lifecycle vocabulary; instrumentation can start immediately)*

Determine whether native events, usage data, session history, Console views, and Modal logs are
enough to operate an advanced agent. Correlate: agent version → session → model events → tool
request → `EnvironmentWorker` → Modal sandbox → filesystem changes → tool response → subagent
activity → compaction → final result. Track session phase, model usage, prompt-caching indicators,
compaction events, tool latency and retries, worker restarts, subagent activity, errors and
recovery, files changed, cost per experiment, and cost per successful task. Instrumentation may only
consume native session APIs, SSE events, usage events, and lifecycle webhooks. **The blind spots are
the finding** — do not build a standalone observability product to cover them.

### K. Devin-parity interaction model *(no dependencies — start immediately)*

Own the parity rows that A–J do not cover. Specifically:

1. **Mid-run steering** — inject a user message into an actively working session and determine
   whether it re-plans, versus merely aborting the current tool call.
2. **Ask-and-block** — can the agent stop for a human decision and resume much later with the
   sandbox workspace intact, or does the worker lease/idle timeout destroy it? Establish the actual
   survivable wait.
3. **Wake-on-event** — how close can Deployment polling plus a persistent session get to reacting to
   a new Linear ticket or GitHub comment, given there is no GitHub → Anthropic event path?
4. **The PR-review and CI loop** — the agent responding to review comments and fixing failing CI on
   its own PR through GitHub MCP. A "green PR" agent is incomplete without this.
5. **Skills as playbooks** — reusable named procedures a user can invoke; land the useful ones in the
   agent definition.
6. **Self-improvement** — the agent writing back learnings (memory entries, new Skill versions) at
   task end, and whether that measurably helps the next task.
7. **Session forking** — whether a session can be branched from a checkpoint at all. Confirm quickly
   and classify; do not build a fork mechanism if none exists.

### J. Integrated gauntlet *(gated: needs A, C, E, F, and K's parity findings)*

Combine the strongest native configurations into one agent version and require it to: receive a
broad, ambiguous coding task; inspect the project; build and maintain a long-running plan; use
Memory Stores for prior knowledge; delegate investigation to built-in subagents; make changes in the
Modal sandbox; recover from an injected worker or tool failure; survive compaction; run tests and
review its own work; revisit its implementation after feedback; and finish with minimal human
intervention. (The original browser step is removed.) Run it repeatedly across configurations and
score every component on Managed Agents provenance.

## 6. Output contract — every session must produce all of this

1. **Findings file** at `context/findings/<workstream>-<topic>.md`, opened as its own PR.
2. **A/B/C/D class per capability**, with evidence: session IDs, event excerpts, Modal logs, usage
   numbers. No claim without evidence.
3. **Reproduction**: steps or a script under `experiments/<workstream>/` that another session can
   rerun to reach the same conclusion.
4. **Provenance ledger**: every line of code you added, attributed to the primitive it configures,
   implements, extends, observes, or tests, plus the invocation path and why configuration alone was
   insufficient. Code with no attributable primitive must not exist.
5. **Distance-to-Devin note** for each parity row you touched.
6. **What you could not test, and why** — including anything you deliberately did not build because
   it was class D.
7. **Cleanup ledger**: every temporary resource created, its cleanup action, and the result. Do not
   hide cleanup failures.

## 7. Operating rules

**Autonomy.** Work autonomously from start to finish. Do not ask for review, confirmation, or
progress sign-off. Ping Humza on Slack **only** when you are genuinely blocked — a missing
credential, an unavailable platform surface, an exhausted balance, or a decision only he can make —
and state precisely what you need. Everything else you decide yourself, including experiment order,
role framing, and how you coordinate with sibling sessions.

**Git and merging.** All work lands in `COG-GTM/clevin`; the goal is for everything to be mergeable
into this repo. You have full freedom to merge into any branch **except `main`** — `main` is the
master session's call. Use the shared integration branch `swarm/integration` as the default merge
target, and branch your own work off it as `devin/<timestamp>-<workstream>-<slug>`. Never force-push
a shared branch, never amend, never skip hooks, and never run destructive git commands. To avoid
collisions with sibling sessions: you own `context/findings/<your-workstream>-*` and
`experiments/<your-workstream>/` outright; changes to shared files
(`packages/provision/src/agent-definition.ts`, the runtime, existing docs) must be minimal, focused,
and called out in your PR body. Run the full verification suite in §4 before opening a PR.

**Resources.** Never mutate the production agent version in place. Create a new agent version, or
create temporary resources named `clevin-swarm-<workstream>-<UTC timestamp>-<short id>`, and clean
them up. Do not modify existing environments, memory stores, vaults, Modal apps, volumes, images, or
deployments unless your assignment explicitly requires it. Never print, log, or commit credential
values.

**Spend.** Use the existing prepaid Anthropic and Modal balance freely: start as many sessions as
useful, run long workloads, repeat experiments, generate substantial usage. Cost optimisation is not
a goal. Do **not** purchase credits, enable auto-recharge, upgrade a plan, add or change a payment
method, make organisation-level billing changes, spend beyond the existing balance, or continue once
it is exhausted.

**Standard.** Information gain, depth, reproducibility, functional capability, and Managed Agents
provenance are the goals. Do not optimise for a favourable demo. Determine the actual maximum
capability of the native model — and where that maximum falls short of Devin, say so plainly and
leave the gap unbuilt.
