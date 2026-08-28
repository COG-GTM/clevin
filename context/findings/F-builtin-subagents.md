# Workstream F — Built-in subagents

What native Managed Agents subagents actually do, measured against the Devin-parity rows they are
supposed to carry. Every claim below cites a session, thread, or request ID from
`experiments/F/artifacts/`; nothing is classified from an agent's own assertion.

Environment: cloud environment `env_01F4KCNxYngRzYKG5a1QLRZT`, model `claude-opus-5` (coordinators)
and `claude-sonnet-5` (most children), toolset `agent_toolset_20260401`, all agents temporary and
named `clevin-swarm-F-<role>-<UTC>-<id>`. Production agent `agent_01Eef1xLtkWW2cDg1shFUpms` was
never mutated by an experiment.

## 0. The primitive in one page

`multiagent: {type: "coordinator", agents: [...]}` on an agent definition is the whole surface. There
is no other delegation lever: no per-session roster, no runtime spawn of an unrostered agent, no
delegation depth beyond one.

A coordinator's roster entry may be an agent ID string, `{type: "agent", id, version}`,
`{type: "self"}`, or `{type: "advisor", model}`. At runtime the coordinator receives three implicit
tools — `create_agent`, `send_to_agent`, `list_agents` — and each child receives `send_to_parent`
(observed in the children's own tool listings, `sesn_0155ruKCQ9qp7RSyTR83sTSg`,
`sesn_018Hit5DRZ1GQ2R6iGoFMSwi`). Delegation creates a *session thread*: `session.thread_created`,
then `session.thread_status_running` / `_idle` per thread, with `agent.thread_message_sent` /
`agent.thread_message_received` carrying task text and the child's single final reply.

A child sees exactly this and nothing else (`sesn_01GToFDwRnwW56n6J2wW2iCd`, child's verbatim
report):

```
<agent-notification from="sthr_01V5ik3P8K3aqJ83eHzQLg5V"> Run your standard checks. The token for
this task is beta-2249. Reply by calling send_to_parent when you are done. If anything is unclear,
use send_to_parent to ask — do not guess or proceed on assumptions. </agent-notification>
```

Hard limits, all rejected at the API with actionable messages (`exp5`,
`artifacts/exp5_topology_limits/*/result.json` → `api_level_checks`):

| Rule | Evidence |
| --- | --- |
| Max depth 1 — a rostered agent may not have its own roster | `subagent agent_01GGLS1U8Bpzdm3JbU2vmrA4 has its own subagents; maximum depth is 1` (req_011CeULbYZDcqDt3rQCeAf2G) |
| Enforced on update too, not just create | same message on `agents.update` (req_011CeULbxAnjJBUB2Prwy77f) |
| No duplicate roster entries | `subagent agent_01PtVdtMFfs9DXEr1yFiTTuN referenced multiple times` (req_011CeULbZtLQKZfje4ZLavrk) |
| At most one advisor | `multiagent.agents.1: at most one {"type":"advisor"} entry is allowed` (req_011CeULbbUauQqkLZMiQadsL) |
| Max 20 roster entries | `multiagent.coordinator.agents: must contain no more than 20 item(s)` (req_011CeULbwe2n3ZnPJrq5yMdj) |
| `{type:"self"}` alongside ordinary members is accepted | `agent_01PXE3Pe6DjPB5bPHN6EMPsU` |
| Advisor-only roster is accepted | `agent_01Xs2VcLF4dtwXHJHbVvWH1f` |

## 1. Delegation, context isolation, and shared state — class A

`sesn_01GToFDwRnwW56n6J2wW2iCd` (exp1 rerun; the first run, `sesn_018KckoVenWSAJ7tYzZNg5Nd`, stopped
on `budget_reached` at a $12 budget with list cost 14 — itself useful evidence that budgets bind
subagent work).

- The parent delegated with `create_agent` and quoted the child's reply; the child's task text
  arrived verbatim with no parent history (`parent_history_visible: false`).
- Same sandbox, separate processes: parent `hostname=vm pid=491`, child `hostname=vm pid=658`.
- Shared filesystem in both directions: the child listed and read `/workspace/parent_note.txt`
  (`PARENT_TOKEN=alpha-7731`) written by the parent before delegating, and the parent then read
  `/workspace/child_explorer.txt` (`CHILD_EXPLORER_TOKEN=beta-2249`) written by the child.
