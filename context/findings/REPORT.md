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

### 3.1 Summary table

Column two is the concrete surface you get out of the box — which objects exist, what the API does, and
 what has no interface at all. Column three is how far this program pushed it, and where it stopped.

| Primitive / capability | What Managed Agents offers | Upper bound: how far we pushed it, and what stopped us |
|---|---|---|
| **Autonomous execution** | One API call (`POST /sessions` with a prompt) starts a hosted loop that keeps taking turns on its own for hours. You get a built-in tool set — shell, read/write/edit files, search, web fetch — and the model shortens its own history when it gets too long. There is no interface: no chat window, no progress view, only the event list you read yourself. | **How far:** a 27-file money-rounding migration, done unattended, 12 of 13 runs scoring full marks against a hidden grader, no human help at all, $41–62 a run; a 5.4 MB run shortened its own history twice and still remembered its original instructions exactly. **What stopped us:** we could not build a *job*. A spend cap ended one run mid-edit and another one command short of pushing its work, and nothing recorded that the work was unfinished; every failure looks the same, so we could never tell "the tests never passed" from "the machine died". We also had to write our own independent grader, because the platform never reports whether the work was actually done — the run that finished incomplete looked no different from the ones that succeeded. |
| **Session management** | An API for sessions as objects: create, fetch, list across the whole account with filters, rename, re-tag, change the spend cap, and append events. Each has an ID that also names its sandbox and its files, so it survives being left alone. The console shows the same sessions for reading; there is no product around them — no queue, no assignment, no chat. | **How far:** we resumed a session 75 minutes after it stopped, in 0.2 seconds, and rescued a run that had hit its spend cap one command before pushing — same session, same history, same working files. **What stopped us:** three specific things. (1) **No branching.** We probed for it directly: fork, branch, copy and checkpoint are all missing, and a new session cannot even be started with a copy of an old one's history — only a human-written summary. So "try two fixes from this exact state and compare" is impossible; the nearest thing is re-explaining the situation to a fresh agent, which loses its reasoning and its cheap cached history. (2) **Almost nothing can be changed mid-run.** Only the tool list is editable; the model, the instructions, the attached procedures and the helper list are frozen when the session starts, so correcting a badly-worded brief means starting over. (3) **What survives is narrower than it looks.** After an hour idle the machine is thrown away — only files on disk come back, so anything running, installed or held in memory is gone, and we had to make re-entry rebuild it. |
| **Memory & learning** | A separate "memory store" object with its own API for writing and reading notes, plus safe handling when two writers collide. Attach it to a session and it appears as a folder of files inside the sandbox. Nothing else: no search, no ranking, no automatic use. | **How far:** 12 agents wrote 260 notes in 4.7 seconds with no lost writes and safe conflict handling; a repeat task got cheaper because the agent remembered (69 s/$17 → 45 s/$12); one agent even caught and corrected a wrong note another agent had written. **What stopped us:** there is no recall. Nothing is ever put in front of the agent — it has to go read the files itself, so a 200-note store helped no more than a 2-note store, and remembering a cheap fact cost more than looking it up again. We deliberately did not build the retrieval layer this needs. |
| **Subagents / delegation** | One field in the agent definition listing up to 20 helpers (other agents, a copy of itself, or a model to consult). When it is set, the agent silently gains tools to start helpers and message them, and each helper's messages and costs appear in the parent's own event list. | **How far:** seven helpers at once; a helper handed a deliberately wrong report and the parent worked out which one to trust; helpers pinned to older versions behaved like those older versions. **What stopped us:** delegation is one level deep and only to helpers named in advance — an agent can never create a new helper while working. The parent could not cancel, time out, or check on a helper; very large replies vanished with no error; and in every quality test helpers cost 3–4× more without doing better work. |
| **Skills** | A "skill" object: you upload a folder of written instructions (and scripts) through the API, then name it in the agent definition, and it appears read-only inside the sandbox. That is the entire feature — a mounted folder. | **How far:** once told where to look, the agent followed a pull-request procedure exactly, including the parts it had no other way of knowing. **What stopped us:** attaching a procedure does nothing by itself — in 3 of 3 tests the agent insisted no procedure existed until we added a line to the prompt naming the file path. The agent cannot list what procedures exist or pick the right one for the task, so the "which playbook applies here" layer had to be prompt text. |
| **Human interaction & steering** | An events API: you append a message or an interrupt to a running session, and read its events back live. To let the agent ask *you* something, you define your own tool; the session then parks until you post the answer back as an event. A webhook can tell your own system that a session is waiting, but that is the extent of it: no chat interface, no inbox, nobody is told — you write the human side. | **How far:** an interrupt was accepted in half a second and the agent genuinely re-planned and correctly described what it had already done; a custom tool let it stop and ask a question, wait 75 minutes, and carry on 0.2 seconds after we answered — waiting cost almost nothing. **What stopped us:** none of the convenience exists. You cannot leave a message for a busy agent (it is rejected outright), so we had to write interrupt-then-send ourselves, and a waiting agent looks exactly like a broken one, so we had to work out the difference from its event history. |
| **Integrations** | The agent definition lists outside tool servers (MCP) by URL, with a per-server list of which tools are allowed. For code hosting there is one first-class helper: a session can be given repositories by URL and token, and the platform clones them into the sandbox at a path you choose, on a branch or commit you name, caching them so later sessions start faster — several at once, and it also picks up any procedures kept in the repository. Everything else about a service is only what its tool server exposes. | **How far:** one session took a failing pull request all the way to passing — read the diff, comments and failed checks, fixed the code, tested locally, pushed, waited for CI to go green and replied on the review, in 10 tool calls for $58. **What stopped us:** the agent knows only what the connector exposes — no real model of pull requests, CI or reviews, and every action appears to come from one shared account rather than a named person. It cannot use a browser at all, so it can run tests but never show them. And nothing outside can wake it: the trigger, not the ability, was the blocker. We also cloned repositories by hand with shell commands and never used the built-in repository mount — a native primitive this program left untested, and its caching is exactly the warm-start we said was missing. |
| **Permission controls** | Two layers that never meet. **Around the account:** the same access control as the rest of the platform. People are organisation members with one of five roles (use the playground; also use Claude Code; also manage keys; also manage billing; admin, who manages people), grouped into workspaces where they hold a second, workspace-level role, and every agent, environment and session belongs to a workspace. Keys can be tied to one workspace, machines can hold service accounts or federated identity instead of keys, and all of it is manageable by API for joiners and leavers, with enterprise plans adding groups and custom roles. **Inside a run:** nothing about people at all — only tools. Every server-run tool carries a policy: run it, or stop and ask. You set it per tool set or per single tool, you can switch a tool off entirely, and the web tools additionally take lists of sites they may or may not visit. When a tool needs approval the session parks and names the exact call; you answer allow or deny for each, and a denial can carry a written reason the agent then reads. Sensible defaults: built-in tools run, outside tool servers ask. | **How far:** we ran everything on always-allow and enforced read-only helpers by handing them a reduced tool set, which held — a read-only helper could not edit. Shared notes can also be attached read-only, and that is genuinely enforced. **What stopped us:** the roles govern who may *configure and spend*, not what an agent may *do*. Workspaces are the only boundary — the natural way to say "this team's agents are separate" is a separate workspace with its own keys — and we never used more than one, so that isolation is untested here. Inside a run, the person is absent: a session's fields are its agent, environment, resources, vaults, status, stats and free-text tags, and none of them names who asked for it — an agent cannot act as the human who asked, roles do not reach any tool, and no role says "may not touch this repository." Approval is a one-size gate on a raw command, so "anything but a force push" cannot be expressed; nobody is notified that approval is waiting; and asking for approval looks the same as the agent asking a question. |
| **Network controls** | Two separate controls for two separate places traffic leaves from. The web tools run on Anthropic's side, and each of "search the web" and "fetch a page" takes its own list of sites it may reach, or may never reach, subdomains included — a blocked fetch returns a named error to the agent, a blocked search result is silently dropped. The sandbox's own outbound traffic is set on the environment instead: open by default apart from a general safety blocklist, or restricted to a named list of hosts, with separate switches to let package managers and outside tool servers through anyway. The two do not affect each other. On your own machines neither applies — Anthropic never sees where a command connects, so shell traffic is governed by whatever infrastructure you chose. | **How far:** untested. We left the web tools unrestricted and never set a restricted host list, and on our own machines set nothing either, so an agent could reach whatever its sandbox could reach — our runner offers ways to cut that off and we used none of them. **What stopped us:** nothing is a platform ceiling here; the limits are shape and evidence. A host list is a network rule, not an intent rule, and it cannot express the thing you actually want — "may install dependencies, may push to our repositories, may not exfiltrate the repository" — so the switches for package managers and tool servers exist precisely because host lists are too blunt. And nothing records what happened: no event names a host, so "what did this agent talk to" is answerable only from your own network logs, which for the hosted sandbox you do not have. |
| **Credentials (vaults)** | A vault object holding credentials for outside tool servers, matched to a server by its address. The secret is stored and used on Anthropic's side, never sent to your machines and never returned by the API; you attach the vault when the session starts, and you can replace a token later without disturbing the session. | **How far:** the arrangement worked as advertised: sessions used GitHub and Linear with no token anywhere in our infrastructure, and outside calls kept working when our own machines were torn down. **What stopped us:** one credential per service, so every action appears as one shared account — no per-person identity, no way to limit an agent to some repositories, and no audit trail naming a human. It only covers outside tool servers: anything the shell needs — the git credentials, cloud keys — is yours to inject, and ours had to be rewritten on every machine restart because nothing about a sandbox survives. |
| **Configuration & release** | An agent is nine fields you send as JSON. Every publish creates a numbered version that can never change, and stale writes are rejected. The same objects can be edited in the console or through Anthropic's `ant` command-line tool, so "agent as code" is genuinely the whole definition. | **How far:** we rebuilt an agent from code alone with zero differences, set up 12 agents in 4.8 seconds, rolled back by republishing an old version byte for byte, and ran three versions side by side over one benchmark for free. **What stopped us:** there is no release management. Nothing lets "production" point at a version, old versions cannot be deleted or tidied, and the platform keeps no record of what you intended — so we had to write our own drift detection, because the server quietly adds its own fields and makes naive comparison useless. |
| **Scheduling & automation** | A "deployment" object: a timetable plus the opening messages for the session it should start. It is the only built-in way for a session to begin without a person, and the sessions it makes are ordinary ones with full abilities. | **How far:** eight scheduled runs fired on time with a few seconds' variance, each with the full abilities of a normal session (helpers, outside tools, memory), and one noticed a newly filed Linear ticket about a minute later. **What stopped us:** it is a timer, not a work manager. Once a minute is the fastest, every run starts cold with no memory of the last one, and there is no way to say "skip this fire if the last one is still going" — with a run body longer than the interval, three copies ran at once. Firing on time is correct; the missing thing is the choice, which ordinary schedulers give you. Failures are never retried, and one broken setting quietly stopped the whole schedule with nothing in the logs. We chose not to build the scheduler this needs. |
| **Observability** | An append-only event list per session, 35 kinds, fetchable in pages or streamed live: the agent's own reasoning, its messages, every command it ran and the exact output that came back, every outside-service call and reply, every question it asked and answer given, interrupts, errors, and a start/end pair around each model request carrying tokens, cache use and speed. Money and time come with it — per session, per helper, plus a spend cap — and free-text tags let you group the whole account after fetching it — sessions can be listed by agent, version, date, status, deployment or shared-notes store, but not by tag, so tag grouping is arithmetic you do yourself. A separate set of 44 notifications to your own systems covers alerting. The console replays the same transcript for reading; we never evaluated it, so anything visible only there is unverified. | **How far:** we reconstructed entire runs — which version, which command, what it cost — from one or two calls; live updates arrived in 9 ms; costs added up to the cent across helper agents; and we summarised 164 sessions and $104 of spend in under a second. **What stopped us:** you can explain the past but not run the present. There are no summaries, error rates or alerts, and nothing tells you a session is stuck — we had to detect that ourselves from three unrelated fields. Spend caps are checked between turns, not inside one, so a $0.05 cap let a single turn spend $0.11. And grouping cost is a labelling habit rather than a feature: there is no project or cost-centre object and no account-wide rollup, only free-text labels you must remember to set, which we forgot on 50 sessions worth $15.79 — recoverable only by reading each session back and re-labelling it. |
| **Reliability & recovery** | The work queue hands each session to one machine at a time and requires it to keep checking in; if it stops, the claim expires and a stale machine is locked out. Anthropic also sends a webhook when a run starts, so you know work is waiting. | **How far:** we killed a worker mid-command and the platform noticed the abandoned session about six minutes later; a revived worker was correctly locked out; and we recovered a live session on a fresh machine with nothing but an environment key. **What stopped us:** it notices problems and never fixes them. Nothing ever hands the work to another machine, so the session waited forever until we intervened; replaying the start-up notification created nothing; and a session killed by running out of credit stayed dead even after paying. Recovery can also re-run a command that already ran, so safety is on you. |
| **Model selection / routing** | A single `model` field in the agent definition, set per agent and per helper, plus the option of one extra model listed purely as an adviser to consult. | **How far:** we pinned different models to different agents and helpers, and compared them side by side cheaply. **What stopped us:** you choose once. The model is fixed when the session starts and cannot be changed, so there is no switching mid-task, no falling back when one fails, and no cheap-to-explore/strong-to-fix pattern — the one thing that would have brought the $716 ticket price down. The choice also silently changes what is possible: a small model cannot handle long work at all, with no warning. |
| **Execution environments** | Two kinds of "environment" object. **Anthropic-run:** you describe a machine — packages to pre-install by any of six package managers, with versions pinnable, cached between sessions that share the environment — and Anthropic gives every session a fresh Linux container with common languages, databases and tools already present, plus a choice of open or restricted outbound network. Nothing to operate. **Your own machines:** the same object becomes a queue your hardware polls over an outbound-only connection, using a key minted by hand in the console; the thinking stays hosted, every command runs on your hardware, and no worker software is provided — the poller, the sandbox, the image and the storage are all yours to write. | **How far:** we used both. Anthropic-run sandboxes carried most experiments with no infrastructure at all; on our own we ran each session in its own locked-down sandbox with a workspace that survives between sessions, shared notes mounted as files, and safeguards that shut out a duplicate worker. **What stopped us:** neither kind has a fleet layer. Work goes to whichever machine asks first — one of our workers accidentally picked up four other sessions' work — so sending certain jobs to certain machines, or limiting how many run at once, is yours to build; access is one all-powerful key created by hand. The hosted description is a package list, not a machine image: there is no setup script, no repository-aware build, no saved known-good state to return to, and environments are not even versioned, so you cannot tell later which configuration a session used. |

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
