# Claude Managed Agents — Capability Buckets, and How Far Each Can Be Taken

A higher-level companion to `REPORT.md`. Each bucket gets: what the platform offers, how far we
were able to push it, and where it tops out. Detail and evidence live in `REPORT.md` and the
per-workstream findings.

```
bucket                       ceiling verdict
──────────────────────────── ─────────────────────────────────────────────────────
Autonomous execution         ██████████  near Devin parity for a single attended run
Human interaction & steering █████████░  parity behavior; you build the client glue
Integrations (MCP, CI/PR)    █████████░  full red→green PR loop; missing only the trigger
Testing & verification       ████████░░  graded, uncheatable runs; success isn't native
Config & release management  ████████░░  best-in-class agent-as-code; no release layer
Subagents / delegation       ███████░░░  real parallelism; no runtime control, 3–4× cost
Observability & cost         ███████░░░  superb forensics; zero operations/alerting
Skills / playbooks           ██████░░░░  followed exactly — once you tell the model they exist
Memory & learning            █████░░░░░  durable storage; retrieval is "hope it greps"
Scheduling & automation      ████░░░░░░  cron only, cold sessions, no execution guarantees
Reliability & recovery       ███░░░░░░░  self-diagnosing, never self-healing
Model selection / routing    ██░░░░░░░░  pick one model per session, forever
```

## 1. Autonomous execution
**Offers:** hosted reasoning loop + shell/file tools in your own sandbox, durable event history,
budgets, native context compaction on large-window models.
**How far it goes:** very far. A 27-file migration completes unattended with zero nudges,
constraints honored and tests uncheated, for ~$50 and five minutes; the integrated gauntlet drove
an ambiguous ticket to a CI-green PR in one session. Compaction genuinely works (95% context
reduction with exact constraint recall) on Opus.
**Where it tops out:** there is no durable notion of a *job* — a budget stop is a mid-edit
guillotine with no wrap-up, all failure modes collapse into the same idle state, and the
economics (an Opus roster ticket cost ~$716) are the real ceiling, not capability.

