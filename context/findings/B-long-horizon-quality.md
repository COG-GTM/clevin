# Workstream B — Long-horizon agent quality

Does the Managed Agents model support long-running, minimally supervised engineering work? Tested
with an objectively graded multi-file migration run as 13 graded native sessions under `experiments/B/`.
Every number below comes from a session's own `sessions.events.list` history
(`experiments/B/artifacts/<arm>/<run>/events.json`) and its `session.usage` event; the roll-up is
`experiments/B/artifacts/summary.md`.

**Headline.** On a task that fits in one context window, the native loop is genuinely
minimally-supervised: **12 of 13 graded runs scored 4/4 on an objective grader, and all 10 that ran
to `end_turn` did so with 0 human nudges**, retaining a constraint stated once in the opening message and never repeated. That is the
strong result, and it is class A — no extension point was needed, only an agent, a session, the
built-in toolset and a system prompt.

Everything that makes work *long*-horizon rather than merely *large* is weaker. The three levers the
brief asks about — plan-oriented prompting, built-in subagents, Memory Store — produced **no
measurable quality difference** on this workload; they only moved cost (subagents +20 %, memory
+50 % list cost for the same 4/4). And the one arm that tested duration directly failed: after a
**15-minute idle gap a resumed session accepted the message but never produced a turn**, ending
`retries_exhausted` (class C — the session object survives, the ability to continue it did not).
The honest summary is that Managed Agents gives you a very good *single-stretch* worker, and the
"across hours and compactions" half of the Devin row is not demonstrated.

---

## 1. The workload

`experiments/B/fixture/` + `experiments/B/fixture_gen.py` build a Python package
(`acme_billing`) that must be migrated off `float` money onto `Decimal`/`Money` with
`ROUND_HALF_UP`:

- **27 source files** the agent must touch: 9 hand-written modules (cart, invoice, discounts,
  legacy, reports, catalog, cli, money) plus 18 generated regional modules `regions/r00..r17.py`,
  each with its own rate table and a deliberate legacy exception that must *not* be migrated.
- **Protected files** (`tests/test_contract.py`, `tests/test_smoke.py`, `tests/test_wide.py`,
  `grade.py`) that the agent is forbidden to modify — SHA-checked by the grader.
- **`grade.py`**, an in-sandbox objective grader scoring 4 checks: tests pass, protected files
  intact, the standing constraint marker present on every touched file, and no float in the money
  path. It prints a JSON blob the harness lifts out of the native `agent.tool_result` event, so the
  score is never the model's self-report.
- **Standing constraints** stated once, in the first message, and never restated (`prompts.py`):
  C1 marker line, C2 protected files, C3 Decimal/ROUND_HALF_UP, C4 remember release codename
  `RELEASE_CODENAME_INDIGO_9`, C5 stay in the workspace. C4 is a pure recall probe with no
  functional effect, so it measures constraint retention independently of task success.

The reference solution (`reference_solution.py`) scores 4/4, 16/16 tests — the grader is reachable.
The fixture ships to the sandbox as a **native uploaded file resource**, not via git.

> Fixture tests use stdlib `unittest`, not pytest: the cloud sandbox image has no pytest and the
> workload forbids network/package installs. This is an adaptation to the environment, not a
> relaxation — the protected-file SHA check still holds.

---

## 2. Results

`experiments/B/artifacts/summary.md`; "core" = the 9-module first workload, "wide" = the full
27-file one. Cost is the session's own `list_cost`, USD.

