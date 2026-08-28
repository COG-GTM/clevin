# Workstream K — Devin-parity interaction model

Owner: child session K. Base: `swarm/integration`. All evidence in
`experiments/K/evidence/`, produced by the drivers in `experiments/K/` (rerunnable:
`PYTHONPATH=experiments/K uv run --project runtime python experiments/K/<driver>.py`).

Everything below was measured against the live account on 2026-08-28 with the
production agent `agent_01Eef1xLtkWW2cDg1shFUpms` (version 7) and the self-hosted
environment `env_0152FZKRpy9f8uVw38Guzosy`. Probes that did not need a ticket used
the `CLEVIN_SMOKE_TEST` prefix.

## Classification summary

| # | Capability | Class | One-line basis |
| --- | --- | --- | --- |
| K1 | Mid-run steering: user messages a live session and it re-plans | **A** | `user.interrupt` + `user.message` re-plans with full awareness of what it had completed; a bare `user.message` is *rejected* while a tool call is outstanding |
| K2 | Ask-a-question-and-block, resume later with workspace intact | **B** | Custom tool → `requires_action` idle; answered after 15 min, workspace file intact with original mtime, resume in 0.2 s, $18 for a 945 s session |
| K3 | Sleeps, then wakes on a new ticket or comment | **C** | Deployment cron floor is 1 minute, each run is a *new* session; cross-run continuity only via Memory Store. No GitHub/Linear → Anthropic event path exists |
| K4 | Responds to PR review comments and fixes CI failures | **A** | One session took PR #6 from red to green, replied to the inline review comment, 10 GitHub MCP calls, 91 s active, $58 list cost |
| K5 | Playbooks: reusable named procedures (Skills) | **C** | Skills are delivered only as `/workspace/skills/<name>/SKILL.md` files, with no prompt listing and no skill tool; unusable by name until the system prompt says where to look, then exact |
| K6 | Self-improvement: writes back learnings after a task | **C** | Memory write-back works and helps the next session (1 install attempt / 45 s / $12 vs 3 attempts / 69 s / $17); Skill write-back has no agent-side primitive |
| K7 | Session forking from a checkpoint | **D** | No fork/branch/copy/checkpoint surface in SDK or REST; a new session cannot even be seeded with agent-side history |

## K1 — mid-run steering (class A)

Driver: `experiments/K/k1_midrun_steering.py` (`message` and `interrupt` modes).
Evidence: `k1-message-events.json`, `k1-interrupt-events.json`.

Both sessions were given a three-step "plan A" of long `sleep` commands, announced
the plan, started it, and were then told mid-flight to abandon plan A for a
different plan B.

- **A bare user message is rejected while a tool call is outstanding.** In
  `sesn_0167PUncQtoQ73rGDnC3ATyY`, with the `bash: sleep 240` tool call unanswered,
  `events.send` returned HTTP 400: `Invalid user.message event at events[0]:
  waiting on responses to events [...]; only user.tool_confirmation,
  user.custom_tool_result, user.tool_result, or user.interrupt may be sent`. The
  driver retried until the tool call resolved: `seconds_to_accept: 95.0`. Steering
  latency without an interrupt is therefore bounded by the *current tool call*.
- **Interrupt-then-message is immediate and is the correct pattern.**
  `sesn_01JhLxxtStGo6aMsBweh6C8i` (same workload, `interrupt_first: true`):
  interrupt accepted, then the message accepted with `seconds_to_accept: 0.5`, and
  the revised turn settled `seconds_to_settle: 110.4`, list cost $28.
- **It re-plans; it does not merely abort.** In both sessions the next turn
  abandoned plan A, executed plan B (`write /workspace/k1-phase-b.txt`), and
  answered the audit questions correctly — including "Plan-A markers completed when
  the instruction arrived: none — zero of three", i.e. it reasoned about the
  interrupted state rather than restarting.

**Distance to Devin:** none in substance, one in ergonomics. Devin accepts a
message at any moment; here the client must send `user.interrupt` first (or wait
out the tool call) or the API rejects the message. That is a wrapper concern, not a
capability gap.

## K2 — ask-and-block and delayed resume (class B)

