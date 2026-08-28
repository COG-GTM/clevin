# J — Integrated gauntlet

**Question.** Compose the winning configurations from A, C, E, F and K into one agent version and
make it do a broad, ambiguous coding task end to end: inspect the project, plan, use Memory Store
prior knowledge, delegate to built-in subagents, edit in the self-hosted Modal sandbox, run tests,
review its own work, ask a human only when genuinely blocked, and land a CI-green PR — then score
every component on Managed Agents provenance.

**Headline.** The integrated native configuration got a broad, defect-diagnosis ticket to a
**CI-green PR (#14) with two human interventions**, one of which was the intended blocking question.
Nothing in the loop needed a custom orchestrator: coordinator roster, Memory Store mount, Skill,
`ask_human` custom tool, Linear/GitHub MCP and the self-hosted `EnvironmentWorker` composed without
conflict on the first attempt. **The binding limit was not capability, it was economics and budget
mechanics**: the run cost **$716 list** for one ~15-minute ticket, hit `budget_reached` at $414
before it could push, and needed a `sessions.update(budget=...)` plus a nudge to continue. The
Anthropic **balance was then exhausted mid-review**, which is why only one arm ran — the reduced
arms, compaction survival and the chaos arm are recorded as untested below, not as classes.

- Agent: `agent_01Bj4NG7ZT2gkxvgzrcFY5r9` (temporary, archived) — coordinator over
  `agent_01DMSULxChYEMfSVjeVfhBQZ` (explorer), `agent_016nr9DwVkpvDBAESYJ56yjc` (test debugger),
  `agent_01DCXuCDuebYuVVexQcxwUYF` (reviewer)
- Session: `sesn_01EGsNu8uYt4SnS36Bk1JKvN` — 666 events, 3 threads, 0 compactions,
  `active_seconds` 675.2, `list_cost` **$716**
- Memory Store `memstore_01F13Wi2Vd3iipGscxSUSkM8` (temporary, archived), Skill
  `skill_01VxWTuMB2kPEk5293F3uPPL` (temporary, deleted), environment `env_0152FZKRpy9f8uVw38Guzosy`,
  Modal sandbox `sb-aMJS9gYX270pQQEsi22IVa`
- Ticket `HUM-16` → PR <https://github.com/COG-GTM/clevin/pull/14>, check `fixture-check`
  conclusion `success`
- Raw evidence: `experiments/J/evidence/gauntlet-full-20260828T043244Z-c4297e.json`,
  `experiments/J/evidence/memory-after-full.json`,
  `experiments/J/evidence/chaos-probe-full-session.json`

## 1. What was run

The fixture (`experiments/J/fixture/`) is a "monthly revenue report cannot be trusted" package with
one red test and four unstated defects: `str.split(",")` parsing that shifts columns on quoted
descriptions, binary-float money accumulation, quadratic month grouping, and a divide-by-zero for an
empty month — plus a **deliberately undecidable** rounding policy whose README says the decision was
never made and must not be guessed. The ticket (created in Linear by `experiments/J/make_ticket.py`)
names none of the defects: it says the report is untrustworthy and slow, requires tests, requires a
PR against `swarm/j-gauntlet-base` with a commit tag, and forbids weakening test expectations.

`experiments/J/gauntlet.py --arm full` then composed the integrated version and supervised it,
answering the one blocking question and collecting evidence from native surfaces only.

Observed timeline (session event timestamps, UTC 2026-08-28):

| t+ | What happened |
| --- | --- |
| 00:00 | Session created; Linear `get_issue`/`list_comments`, issue moved to In Progress via MCP |
| 01:11 | `ls /workspace/skills` + `ls -R /mnt/memory` — the agent discovered both by looking, per the prompt paragraph |
| 01:11 | Explorer child `sthr_01QYpqTQjoTWTs8i3ufwN7QB` created and briefed (`sevt_01584h3XBU9rTKXBok1Qbu4m`) |
| 04:26 | `ask_human` (`sevt_01BVR6grQLdHxMuWj9F9pMXM`) — rounding policy, 4 options, after proving no policy exists in the repo |
| 04:27 | Answered with `user.custom_tool_result` (`sevt_013W7wA3fiGHuT6yG1sU4C5o`); work resumed |
| 07:45 | 23 tests pass; agent benchmarks the quadratic fix (0.074→0.316 s old vs flat 0.055 s new) |
| 07:52 | `session.status_idle` / `budget_reached` at $414 — **stopped one step before the push** |
| 09:15 | `sessions.update(budget=900)` (`sevt_011CeUXtC4gHWoPxo69d4ch2`) + one `user.message`; run continues in the same session |
| 12:35 | Explorer reports the working tree "changed under it" and accuses the parent of a fabricated policy; parent adjudicates and rejects it |
| 12:50 | Reviewer child `sthr_01DeNNaQWiLJDLcHTfgsKGQE` created; PR #14 opened via GitHub MCP `create_pull_request` |
| 13:20 | `pull_request_read get_check_runs` polled to `success`; parent corrects the false memory entry |
| 13:46 | `session.error` `billing_error` ×2 → `retries_exhausted`; the reviewer child died with the balance |

Resulting diff (agent's own work): 462 insertions across 4 files —
`decimal.Decimal` accumulation with a strict amount grammar, `csv`-module parsing, one-pass grouping,
explicit `ValueError` for malformed dates, a `format_amount()` presentation seam, the policy recorded
in `README.md` and `aggregate.py`, and 273 lines of new tests.

## 2. Classifications

Every class below is scored on what *this integrated run* showed. Where the integrated run did not
exercise a row, it is in §4 (untested), not here.

| # | Capability | Primitive | Class | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Ticket in → CI-green PR out, unattended | Session + `EnvironmentWorker` + MCP | **C** | PR #14 green, but 2 interventions: `ask_human` answer (by design) and a `budget_reached` budget raise + nudge (not by design) |
| 2 | Builds a plan, then revises it as facts change | System prompt + subagents | **A** | Parent re-planned after the explorer's contradicting report `sevt_01Pu95pZucmQiat5tKzmpxLN`, and correctly rejected its false premise |
| 3 | Parallel investigation, then synthesis | Built-in subagents | **A** (depth 1) | 2 children, `session.thread_created` ×2, `agent.thread_message_received` ×3, per-thread cost visible |
| 4 | Learns across tasks — write-back | Memory Store writes | **A** | `memory-after-full.json`: agent rewrote `failures.md` and `conventions.md` with the decided policy and a generalised lesson |
| 5 | Learns across tasks — retrieval | Memory Store read | **C** | Retrieval only happens because the prompt tells the model to `ls -R /mnt/memory`; nothing is injected. Its first `cat` used a case-folded mount path and failed, needing a `find` to recover |
| 6 | Asks for help only when genuinely blocked | System prompt + tool design | **A** | Exactly 1 `agent.custom_tool_use` in 666 events, raised only after a repo-wide grep proved no policy existed (proof recorded in memory) |
| 7 | Ask-and-block, resume later with workspace intact | Session idle + worker lease | **B** | Session sat idle 84 s at `budget_reached`; sandbox `sb-aMJS9gYX270pQQEsi22IVa` still alive with `git HEAD 7e65674` and `/workspace/skills/` intact; work continued in place |
| 8 | Responds to CI on its own PR | GitHub MCP | **A** | `pull_request_read get_check_runs` polled 4× to `conclusion: success`; PR base/mergeable verified by the agent itself |
| 9 | Playbooks | Skills | **C** | Skill materialised at `/workspace/skills/revenue-report-hardening/SKILL.md` and was read and followed — but only because the system prompt says to look; there is no skill-listing surface |
| 10 | Per-task cost accounting | `session.usage` + budget | **A** | `list_cost` $716 total, split per thread: parent $413, explorer $255, reviewer $47 |
| 11 | Budget stop is recoverable | `sessions.update` | **A** (new) | `budget_reached` is not terminal: raising `budget` + one `user.message` resumed the same session, history, roster and workspace |
| 12 | Observable, attributable run history | Events + threads | **C** | Parent events, thread costs and stop reasons are all native; child *internal* reasoning is not in the parent's event list, and the only signal of the reviewer's death was a `[child … failed: …]` text message |
| 13 | Warm environment | Sandbox image + volume | **B** | Repo pre-cloned at `/workspace/repos/clevin` on branch `clevin/j-full-529843` from the prebuilt image; no clone step in the agent's own timeline |
| 14 | Unattended recovery of a dead worker | Lifecycle webhooks | **D** (confirms C) | With the session idle and no queued work, the signed `session.status_run_started` replay returned `{"spawned":[]}` and produced **zero** new events; killing the sandbox did not cause re-provisioning |

### Notable integration findings

**Conflicting subagent output is a real hazard, and the parent handled it natively.** The explorer,
seeing the parent's own edits appear underneath it, concluded the parent had fabricated a policy —
and wrote that accusation into the *shared* Memory Store. The parent detected the false premise
(a read-only child cannot see the parent's `ask_human` exchange), rejected the conclusion, kept the
child's accurate findings, and **corrected the poisoned memory entry** before finishing. This is the
strongest single observation in J: shared-store writes by children can poison future sessions, and
the only native defence is parent-side adjudication driven by the prompt.

**The cost curve, not the capability curve, is the ceiling.** One 15-minute ticket on Opus with a
3-agent roster cost $716 list, with 6.4 M cache-read tokens. A Devin-scale workload of dozens of
tickets a day is not economically expressible at this configuration, and the reduced arms that would
have quantified the roster's marginal cost could not be run once the balance emptied.

## 3. Distance to Devin

- **Ticket → green PR**: functionally there, mechanically not. Devin does not stop at a spend ceiling
  and wait for a human to raise it; here `budget_reached` landed one tool call before the push and
  needed an operator. Distance = a native "raise and continue" policy (or a budget high enough that
  it never fires).
- **Plan/revise, parallel investigation**: at parity for depth-1 fan-out on a single task.
- **Learning across tasks**: writes are at parity and better than expected (self-correcting a wrong
  entry); retrieval is behind — Devin injects scoped knowledge, Managed Agents makes the model grep a
  mount, so knowledge only lands when the prompt insists.
- **Ask for help**: at parity in behaviour (one question, correctly chosen), behind in packaging —
  it needs a hand-declared `ask_human` custom tool and an operator loop watching for
  `requires_action`.
- **Playbooks**: content parity, discovery gap — a Skill the model is not told to look for is dead
  weight.
- **CI loop**: at parity inside a live session; still no event path to wake on a later CI/review
  event (H/K own that).
- **Crashed-worker recovery**: unchanged from C — no unattended path; this is where Devin is
  structurally ahead.
- **Cost accounting**: ahead of parity in granularity (per-subagent thread costs), far behind in
  absolute cost.

## 4. What was not tested, and why

- **Reduced arms (`no-memory`, `no-subagents`)** — implemented and runnable, never run: the Anthropic
  credit balance was exhausted at 04:46 UTC mid-run (`session.error` `billing_error`,
  `retry_status: exhausted`), and §7 forbids purchasing credits. The intended design was a fixed
  $250 budget per arm compared on milestones reached, which needs ~$500 more balance.
- **Chaos arm (worker killed mid-implementation)** — the driver supports it
  (`--arm chaos --chaos-after N`), but a chaos run needs model tokens to demonstrate recovery. The
  post-hoc probe in `chaos-probe-full-session.json` (kill the live sandbox on an idle session, replay
  the signed webhook) is reported above as confirmation of C's class D only: with no queued work the
  webhook is a no-op. **Whether an integrated run recovers from a mid-tool-call worker kill remains
  untested by J.**
- **Compaction survival** — 0 `compaction` events in 675 s of active work. The integrated task simply
  finished inside one context window, so J adds nothing to A's compaction findings.
- **Mid-run steering** — the driver injects `user.interrupt` + `user.message` at `--steer-after`; the
  run finished before the timer fired, so the "revisit implementation after feedback" leg is carried
  only by the resume nudge, not by a true mid-run steer. K's steering findings stand unextended.
- **Repeated runs / variance** — one arm, one run. No variance estimate.
- **Deliberately not built (class D)**: no external retry/orchestration wrapper around the dead
  worker, no custom knowledge injector to fix Memory Store retrieval, no cross-session budget
  manager, no session fork. Each of those would have converted a D/C into a fake B.

## 5. Reproduction

```bash
# fixture red state (2 pass, 1 fail) before the agent touches it
uv run --project runtime python -m pytest experiments/J/fixture/tests -q

# one integrated arm end to end (creates temporary Linear issue, memory store,
# skill, 3 subagents, coordinator agent, self-hosted session; cleans up after)
uv run --project runtime python experiments/J/gauntlet.py --arm full --budget 900
uv run --project runtime python experiments/J/gauntlet.py --arm no-memory --budget 250
uv run --project runtime python experiments/J/gauntlet.py --arm no-subagents --budget 250
uv run --project runtime python experiments/J/gauntlet.py --arm chaos --chaos-after 600

# continue a run that stopped at budget_reached (native budget raise + nudge)
uv run --project runtime python experiments/J/gauntlet.py \
  --resume-file gauntlet-full-<run-id>.json --budget 900
```

Requires `ANTHROPIC_API_KEY`, `ANTHROPIC_WEBHOOK_SECRET`, `CLEVIN_ENVIRONMENT_ID`, `CLEVIN_VAULT_ID`,
`GITHUB_TOKEN`, `LINEAR_API_KEY`, and (for the chaos arm) `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`
bound into the environment. Every run writes a self-contained evidence JSON under
`experiments/J/evidence/`.

## 6. Provenance ledger

| Code | Primitive it configures / observes | How Managed Agents consumes it | Why configuration alone was insufficient |
| --- | --- | --- | --- |
| `experiments/J/j_common.py` — `AGENT_TOOLSET`, `READ_ONLY_TOOLSET`, MCP blocks, `ASK_TOOL`, `SUBAGENTS`, `system_prompt()`, `skill_archive()` | Agent configuration, built-in toolsets, MCP config, custom tools, Skills, coordinator roster | Passed verbatim to `beta.agents.create`/`beta.skills.versions.create` | These are the configuration payloads under test; the prompt is read out of `packages/provision/src/agent-definition.ts` so the experiment cannot drift from the shipped definition |
| `j_common.py` — `events()`, `pending_ask()`, `is_finished()`, `answer_ask()`, `steer()` | Session events (`sessions.events.list/send`), `requires_action`, `user.custom_tool_result`, `user.interrupt` | Reads and writes the native event stream | Observing and answering a blocking custom tool has no CLI/Console equivalent; an operator loop is the only way to exercise it |
| `j_common.py` — `modal_state()`, `kill_sandbox()`, `replay_webhook()` | Self-hosted `EnvironmentWorker` + `session.status_run_started` lifecycle webhook | Terminates the Modal sandbox behind a live worker; posts a signed webhook to the deployed handler | Fault injection cannot be requested through the API; the webhook replay is the only native re-dispatch path |
| `j_common.py` — `gh()`, `git()`, `memory_entries()`, `Ledger` | Verification of MCP-side effects and Memory Store contents; temporary-resource accounting | Read-only checks of what the agent's native tool calls actually produced | Claims about a green PR or a memory write must be verified outside the agent's own assertion |
| `experiments/J/gauntlet.py` — `arm_config`, `build`, `seed_memory`, `start_session`, `supervise`, `collect`, `cleanup`, `resume` | Agent versions, Memory Stores, sessions on a self-hosted environment, budget updates, threads, usage | Creates the native resources, drives one session, then reads native evidence back | This is the experiment supervisor, not product code: it configures primitives and observes them. It must never ship inside Clevin (§2) |
| `experiments/J/make_ticket.py` | Linear ingress that the agent consumes through the Linear MCP | Creates the issue the agent reads with `get_issue` | The gauntlet needs a real, ambiguous ticket with measurable constraints |
| `experiments/J/fixture/**`, `.github/workflows/j-gauntlet-fixture.yml` | The workload and the required GitHub check the agent must turn green | Read and edited by the agent inside the sandbox; the check is polled through GitHub MCP | A broad diagnosis task and a real CI signal cannot be simulated |
| `experiments/J/skill/revenue-report-hardening/SKILL.md` | Skills | Uploaded as a Skill version and materialised into `/workspace/skills/` | Skill content is the thing under test |

No line of J code implements planning, delegation, memory, scheduling or recovery logic on Clevin's
behalf; the supervisor only supplies operator inputs (an answer, a budget raise, a fault) and reads
native surfaces.

## 7. Cleanup ledger

| Resource | Action | Result |
| --- | --- | --- |
| `agent_01Bj4NG7ZT2gkxvgzrcFY5r9` (coordinator) | archive | archived 04:40:44Z (verified via `agents.retrieve`) |
| `agent_01DMSULxChYEMfSVjeVfhBQZ`, `agent_016nr9DwVkpvDBAESYJ56yjc`, `agent_01DCXuCDuebYuVVexQcxwUYF` | archive | archived 04:40:43Z (verified) |
| `skill_01VxWTuMB2kPEk5293F3uPPL` | delete | first attempt failed — *a skill cannot be deleted while versions exist*; driver fixed to delete versions first; skill now returns 404 |
| `memstore_01F13Wi2Vd3iipGscxSUSkM8` | contents captured, then archive | archived (contents in `evidence/memory-after-full.json`) |
| `sesn_01EGsNu8uYt4SnS36Bk1JKvN` | retained as evidence | idle, no compute attached |
| Modal sandbox `sb-aMJS9gYX270pQQEsi22IVa` | terminated during the chaos probe | terminated (exit 137); `Sandbox.list` now empty |
| Linear `HUM-16` | cancelled with a comment linking PR #14 | state `Canceled` |
| Branch `clevin/j-full-529843` + PR #14 (base `swarm/j-gauntlet-base`) | retained as evidence | open; it is the agent's own output and is not merged anywhere |
| Temporary dry-run resources (`…-dryrun-…` agent, memory store, skill) | archived / deleted | all cleaned; the leftover dry-run skill was deleted after the version-delete fix |

Nothing production-side was mutated: the production agent, its versions, `env_0152FZKRpy9f8uVw38Guzosy`,
`memstore_01JCboyFNzqNzucVq3xFpnYZ`, the vault, the Modal app, image and volume were used read-only.

## 8. Integrated ceiling

Composed natively, Clevin can take an ambiguous "this is broken and slow" ticket, discover its own
instructions (AGENTS.md, Skill, Memory Store) because the prompt tells it to look, diagnose four
unstated defects, escalate the one genuinely undecidable question, implement and test a good fix in a
warm self-hosted sandbox, get an adversarial second opinion, land a CI-green PR, and leave corrected
knowledge behind for the next run — with one unintended human touch. The gap to Devin is not
reasoning or tool reach; it is **three mechanical properties**: spend ceilings that stop a run mid-flight
and need a human to lift, knowledge that is only retrieved if the model remembers to grep for it, and
a dead worker that nothing re-dispatches. The first is a policy knob, the second is a prompt tax, and
the third is the only one that is genuinely class D.