- The parent does **not** see the child's intermediate tool calls
  (`could_you_see_child_tool_calls: false`); those events exist only on the child thread
  (`sessions.threads.events.list`), so the parent's own context stays small but the parent also
  cannot audit *how* a child reached its answer without the operator reading thread events.

**Class A.** Delegation, context isolation and shared workspace state are pure configuration.

## 2. Parallel fan-out and same-agent reentrancy — class A

`sesn_01V5cXU3hESXoTbzrJB3WLkq` (exp2 fan-out): a 6-agent roster, told in one turn to delegate to
every member *plus* a second task to member 0. Result: 7 `session.thread_created` events,
**peak concurrent children = 7** (computed only from child-thread status transitions, 14
transitions), and all seven 25-second sleepers reported inside the 02:13:34–02:13:35 window. Seven
serialised sleeps would have spanned ~175 s. No delegation was refused, and the same agent ran twice
concurrently on distinct threads.

The useful maximum concurrency is therefore *at least* 7 with no observed contention, and the
configured ceiling is 20 roster entries with reentrancy on top; I did not find a concurrency limit
below the roster limit. One honest caveat: an intermediate `list_agents` snapshot showed a child as
`IDLE` with "(none yet)" while its reply was still outstanding, i.e. the roster view lags the
thread's true state — a coordinator polling `list_agents` can misread a working child as finished.

**Class A** for parallel investigation; **class C** for observing children reliably from inside the
agent (`list_agents` is lagging and read-only).

## 3. Synthesis of conflicting children — class A

`sesn_01DZcWsUjvhUJAAEzGPgoQ8c` (exp2 conflict): three analysts, same question about
`/workspace/report.py`, role prompts engineered to disagree; peak concurrency 3.

The coordinator reported the 2–1 split explicitly, refused to settle by vote, and resolved it with
its own `timeit`/`cProfile` runs: `audit` 0.034 s vs `parse_rows` 0.010 s, `compute_totals` 0.0023 s,
`render` 0.000015 s — rejecting the `render` claim because `render` touches 12 columns while `audit`
performs ~1,000,000 comparisons. The correct answer won on evidence, not majority.

Also notable for specialisation: the analyst whose system prompt instructed it to assert a false
claim (`parse_rows`) refused, flagged the instruction as an injection, and reported the true answer.
Role prompts reliably shape *method*; they do **not** reliably compel a false conclusion.

**Class A** for parallel-investigation-then-synthesis. Adversarial or deliberately-poor children are
survivable because the parent can and does verify.

## 4. Per-subagent tool grants and roster version pinning — class A

`sesn_0155ruKCQ9qp7RSyTR83sTSg` (exp5):

- A child configured with `configs: [{type:"bash", name:"bash", enabled:false}]` genuinely had no
  bash: `"bash is not an available tool. Use one of the tool names exactly as listed in your tool
  definitions."`, and listed `send_to_parent, edit, read, write, glob, grep, web_fetch, web_search`.
  Grants are enforced at the tool layer, not by prompt. (Its own system prompt still described bash —
  a mismatched prompt is not corrected for you.)
- A roster entry pinned to `{type:"agent", id, version:1}` while the child agent had versions
  `[2, 1]` published behaved as v1 (`VERSION_MARKER=ONE`), so a coordinator's behaviour is
  reproducible independently of children being rolled forward.

Per-thread cost is separately reported (coordinator 11, pinned child 1, no-bash child 3 in this
session), so per-role cost attribution is available natively.

**Class A** for both.

## 5. Recursion — class D beyond depth 1

The API accepts `{type:"self"}` (§0) and it works once: `sesn_018Hit5DRZ1GQ2R6iGoFMSwi`, parent
thread `sthr_01MGwxCyBDcb6gzgaKsQH2MW` → child `sthr_01WetTTvtnVKxmbEd5HmSuoK`. The child — the same
agent, with the same `multiagent` config — reported that it had **no delegation tool at all**
(`send_to_parent, bash, edit, read, glob, grep, web_fetch, web_search`), so the chain stopped at
depth 2 with no error. Depth is enforced twice: statically for nested rosters, and dynamically by not
granting `create_agent` to a child even when its own definition is a coordinator.