Driver: `experiments/K/k2_ask_and_block.py <wait_seconds>`.
Evidence: `k2-ask-and-block-900s.json`, `k2-ask-and-block-4500s.json`.

The blocking mechanism is a native **custom tool** (`type: "custom"`, name
`ask_human`): the model calls it, the session parks in `idle` with
`stop_reason = {"type": "requires_action", "event_ids": [<agent.custom_tool_use>]}`
and stays there until a `user.custom_tool_result` arrives. There is no timeout on
that state in either run.

`sesn_01X7BTLEQ2zzFRpT6UVaUqxr` (15 min): blocked on
`sevt_01D62rMa4j8kjcUvbYy5HPkY` at 02:12:41Z, answered `gap_seconds: 900.9`
later, resumed in **0.2 s**, and its own report is the evidence: "Marker line read
back: `K2-MARKER-1787883147` — identical … 21 bytes, with its original mtime of
`Aug 28 02:12` … Hostname changed: No. `modal` both before and after." (The
hostname is a constant in this image, so it corroborates rather than proves
container identity; the durable thing is the volume.) Session
`duration_seconds: 945.1`, `active_seconds: 20.3`, list cost **$18** — blocking is
nearly free: you are billed for active work, not for waiting.

`sesn_01Mo2kMJ2c6m1J7G1WVPkgy5` (75 min requested, i.e. beyond the 3600 s
`APP_SANDBOX_TIMEOUT_SECONDS` and far beyond the 120 s worker `max_idle`) is the
longer replication; see `k2-ask-and-block-4500s.json`. Note that the Modal volume
samples in the 900 s file all carry
`AttributeError: 'function' object has no attribute 'aio'` — the first version of
the sampling helper called `modal.Volume.from_name.aio`, which does not exist. The
helper now uses the synchronous `modal.Volume.from_name(..., version=2).listdir`,
so Modal-side state sampling is only available in the 4500 s file.

Two structural points:

1. `requires_action` is **not** a human-input signal by itself. Every native tool
   call parks the session in `idle`/`requires_action` while the `EnvironmentWorker`
   executes it. Distinguishing "waiting for a human" from "running bash" requires
   correlating `stop_reason.event_ids` against `agent.custom_tool_use` events —
   `experiments/K/k2_ask_and_block.py:blocked_event_id` and
   `experiments/K/common.py:wait_for_turn_end` exist only because of this.
2. What survives the wait is the **`clevin-sessions` volume sub-path**, not a warm
   container. The worker's idle timeout ends the sandbox long before a human
   answers; the next turn gets a fresh sandbox with the same volume mounted.
   Anything not on the volume (installed packages, running processes, shell state)
   is gone.

**Distance to Devin:** Devin keeps the machine; Managed Agents keeps the volume.
Blocking for hours is safe for files and unsafe for process state, so a resumed
plan must re-establish its environment. The waiting itself has no measured ceiling.

## K3 — wake-on-event (class C)

Driver: `experiments/K/k3_wake_on_event.py`. Evidence:
`k3-deployment-polling.json`. Temporary deployment
`depl_0118D9QiqeNRZPN5FUEocguk` (`* * * * *`, UTC), archived at the end of the run.

- **Schedule floor is one minute**, and it is honoured: measured intervals
  `[50.6, 62.2, 63.0, 59.0, 61.0, 55.8, 63.6] s`, each run created 5–10 s after its
  `scheduled_at`. A manual `deployments.run` started a session in **1.23 s**.
- **Every run is a brand-new session**: 8 runs → 8 distinct session IDs
  (`run_count: 8`, `distinct_sessions` has 8 entries). A deployment cannot continue
  a prior session, so there is no "persistent session that wakes up".