## 2. Memory & learning
**Offers:** versioned, attributable, concurrency-safe Memory Stores mounted into the sandbox;
survives sessions, compaction, and sandbox death.
**How far it goes:** cross-session learning is real when rediscovery is expensive (a remembered
environment gotcha cut the next run's cost ~30%); agents write back good lessons and even correct
poisoned entries; provenance (who wrote what, when) is enterprise-grade.
**Where it tops out:** it is storage, not retrieval. Nothing is injected — the model must choose
to grep the mount, guided only by prompt text. No scoping, ranking, or knowledge selection
exists; memory can't be bound to an agent version; entries are a permanent prompt-injection
surface that can be redacted but never deleted. Devin's "already-primed with the right org
knowledge" has no counterpart.

## 3. Subagents / delegation
**Offers:** a one-field coordinator roster: depth-1 parallel children, shared filesystem, tool
grants enforced at the platform layer, per-child model/version pinning, per-thread cost.
**How far it goes:** 7+ genuinely concurrent children; conflicting child reports were synthesized
on evidence rather than by vote; per-role cost attribution is better than most agent products.
**Where it tops out:** depth 1, hard. Once a child runs there is no cancel, timeout, or heartbeat
from inside; oversize replies vanish silently; concurrent edits are silent last-writer-wins.
Measured quality gain from delegation: none — you buy parallelism and isolation at 3–4× cost.

## 4. Skills / playbooks
**Offers:** versioned file bundles delivered into the sandbox; pinnable per attachment.
**How far it goes:** once a system-prompt paragraph says where skills live, the agent quotes and
follows the playbook's numbered commands verbatim. Versioning works.
**Where it tops out:** attachment alone is a silent no-op — there is no listing/loading tool, so
an unmentioned Skill is dead weight. And the agent cannot publish or update a Skill from inside a
session: self-improvement of knowledge is native, of *procedures* is not.

## 5. Human interaction & steering
**Offers:** `user.interrupt` mid-run, custom tools that park the session for human input,
resumable idle sessions.
**How far it goes:** behavioral parity with Devin. Interrupt-then-message is accepted in ~0.5 s
and produces genuine re-planning; an `ask_human` tool parked a session for 75 minutes and resumed
in 0.2 s, nearly free while waiting; the gauntlet agent asked exactly once, only after proving
the question was undecidable.
**Where it tops out:** every piece of the pattern (interrupt-first client logic, the ask tool,
the operator servicing the park) is glue you write; and "waiting for a human" is indistinguishable
from "waiting for a machine" without event correlation.

## 6. Integrations (MCP, CI/PR)
**Offers:** server-side MCP connectors (GitHub, Linear) that execute on Anthropic's side — immune
to your sandbox dying.
**How far it goes:** one session took a PR from red CI to green — read the diff, review comments
and check runs, fixed the code, tested locally, pushed, polled to success, replied inline — for
$58 with no custom code.
**Where it tops out:** the mechanics are at parity; what's missing is purely the *trigger* — no
event can start or wake a session (see bucket 9).

## 7. Testing & verification
**Offers:** native outcome evaluation (`user.define_outcome` rubrics graded by the platform),
plus everything needed for in-sandbox graders.
**How far it goes:** a rubric correctly failed a run over a one-word output mismatch; our
SHA-protected graders ran uncheated in 13/13 sessions — the agent never once edited a test to
pass. Scheduled runs can carry acceptance criteria, not just prompts.
**Where it tops out:** unless you define outcomes up front, *success is not a native field* —
cost per green PR is unobtainable retroactively. Grading is slow under load and the model's own
completion claims are not trustworthy (the grader caught one false success).

## 8. Configuration & release management (agent-as-code)
**Offers:** the whole agent is nine declarative fields; every change is an immutable version;
optimistic-concurrency guards; version pinning that fails closed.
**How far it goes:** arguably better than Devin's config story — byte-exact roll-forward
rollback, zero-orchestration canarying across pinned versions, drift detection (with our thin
client-side extension), 12-agent fleets created in seconds.
**Where it tops out:** no aliases or channels ("prod" cannot be a pointer), no version deletion
or tagging, no server-side desired state (convergence is your code), and no deterministic replay
— identical config and prompt vary ±25% in cost/time.

## 9. Scheduling & automation
**Offers:** cron deployments (minute floor) that create fully-capable sessions; a typed
auto-pausing dependency supervisor; manual run trigger.
**How far it goes:** ~61 s wake-on-ticket by polling Linear; punctual fires; scheduled runs can
use subagents, memory, MCP, and graded outcomes — a scheduled agent is not a lesser agent.
**Where it tops out:** every fire is a new cold session (no continuation, ever); no concurrency
control, retry, or catch-up; one bad dependency silently pauses the automation indefinitely; and
there is no event ingress at all — GitHub/Linear cannot wake anything. The always-on,
event-driven lifecycle is the single largest structural gap vs Devin.

## 10. Observability & cost
**Offers:** a complete durable event log, per-request token/cache/cost accounting, ~9 ms SSE
streaming, per-thread cost attribution, webhooks.
**How far it goes:** forensics are superb — a full run reconstructs from two API calls, costs
reconcile to the cent across subagent threads, fleet-wide spend rolls up in under a second.
**Where it tops out:** it is forensics, not operations. No alerting of any kind, no stuck-session
signal, no error-rate API, no record of which files changed or which sandbox ran a call; budget
events are webhook-only. Every operational signal must be derived by a monitor you run.

## 11. Reliability & recovery
**Offers:** lease-fenced tool dispatch, native detection of dead workers (~6 min), automatic
retry of errors the platform itself classifies transient, clean interrupt semantics.
**How far it goes:** detection and diagnosis are honest and precise; a manual re-attach with just
the environment key can re-drive a stranded tool call.
**Where it tops out:** the loop is **self-diagnosing, not self-healing**. Nothing ever
re-dispatches abandoned work (structurally: a work item *is* the session's one activation);
billing-exhausted sessions never resume when credits return; side effects are at-least-once with
no idempotency support. Every long-running agent needs an external supervisor — the irreducible
piece Devin has and this platform does not.

## 12. Model selection / routing
**Offers:** any Claude model per agent version; different models per subagent roster entry plus
one advisor.
**How far it goes:** that's it — you can compose a roster where the coordinator and children run
different models.
**Where it tops out:** a session is pinned to one model at creation, immutable for life. No
per-step routing, no fallback, no cost-based downgrade — and the model silently sets the
capability class (Opus compacts; Haiku dies at its context limit). With ~$716/ticket Opus
economics and no routing lever, this is the bucket that most directly caps fleet viability.

---

**One line:** Managed Agents takes the *run* itself about as far as Devin — and takes the
*product around the run* (waking, healing, knowing, routing, operating) almost nowhere; that
layer is yours to build.