| arm | workload | session | outcome | score | nudges | tools | compactions | elapsed s | cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| b1_baseline | core | `sesn_014DCfuUvsNpkxGGjQWHEJUQ` | pass | 4/4 | 0 | 15 | 0 | 124 | 26 |
| b1_baseline | wide | `sesn_01UE36DCGwwQtwTrxCGtyfdw` | pass | 4/4 | 0 | 34 | 0 | 355 | 56 |
| b1_repeat | core | `sesn_011Kqdd2zcwVzwdMMcDYTXbX` | pass | 4/4 | 0 | 18 | 0 | 154 | 32 |
| b1_repeat | wide | `sesn_01Ti1qMo3nuztoxfx6cYY6bb` | pass | 4/4 | 0 | 34 | 0 | 279 | 41 |
| b2_planned | core | `sesn_015FewBkngdUWXbEJ6A27EZ6` | pass | 4/4 | 0 | 15 | 0 | 139 | 28 |
| b2_planned | wide | `sesn_011fwZNLZZPBzoGauFzdWQer` | pass | 4/4 | 0 | 25 | 0 | 294 | 43 |
| b3_subagents | wide | `sesn_01GaubMzQNcR9AfkigieR2X8` | pass | 4/4 | 0 | 26 | 0 | 340 | 50 |
| b4_memory | core | `sesn_017GjF4Zxx8KP2mkfq7PxbEG` | pass | 4/4 | 0 | 23 | 0 | 169 | 36 |
| b4_memory | wide | `sesn_0173N5Tn7J7Tmau5H1ktx3Th` | 4/4 then `budget_reached` | 4/4 | — | 40 | 0 | — | 61 |
| b5_resume | wide | `sesn_012LhdngwoQCEWot586kMghx` | **`retries_exhausted` after idle gap** | 4/4 | — | 28 | 0 | — | 48 |
| b6_changing | wide | `sesn_01JZyfBFEttydpgbyDQVctBc` | pass | 4/4 | 0 | 31 | 0 | 367 | 55 |
| b7_interrupt | wide | `sesn_015eCLRAcPtEFt8DKaHaZTTZ` | `budget_reached`, no interrupt fired | **3/4** | — | 32 | 0 | — | 61 |
| b8_compaction | wide | `sesn_01WxxX6jTBjEHKjWBPu2XK1x` | pass | 4/4 | 0 | 36 | **0** | 370 | 61 |
| b9_toolfail | wide | `sesn_01MfEiwmPsGrHvyLGncqRHPa` | pass | 4/4 | 0 | 32 | 0 | 390 | 62 |

Derived measures:

- **Completion**: 12/13 graded runs 4/4. The single functional miss is b7, which passed 16/16 tests
  and the Decimal check but hit its budget before putting the C1 marker on every touched file
  (`constraint_marker_on_touched_files: false`) — degradation under a hard stop is partial, not
  catastrophic.
- **Human interventions**: 0 nudges in every arm that ran to `end_turn`. The harness was willing to
  nudge (`prompts.NUDGE`) on idle-without-completion and never had to.
- **Constraint retention**: protected-file SHAs intact in **13/13** graded sessions — the agent never
  edited a test to make it pass. Codename recall answered correctly in **6/6** wide runs where the
  probe was delivered before the session ended (the `False` rows are runs whose probe was never
  serviced, not wrong answers).
- **Repeated mistakes / regression rate**: 0 runs regressed a previously passing test; the grader's
  test tail shows 16/16 in every wide run that reported. The recurring *tool*-level mistake is the
  seed path — 4 runs first tried `/workspace/seed/...` and got one `is_error` bash result before
  finding `/mnt/session/uploads/...`. One error, self-corrected, in every case.
- **Plan stability**: b6 absorbed a new requirement injected mid-run (audit line + `ROUNDING_POLICY`
  constant) and still finished 4/4 without abandoning completed work.
- **Variance**: two wide baseline-equivalent runs (b1_baseline, b1_repeat) differ 279 s vs 355 s and
  $41 vs $56 for identical 4/4, 34-tool-call outcomes — roughly ±25 % on time and cost, 0 % on
  outcome. Small n; treat as an order of magnitude, not a distribution.

---

## 3. Classification

| Capability | Class | Evidence |
| --- | --- | --- |
| Large multi-file refactor in one unattended session | **A** | 27 files, 0 nudges, objective 4/4 — `sesn_01UE36DCGwwQtwTrxCGtyfdw`, `sesn_01Ti1qMo3nuztoxfx6cYY6bb`. Pure agent+session+`agent_toolset_20260401` config |
| Constraint retention across a whole session | **A** | C1–C3 honoured and C4 recalled verbatim at the end of ~35-tool-call sessions with no restatement |
| Objective, model-independent grading of a run | **A** | grader run in-sandbox; harness reads only `agent.tool_result` events |
| Changing requirements mid-run | **A** | `sesn_01JZyfBFEttydpgbyDQVctBc`: `user.message` injection mid-work, new requirement satisfied, prior work retained, still 0 nudges |
| Recovery from a failing tool | **B** | `sesn_01MfEiwmPsGrHvyLGncqRHPa`: native `type: "custom"` tool answered with two `user.custom_tool_result` `is_error: true` then one success; the agent re-verified its work between attempts and finished 4/4. Requires the client to keep servicing `requires_action` — i.e. an extension point, not configuration |
| Plan-oriented system prompt improves quality | **C (no effect measured)** | b2 vs b1: same 4/4, same 0 nudges, fewer tool calls (25 vs 34) and lower cost. Prompt strategy is fully configurable (A), but the *benefit* is unproven on a task this size |
| Built-in subagents improve quality | **C (no effect measured)** | b3 4/4 / 0 nudges, but 340 s / $50 vs a $41 baseline. Roster creation itself needs concrete agent IDs (`multiagent.agents`, depth limit 1) — see F |
| Memory Store improves long-horizon quality | **C** | b4 wide reached 4/4 but consumed $61 and 40 tool calls (the most of any arm) and blew its $60 budget before the run could be scored by the harness. Mount cost is real; benefit unobserved on a single-task workload. Consistent with E: retrieval is the model grepping the mount |
| Resume a session after a long idle gap | **C** | `sesn_012LhdngwoQCEWot586kMghx`: 900 s gap, `user.message` accepted (200), session went `running`, then `session.error` → `session.status_idle` with `stop_reason: retries_exhausted`. The session *object* and its history survive and are readable; continuing the work did not happen. See §5 for the confound |
| Survive several native compactions | **not tested — see §5** | 0 compactions in 13 graded sessions; peak context 59 075 tokens (b8) against the ~200 k threshold A observed. The workload is too token-efficient to force compaction |
| Steer via `user.interrupt` during a tool call | **not tested here** | b7 never fired: the driver polled `events.list` every 15 s and never sampled while a tool call was outstanding, then hit budget. Fixed in `run_stress.py` (match newest unanswered `tool_use`, 3 s poll) but not re-run — credits. K already established the primitive works |
| Worker-kill recovery | **D (inherited from C)** | Not re-tested. C established a dead worker strands the session in `requires_action` forever, recoverable only by external re-attach. Injected tool failure (b9) is *not* a substitute — the worker stayed alive throughout |

