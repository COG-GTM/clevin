# Is This Production-Grade Remote Development for Agents?

The third report of the Managed Agents ceiling program. `REPORT.md` answers "how far can you push
the primitives"; `CAPABILITY-BUCKETS.md` answers "what are the buckets and their ceilings." This
one answers the operator's question: **if an enterprise put engineers and agents on this platform
tomorrow, is it production-grade remote development?** Each section is one of the guiding
questions, answered from the program's evidence (workstream letters cite the findings files).

**Verdict up front:** the *run* is production-grade — durable, auditable to the cent, safely
configurable as code. The *platform around the run* is not: access control stops at a single
Console-minted environment key, there is no per-engineer identity anywhere in the chain, re-entry
into a known-good environment is a convention you build from an image and a volume, browser-based
verification is rejected outright, and every event-driven workflow (PR review included) bottoms
out in ≥1-minute cron polling with cold sessions. Production-grade *execution*, assemble-your-own
*platform*.

```text
                     what "production-grade" needs        what Managed Agents provides
  ┌──────────────────────────────────────────────┬─────────────────────────────────────────┐
  │ per-engineer / per-repo access control       │ one env key per environment, Console-only│
  │ horizontal scale with fleet controls         │ scales, but zero fleet controls          │
  │ reproducible known-good environments         │ image + volume; no snapshot/verify       │
  │ durable, permissioned MCP                    │ yes — at agent level, not user level     │
  │ complete audit trail                         │ perfect event log; no sandbox/file join  │
  │ deep SCM integration                         │ MCP-deep only; no native PR/CI model     │
  │ browser for e2e verification                 │ rejected tool type — class D             │
  │ event-driven PR review                       │ cron polling + cold sessions only        │
  └──────────────────────────────────────────────┴─────────────────────────────────────────┘
```

## 1. How do different engineers get different access to different repos/services/envs?

They don't, natively. The platform's only isolation unit is the **environment**, and its only
credential is the **environment key**:

- One key per environment, and it is all-powerful within it: any holder can claim *any* session's
  work item (the queue has no routing — C accidentally claimed 4 sibling sessions) and can answer
  pending tool calls in *any* session in the environment (the bare `SessionToolRunner` re-attach,
  C-5). There are no scoped or read-only keys.
- Environment keys are **Console-only** — they cannot be minted via the API (C), which blocks
  programmatic per-tenant/per-team provisioning. Standing up "one environment per team" is a
  manual Console workflow per boundary.
- Repo/service access is carried entirely by **vault credentials bound to the agent version**,
  not to a user. Every session runs as the vault credential's identity (e.g. one GitHub PAT);
  whatever that token can see, every session on that agent can see.
- There is no user object, no RBAC, no session-level credential injection, and no way to say
  "engineer X's sessions may touch repo A but not repo B" short of provisioning a separate
  agent + vault + environment per access tier — combinatorial, manual, and key-sprawl-prone.

**Ceiling:** access control is *per-agent-blast-radius*, not per-engineer. An enterprise access
model means N(environments) × N(agents) × N(vaults) provisioned by hand, with the Console in the
loop for every key.

## 2. How does the infra handle scaling to very high session volume?

The Anthropic side scales; the controls don't exist; the self-hosted side is entirely yours.

- Sessions are cheap to create and the control plane handles fleets fine — I's rollup covered 164
  sessions / $104.42 in 2 API calls and 0.96 s, and F ran 7 concurrent subagent threads with no
  platform strain observed.
- But there are **zero fleet controls**: no max-concurrent-sessions, no dedupe, no admission
  control. H's 150 s deployment body on a 1-minute cron simply ran 3 sessions concurrently; a
  minute-poller costs ~1,440 cold sessions/day, each a full cold start.
- The execution plane scales only as far as *your* worker fleet: one Modal sandbox per session,
  workers polling a queue with **no routing** — at volume you must build session-affinity on top
  of leases yourself or accept workers claiming each other's sessions.
- Cost scales linearly and noisily (±25% run-to-run, B) with no native budget grace — budgets
  overshoot 2.2× in a single turn (I) and stop mid-edit (B), so per-session budgets are a blunt
  fleet-cost instrument.