**Class D** for hierarchical/recursive delegation. Not built around: the brief forbids spawning
top-level sessions to fake depth, and I did not.

## 6. Failure, silence, hangs, cancellation, redirection

`exp3` (`artifacts/exp3_failure_and_control/20260828T021536Z-a59a50/`) and `exp7`.

**Silent child — class A (graceful).** `sesn_01PaQPWEg4fx2ncT81L8aCEt`: a child instructed to end
without text idled with `end_turn`, and the parent received a platform-generated placeholder,
verbatim: `[child sthr_01KcYKwxmjnx3uc4zs11EtUQ completed but produced no text output]`. The parent
correctly identified it as a placeholder rather than child text and completed the task itself.

**Failed child — class A (graceful).** When the account balance ran out mid-run
(`sesn_01CQUg61Qfj2bsu7AJ7fmHva`), two children surfaced to the parent as
`[child sthr_017sTAvz16nPUNdq8mzqDL7n failed: Your credit balance is too low to a…]`. Child failures
are reported to the parent as message content, not as a session-level abort.

**Very large child output — class C, and a silent-drop hazard.** exp8
(`sesn_01CQUg61Qfj2bsu7AJ7fmHva`) laddered one child per size, measuring the delivered
`agent.thread_message_received` rather than trusting the parent: 2 000 chars → 2 001 delivered,
16 000 → 16 000 delivered (event body 16 047 chars). The 64 000 and 256 000 rungs never ran (balance
exhausted), so the ceiling sits somewhere above 16 KB and is unmeasured. exp7 bounds it from the
other side: a child told to return ~533 KB emitted `output_tokens: 0` and idled with `end_turn` in
**3 of 3 runs** (`sesn_01VXQxidKWMXbmaqZAdFRube`, `sesn_01Jy86D3hNf355XH1sCbVRGL`,
`sesn_01Tzf6vpydSG7t7uMvfBoZPv`), and each parent received only the generic "produced no text
output" placeholder — **no size diagnostic anywhere in the event stream**. All three coordinators
refused to call it truncation, correctly, since nothing distinguishes "oversize reply dropped" from
"child said nothing". Operationally: children must write large artefacts to the shared filesystem and
report a path, and a roster prompt should say so.

**Overlapping edits — class D (last write wins, silently).** `sesn_019L7Ah2GMgknxeXz9Rr5CNk`: two
children were told in one turn to overwrite `/workspace/shared.txt`. Editor Y reported
`BEFORE=ORIGINAL AFTER=EDITOR_Y_WAS_HERE SAW_OTHER_EDITOR=no`; editor X wrote
`EDITOR_X_WAS_HERE`, re-read the file, found `EDITOR_Y_WAS_HERE`, and reported the clobber. There is
no locking, no conflict signal, and no platform notice — one child's work was lost and only its own
verification caught it. Avoidance is a prompt discipline (disjoint file ownership per child), not a
primitive.

**Hang, cancel, redirect — class B via session events, class D from inside the agent.**
`sesn_01Qc9Z6HVpLJuZUCf7zzw5Ae`: a child was told to `sleep 900`. Two independent findings:

1. The coordinator enumerated its actual levers and had no cancellation: only `create_agent`,
   `send_to_agent`, `list_agents`; no cancel/kill/timeout parameter, and `send_to_agent` is
   cooperative — the message lands as the child's *next* message and cannot preempt a child inside a
   long tool call. It also stated plainly that a hung child and a working child are
   indistinguishable to it (no heartbeat). Its only remedy is abandonment, which does not stop the
   child (the child kept running and ultimately reported success after working around a ~295 s
   foreground bash cap by backgrounding and polling — thread `sthr_017mP5taoLzucH5DqJbwJjNZ`,
   active 1 255 s).
2. The operator *does* have a lever, and the platform names it. `sessions.threads.archive` on a
   running child was rejected with: `Thread sthr_017mP5taoLzucH5DqJbwJjNZ cannot be archived while
   its status is "running". Send user.interrupt with session_thread_id set to bring it to idle
   first.` (req_011CeULnhdE3xHxWqMKV32nm). So per-child cancellation exists as a native session
   event addressed at a thread; I could not execute it before the balance ran out (§9).

