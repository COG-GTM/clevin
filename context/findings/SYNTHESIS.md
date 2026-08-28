# Swarm Synthesis — Clevin Managed Agents Ceiling

Maintained by the master orchestrator session. Rolls up child workstream findings as they land in
`context/findings/<workstream>-*.md`. Status: **in progress** — workstreams A, C, D, E, F, H, K
dispatched 2026-08-28; B, I, J gated.

## Workstream status

| WS | Topic | Status | Findings |
| --- | --- | --- | --- |
| A | Control plane & session semantics | running | — |
| B | Long-horizon agent quality | gated on A | — |
| C | Runtime reliability, recovery, tool surface | running | — |
| D | Agent-as-code & configuration lifecycle | **merged** (PR #7) | `D-agent-as-code.md` |
| E | Native Memory Store | **merged** (PR #9) | `E-native-memory-store.md` |
| F | Built-in subagents | running | — |
| H | Deployments & automation | **merged** (PR #8) | `H-deployments-automation.md` |
| I | Observability & economics | gated on A | — |
| J | Integrated gauntlet | gated on A, C, E, F, K | — |
| K | Devin-parity interaction model | running | — |

## Parity table (current classes)

Classes: A = pure configuration; B = native extension point; C = partial; D = not achievable.
Unfilled rows are pending evidence — no class is assigned without it.

| Devin-like capability | Primitive | Class | Evidence / owner |
| --- | --- | --- | --- |
| Ticket in → CI-green PR out, unattended | Session + EnvironmentWorker + GitHub/Linear MCP | — | B/J |
| Long-horizon work across hours and compactions | Session state + compaction + Memory Store | — | A/B |
| Builds a plan, then revises it | System prompt + built-in subagents | — | F |
| Parallel investigation, then synthesis | Built-in subagents | — | F |
| Learns across tasks | Memory Store | **C** (session half); **A** (deployment half: recurring runs read/write store) | E §3; H §8. Storage/provenance A; nothing *pulls* knowledge — retrieval is the model grepping the mount |
| Recovers from crashed sandbox / failed tool | EnvironmentWorker + session APIs + webhooks | — | C |
| Asks for help only when genuinely blocked | System prompt + tool design | — | B/K |
| Mid-run steering | Session events (user.interrupt, message injection) | — | K |
| Ask-and-block, resume with workspace intact | Session idle state + worker lease/idle timeout | — | K |
| Sleeps, wakes on new ticket/comment | Deployments (polling) + lifecycle webhooks | **A** (mechanism) / **C** (behaviour) | H §8: polling detects change in ~61 s; event ingress is D; per-fire amnesia |
| Responds to PR review comments, fixes CI | GitHub MCP + Deployment-driven polling | **C** (deployment half; K/J to demo end-to-end) | H §8: each fire is a cold session; loop re-derived from GitHub state |
| Playbooks: reusable named procedures | Skills | — | K |
| Knowledge auto-selected by repo/task scope | Memory Store (scoping weak point) | **D** (auto-selection) / **C** (in practice) | E §3: no scope field/selection/ranking; path hierarchy + one store per scope reaches the outcome at 200-entry scale |
| Warm environment (blueprint/snapshot) | Sandbox image + clevin-sessions volume | — | C/D |
| Self-improvement: writes back learnings | Memory Store writes + Skill upload | **C** (memory half) | E §3: clean write-back and self-correction when asked; no quarantine/deletion of superseded entries. K owns the Skill half |
| Session forking from a checkpoint | No known primitive — likely class D | **D** (confirmed from deployment side; K to confirm session side) | H §8 |
| Attachments / multimodal input | Session message content | — | K |
| Runs on a schedule | Deployments | **A** | H §8: POSIX cron + timezone + version pinning; ≥1-minute granularity; no concurrency control, retry, or session continuation (all D) |
| Observable, attributable run history | SSE + usage events + Console | — | I |
| Per-task cost accounting | session.usage + budget events | **A** (via session; deployment roll-up is client-side) | H §8; I to confirm |
| Fleet of agent variants managed as code | Agent versions + provisioner | **A** (essentially at parity; memory & deployment dimensions C) | D §3–4: reconstructible, drift detectable (B), canary/rollback A; no version aliases/channels/deletion (D); pinning ≠ behavioural reproducibility (C) |
| Browser / Computer Use | (rejected tool types) | **D** | Pre-verified (§4 of swarm prompt); workstream cancelled |

## Provenance ledger (rolled up)

- **D**: `packages/provision/src/drift.ts` + tests (drift detection — extends the provisioner, class B); drivers `experiments/D/*`; evidence JSONs. See D §5.
- **E**: probes `experiments/E/*` (SDK-driven Memory Store lifecycle/semantics/provenance/mount tests). See E §4.
- **H**: drivers `experiments/H/*` (deployment lifecycle, frequency/overlap, failure/auto-pause, memory continuity, subagents/outcome/self-hosted, polling wake). See H §10.

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

## Open handoffs between workstreams

- E → A: "memory survives compaction" untested — 5.69M cache-read tokens produced no compaction
  event; A owns forcing/observing compaction.
- D: production v7 carries a system prompt not in the repo (drift recorded, not reconciled).
- H → K/J: PR-review/CI loop end-to-end demo; outcome-evaluation loop never reached terminal
  `passed` under sibling load (untested).

## Answer: the absolute ceiling of a cloud agent built purely on Managed Agents primitives

Pending synthesis of child findings.