**Ceiling:** volume itself is fine; *operating* at volume (dedupe, concurrency caps, affinity,
graceful budget enforcement, fleet dashboards/alerts) is 100% supervisor code you write.

## 3. How does the agent reliably re-enter a known-good environment?

By convention, not by mechanism. The two native pieces are the sandbox **image** (declarative,
rebuildable — `sandbox_image.py`) and the per-session persistent **volume**:

- The sandbox itself lives ~1 hour (K); when it dies, only the volume survives. Re-entry means a
  cold sandbox from the image with the volume re-mounted — workspace state survives, process
  state does not.
- There is **no snapshot, fork, checkpoint, or clone** of a session or its environment — every
  candidate endpoint 404s (K). There is no "verified known-good" notion: no blueprint validation
  step, no environment health check, no drift detection between image and reality (D's drift.ts
  covers *Anthropic resource* drift, not sandbox drift).
- Recovery after a crash is possible (bare re-attach with the env key re-drives a pending tool
  call, C) but *deciding when* is entirely external — the platform never re-dispatches (J:
  `work_id == session_id`, reclaiming polls re-offer nothing).

**Ceiling:** "known-good" is image discipline + volume hygiene + your own watchdog. Compare
Devin's snapshot/blueprint model, where the verified environment is a first-class, versioned,
restorable artifact.

## 4. How do you support MCP integrations in a durable, permissioned, enterprise-safe way?

This is one of the platform's genuinely strong answers — with one structural gap.

- **Durable:** MCP servers and toolsets are fields of the immutable agent version (D). A config
  change is a new version; rollback is byte-exact roll-forward. The gauntlet agent reached Linear
  and GitHub exclusively through native MCP + vault (H, J).