Operator steering did land: a `user.message` sent into the live session while the child was running
was accepted and the coordinator answered from it (`user.message` count 2 on that session).

**Parent compaction while children run — untested.** No `agent.thread_context_compacted` event
occurred in any F session; the sessions were minutes long, not hours. Left to workstream A/B rather
than asserted.

## 7. Does the roster improve outcomes, or only cost? — class C at best

exp4 (`artifacts/exp4_ab_quality/20260828T021805Z-06fbc6/`) ran the same task — implement
`parse_duration`, `chunk`, `retry` against a fixed 11-test suite whose SHA the agent had to print —
twice solo and twice with a planner/implementer/reviewer roster, scored **only** from native
`agent.tool_result` events:

| Arm | Passed | Verification runs | Session list cost | Child threads |
| --- | --- | --- | --- | --- |
| solo run 1 | yes (`OK`) | 1 | 21 | 0 |
| solo run 2 | yes (`OK`) | 2 | 25 | 0 |
| roster run 2 | yes (`OK`) | 2 | 76 | 3 |
| roster run 1 | yes (`OK`) | 3 | 97 | 3 |

Same outcome, **3–4× the cost**. On a task with visible tests the solo agent simply iterates against
the oracle, and delegation buys nothing.

exp6 removed the visible oracle: implement an arithmetic-expression parser scored by a base64-encoded
30-case hidden grader (precedence, associativity, unary and negative exponents, int/float semantics,
floor division, modulo signs, parentheses, syntax errors, division by zero), run once, scored only
from the `SCORE=<n>/30` in native `agent.tool_result` events. It was killed by the balance
mid-run, so its artefact directory holds session JSONs and no `result.json`, but four of five arms
completed:

| Arm | Session | Score | Session list cost | Child threads |
| --- | --- | --- | --- | --- |
| solo | `sesn_01Q1Z8EKv8XhFeJJnB4ydL8h` | 30/30 | 54 | 0 |
| solo | `sesn_01EHYeCmMn5UYgcszjnamqHb` | 30/30 | 111 | 0 |
| solo | `sesn_018idkR9XWbz1N8ghfmLjnCx` | 30/30 | 121 | 0 |
| roster (spec analyst → implementer → adversarial reviewer) | `sesn_0161m9EimLWXnxg5qtDKRTv9` | 30/30 | 140 | 3 |
| roster | `sesn_01QwCmkKyiZbWESbX28SQpJc` | no score — balance exhausted mid-run | 110 | 3 |

Even against a hidden oracle the roster did not beat solo: both arms saturated the grader, and the
roster arm again cost more. The honest finding across exp4 and exp6 is: **subagents demonstrably buy
parallelism, isolation and cost; on both a visible-oracle and a hidden-oracle single-module task I
found no quality advantage.** With one completed roster run at 30/30 this does not rule out an
advantage on harder tasks — a saturated grader cannot show one. Their defensible uses from this
workstream's data are wide read-only investigation (§2), independent adversarial verification (§3),
and role-scoped tool restriction (§4).

## 8. Parity rows and classes

| Parity row | Class | Distance to Devin |
| --- | --- | --- |
| Parallel investigation, then synthesis | **A** | At parity: 7 concurrent children, conflicting results synthesised on evidence; unlike Devin the parent cannot see child intermediate steps without reading thread events. |
| Builds a plan, then revises it as facts change | **C** | Planning is prompt-driven and works, but the roster adds no measured quality (§7) and there is no plan artefact primitive — revision quality is unproven, not absent. |
| Recovers from a failed tool / bad child result | **C** | Child failure and silence degrade gracefully with a platform placeholder, but a hung child is undetectable from inside the agent and the parent has no cancel tool. |
| Mid-run steering | **B** | `user.message` into a live parent works; per-child interrupt exists as `user.interrupt` with `session_thread_id` (named by the platform, unexecuted here). |
| Fleet of agent variants managed as code | **A** | Roster entries pin child versions, so a coordinator is reproducible; landed in the provisioner (§10). |
| Per-task cost accounting | **A** | Per-thread `list_cost` gives per-role attribution Devin does not expose. |
| Hierarchical delegation / sub-subagents | **D** | Depth is capped at 1 statically and dynamically; Devin's nested delegation has no native equivalent, and I did not build one. |
| Concurrent edits by parallel workers | **D** | No locking or conflict signal; last write wins silently. Devin's single-writer discipline must be recreated in prompts. |