---

## 4. Distance to Devin

| Parity row | Distance |
| --- | --- |
| Long-horizon work across hours and compactions | **Half there.** Hours-of-work-in-one-stretch: yes, unattended and graded. Across *hours of wall clock with gaps*: no — a 15-minute idle gap ended in `retries_exhausted`. Across compactions: unproven here (never triggered); A's separate result is the only evidence |
| Builds a plan, then revises it as facts change | **Close.** Mid-run requirement injection was absorbed without a restart or a nudge. What is missing versus Devin is an inspectable plan artifact: the plan lives in the transcript, so nothing outside the model can see or diff it |
| Parallel investigation, then synthesis | **Costs more, delivers the same** on this workload. Subagents did not raise a success rate that was already 100 % |
| Learns across tasks | **Not shown by B.** Memory-on cost 50 % more for the same score in a single-task setting; the cross-task benefit is E's question |
| Recovers from a crashed sandbox or failed tool | **Failed tool: yes** (two injected `is_error` results, self-recovered). **Crashed sandbox: no** — class D per C |
| Asks for help only when genuinely blocked | **Better than asked.** 0 human nudges across all 10 runs that reached `end_turn`; the agent never stopped to ask on a well-specified task |
| Mid-run steering | Not measured by B; K owns it |
| Per-task cost accounting | **There.** `session.usage` gives per-session list cost and cache figures; a 27-file migration is $41–62 |

---

## 5. What I could not test, and why

1. **Several native compactions.** Peak context was 59 k tokens; the migration is simply not
   token-hungry enough, and `INFLATE` (repeated whole-repo rereads) only reached 59 k because the
   fixture is small. Forcing 200 k needs a fixture an order of magnitude larger, which I ran out of
   credit to build and run. **The compaction-survival rows in this document rest on A's evidence,
   not mine.**
2. **`user.interrupt` mid-tool-call.** Detection bug (above), fixed but unrerun.
3. **Resumption is confounded.** b5's `retries_exhausted` coincides with account credit exhaustion,
   which produces the same `session.error` → `retries_exhausted` signature. I re-probed the session
   afterwards (`user.message` accepted, session stayed idle, no turn) — consistent with either
   cause. **Do not read b5 as proof that idle resumption is broken; read it as untested.** The clean
   rerun is one arm: `B_IDLE_S=900 uv run --project runtime python experiments/B/run_stress.py b5_resume`.
4. **Memory-on wide comparison** never got a like-for-like budget: the $60 arm hit `budget_reached`
   after passing the grader, and the $150 rerun failed at session creation with
   `Your credit balance is too low to access the Anthropic API`.
5. **Dependency upgrade and framework migration** as distinct workloads. Both were folded into the
   Decimal migration (an API-shape change across 27 files with a protected contract); no
   package-manager or framework-specific behaviour was exercised, because the sandbox forbids
   network installs.
6. **Self-hosted fault arms on Modal.** All B runs used the shared cloud environment
   `env_01F4KCNxYngRzYKG5a1QLRZT`; a temporary environment created early returned
   `403 Token not authorized for this environment` (archived, see the cleanup ledger), and the
   Modal-side worker-kill case is C's and is already class D. No Modal logs are cited by B for that
   reason.
