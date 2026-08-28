# Swarm Synthesis — Clevin Managed Agents Ceiling

Maintained by the master orchestrator session. Rolls up child workstream findings as they land in
`context/findings/<workstream>-*.md`. Status: **in progress** — workstreams A, C, D, E, F, H, K
dispatched 2026-08-28; B, I, J gated.

## Workstream status

| WS | Topic | Status | Findings |
| --- | --- | --- | --- |
| A | Control plane & session semantics | **merged** (PR #12) | `A-control-plane.md` |
| B | Long-horizon agent quality | **merged** (PR #17) | `B-long-horizon-quality.md` |
| C | Runtime reliability, recovery, tool surface | **merged** (PR #11) | `C-runtime-reliability-and-tool-surface.md` |
| D | Agent-as-code & configuration lifecycle | **merged** (PR #7) | `D-agent-as-code.md` |
| E | Native Memory Store | **merged** (PR #9) | `E-native-memory-store.md` |
| F | Built-in subagents | **merged** (PR #13) | `F-builtin-subagents.md` |
| H | Deployments & automation | **merged** (PR #8) | `H-deployments-automation.md` |
| I | Observability & economics | **merged** (PR #15) | `I-observability-and-economics.md` |
| J | Integrated gauntlet | **merged** (PR #16; self-healing addendum PR #18) | `J-integrated-gauntlet.md`, `J-self-healing.md` |
| K | Devin-parity interaction model | **merged** (PR #10) | `K-parity-interaction.md` |

## Parity table (current classes)

Classes: A = pure configuration; B = native extension point; C = partial; D = not achievable.
Unfilled rows are pending evidence — no class is assigned without it.

| Devin-like capability | Primitive | Class | Evidence / owner |
| --- | --- | --- | --- |
| Ticket in → CI-green PR out, unattended | Session + EnvironmentWorker + GitHub/Linear MCP | **C** | J §2: ambiguous ticket → CI-green PR #14 with 2 human touches (`ask_human` by design; `budget_reached` raise+nudge not). C §6: dead worker strands the run (D). B: 27-file migration, 12/13 runs 4/4, 0 nudges |
| Long-horizon work across hours and compactions | Session state + compaction + Memory Store | **C** | A: Opus preserved an early constraint through two compactions. B §3: hours-in-one-stretch yes (unattended, graded); across idle gaps no — 900 s gap ended retries_exhausted; compaction never triggered at 59k peak context |
| Builds a plan, then revises it | System prompt + built-in subagents | **A** (behaviour) / no plan artefact | J §2: parent re-planned on a child's contradicting report and rejected its false premise; B: mid-run requirement change absorbed, 0 nudges. F §8: no measurable roster benefit; plan lives only in the transcript |
| Parallel investigation, then synthesis | Built-in subagents | **A** | F §8: 7 concurrent children, conflicting results synthesised on evidence; depth capped at 1 (sub-subagents D); concurrent-edit conflicts D (last write wins) |
| Learns across tasks | Memory Store | **C** (session half); **A** (deployment half: recurring runs read/write store) | E §3; H §8. Storage/provenance A; nothing *pulls* knowledge — retrieval is the model grepping the mount |
| Recovers from crashed sandbox / failed tool | EnvironmentWorker + session APIs + webhooks | **A** (failed tool) / **D** (dead worker, unattended) | C §1–2: tool timeout yields a clean is_error result (A); a killed worker strands the session forever — detection is native (J2: lease expiry ~360 s, `actor: null`; narrow `retry_status: retrying` errors retried) but nothing re-dispatches (D); manual `SessionToolRunner` re-attach recovers (C-3, class C) |
| Asks for help only when genuinely blocked | System prompt + tool design | **A** (behaviour) / **B** (mechanism) | J §2: exactly 1 ask_human in 666 events, raised only after proving no policy existed; B: 0 nudges in all 10 completed runs. Mechanism needs a declared custom tool + operator loop (B) |
| Mid-run steering | Session events (user.interrupt, message injection) | **A** (session) / **B** (per-child) | K1: user.interrupt + user.message re-plans with full awareness; bare user.message rejected mid-tool-call. A: interrupt stops generation cleanly. Per-child interrupt named by platform, unexecuted (balance; F §9) |
| Ask-and-block, resume with workspace intact | Session idle state + worker lease/idle timeout | **B** | K2: answered after 15 and 75 min (sandbox torn down at 3600 s, volume file intact), resume 0.2 s, $18/945 s; waiting-on-human only detectable by matching stop_reason.event_ids to agent.custom_tool_use |
| Sleeps, wakes on new ticket/comment | Deployments (polling) + lifecycle webhooks | **C** | H §8 + K3: 1-minute cron floor honoured; every fire is a brand-new session; continuity only via Memory Store; no GitHub/Linear→Anthropic event path; /mnt/memory root read-only |
| Responds to PR review comments, fixes CI | GitHub MCP + Deployment-driven polling | **A** (in-session loop) / **C** (unattended trigger) | K4: one session took PR #6 red→green, replied to the inline review comment (10 MCP calls, 91 s active, $58 list). H §8: the unattended trigger is a cold polled session |
| Playbooks: reusable named procedures | Skills | **C** | K PR #10: attached Skill is invisible to the model (volume files only, no listing tool, no prompt injection); one system-prompt paragraph pointing at /workspace/skills makes it usable |
| Knowledge auto-selected by repo/task scope | Memory Store (scoping weak point) | **D** (auto-selection) / **C** (in practice) | E §3: no scope field/selection/ranking; path hierarchy + one store per scope reaches the outcome at 200-entry scale |
| Warm environment (blueprint/snapshot) | Sandbox image + clevin-sessions volume | **B** | A: sandbox image + session volume are native environment extension points, but separate from the Anthropic session snapshot — no atomic history+filesystem checkpoint (D) |
| Self-improvement: writes back learnings | Memory Store writes + Skill upload | **C** (memory yes, Skills no) | E §3 + K6: memory write-back measurably helps the next session (1 attempt/45 s/$12 vs 3/69 s/$17); Skill write-back has no agent-side primitive (D) |
| Session forking from a checkpoint | No known primitive | **D** (confirmed on all three sides) | H §8 (deployment); A (no clone/fork/checkpoint in session API); K PR #10 (K7 confirmed D, not built) |
| Attachments / multimodal input | Session message content | — (untested; balance exhausted before K reached it) | K §untested |
| Runs on a schedule | Deployments | **A** | H §8: POSIX cron + timezone + version pinning; ≥1-minute granularity; no concurrency control, retry, or session continuation (all D) |
| Observable, attributable run history | SSE + usage events + Console | **A** (attribution) / **C–D** (alerting) | I: full chain reconstructed from events.list alone; SSE median lag 9 ms; session cost == sum of thread costs. But no native alerting, no tool-call→sandbox join (D), no files-changed event (D), budget exhaustion only a webhook |
| Per-task cost accounting | session.usage + budget events | **A** | I: session cost == Σ thread costs; fleet roll-up of 164 sessions in 2 API calls. Cost-per-*successful*-task is D as configured. Absolute cost is the practical ceiling: J's one 15-min ticket on Opus + 3-agent roster = $716 list |
| Fleet of agent variants managed as code | Agent versions + provisioner | **A** (essentially at parity; memory & deployment dimensions C) | D §3–4: reconstructible, drift detectable (B), canary/rollback A; no version aliases/channels/deletion (D); pinning ≠ behavioural reproducibility (C) |
| Browser / Computer Use | (rejected tool types) | **D** | Pre-verified (§4 of swarm prompt); workstream cancelled |

## Provenance ledger (rolled up)

- **D**: `packages/provision/src/drift.ts` + tests (drift detection — extends the provisioner, class B); drivers `experiments/D/*`; evidence JSONs. See D §5.
- **E**: probes `experiments/E/*` (SDK-driven Memory Store lifecycle/semantics/provenance/mount tests). See E §4.
- **H**: drivers `experiments/H/*` (deployment lifecycle, frequency/overlap, failure/auto-pause, memory continuity, subagents/outcome/self-hosted, polling wake). See H §10.
- **A**: probe `experiments/A/managed_agents_probe.py` + results JSONs. See A §Reproduction.
- **C**: drivers `experiments/C/*` (timeout, worker-kill, recovery, lease fencing, routing, concurrency); no product code. See C §3.
- **F**: `experiments/F/*` (harness, surface/delegation/fan-out/failure probes, report); roster version pinning landed in `packages/provision`. See F §12.
- **K**: `experiments/K/*` drivers + evidence; `packages/provision` gains `parseSkillIds` (CLEVIN_SKILL_IDS) + one system-prompt paragraph for skill discovery — default behaviour unchanged, no production version mutated. See K §Provenance.

## Irreducible limitations (class D register)

- Browser and Computer Use: `browser_toolset_20260801` / `computer_toolset_20260801` rejected as
  invalid Managed Agents tool types (pre-verified readiness smoke test).
- Admin API unavailable (401/403; `/v1/organizations/webhooks` does not exist); per-session
  `session.usage` and budget events are the only cost instrument.
- Version aliases/named channels, version deletion/tagging, server-side fleet selection,
  deterministic (seeded) replay — absent from `agents.versions` (D).
- Memory Store: no scope/selection predicate, no version binding (`agents.*` has no `resources`),
  no per-path ACL, no subagent attribution (E).
- Deployments: no concurrency control, no run retry/backoff, no session continuation across fires,
  no external event ingress, no sub-minute schedules (H-10–14).
- Dead-worker recovery: the loop is self-diagnosing, not self-healing (J2). Detection is native
  (lease expiry ~360 s after last heartbeat, `actor: null`) and narrow transient errors are
  retried (`retry_status: retrying`), but nothing re-dispatches — reclaiming polls re-offer
  nothing, webhook replay spawns nothing, and a `billing_error`-exhausted session stays inert
  after the balance returns. The session sits in `requires_action` forever; recovery requires an
  external re-attach (C-2/C-3, J2-3/J2-4).
- Exactly-once tool side effects across a worker crash: at-least-once only (C-5).
- Mid-session update surface is tools/MCPs only; model/system/Skills/subagents are immutable
  mid-session (A).
- Atomic Anthropic-history + filesystem checkpoint; session fork/clone (A, H, K).
- Subagent recursion beyond depth 1; parent-side cancel of a hung child (F).
- Skills are not surfaced to the model natively — no listing tool, no prompt injection (K).

## Untested due to exhausted Anthropic prepaid balance (~02:43 UTC)

- C-10 malformed/oversized/duplicate tool_result injection (driver written, unrun).
- F: 5th grader arm, per-child `user.interrupt`, 64–256 KB payload rungs, parent compaction
  under children, hours-long repeated delegation.
- K: 4500 s ask-and-block replication (post-sandbox-teardown resumed work) caught mid-resume by billing_error; attachments/multimodal row never reached.
- Second exhaustion (04:46 UTC) after top-up: B's compaction/idle-gap-resume and interrupt arms; J's no-memory/no-subagents/chaos and compaction/steering arms (~$500 est.); I's remaining probes.
- New from the gated wave: `budget_reached` is recoverable natively (raise budget + one user.message resumes the same session/history/workspace — J #11, class A); subagent internal reasoning invisible from the parent stream (I-11, C); a child's write can poison the shared Memory Store, with parent-side adjudication the only native defence (J).

## Open handoffs between workstreams

- E → A: "memory survives compaction" untested — 5.69M cache-read tokens produced no compaction
  event; A owns forcing/observing compaction.
- D: production v7 carries a system prompt not in the repo (drift recorded, not reconciled).
- H → K/J: PR-review/CI loop end-to-end demo; outcome-evaluation loop never reached terminal
  `passed` under sibling load (untested).

## Answer: the absolute ceiling of a cloud agent built purely on Managed Agents primitives

*Based on A, C, D, E, F, H, K. Gated workstreams B, I, J are blocked on the exhausted Anthropic
balance; their results could refine (not likely overturn) the classes below.*

**The ceiling is a competent single-shot task executor with strong fleet/config management and
cost accounting, but without unattended durability, event-driven wakefulness, or native
knowledge/skill surfacing.** Concretely:

What reaches parity through pure configuration or native extension points (A/B):
- Agent-as-code: immutable versions, canary/rollback, drift detection (D), roster version pinning
  for subagents (F) — arguably *beyond* Devin's config ergonomics.
- Parallel investigation with synthesis at depth 1, with per-role cost attribution (F).
- Mid-run steering via `user.interrupt` (0.5 s to accept) and ask-and-block via a custom tool +
  `requires_action`, both nearly free while blocked (K, A).
- Scheduled runs, per-task cost accounting, persistent workspace volume (H, A).
- Clean failed-tool semantics: timeouts and errors return reasoned, recoverable results (C).

What caps the ceiling (irreducible D, or C with model-dependent behaviour):
1. **Unattended durability is the hardest cap.** A dead worker strands a session in
   `requires_action` forever; detection is native (~360 s lease expiry) but nothing re-dispatches
   (C-2, J2). "Ticket in →
   CI-green PR out, unattended" is therefore capped at C regardless of how well the happy path
   works: any mid-run infrastructure failure silently kills the run.
2. **No event ingress.** Wake-on-event is polling at ≥1-minute granularity with per-fire amnesia;
   each deployment fire is a cold session that must re-derive its loop from external state (H).
3. **No session continuity primitives.** No fork/clone/checkpoint (A, H, K), no atomic
   history+filesystem snapshot, no session continuation across deployment fires, no mid-session
   change of model/system/Skills/subagents (A).
4. **Knowledge and skills are push-less.** Memory Store has no scoping/selection/ranking — the
   model must choose to grep the mount (E). Attached Skills are invisible without a
   system-prompt workaround (K). Both reach usable behaviour only through prompting, i.e. they
   are model-dependent C, not platform A.
5. **No browser/Computer Use** — the tool types are rejected outright (pre-verified D).

```text
                Devin                          Managed Agents ceiling
  ┌─────────────────────────────┐        ┌─────────────────────────────┐
  │ LIFECYCLE LAYER             │        │ LIFECYCLE LAYER             │
  │  webhook wake (seconds) ────┼──►     │  cron polling (≥60 s), D    │
  │  crash detect + re-dispatch │        │  dead worker strands run, D │
  │  session fork / checkpoint  │        │  no fork/clone/snapshot, D  │
  │  scoped knowledge push      │        │  model greps the mount, C   │
  ├─────────────────────────────┤        ├─────────────────────────────┤
  │ EXECUTION LAYER             │        │ EXECUTION LAYER             │
  │  sandboxed multi-hour runs  │  ≈     │  sessions + volume, A/B     │
  │  steering / ask-and-block   │  ≈     │  interrupt + custom tool, A/B│
  │  delegation (nested)        │  ~     │  subagents (depth 1), A     │
  │  agent config as code       │  ≈     │  immutable versions, A      │
  │  cost accounting            │  ≈     │  usage + per-thread cost, A │
  └─────────────────────────────┘        └─────────────────────────────┘
        ≈ at parity   ~ partial   D = missing primitive, forbidden to rebuild
```

Distance to Devin, in one sentence: Managed Agents can natively reproduce Devin's *execution*
(sandboxed multi-hour coding with steering, delegation, scheduling, and accounting) but not
Devin's *lifecycle* — the always-on, event-woken, crash-recovering, knowledge-primed loop around
that execution is exactly the layer the platform does not provide and that the rules of this
exercise correctly forbade rebuilding.