## 9. What I could not test, and why

- **Hidden-grader quality A/B (exp6) — 4 of 5 arms only**, and its grader saturated at 30/30 in every
  completed arm, so it cannot discriminate. The fifth arm and any harder-task follow-up died with the
  balance (400 `Your credit balance is too low…`, req_011CeUP1Nzec6inMYSESAdps). A grader hard enough
  to separate the arms is the experiment that could still overturn §7.
- **`user.interrupt` with `session_thread_id`** — the platform names it as the way to bring a running
  child to idle (§6). Same reason. Until it is run, per-child cancellation is class B *by platform
  message*, not by demonstration.
- **Payload ceiling between 16 KB and 533 KB** — the 64 KB/256 KB rungs failed on balance, not on
  size.
- **Parent compaction while children run** — no compaction event occurred in these session lengths;
  deliberately not asserted.
- **Repeated delegation over an hours-long session** — out of budget; workstream B owns long-horizon
  behaviour.
- **Deliberately not built (class D):** no external delegation engine, no top-level sessions spawned
  inside Clevin to fake depth or cancellation, no file-locking layer for concurrent children, no
  child-output chunking transport.

## 10. What landed in the product

`packages/provision/src/agent-definition.ts` gains three subagent definitions and the production
agent gains a roster of them, justified by the evidence above: **repository explorer** (read-only,
`write`/`edit` disabled per §4 — the enforcement is real, so read-only investigation is safe),
**test debugger** (full toolset), **adversarial reviewer** (read-only, `VERDICT=` contract, per §3
where an evidence-checking verifier caught a false claim). The system prompt's delegation policy
encodes the hazards this workstream found: children get no history (§1), never give two children
overlapping edits (§6), verify a child's claim before relying on it (§3), never delegate the push or
the PR. `CLEVIN_SUBAGENT_IDS` lets the provisioner reconcile existing subagents instead of recreating
them, and the manifest records their IDs. The provisioner was **not** run — landing is code-level;
production `agent_01Eef1xLtkWW2cDg1shFUpms` still has `multiagent: null` until someone provisions
deliberately.

## 11. Reproduction

```bash
uv run --project runtime python experiments/F/probe0_surface.py          # read-only surface
uv run --project runtime python experiments/F/exp1_delegation_basics.py  # context + filesystem
uv run --project runtime python experiments/F/exp2_fanout_and_conflict.py
uv run --project runtime python experiments/F/exp3_failure_and_control.py
uv run --project runtime python experiments/F/exp4_ab_quality.py
uv run --project runtime python experiments/F/exp5_topology_limits.py
uv run --project runtime python experiments/F/exp6_hidden_grader_ab.py   # never completed: balance
uv run --project runtime python experiments/F/exp7_oversize_and_null_turn.py
uv run --project runtime python experiments/F/exp8_payload_ladder.py
uv run --project runtime python experiments/F/report.py experiments/F/artifacts/<exp>/<run>/  # render evidence
```

Each driver writes `experiments/F/artifacts/<experiment>/<UTC>-<id>/` containing one JSON per session
(full native event list, thread list, per-thread usage) plus `result.json` (observations and the
cleanup ledger). All committed artefacts are the runs cited above.

## 12. Provenance ledger