- **Permissioned:** credentials live in the vault, never in prompts or env vars; tool grants are
  enforced at the platform layer, including for subagents (F — a child cannot use a tool its
  roster entry doesn't grant). MCP egress can be disabled workspace-wide, and dependency failures
  (missing/archived vault) auto-pause deployments with typed reasons (H).
- **Enterprise-safe caveats:** credential↔server matching is by URL string with undocumented
  normalization (K's trailing-slash probe); an archived *agent* dependency fails with zero
  run-log evidence (H); and — the structural gap — **there is no user identity**: every MCP call
  is made as the vault credential, so "which engineer caused this Jira ticket/GitHub push" is
  unanswerable natively.

**Ceiling:** best-in-class agent-level MCP governance; nonexistent user-level attribution. An
enterprise needs an identity-mapping layer (per-user vaults or an impersonation proxy) built
outside the platform.

## 5. How do you reliably track agent actions for safe deployment / auditing?

The forensic record is superb; the audit *system* is missing.

- Every session's event history is complete, ordered, and exactly replayable (A: 40/40
  byte-identical on re-list); cost reconciles to the cent across subagent threads (I); every
  claim in this program is backed by session/event/request IDs.
- But the record has **no joins to effects**: there is no native tool-call→sandbox-action link,
  no changed-files ledger, no diff artifact — what the bash tool *did* is only knowable by
  parsing tool inputs/outputs yourself. Bash runs unconfined inside the sandbox (C), so the audit
  boundary is the event log, not syscalls.
- History and webhooks are **disjoint surfaces** (35 vs 44 event types, I): you cannot alert from
  history or reconstruct from webhooks; a stranded session is detectable from three fields and
  announced by none.
- Attribution is opt-in: 50 sessions worth $15.79 had no metadata and were unattributable (I).
  Nothing forces a session to carry a ticket ID, requester, or purpose.

**Ceiling:** you can *always* answer "what did the agent say and spend" after the fact; you can
only answer "what did it change, who asked, and should someone be paged" if you built the
metadata discipline, the effect-joining pipeline, and the alerting yourself.

## 6. How deep is SCM integration?

Exactly as deep as the GitHub MCP server — no deeper.

- In-session capability is real: one session took a PR from red CI to green while replying to the
  inline review comment — 10 GitHub MCP calls, ~91 active seconds, $58 (K4). The gauntlet drove
  an ambiguous ticket to a CI-green PR first try (J).
- But there is **no native SCM model**: no PR object, no branch/permission awareness, no CI
  integration (the agent *polls* check-run conclusions via MCP), no commit signing, no
  branch-protection interplay, no PR-template/description tooling, no preview-deploy awareness.
- All SCM actions run as the single vault PAT (see §4) — one bot identity for every engineer's
  work, with that token's full scope as the blast radius.

**Ceiling:** "the model is good at using the GitHub API" — which is a lot — but every product-level
SCM feature (auto-PR creation conventions, CI watch/fix loops that wake on failure, review-bot
identity, per-repo permissions) is yours to build.

## 7. How does the container support browser access for demonstrating e2e automated testing?

It doesn't. This is a hard class-D wall, pre-verified at program start:

- The `browser_toolset_20260801` / `computer_toolset_20260801` tool types are **rejected by the
  API outright** (SYNTHESIS §4). There is no native browser, no screenshot artifact channel, no
  recording.
- The gVisor sandbox could in principle run a headless browser driven through bash (nothing
  forbids `playwright` in the image), but that yields text-mode assertions only: there is no
  artifact store for screenshots/videos, no visual-verification loop, and the ~100 KiB bash
  output cap (C) forecloses shipping images through tool results.
- Contrast: Devin's recorded browser sessions — the agent *showing* the e2e test passing — have
  no analog. The closest native evidence of verification is B's in-sandbox grader pattern
  (uncheatable SHA-pinned tests), which proves correctness but cannot demonstrate it visually.

**Ceiling:** e2e testing can be *run* (headless, self-assembled) but never *shown*. For a product
whose trust model depends on watching the agent verify its work, this is a from-scratch build.

## 8. How does the agent system organically integrate into an agentic PR review system?

The response half exists; the trigger half doesn't.

```text
   PR opened ──► webhook ──► review session wakes in seconds        (Devin)
   PR opened ──► ...nothing... ──► cron fires (≤60 s + jitter) ──►
     cold session ──► MCP-polls for new PRs ──► reviews             (Managed Agents)
```

- **Responding is at parity:** once a session is looking at a PR, it handles review organically —
  K4's session replied to an inline review comment and fixed the CI failure in the same run;
  interrupt-steering (0.5 s) lets a human redirect a review in flight.
- **Being triggered is class D:** there is no event ingress. A PR-review bot is a ≥1-minute cron
  deployment (H) that cold-starts a session per fire, MCP-polls for PRs needing review, and needs
  your own dedupe (no native memory of "already reviewed" beyond what you write to the Memory
  Store — and nothing injects that; the model must grep the mount, E).
- Review state across fires is manual: each fire is a fresh session with no continuity; threading
  "my previous review comments" back in is a Memory-Store-plus-prompt convention.

**Ceiling:** a credible-but-clunky polling review bot is buildable today; a *responsive* one
(sub-minute, deduped, stateful, identity-bearing) requires the ingress, supervisor, and identity
layers this platform doesn't have.

## Conclusion

Scoring the guiding questions:

| Question | Grade | One line |
|---|---|---|
| Per-engineer access to repos/services/envs | ✗ | one all-powerful Console-minted key per environment; identity = the vault PAT |
| Scaling to very high session volume | ~ | volume fine; every fleet control is yours to build |
| Reliable re-entry to a known-good environment | ~ | image + volume convention; no snapshot/verify/re-dispatch |
| Durable, permissioned, enterprise-safe MCP | ✓~ | excellent at agent level; no user-level identity |
| Tracking agent actions for deployment/audit | ✓~ | perfect event log; no effect joins, alerting, or forced attribution |
| Depth of SCM integration | ~ | MCP-deep; no native PR/CI/identity model |
| Browser access for demonstrable e2e testing | ✗ | tool types rejected; class D |
| Organic agentic PR review | ~ | responds at parity; can only be woken by cron |

**Is this production-grade remote development for agents?** As an *execution substrate* — yes,
and unusually honest about itself: immutable config, replayable history, cost to the cent. As a
*remote development platform* — no: it has no concept of an engineer, a repo permission, a
verified environment, a visual proof, or an event. Every one of those is the supervisor/identity/
ingress layer an enterprise would have to build — and that layer, not the agent loop, is the
product.