- **Continuity comes only from the Memory Store.** Run 1 found nothing; runs 2–8
  read the log written by their predecessors and reported `prior state found: yes`
  with the previous timestamps (e.g. `sesn_016vDGtLtEjVydsavKjaw12W`: "this is the
  third recorded poll ... first recorded poll 02:19:33Z").
- **`/mnt/memory` root is read-only.** Writes must go to the store's own
  subdirectory (`/mnt/memory/clevin-repository-learnings/...`); the runs discovered
  this themselves and recorded it. Any prompt that names a path directly under
  `/mnt/memory` will fail.
- No GitHub or Linear → Anthropic event path exists (§4 already establishes there
  is no org webhook surface). Polling is therefore the *only* native trigger, and
  its floor is a minute plus the poll's own MCP round-trip.

**Distance to Devin:** Devin reacts to a webhook in seconds with the prior
conversation intact. Native gets ~1-minute latency, a cold session each time, and
must reconstruct "what am I in the middle of?" from memory. Adequate for "notice a
new ticket", weak for "continue the discussion on my PR".

## K4 — the PR-review and CI-failure loop (class A)

Drivers: `experiments/K/k4_mcp_credential_probe.py` (auth) and
`experiments/K/k4_pr_review_ci_loop.py <pr>` (the loop).
Evidence: `k4-mcp-credential-probe.json`, `k4-review-ci-pr6.json`.
Test bed: PR #6 with a deliberately failing required check
(`experiments/K/fixture/check.py` requires `VALUE == 42`, the branch shipped 41)
plus one inline review comment carrying two requests.

Session `sesn_01GZxwvwfnnjyQRuduEBHddu`, 144 events, 10 GitHub MCP calls,
`active_seconds: 90.9`, list cost $58, ended `end_turn`:

1. `pull_request_read get` / `get_diff` / `get_review_comments` / `get_check_runs` —
   read the PR, the diff, the review comment and the red check.
2. Cloned over bash into `/workspace/repos/clevin`, checked out the PR head branch,
   edited `value.py`, ran the fixture check locally *before* pushing, committed and
   pushed `e8a580c` to the PR branch (no force, no default-branch push).
3. Polled `get_check_runs` until `fixture-check` went `queued → in_progress →
   success`, then cross-checked `get_status` and correctly noted that the
   combined-status endpoint reports 0 statuses because the repo uses check runs.
4. `add_reply_to_pull_request_comment` — replied inline stating what changed and
   the resulting status.

Both requests in the review comment were satisfied, and the final report matched
the authoritative check-run conclusion. Credential note: the GitHub MCP URL works
both with and without the trailing slash (`sesn_01UMahEP4pAp6EjJBR5DGdYt`,
`sesn_01PSUFa2pyaXzdkpSiep8yad`), so the production URL needs no change.

**Distance to Devin:** effectively none for the mechanics — read review, fix,
push, poll, reply are all native. The gap is the *trigger*, which is K3: nothing
tells the agent a comment appeared, and no session is waiting to hear it.

## K5 — Skills as playbooks (class C)

Drivers: `k5_skills_as_playbooks.py`, `k5b_skill_discovery.py`,
`k5c_skill_discovery_prompt.py`. Evidence: `k5-skills-playbooks.json`,
`k5b-skill-discovery.json`, `k5c-skill-discovery-prompt.json`.
Skill created: `skill_01G8E6G4hGVRiXscLuhnsK8g` (`clevin-verification`), versions
`1787883822447335` (v1) and `1787883845989335` (v2, adds a marker line).

- Upload and versioning work exactly as documented: the archive must be
  `<name>/SKILL.md` under one top-level directory; `skills.versions.create`
  produces a new version and `latest_version` moves.
- **Attaching a Skill does not make it invocable.** With the skill attached through
  `agent_with_overrides` (confirmed present on the session resource:
  `agent.skills = [{skill_id, type: custom, version}]`), all three sessions
  (`sesn_0124MazLSLKkRmxyiEyWahoB`, `sesn_01KnXPNRV9rSxWEj5BKURjJM`,
  `sesn_01Tejtb1ayCa8SuU1TmSaW4U`) answered that no such playbook exists and
  refused to invent one.
- **Why:** `sesn_01ApSEqWounmC9MbY1RvYWDi` searched the sandbox and found the skill
  at `/workspace/skills/clevin-verification/SKILL.md` (inside the
  `clevin-sessions` volume). It is *not* in the system prompt, *not* in the tool
  definitions, and there is no skill-listing or skill-loading tool — the model's
  tool set is exactly `bash, edit, read, write, glob, grep, web_fetch, web_search`.
  Nothing announces the skill, so nothing invokes it.
- **One prompt paragraph fixes it.** `sesn_01EC7pdWNdtPewNAg6NomHRh` used the same
  attached skill with a paragraph appended to the agent's own system prompt saying
  where skills live; it then quoted the playbook's numbered commands verbatim
  *and* the v2 marker (`quoted_commands: true`, `quoted_v2_marker: true`).

Landed in the agent definition (`packages/provision/src/agent-definition.ts`): the
skill-discovery paragraph, and an env-driven `skills` list (`CLEVIN_SKILL_IDS`),
because custom Skill IDs are workspace-scoped and cannot be committed.

**Distance to Devin:** Devin auto-selects a matching skill; native Skills are
content delivery only — no auto-selection, no listing, no invocation surface, and
version pinning is per-attachment. With the prompt paragraph, "run the
`clevin-verification` playbook" works verbatim; without it, an attached Skill is
invisible. Hence C, not A.

## K6 — self-improvement (class C: memory yes, Skills no)

Driver: `experiments/K/k6_self_improvement.py` (plus `--collect` to re-gather
evidence for existing sessions). Evidence: `k6-self-improvement.json`,
`k6-tool-inventory.json`.

Task with a real trap: make `python3 -c "import numpy"` succeed in the sandbox,
where `python3` is a uv venv (`/.uv/.venv/bin/python3`) but `pip3` belongs to a
*different* interpreter, so `pip3 install numpy` "succeeds" and the import still
fails.

| Run | Session | Tool calls | Install attempts | Active | List cost |
| --- | --- | --- | --- | --- | --- |
| Seed (memory RW) | `sesn_0118gtfxnEj121tEkCYY2QqQ` | 15 | 1, after 3 diagnostic commands | 97.6 s | $29 |
| Replay **with** memory | `sesn_01DgUi228gLuUsRWb7eRrSPa` | 8 | **1**, correct first time | **45.1 s** | **$12** |
| Replay **without** memory | `sesn_01UdP9MhwNwnGRZsSiZmhuPo` | 8 | **3** (`pip3`, `python3 -m pip`/`uv` fallback, then a site-packages fix-up) | 68.9 s | $17 |

Tool-call *count* is the wrong instrument here — both replays used 8, because the
memory-equipped run spent calls locating and updating the store. What memory
bought is visible in the calls themselves: it read `sandbox-facts.md`, ran the
correct `uv pip install --python /.uv/.venv/bin/python3 numpy && python3 -c
"import numpy"` as a single first attempt (34 % less active time, 29 % less cost),
while the control burned three attempts rediscovering the interpreter mismatch. It
also
**updated the entry** with `edit` rather than appending a duplicate. It also caught a
second recorded fact — `rg` is absent, so `rg --files /mnt/memory` returns an empty
result that is indistinguishable from an empty store — which is precisely the class
of learning that makes the next session faster. The seed run also *refused* the
prompt's bogus memory path and wrote into the store's writable subdirectory
instead, reporting the deviation.

**Skill write-back is not available.** `sesn_018bpVLdhxdQQZcx2eBWKUwY` enumerated
its tools (`bash, edit, read, write, glob, grep, web_fetch, web_search`) and
confirmed none can create a Skill or a Skill version: `web_fetch` cannot issue
authenticated writes, and no Skills-API credential is in session scope. Publishing
a Skill from inside a run would require handing the agent an Anthropic API key and
having it call the control plane over bash — that is not a Managed Agents extension
point, so it is **not built** (class D for the agent-side half). The human/CI half
is trivially available (`skills.versions.create`, as this workstream did).

**Distance to Devin:** memory write-back reaches parity in substance and beats
nothing-at-all measurably (−5 shell commands, −$5, zero false starts on a repeat
task). Learning that changes the agent's *procedures* (Skills) cannot be done by
the agent itself; it needs an out-of-band publish step.

## K7 — session forking from a checkpoint (class D — confirmed, not built)

Driver: `experiments/K/k7_session_fork.py`. Evidence: `k7-fork-probes.json`.

- `beta.sessions` exposes `archive, create, delete, list, retrieve, update` and the
  sub-resources `events, resources, threads`. No fork, branch, copy, checkpoint, or
  snapshot method exists.
- Direct REST probes against `sesn_012k3LBeNTwXqU63chmX7HeD`: `POST /fork`,
  `POST /branch`, `POST /copy`, `POST|GET /checkpoints` → all **404**;
  `POST /v1/sessions` with `fork_from_session_id` or `source_session_id` → **400
  unknown field**.
- A new session cannot even be *seeded* with the parent's agent-side history:
  `initial_events` accepts only `user.message` and `user.define_outcome`
  (`agent.message` → 400 `not a valid value`).

The closest native approximation is what the driver did: start a fresh session
(`sesn_01DJBUKKDatCWuxSsURg3uMr`) whose first `user.message` is a human-written
digest of the parent, sharing the `clevin-sessions` volume path. That is a
re-briefing, not a fork: no reasoning history, no cache reuse, no divergence point.
Per §2 this is recorded as **class D** and no fork mechanism was built.

**Distance to Devin:** total for this row. "Try two approaches from the same
checkpoint" is not expressible; the fallback is two independent sessions given the
same brief, at full context cost each, with any shared workspace state to
coordinate by hand.

## Provenance ledger

Every file below exists to configure, observe, or empirically test a named
primitive. Nothing here is a product component: no agent loop, no orchestrator, no
scheduler, no memory layer.

| Added code | Primitive | How Managed Agents invokes/consumes it | Why configuration alone was insufficient |
| --- | --- | --- | --- |
| `experiments/K/common.py` | `beta.sessions`, `sessions.events` (`list`/`send`) | SDK calls; `steer()` sends `user.interrupt` + `user.message` | The API distinguishes tool-action `requires_action` idle from a real turn end only via `stop_reason` + event correlation; no single call answers "is the turn over?" |
| `experiments/K/k1_midrun_steering.py` | Session events: `user.interrupt`, `user.message` | Injects events into a live session and replays the resulting history | Whether injection re-plans or merely aborts is observable only by driving a real interruption mid-tool-call |
| `experiments/K/k2_ask_and_block.py` | Custom tool (`type: "custom"`), `EnvironmentWorker` idle timeout, `clevin-sessions` volume | Declares the tool in the session's `tools` override; answers with `user.custom_tool_result`; samples Modal state | The survivable wait and what survives it are empirical facts about worker/sandbox lifetimes, not documented configuration |
| `experiments/K/k3_wake_on_event.py` | `beta.deployments` (`create`/`run`/`runs.list`/`pause`/`archive`) + Memory Store resource | Creates a real cron deployment and reads its append-only run records | Schedule floor, per-run session identity, and cross-run continuity are only visible from actual runs |
| `experiments/K/k4_mcp_credential_probe.py` | `mcp_toolset` + vault credential binding | Two sessions differing only in MCP server URL | Whether the trailing slash matters for credential matching is not documented |
| `experiments/K/k4_pr_review_ci_loop.py` | GitHub `mcp_toolset` + native bash in the self-hosted sandbox | One session with a review-response system prompt override | The red→green loop is the parity claim; only an end-to-end run against a real red PR can evidence it |
| `experiments/K/k5_skills_as_playbooks.py` | `beta.skills` (`create`, `versions.create`, `retrieve`) + agent `skills` | Uploads a `<name>/SKILL.md` archive and attaches it per session | Version-pinning and invocability semantics are unstated; attachment turned out not to imply invocation |
| `experiments/K/k5b_skill_discovery.py` | Skill delivery mechanism | Asks the session to locate its own attached skill | The delivery path (`/workspace/skills/...`) is undocumented and explains the K5 failure |
| `experiments/K/k5c_skill_discovery_prompt.py` | Agent `system` + `skills` overrides | Same skill, one added prompt paragraph | Validates the exact agent-definition change before landing it |
| `experiments/K/k6_self_improvement.py` | Memory Store session resource (`read_write`) + native tool inventory | Seed/replay/control sessions over one store | Whether write-back measurably helps the next task can only be measured |
| `experiments/K/k7_session_fork.py` | `beta.sessions` surface + `initial_events` | SDK introspection and raw REST probes | A negative capability claim needs an exhaustive probe, not an assumption |
| `packages/provision/src/agent-definition.ts` (skill paragraph, `parseSkillIds`) | Agent configuration: `system`, `skills` | The provisioner publishes it as a new agent version | K5b/K5c proved an attached Skill is inert without the prompt paragraph, and Skill IDs are workspace-scoped so they cannot be literals |
| `packages/provision/test/provision.test.ts` (2 assertions) | Same | Repo test suite | Guards the landed prompt text and the ID filter |

Shared-file changes are limited to the two files in the last rows: one paragraph
appended to `CLEVIN_SYSTEM_PROMPT`, one exported `parseSkillIds`, `skills` derived
from `CLEVIN_SKILL_IDS` (unset ⇒ `[]`, i.e. behaviour unchanged by default), and
two test cases.

## What could not be tested, and why

- **Deliberately not built (class D):** session forking/checkpointing (K7) and
  agent-side Skill publishing (K6). Both would have required a non-primitive
  mechanism — a snapshot/replay layer, or handing the agent a control-plane
  credential — which §2 forbids.
- **Wake-on-*event*** in the true sense: there is no GitHub/Linear → Anthropic
  event path and no org webhook surface (§4), so only polling was measurable. A
  Modal-side webhook receiver that pokes Anthropic would be a custom automation
  platform; not built.
- **Steering *during model generation*** (as opposed to during a tool call) was not
  isolated: the generation window is seconds and the sessions here were almost
  always inside a tool call. `user.interrupt` is already confirmed working against
  an actively working session (§4).
- **Long-block ceiling above 75 minutes** (multi-hour or multi-day resume) — the
  4500 s run is the longest measured; nothing observed suggests a limit, but hours
  were not tested.
- **Multi-round review loops:** K4 covered one review comment and one CI failure.
  A second round (new comment after the fix) was not run.
- **Skill auto-selection quality across many skills:** one skill was tested. How
  the model chooses among a dozen `/workspace/skills` entries is untested and is
  the obvious follow-up.
- **Memory scoping** is workstream E's; K6 only used one store and one file.

## Cleanup ledger

| Resource | Cleanup action | Result |
| --- | --- | --- |
| Deployment `depl_0118D9QiqeNRZPN5FUEocguk` (`clevin-swarm-K-k3-poll-…`) | `pause` then `archive` in-driver | Done — `archived_at: 2026-08-28T02:25:17Z`, `upcoming_runs_at: []` |
| Memory entry `k3-poll-log.md` in `memstore_01JCboyFNzqNzucVq3xFpnYZ` | `memories.delete` via `experiments/K/k_cleanup.py` | Deleted; re-run reports `absent (already deleted)`. **Cleanup imperfection:** the first invocation deleted the entry and then crashed on an unrelated attribute before writing the archived copy, so the deleted body is not in the evidence files — its content is quoted in the K3 section above and its per-run text is in `k3-deployment-polling.json` |
| Memory entry `sandbox-facts.md` in the same store | **Retained deliberately** — it is a true, useful sandbox fact and is K6's evidence of write-back | Retained |
| Skill `skill_01G8E6G4hGVRiXscLuhnsK8g` (`clevin-verification`, v1+v2) | **Retained deliberately** — it is the playbook referenced by the landed agent-definition change; attach via `CLEVIN_SKILL_IDS` | Retained |
| PR #6 + branch `devin/1787884400-K-k4-ci-fixture` (red-CI fixture, `experiments/K/fixture/**` and its workflow) | PR closed unmerged; branch left in place so the K4 loop can be rerun. Fixture files never entered `swarm/integration` | See ledger file |
| Experiment sessions (~25, all `metadata.experiment = clevin-swarm-K`) | Left in place as evidence; they are terminal and cost nothing idle | Retained by design |
| Production agent version | Not mutated in place; the agent-definition change lands as a repo change for the master to provision | No production write performed by this session |

Total measured spend for this workstream is in the per-probe `usage` blocks of the
evidence files (largest single probe: K4 at $58 list cost).
