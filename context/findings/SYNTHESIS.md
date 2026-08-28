# Swarm Synthesis — Clevin Managed Agents Ceiling

Maintained by the master orchestrator session. Rolls up child workstream findings as they land in
`context/findings/<workstream>-*.md`. Status: **in progress** — workstreams A, C, D, E, F, H, K
dispatched 2026-08-28; B, I, J gated.

## Workstream status

| WS | Topic | Status | Findings |
| --- | --- | --- | --- |
| A | Control plane & session semantics | **merged** (PR #12) | `A-control-plane.md` |
| B | Long-horizon agent quality | gated on A | — |
| C | Runtime reliability, recovery, tool surface | **merged** (PR #11) | `C-runtime-reliability-and-tool-surface.md` |
| D | Agent-as-code & configuration lifecycle | **merged** (PR #7) | `D-agent-as-code.md` |
| E | Native Memory Store | **merged** (PR #9) | `E-native-memory-store.md` |
| F | Built-in subagents | **merged** (PR #13) | `F-builtin-subagents.md` |
| H | Deployments & automation | **merged** (PR #8) | `H-deployments-automation.md` |
| I | Observability & economics | gated on A | — |
| J | Integrated gauntlet | gated on A, C, E, F, K | — |
| K | Devin-parity interaction model | PR #10 open (session finishing) | — |

## Parity table (current classes)

Classes: A = pure configuration; B = native extension point; C = partial; D = not achievable.
Unfilled rows are pending evidence — no class is assigned without it.

| Devin-like capability | Primitive | Class | Evidence / owner |
| --- | --- | --- | --- |
| Ticket in → CI-green PR out, unattended | Session + EnvironmentWorker + GitHub/Linear MCP | **C** (capped by C-2) | C §6: worker death mid-run silently parks the session (`requires_action` forever, no re-dispatch); native recovery mechanism exists (C-3) but no native trigger. J to demo the happy path |
| Long-horizon work across hours and compactions | Session state + compaction + Memory Store | **C** | A: Opus preserved an early constraint through two native compactions; Haiku terminated at 200k without compacting — model-dependent, no inspectable/restorable checkpoint. B (gated) owns quality over hours |
| Builds a plan, then revises it | System prompt + built-in subagents | **C** | F §8: planning is prompt-driven and works; roster added no measured quality (grader saturated); no plan artefact primitive |
| Parallel investigation, then synthesis | Built-in subagents | **A** | F §8: 7 concurrent children, conflicting results synthesised on evidence; depth capped at 1 (sub-subagents D); concurrent-edit conflicts D (last write wins) |
| Learns across tasks | Memory Store | **C** (session half); **A** (deployment half: recurring runs read/write store) | E §3; H §8. Storage/provenance A; nothing *pulls* knowledge — retrieval is the model grepping the mount |
| Recovers from crashed sandbox / failed tool | EnvironmentWorker + session APIs + webhooks | **A** (failed tool) / **D** (dead worker, unattended) | C §1–2: tool timeout yields a clean is_error result (A); a killed worker strands the session forever — nothing native notices or re-dispatches (D); manual `SessionToolRunner` re-attach recovers (C-3, class C) |
| Asks for help only when genuinely blocked | System prompt + tool design | **B** (mechanism, via K's ask-and-block) | Behavioural quality untested (balance); B gated |
| Mid-run steering | Session events (user.interrupt, message injection) | **A** (session) / **B** (per-child) | K PR #10: bare user.message rejected mid-tool-call; user.interrupt accepted in 0.5 s and agent genuinely re-plans. A: interrupt stops generation cleanly. Per-child interrupt named by platform, unexecuted (balance) |
| Ask-and-block, resume with workspace intact | Session idle state + worker lease/idle timeout | **B** | K PR #10: custom tool + requires_action blocks nearly free ($18/945 s, 20 s active); volume survives, container does not; waiting-on-human only detectable by matching stop_reason.event_ids to agent.custom_tool_use |
| Sleeps, wakes on new ticket/comment | Deployments (polling) + lifecycle webhooks | **A** (mechanism) / **C** (behaviour) | H §8: polling detects change in ~61 s; event ingress is D; per-fire amnesia |
| Responds to PR review comments, fixes CI | GitHub MCP + Deployment-driven polling | **C** (deployment half; K/J to demo end-to-end) | H §8: each fire is a cold session; loop re-derived from GitHub state |
| Playbooks: reusable named procedures | Skills | **C** | K PR #10: attached Skill is invisible to the model (volume files only, no listing tool, no prompt injection); one system-prompt paragraph pointing at /workspace/skills makes it usable |
| Knowledge auto-selected by repo/task scope | Memory Store (scoping weak point) | **D** (auto-selection) / **C** (in practice) | E §3: no scope field/selection/ranking; path hierarchy + one store per scope reaches the outcome at 200-entry scale |
| Warm environment (blueprint/snapshot) | Sandbox image + clevin-sessions volume | **B** | A: sandbox image + session volume are native environment extension points, but separate from the Anthropic session snapshot — no atomic history+filesystem checkpoint (D) |
| Self-improvement: writes back learnings | Memory Store writes + Skill upload | **C** (memory half) | E §3: clean write-back and self-correction when asked; no quarantine/deletion of superseded entries. K owns the Skill half |
| Session forking from a checkpoint | No known primitive | **D** (confirmed on all three sides) | H §8 (deployment); A (no clone/fork/checkpoint in session API); K PR #10 (K7 confirmed D, not built) |
| Attachments / multimodal input | Session message content | — (K final report pending) | K |
| Runs on a schedule | Deployments | **A** | H §8: POSIX cron + timezone + version pinning; ≥1-minute granularity; no concurrency control, retry, or session continuation (all D) |
| Observable, attributable run history | SSE + usage events + Console | **C** | A: version/session/model/tool/usage/compaction events attributable; sandbox process & filesystem transitions absent from Anthropic history. C §6: lease/heartbeat traffic detects strands externally. I (gated) owns depth |
| Per-task cost accounting | session.usage + budget events | **A** | A: per-request cache/token fields + cumulative usage, no Admin API needed; F: per-thread list_cost gives per-role attribution; H §8 |
| Fleet of agent variants managed as code | Agent versions + provisioner | **A** (essentially at parity; memory & deployment dimensions C) | D §3–4: reconstructible, drift detectable (B), canary/rollback A; no version aliases/channels/deletion (D); pinning ≠ behavioural reproducibility (C) |
| Browser / Computer Use | (rejected tool types) | **D** | Pre-verified (§4 of swarm prompt); workstream cancelled |

## Provenance ledger (rolled up)

- **D**: `packages/provision/src/drift.ts` + tests (drift detection — extends the provisioner, class B); drivers `experiments/D/*`; evidence JSONs. See D §5.
- **E**: probes `experiments/E/*` (SDK-driven Memory Store lifecycle/semantics/provenance/mount tests). See E §4.
- **H**: drivers `experiments/H/*` (deployment lifecycle, frequency/overlap, failure/auto-pause, memory continuity, subagents/outcome/self-hosted, polling wake). See H §10.
- **A**: probe `experiments/A/managed_agents_probe.py` + results JSONs. See A §Reproduction.
- **C**: drivers `experiments/C/*` (timeout, worker-kill, recovery, lease fencing, routing, concurrency); no product code. See C §3.
- **F**: `experiments/F/*` (harness, surface/delegation/fan-out/failure probes, report); roster version pinning landed in `packages/provision`. See F §12.
- **K** (PR open): `experiments/K/*`, `context/findings/K-parity-interaction.md`; `packages/provision` gains `parseSkillIds` (CLEVIN_SKILL_IDS) + one system-prompt paragraph for skill discovery — default behaviour unchanged.

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
- Dead-worker detection/re-dispatch: none — a session whose worker dies mid-tool-call sits in
  `requires_action` forever; recovery requires an external re-attach (C-2/C-3).
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
- K: longer ask-and-block replication was still parked at report time.
- B, I, J live experiments — gated workstreams cannot run until balance is restored.

## Open handoffs between workstreams

- E → A: "memory survives compaction" untested — 5.69M cache-read tokens produced no compaction
  event; A owns forcing/observing compaction.
- D: production v7 carries a system prompt not in the repo (drift recorded, not reconciled).
- H → K/J: PR-review/CI loop end-to-end demo; outcome-evaluation loop never reached terminal
  `passed` under sibling load (untested).

## Answer: the absolute ceiling of a cloud agent built purely on Managed Agents primitives

*Draft — pending K's final report and the gated B/I/J workstreams (blocked on Anthropic balance).*

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
   `requires_action` forever; nothing native notices or re-dispatches (C-2). "Ticket in →
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

Distance to Devin, in one sentence: Managed Agents can natively reproduce Devin's *execution*
(sandboxed multi-hour coding with steering, delegation, scheduling, and accounting) but not
Devin's *lifecycle* — the always-on, event-woken, crash-recovering, knowledge-primed loop around
that execution is exactly the layer the platform does not provide and that the rules of this
exercise correctly forbade rebuilding.