7. **Statistical significance.** n = 1–2 per arm. Every "no effect measured" above means exactly
   that: no effect was measured, not that no effect exists.

---

## 6. Provenance ledger

All code is under `experiments/B/`; nothing in `runtime/` or `packages/` was touched, and the
production agent was not mutated.

| File | Lines | Primitive it configures / observes / tests | Invocation path | Why configuration alone was insufficient |
| --- | --- | --- | --- | --- |
| `harness.py` | 433 | agents + versions, sessions, session resources (files, memory stores), `multiagent` rosters, `sessions.events.list/send`, `session.usage`, compaction events | `anthropic.beta.agents/sessions/...` directly | Measuring completion, nudges, compactions and cost per arm requires reading native event history; there is no Console/API report that answers "how many human interventions did this run need" |
| `prompts.py` | 167 | system prompt + first-message content — the only levers for constraint retention and plan strategy | passed as `agent.system_prompt` / `user.message` | The A/B is *between prompts*; the prompts are the experiment |
| `bundle.py` | 128 | native uploaded **file resources** as the code-delivery path into a session sandbox | `beta.files.upload` + session `resources` | Establishes the real mount root (`/mnt/session/uploads/...`, not the requested path) — only discoverable by running it |
| `fixture_gen.py`, `fixture/`, `reference_solution.py` | 263 + ~370 + 321 | not a primitive — the graded workload and its reachability proof | executed inside the session sandbox | An objective grader is the only way to score "completion" without trusting the model's own claim |
| `run_arm.py` | 143 | agent config A/B: baseline vs plan prompt vs `multiagent` roster vs Memory Store attachment | one session per arm, in parallel | These are four different *configurations* of the same primitive; comparing them requires running them |
| `run_stress.py` | 244 | `user.message` injection into a live session, `user.interrupt`, idle-gap resumption, context growth toward compaction | `sessions.events.send` | Duration and steering behaviour are only observable by driving a live session |
| `run_fault.py` | 179 | native **custom tool** (`type: "custom"`) + `user.custom_tool_result` with `is_error: true` | client services `requires_action` | The only supported way to inject a tool failure the model must recover from |
| `rescue.py` | 54 | session history as the durable record | `sessions.events.list` on a finished session | Demonstrates (and exploits) that a killed driver loses nothing — the b5/b7/b4-wide rows here were reconstructed this way after credits ran out |
| `summarize.py` | 75 | roll-up of native metrics across runs | reads saved reports | Reporting only |
| `cleanup.py` | 55 | resource lifecycle (`agents.archive`, `environments.archive`, `files.delete`) | Anthropic SDK | §7 requires a cleanup ledger with results |

---

## 7. Cleanup ledger

Machine-readable: `experiments/B/artifacts/cleanup-ledger.json` (written by `cleanup.py`, which
records failures rather than hiding them).

| Resource | Count | Action | Result |
| --- | --- | --- | --- |
| Temporary agents `clevin-swarm-B-*` (arm agents + subagent roster members) | 20 | `beta.agents.archive` | all archived |
| Temporary environment `env_01NafgppWcCBUZRMxfxXknqE` | 1 | `beta.environments.archive` | archived (it was unusable — `403 Token not authorized`) |
| Uploaded seed tarballs `acme_billing.tar.gz` | 6 | `beta.files.delete` | all deleted |
| Experiment sessions (`sesn_…`) | 15 | **kept deliberately** | they are the evidence for every claim above; archiving them would delete the citations |
| Memory Store | 0 created | — | b4 attached the existing `memstore_01JCboyFNzqNzucVq3xFpnYZ` read-side only; nothing written to it |
| Modal resources | 0 created | — | B never ran on the self-hosted environment |
| Production agent `agent_01Eef1xLtkWW2cDg1shFUpms` | — | untouched | no new version created |

---

## 8. Reproduction

```bash
uv sync --project runtime
# arms (parallel): b1_baseline b1_repeat b2_planned b3_subagents b4_memory
uv run --project runtime python experiments/B/run_arm.py b1_baseline b2_planned b3_subagents
# stress arms
B_IDLE_S=900 uv run --project runtime python experiments/B/run_stress.py b5_resume b6_changing b7_interrupt b8_compaction
# injected tool failure
uv run --project runtime python experiments/B/run_fault.py
# roll-up, and reconstruction of any run whose driver died
uv run --project runtime python experiments/B/summarize.py
uv run --project runtime python experiments/B/rescue.py <arm> <session_id>
uv run --project runtime python experiments/B/cleanup.py
```

`ANTHROPIC_API_KEY` must be bound; `B_ENV_ID` overrides the shared cloud environment. Each run
writes `experiments/B/artifacts/<arm>/<utc>-<id>/{events.json,report.json}`.