| Code | Primitive it configures / observes / tests | How Managed Agents consumes it | Why configuration alone was insufficient |
| --- | --- | --- | --- |
| `experiments/F/harness.py` — `create_agent`, `create_session`, `wait`, `collect`, `note`, cleanup ledger | `beta.agents.create/update/archive`, `beta.sessions.create/retrieve`, `beta.sessions.events.list`, `beta.sessions.threads.list/events.list` | Calls the native APIs; nothing here runs inside an agent | Rosters must be created, run and observed to answer §1–§8; the Console shows no per-thread event stream |
| `harness.concurrency_profile` | `session.thread_created`, `session.thread_status_running/_idle` | Reads native events only | Peak child concurrency (§2) is not reported by any native field |
| `harness.in_parallel` | Nothing — test-driver helper that launches independent API calls concurrently | Not consumed by Managed Agents | Needed to run A/B arms in one wall-clock window; explicitly experiment scaffolding, not product code |
| `exp1_delegation_basics.py` | Coordinator roster + session threads + shared sandbox filesystem | Prompt drives native `create_agent`; assertions read native events | Context isolation and filesystem sharing are undocumented behaviours |
| `exp2_fanout_and_conflict.py` | Roster width, same-agent reentrancy, conflicting child reports | Same | Concurrency ceiling and conflict handling are empirical |
| `exp3_failure_and_control.py` | `sessions.threads.archive`, `sessions.events.send` (`user.message`), child failure/silence paths | Native archive + event-send APIs against a live session | Cancellation semantics are only discoverable by attempting them |
| `exp4_ab_quality.py` + `grade()` | Roster vs solo outcome and `session.usage` list cost | Scores native `agent.tool_result` events | Cost/benefit of delegation is not derivable from configuration |
| `exp5_topology_limits.py` | `multiagent` validation rules, `{type:"self"}`, advisors, roster version pinning, per-subagent tool `configs` | Native create/update rejections and a live self-roster session | The limits are unpublished; each was probed once and recorded |
| `exp6_hidden_grader_ab.py` + base64 grader | Roster vs solo quality against a hidden oracle | Grader is written and run by the agent's own bash tool; scored from native tool results | A visible-oracle task cannot separate quality from iteration (§7) |
| `exp7_oversize_and_null_turn.py` | Child→parent payload path; `sessions.events.send` as recovery for an empty turn | Native events | The empty-turn behaviour seen once in exp3 needed repetition to be a finding |
| `exp8_payload_ladder.py` | Same, measured from `agent.thread_message_received` | Native events | Parent self-reports cannot be trusted for payload size |
| `experiments/F/report.py` | Renders persisted native events (messages, thread messages, tool use/result, usage, lifecycle, compaction) | Pure reader of stored native events | Evidence in findings must be quotable; no native viewer exists for thread-level events |
| `probe0_surface.py` | `agents.retrieve`, `agents.versions.list`, `environments.list` | Read-only native calls | Confirms the production agent's roster state before/after experiments |
| `packages/provision/src/agent-definition.ts` (subagent definitions, `coordinatorRoster`, delegation policy) | `multiagent` coordinator roster + per-subagent tool grants | Consumed by `beta.agents.create/update` via the provisioner | The finding only counts once it is in the agent definition (brief §4) |
| `packages/provision/src/config.ts` (`CLEVIN_SUBAGENT_IDS`) | Agent identity/versioning of roster members | Read at provision time | Without it every provision run would create duplicate subagents |
| `packages/provision/src/resources.ts` (`reconcileSubagents`, roster wiring, `subagent_ids` manifest) | `agents.create/update` for children, then coordinator roster | Provisioner reconciliation | Children must exist before the coordinator can reference them |
| `packages/provision/test/provision.test.ts` additions | The above reconciliation contract | Vitest | Guards ordering and roster construction without mutating production |

## 13. Cleanup ledger

Per-run ledgers live in each `result.json` (`cleanup_ledger`). Rollup:

| Resource | Count | Action | Result |
| --- | --- | --- | --- |
| Temporary agents `clevin-swarm-F-*` (exp1–exp5, exp7, exp8) | 66 | `beta.agents.archive` at driver exit | archived — every ledger entry reads `"archived"`; no failures |
| Temporary agents from exp6, which was killed before its cleanup ran | 5 | archived afterwards via `beta.agents.list` + `beta.agents.archive` | archived; a follow-up `agents.list` returns 6 agents, the 5 being unrelated, production `agent_01Eef1xLtkWW2cDg1shFUpms` intact and still `multiagent: null` |
| Sessions created by F | 23 | 22 deliberately retained, 1 deleted | `"retained as evidence (idle, no live resources)"` — they are the evidence for §1–§8; all idle, no compute held. The deleted one was a scratch session from the first exp1 attempt. |
| Session threads | all children | left with the session | idle; the one 900 s sleeper finished on its own (§6) |
| Modal / environments / memory stores / vaults / deployments | 0 | none created or modified | n/a — F is cloud-environment only |

No cleanup failed. The only lingering state is the retained idle sessions, intentionally.
