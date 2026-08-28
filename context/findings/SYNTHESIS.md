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
| D | Agent-as-code & configuration lifecycle | running | — |
| E | Native Memory Store | running | — |
| F | Built-in subagents | running | — |
| H | Deployments & automation | running | — |
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
| Learns across tasks | Memory Store | — | E |
| Recovers from crashed sandbox / failed tool | EnvironmentWorker + session APIs + webhooks | — | C |
| Asks for help only when genuinely blocked | System prompt + tool design | — | B/K |
| Mid-run steering | Session events (user.interrupt, message injection) | — | K |
| Ask-and-block, resume with workspace intact | Session idle state + worker lease/idle timeout | — | K |
| Sleeps, wakes on new ticket/comment | Deployments (polling) + lifecycle webhooks | — | H/K |
| Responds to PR review comments, fixes CI | GitHub MCP + Deployment-driven polling | — | K |
| Playbooks: reusable named procedures | Skills | — | K |
| Knowledge auto-selected by repo/task scope | Memory Store (scoping weak point) | — | E |
| Warm environment (blueprint/snapshot) | Sandbox image + clevin-sessions volume | — | C/D |
| Self-improvement: writes back learnings | Memory Store writes + Skill upload | — | K |
| Session forking from a checkpoint | No known primitive — likely class D | — | K |
| Attachments / multimodal input | Session message content | — | K |
| Runs on a schedule | Deployments | — | H |
| Observable, attributable run history | SSE + usage events + Console | — | I |
| Per-task cost accounting | session.usage + budget events | — | I |
| Fleet of agent variants managed as code | Agent versions + provisioner | — | D |
| Browser / Computer Use | (rejected tool types) | **D** | Pre-verified (§4 of swarm prompt); workstream cancelled |

## Provenance ledger (rolled up)

Pending child reports.

## Irreducible limitations (class D register)

- Browser and Computer Use: `browser_toolset_20260801` / `computer_toolset_20260801` rejected as
  invalid Managed Agents tool types (pre-verified readiness smoke test).
- Admin API unavailable (401/403; `/v1/organizations/webhooks` does not exist); per-session
  `session.usage` and budget events are the only cost instrument.

## Answer: the absolute ceiling of a cloud agent built purely on Managed Agents primitives

Pending synthesis of child findings.
