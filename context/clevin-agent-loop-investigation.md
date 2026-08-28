# Clevin Managed Agent Loop Investigation

## Executive summary

**Conclusion (high confidence): Clevin is working as implemented. The repeated short Modal `POST /` requests and immediate `queue drained` messages are primarily expected no-op wakeups, not individual tool calls and not evidence that sessions failed. A fresh controlled run also captured one probable webhook retry after a 28.2-second Modal cold start, so duplicates can contribute to an individual burst even though they are not the main steady-state cause.**

The first `session.status_run_started` webhook for a newly queued session invokes Clevin's Modal handler. That handler claims the environment's single **session work item** and launches a detached, session-named Modal Sandbox. Inside that sandbox, `EnvironmentWorker.handle_item()` owns the session lease and services the complete tool loop. Each tool result can lead Anthropic to begin another model run for the same session. The webhook subscription emits another `session.status_run_started` wakeup for each such run, but the original sandbox already owns the session work item. The new webhook handler performs its configured non-blocking poll, finds no claimable environment work, logs `queue drained`, and returns HTTP 200.

This was directly observed for session `sesn_01LnksfmKUCjwTJASMVsG5Eh`: Modal claimed it once at 23:45:19 PDT, its sandbox continued heartbeating and executing tools, and later run-start events at 23:50 and 00:13–00:14 correlated one-for-one with short Modal POSTs and empty polls. The session executed 79 local agent tool calls with 79 local tool results, plus 8 server-side MCP calls/results. It produced 65 `running` transitions, 80 `requires_action` idle events and one final `budget_reached` idle event. This is exactly the shape expected when some model turns request two local tools: partial result fulfillment re-emits `requires_action` for the remaining event, and only the final result transitions the session back to `running` and generates a new webhook.

The empty-poll behavior is therefore **correct but noisy and somewhat inefficient**. `block_ms=None` makes redundant wakeups return quickly; it is not shown to have lost work. In the fresh test, the first dispatcher invocation claimed and ACKed the work, a separate Modal Sandbox opened the session SSE stream, two local `bash` calls were executed there, and the worker stopped its work item 120 seconds after `end_turn`. Three subsequent dispatcher invocations found the queue empty while that sandbox held the lease. Two followed tool-result submissions and are consistent with distinct run transitions. The first arrived after the original delivery spent more than 32 seconds queued plus executing; its timing matches Anthropic's documented retry backoff and is probably a retry, although confirming that requires the top-level webhook event IDs.

The deeper pass found a separate lifecycle defect: `EnvironmentWorker`'s idle timer applies only to `end_turn`, not `budget_reached`. HUM-6 went idle at its budget at 00:17:45 PDT but continued heartbeating its work lease until 00:44:59, almost exactly the one-hour Modal Sandbox timeout; its work did not reach `stopped` until 00:51:28. Healthy `end_turn` sessions stopped about 120 seconds after their final idle event, matching Clevin's `max_idle=120`. Thus the repeated empty polls are expected, but **budget-paused work is not cleaned up promptly and can consume the remainder of the sandbox lifetime**.

**GitHub and Linear are operational for authenticated minimal read-only use (high confidence).** A second Console-created test attached Clevin's actual MCP credential vault. Linear `get_user` and GitHub `get_me` each completed once, were allowed, had zero failures and produced matching result events. Modal claimed the session and ran its lease/heartbeat loop but logged no local execution for either call, directly confirming that Anthropic's control plane—not the self-hosted sandbox—executes these remote MCP tools. The test did not read ticket/repository content or exercise writes, so broader resource- and write-level authorization remains intentionally untested.

Two browser-created diagnostic sessions were run on Aug 27, 2026. The local-tool lifecycle test used a `$0.25` cap and completed `end_turn` for `$0.08`. The MCP test also used a `$0.25` cap; its single model turn completed both integrations but used `$0.34` after the Console's documented one-turn budget overrun, primarily for a 54.2k-token prompt-cache write. The latter remained heartbeating after `budget_reached`, so only its verified Modal test container was stopped; graceful cancellation posted work stop successfully. Anthropic auto-reload was confirmed off and postpaid invoicing was not active. Modal can charge overages after included credit, but the workspace had `$29.89` of included credit remaining and neither test consumed a displayed cent. No billing setting was changed and no purchase was made.

## Scope and methodology

Read-only inspection covered:

- Anthropic's [Self-hosted sandboxes documentation](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes), including the worker, poller, `handle_item()`, tool runner, webhook and memory sections.
- The authenticated Anthropic Console pages for Clevin's agent, environment, sessions, queue overview and webhook subscription.
- Anthropic session and event metadata through the already-configured SDK/API. Event types, timestamps, status, and non-secret IDs were retained; prompts, tool arguments, results, headers and credentials were not.
- The authenticated Modal application UI, historical function-call table, deployment history, live container/sandbox state and application logs for Aug 23 23:40 through Aug 24 00:20 PDT, including the requested 23:46–23:50 and 00:13–00:14 windows.
- Two new browser-created Clevin sessions on Aug 27: one traced the local-tool lifecycle from webhook enqueue through normal work stop, and one attached Clevin's actual vault to verify a minimal read-only GitHub and Linear MCP call. The latter was traced through controlled termination after its `budget_reached` worker kept heartbeating. The Modal CLI was used only to add non-secret function-call/container evidence and to stop that single verified test container.
- Local source and tests at `/Users/hrabbani/clevin`, plus the installed `anthropic==0.125.0` SDK implementation. No repository or deployment files were changed.

Environment under investigation: `env_0152FZKRpy9f8uVw38Guzosy`.

Limitations:

- The Modal call drawer exposed input IDs but not sanitized webhook payload fields, and the Anthropic webhook UI exposed configuration but no delivery-history view. Top-level webhook event IDs therefore remain unavailable. No signatures or raw headers were inspected.
- Historical environment queue depth and worker-count graphs were not available as queryable records. Current stats were available.
- Effective Modal defaults for concurrency, autoscaling and retries were not all displayed. The deployed behavior and local decorators were inspected; conclusions about omitted settings are labeled as implementation inference.
- Deployed source provenance could not be cryptographically matched to the working tree, although deployment version, SDK version, function behavior and logs are consistent with it.
- The original requested dates did not state a year. Console/API timestamps establish the inspected activity as Aug 23–24, 2026 PDT.

## Documented architecture

The documentation establishes the following data and responsibility boundary:

- Anthropic's control plane retains agent orchestration, session state and Claude model execution.
- The self-hosted environment executes tools, owns its filesystem and controls tool network egress.
- Tool inputs and outputs still cross Anthropic's control plane. A self-hosted worker receives tool-use events and submits tool-result events.
- An environment queue holds **session work items**, not one work item per tool call.
- A worker claims a session, acknowledges the claim, heartbeats the lease, and then handles that session's tool loop.
- `sessions.events.tool_runner()` is the per-session event/tool loop. It is distinct from both queue acquisition and Claude's model loop.
- `work.poller(..., drain=True)` exits after the queue becomes empty. With `block_ms=None`, the check is non-blocking. `reclaim_older_than_ms=2000` permits reclamation of work claimed but not acknowledged within two seconds. `auto_stop=False` leaves the stop responsibility to `handle_item()` or the launched sandbox.
- The webhook-triggered pattern subscribes to `session.status_run_started`; Anthropic specifies that it fires at **every** transition to `running`. The webhook wakes infrastructure, which drains available session work.
- Work is queued when a session is created and can be queued again when a long-dormant session receives new input. A work item therefore represents ownership of a session activation, not a tool call or model request.
- `EnvironmentWorker.handle_item()` handles one already-claimed work item, including session context, tool execution and cleanup. The always-on `.run()` pattern performs acquisition continuously instead.
- Memory stores are downloaded/mounted for a claimed session, synchronized during execution and flushed during final cleanup, subject to the configured deletion policy.

Three loops must not be conflated:

1. **Environment acquisition loop:** `work.poller()` asks the shared environment queue for claimable *sessions*.
2. **Claimed-session tool loop:** `EnvironmentWorker.handle_item()` and the session tool runner consume tool-use events and submit tool results for one claimed session.
3. **Claude reasoning/model loop:** Anthropic starts model runs, emits tool use, accepts tool results and starts subsequent model runs until an end condition.

Consequently, one `/work/poll` request is not one tool call. It is a request for a claimable session work item.

## Actual Clevin architecture

Clevin implements the documented webhook-triggered, sandbox-per-session variant:

- Modal app `clevin`, deployed app ID `ap-rntdxyiE8GU1aYtc3PwBiN`, contains HTTP function `webhook` (function ID `fu-4umqt1TZoqEH0dIUmgJhKc`).
- The handler is a FastAPI-style Modal POST endpoint with a 300-second function timeout. It awaits webhook verification and queue draining.
- The handler verifies the Anthropic signature before acting and ignores all event types except `session.status_run_started`.
- It polls the configured shared environment using exactly `block_ms=None`, `reclaim_older_than_ms=2000`, `drain=True`, `auto_stop=False`.
- For each claimed session, it creates or reuses a Modal Sandbox named by the session ID. Creation is awaited, but the sandbox's lifetime is detached from the HTTP invocation.
- A per-session Modal Volume subpath is mounted at `/workspace`. The sandbox has a 3,600-second timeout and termination grace enabled.
- The sandbox entrypoint creates an Anthropic `EnvironmentWorker` with `max_idle=120` seconds and `memory_sync_deletions="log_only"`, then awaits `handle_item()`.
- The entrypoint creates one tracked asyncio task, cancels it on SIGINT/SIGTERM and awaits cancellation. There is no unobserved `asyncio.create_task()` in the webhook handler and no `asyncio.shield()` in application code.
- No always-on environment worker exists in the inspected source. Current Modal state showed zero webhook containers and zero live sandboxes after the historical sessions completed.

The important implementation-specific difference from a webhook example that directly awaits `handle_item()` is that Clevin's HTTP handler awaits only **sandbox launch**, not the entire session. A short `POST /` therefore does not imply no work was processed. The long-running work continues in the detached Modal Sandbox.

Relevant source locations:

- HTTP endpoint: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/modal_app.py:31`
- Poller and sandbox handoff: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/claude_webhook_handler.py:42`
- Webhook verification/filter: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/claude_webhook_handler.py:97`
- Sandbox creation/reuse: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/sandbox_runtime.py:106`
- `handle_item()` and signal lifecycle: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/sandbox_entrypoint.py:53`
- Session/environment/budget binding: `/Users/hrabbani/clevin/runtime/src/clevin_runtime/agent_runtime.py:55`

## Responsibility and opinionation map

This distinction is essential: "Managed Agents behavior" is not one monolithic runtime. It spans Anthropic's hosted control plane, Anthropic's optional Python SDK helpers running in Modal, Modal's infrastructure semantics, and choices made in the Clevin repository or Console configuration.

| Behavior or decision | Primary owner | What is opinionated versus configured |
|---|---|---|
| Claude inference, agent/session state machine, persisted event log, run scheduling and budget enforcement | **Anthropic Managed Agents control plane** | Anthropic defines and operates the mechanism. Clevin supplies the agent prompt, model/effort selection and per-session budget value. |
| `session.status_run_started` firing on every transition to `running` | **Anthropic Managed Agents control plane** | Platform-defined event semantics. Clevin chose to subscribe its webhook to this one event type. |
| At-least-once webhook delivery, stable event ID across retries, unordered delivery and 5–120 second retry backoff | **Anthropic Managed Agents control plane** | Platform delivery contract. Clevin verifies signatures but does not currently log or deduplicate the event ID. |
| Shared environment queue, exclusive session-work claim, ACK, heartbeat lease and stop API | **Anthropic Managed Agents control plane** | Platform protocol. The queue contains session activations, not tool calls. |
| Model-request → tool-use blocker → tool-result → next model-request loop | **Anthropic Managed Agents control plane** for reasoning and state; **self-hosted worker** for local tool execution | Anthropic decides when the model runs and persists the transitions. Clevin's worker fulfills only locally executable tool events. |
| Remote MCP connector execution | **Anthropic Managed Agents control plane** | Managed Agents connects to the configured MCP servers. Clevin provisioned the Linear/GitHub server definitions and permissions. |
| `work.poller()` ACK-before-yield, generated worker IDs, heartbeats, SSE/history reconciliation, serial local-tool dispatch, result retry and final cleanup | **Anthropic Python SDK helper running inside Modal** | Anthropic-authored client-side opinion, not hidden server execution. Clevin elected to use these helpers and pinned SDK `0.125.0`. |
| Idle stop only after `end_turn`, not `budget_reached`, in the inspected SDK | **Anthropic Python SDK helper** | Current helper behavior. Clevin's `max_idle=120` supplies the duration but not the reason filter. |
| HTTP request queueing/cold starts, container execution, detached Sandbox processes, Volume mounts and platform timeout enforcement | **Modal platform** | Modal provides the primitives and runtime lifecycle. Clevin selects how to compose them and the explicit timeout values. |
| One HTTP drain attempt for every accepted webhook; `block_ms=None`, `drain=True`, `reclaim_older_than_ms=2000`, `auto_stop=False` | **Clevin repository** | Explicit application policy in `claude_webhook_handler.py`; these are not forced by Managed Agents. They follow Anthropic's webhook-worker example. |
| Sandbox-per-session handoff instead of awaiting `handle_item()` in the HTTP request | **Clevin repository** | Explicit architecture in `sandbox_runtime.py`. This is why HTTP success and session success are intentionally decoupled. |
| Per-session workspace Volume, 300-second HTTP timeout, 3,600-second Sandbox timeout, 120-second resume grace, deletion policy, credentials and network policy | **Clevin repository / operator configuration** | User-authored choices applied through Modal and Anthropic resources. |
| Smoke-test branch, ticket workflow, model/effort, tools, MCP permissions and default `$5` production session budget | **Clevin agent definition and repository provisioning** | Product policy supplied by Clevin, executed inside the Managed Agents framework. The fresh Console test overrode only that session's budget to `$0.25`. |
| Repeated empty drains | **Emergent interaction** | Anthropic emits every-run wakeups; Clevin drains the shared queue on every wakeup; the first Clevin sandbox already owns the session. No single layer independently mandates the resulting log pattern. |
| Budget-paused sandbox retention | **Emergent interaction / lifecycle gap** | SDK idle cleanup recognizes `end_turn` only; Clevin has no `budget_reached` watcher and permits a 3,600-second Sandbox lifetime. |

In short: Anthropic owns the durable orchestration and model loop; Modal owns compute/container mechanics; Anthropic's SDK implements the client worker protocol inside that compute; Clevin chooses the dispatcher topology, polling parameters, sandbox lifecycle, local tools and product policy.

## Deep dive: Anthropic–sandbox interaction protocol

This section separates the public Managed Agents contract from behavior observed in the Python SDK pinned by Clevin (`anthropic==0.125.0`). The SDK is client-side code running in Modal, not hidden Anthropic server code, but it exposes the protocol Clevin actually speaks to Anthropic's control plane.

### The webhook is a wakeup, not the work transport

Anthropic documents `session.status_run_started` as firing **at every session status transition to `running`**, not only at initial session creation. A delivery contains a top-level webhook event ID and a `data.id` identifying the triggering session; it does not contain the environment work item, a tool call, or the per-session work secret. Clevin verifies that delivery, then deliberately ignores the triggering session ID and drains the shared environment queue.

That design has two consequences:

- A webhook from session A may cause the dispatcher to claim session B, or several sessions, because the queue—not the webhook payload—is authoritative.
- A webhook retry is safe with respect to exclusive queue claiming but can add a redundant Modal invocation. Anthropic documents the top-level `event.id` as stable across retries; Clevin currently neither logs nor deduplicates it.

There is no inbound Anthropic connection to a per-session Modal Sandbox. The two network paths are:

1. Anthropic webhook delivery to the public Modal dispatcher endpoint.
2. Outbound HTTPS/SSE from the sandbox worker to Anthropic's work and session APIs.

After dispatch, webhooks are not the tool transport. The long-lived SSE session event stream and event POSTs are.

### Protocol sequence

```text
Client              Anthropic control plane       Modal webhook       Modal session sandbox
  | create session          |                           |                       |
  |------------------------>| queue session work       |                       |
  |                         | status -> running         |                       |
  |                         |--- signed webhook ------->|                       |
  |                         |<-- poll environment ------|                       |
  |                         |--- session work+secret -->|                       |
  |                         |<-- ACK work --------------|                       |
  |                         |   work: queued->starting  |--- create sandbox --->|
  |                         |<-- final empty poll ------|                       |
  |                         |--- empty ---------------->|                       |
  |                         |                           | return HTTP 200        |
  |                         |<================ heartbeat lease =================|
  |                         |<================ open session SSE =================|
  |                         |--- agent.tool_use ================================>|
  |                         |   status idle/requires_action                      | run local tool
  |                         |<-- user.tool_result ================================|
  |                         |   all blockers resolved -> running                 |
  |                         |--- signed webhook ------->|                       |
  |                         |<-- nonblocking poll ------|                       |
  |                         |--- empty ---------------->| queue drained          |
  |                         |--- next model request ---------------------------->|
  |                         |        ...repeat event/tool loop...                |
  |                         |--- idle/end_turn or budget_reached --------------->|
  |                         |<================ heartbeat until worker exits =====|
  |                         |<-- force stop work ================================|
```

### Queue claim, ACK and lease

The environment work resource has an independent state machine: `queued -> starting -> active -> stopping -> stopped`.

- `/work/poll` returns at most one claimable work item. The item contains a separate work ID, `data.type=session`, the session ID, environment ID, state/timestamps and—only on the poll response—an optional per-session secret.
- The SDK logs `claimed work` immediately after receiving the poll response, **before ACK**. Therefore that line alone means “poll returned an item,” not “ACK definitely succeeded.”
- The poller ACKs before yielding the item to Clevin. ACK moves the item from `queued` to `starting` and removes it from the queue. Clevin can launch a sandbox only after that ACK succeeded.
- `reclaim_older_than_ms=2000` applies only to work that was claimed but remains **unacknowledged**. It does not reclaim the already-ACKed active HUM-6 lease every two seconds.
- The sandbox's first heartbeat uses an optimistic-concurrency sentinel; each later heartbeat echoes the server's previous heartbeat timestamp. A mismatched value returns 412, which the SDK treats as lost ownership and cancels the runner rather than risking two workers serving the same session.
- The heartbeat response supplies the effective lease TTL. The SDK normally sends again at half that TTL, capped at 30 seconds. It stops the runner if the server reports `stopping`/`stopped` or refuses to extend the lease.
- Transient heartbeat errors are retried only while a successful heartbeat remains within the known lease TTL. Beyond that the SDK assumes the lease is lost and does not send a stop for work that may now belong to another worker.
- On ordinary runner exit, `handle_item()` sends `work.stop(force=True)` in a shielded cleanup block. A 409 means the work was already stopped and is ignored.

For all four inspected Clevin records, the server happened to use the session ID as the work ID. The public API models work and session IDs as distinct concepts, so code and instrumentation should not rely on equality.

Because Clevin does not pass `worker_id`, every new poller instance generates its own hostname-plus-random ID and sends it as `Anthropic-Worker-ID`. Clevin creates a poller per webhook request. Therefore Console `workers_polling`—defined as worker IDs that polled in the last 30 seconds—can count transient webhook drains as many “workers”; it is not a count of live per-session sandboxes or persistent worker processes.

### Credential handoff and scoping

Clevin uses three distinct authority scopes:

- The ordinary organization/workspace API key stays in the client that creates and inspects sessions. It is not forwarded to the tool sandbox.
- The environment key authenticates the dispatcher to the shared environment queue. Clevin forwards it through Modal's secret mechanism to the worker process.
- The poll response may carry an opaque per-session work secret. Clevin forwards it only to the sandbox for that claimed session. The installed SDK extracts a narrower session credential and prefers it for that item's heartbeats, stop calls, session retrieval/event stream/event sends and memory-store operations. The memory APIs reject the environment key and require this session-scoped authority.

The work secret is ephemeral handoff material: the API documents it as populated on the poll response and null on ordinary retrieval paths. A dispatcher must therefore forward it at claim time, exactly as Clevin does. It must never be logged or stored in the shared session volume.

The SDK's bash subprocess removes all `ANTHROPIC_*` variables from its inherited environment, so model-issued shell commands do not normally inherit the environment key or work secret. This is an SDK guardrail, not a complete sandbox boundary: bash itself is otherwise unrestricted inside the container.

Clevin also injects a GitHub credential into the sandbox and installs a Git credential helper. That variable is not covered by the SDK's `ANTHROPIC_*` scrub and is inherited by the model-controlled bash process. This appears intentional for repository work, but it means Modal sandbox isolation, egress policy and the credential's own least privilege—not the Anthropic SDK—bound that credential's blast radius.

### Session event and local tool loop

`SessionToolRunner` opens the live session SSE stream **before** reconciling persisted history. This ordering closes the gap in which an event could otherwise arrive between listing history and attaching the stream. It then:

1. Lists persisted events and builds sets keyed by event/tool-use ID.
2. Matches `agent.tool_use -> user.tool_result` and `agent.custom_tool_use -> user.custom_tool_result`.
3. Deduplicates overlap between history and the live stream by event ID.
4. Queues unanswered calls, executes them one at a time, and posts the matching result event.
5. Reconnects the stream with capped exponential backoff and reconciles again.

The local runner distinguishes three tool paths:

| Event | Executor | Result path in Clevin |
|---|---|---|
| `agent.tool_use` | Modal Sandbox | SDK runs `bash/read/write/edit/glob/grep`; posts `user.tool_result` |
| `agent.custom_tool_use` | Modal only if Clevin registers that custom tool | SDK posts `user.custom_tool_result`; an unowned name is left pending for another client |
| `agent.mcp_tool_use` | Anthropic-side MCP connector | Deliberately excluded from the sandbox runner; Anthropic emits the MCP result |

Clevin registers the standard agent toolset, not extra custom tools. Its configured GitHub and Linear MCP tool calls therefore run through Anthropic's MCP infrastructure, whereas shell/git and filesystem tools run in Modal. “All tools run in Modal” would be incorrect for this agent.

For built-in tools, the control-plane state cycle is:

1. Anthropic completes a model request and emits one or more `agent.tool_use` events.
2. The session becomes `idle` with `stop_reason=requires_action` and lists every unresolved event ID.
3. The Modal runner executes local calls serially and posts each result. Tool-result events are processed immediately.
4. If blockers remain, Anthropic re-emits `session.status_idle/requires_action` with the remaining IDs; it does not transition through `running` yet.
5. When all blockers are resolved, Anthropic transitions the session to `running`, emits the run-start webhook and begins the next model request.

The HUM-6 event counts validate this precisely:

- 65 `session.status_running` events.
- 64 run cycles ended in local-tool `requires_action`; the last ended in `budget_reached`.
- 79 `agent.tool_use` and 79 matching `user.tool_result` events.
- 80 `requires_action` idle events.
- 16 runs requested more than one local tool; the observed maximum was two. Resolving the first of two caused the extra re-emitted idle event, so `64 + 16 = 80`.
- 8 `agent.mcp_tool_use` and 8 MCP result events were handled outside the local runner.

This explains why there can be fewer run-start webhooks than tool calls, and why neither count should be inferred from queue-poll count.

The Aug 27 controlled run exposes the handoff especially clearly. The user event entered Anthropic at 16:08:19 PDT and immediately produced a webhook input. Modal did not start executing that cold request until 16:08:48. The dispatcher claimed and ACKed the session by 16:08:51, but the separate sandbox did not start `SessionToolRunner` until 16:08:55. The runner then attached SSE, reconciled persisted history, and found the first already-persisted `bash` tool-use event almost immediately. This demonstrates why Managed Agents can tolerate slow sandbox startup: Anthropic durably holds session/work state and tool events, while the late worker catches up from history before following the live stream. The control plane can run Claude up to a tool blocker without a live sandbox process; only fulfillment of the local tool call requires Modal.

The same run also distinguishes result transport from wakeups. Each local tool completed in the sandbox and was submitted with `POST /v1/sessions/{session}/events`; those result POSTs are the actual sandbox-to-control-plane continuation mechanism. The public `POST /` requests were separate webhook wakeups to the dispatcher. Conflating these two POST paths is the central source of the misleading log interpretation.

Tool execution is protected but not exactly-once:

- Local calls are serialized and bounded by a 150-second outer runner timeout; the persistent bash helper has a 120-second inner timeout and is torn down on timeout/cancellation.
- Async tools share the event loop with heartbeats, so custom async tools must not block it. Sync tools run on a worker thread.
- A transient tool-result POST retries with exponential backoff bounded by the live work-lease TTL. Fatal 4xx responses stop retrying.
- If a tool produces a side effect but its result never reaches Anthropic, reconciliation can see the call as unanswered and execute it again. A worker crash between side effect and result has the same risk. Self-hosted tool execution should therefore be treated as **at least once**, and externally mutating custom tools should implement their own idempotency keyed by tool-use/event ID.

Permission-gated built-in calls are held until a matching `user.tool_confirmation`; denied calls are never executed. Clevin's inspected agent uses an always-allow policy, so this gate did not explain its idle periods.

### Filesystem, skills and memory interaction

Before tools run, `handle_item()` starts the heartbeat, retrieves one stable session snapshot, downloads the resolved agent's skills into `/workspace/skills/<name>/`, and materializes attached memory stores. Heartbeating begins first because a slow skill or memory download must not let the lease expire and create split-brain execution.

The standard file tools canonicalize paths and enforce symlink-aware confinement to `/workspace` plus explicitly allowed memory roots. `write` and `edit` reject read-only memory roots. Bash ignores those path guards and relies entirely on the Modal sandbox boundary.

For Clevin's attached memory store, the SDK:

- Uses the per-session credential to download each store to its configured path under `/mnt/memory`.
- Adds memory directories to the tool context's allowed roots.
- Checks for synchronization after each completed local tool call, but runs it at most once per 15-second default interval.
- Treats Anthropic's memory store as source of truth during conflicts, while uploading uncontested local changes.
- Runs a final reconciliation on a clean end, then a best-effort write-only flush bounded to 30 seconds, and removes directories it created.
- Uses Clevin's `memory_sync_deletions="log_only"`, so local delete candidates are audited but not propagated as server deletes.

The per-session Modal Volume mounted at `/workspace` is separate from these temporary `/mnt/memory` directories. It gives Clevin durable workspace files across sandbox recreation for the same session ID. Anthropic does not manage or checkpoint that volume; Clevin/Modal owns its retention and isolation.

### Exit semantics and the budget-reached gap

`SessionToolRunner` exits for `session.status_terminated`, `session.deleted`, consumer cancellation, or after an `end_turn` idle remains quiet for `max_idle`. It intentionally does **not** arm the idle clock for `requires_action`, because the worker must remain available to execute/await pending tool work. In SDK 0.125.0, it also does not arm for `budget_reached`.

That distinction is visible in Clevin's work history:

| Session/final reason | Final idle -> last heartbeat | Final idle -> work stopped | Interpretation |
|---|---:|---:|---|
| Smoke / `end_turn` | 107.445 s | 120.416 s | Matches Clevin `max_idle=120` |
| HUM-5 long / `end_turn` | 99.247 s | 120.506 s | Matches Clevin `max_idle=120` |
| HUM-6 / `budget_reached` | 1,633.739 s | 2,022.591 s | Lease stayed active until the one-hour sandbox boundary; stop followed 388.852 s after last heartbeat |

HUM-6's final idle was 00:17:45.495 PDT, its last heartbeat was 00:44:59.234, and the work item stopped at 00:51:28.086. The Sandbox was launched around 23:45:19 with a 3,600-second timeout, so the heartbeat endpoint aligns with Modal's one-hour limit, not with the session budget event. Current upstream Python SDK source still documents/implements the idle timeout only for `end_turn`; upgrading alone should not be assumed to fix this.

This does not invalidate the empty-queue explanation. It is a separate cleanup/cost issue: after Claude has paused at budget, the local worker has no tool to execute but can keep its lease and Modal Sandbox alive until an external stop or the sandbox timeout.

## End-to-end lifecycle

1. A client creates a Clevin session specifying agent/version, `env_0152FZKRpy9f8uVw38Guzosy`, resources and budget.
2. Anthropic stores/orchestrates the session and enqueues one session work item for that environment.
3. Anthropic begins a model run and emits `session.status_run_started` to the configured webhook.
4. Modal invokes `POST /`. Clevin reads the body and verifies the webhook signature before processing it.
5. The handler filters for `session.status_run_started` and invokes `_drain_work()` for the configured environment.
6. `work.poller()` makes a non-blocking `/work/poll`, claims and acknowledges a session item if one is available, and yields it.
7. Clevin creates or reuses a session-named Modal Sandbox and forwards non-loggable opaque work credentials securely.
8. The poller checks again. Because `drain=True`, the first empty result logs `queue drained` and ends this acquisition loop.
9. The Modal HTTP handler returns 200 with launch metadata. This proves only successful HTTP handling/dispatch.
10. Independently, the sandbox entrypoint starts `EnvironmentWorker.handle_item()` for the claimed work and begins lease heartbeats.
11. The worker retrieves session context/resources, sets up the workspace/memory view and enters the session tool runner.
12. Anthropic runs Claude. When Claude emits a tool use, the worker executes the tool locally in Modal and submits a `user.tool_result` event.
13. Anthropic begins the next Claude run. That transition emits another `session.status_run_started` webhook.
14. A new Modal HTTP invocation drains the shared environment queue. The original session remains owned by its sandbox, so the new non-blocking poll normally finds nothing and immediately logs `queue drained`.
15. Steps 12–14 repeat. The existing sandbox—not the short webhook request—runs the tool loop.
16. On `end_turn`, the runner waits Clevin's 120-second resume grace; if no new event arrives it exits, performs final memory synchronization/cleanup and force-stops the work item. Termination, deletion, control-plane stop, lease loss, failure or cancellation have separate immediate exit paths. SDK teardown is shielded against cancellation.
17. `budget_reached` is different: the session pauses before its next model request, but SDK 0.125.0 does not treat that idle reason as an exit. Without another stop signal, Clevin can continue heartbeating until Modal's 3,600-second sandbox timeout.
18. After a normal stop the sandbox exits and the environment returns to zero queued/processing work. A later message to a long-dormant session can requeue that session's work; Clevin creates a fresh sandbox using the same session-specific Modal Volume. A webhook invocation can still return 200 regardless of the session's final status.

## Log interpretation

`poller starting ... drain=True auto_stop=False`

: A webhook invocation entered the *environment acquisition loop*. It does not mean `handle_item()` started and does not identify a tool call.

`GET ... /environments/{id}/work/poll?...reclaim_older_than_ms=2000` / `200 OK`

: Anthropic successfully answered a queue request. HTTP 200 says nothing by itself about whether an item was returned. `block_ms=None` omits long polling, so an empty result is fast.

`claimed work work_id=... work_type=session`

: The poll response contained one session item. The installed SDK emits this log immediately before its ACK call; the item is yielded to Clevin only after ACK succeeds. This line appeared once for HUM-6, and the subsequent sandbox launch proves that iteration advanced past ACK.

`queue drained`

: With `drain=True`, an empty poll ended the acquisition iterator. It can be the final check after one or more claims, or the very first check in a redundant webhook invocation. It is not a session-completion signal.

`POST / -> 200 OK`

: Signature verification, filtering, draining and any sandbox-launch await completed without an unhandled exception. Because Clevin detaches work into a Modal Sandbox, this response is not expected to remain open for the session duration and does not prove Claude completed.

Sandbox heartbeat, local tool execution and `POST .../sessions/{id}/events`

: These are evidence that `handle_item()` and its tool runner are alive inside the detached session sandbox. They may be interleaved in the same Modal application log view with webhook-function logs.

## Correlated timeline

### Fresh controlled run — Aug 27, 2026 PDT

| Time | Modal request / container | Anthropic-side event | Queue/work result | Interpretation |
|---|---|---|---|---|
| 16:07:56 | — | Session `sesn_01BuA8fgCHoWtr479aY7H4ne` created idle against the exact Clevin environment | Work exists but no user run yet | Console-created `$0.25` diagnostic session |
| 16:08:19.595 | Initial HTTP input created; `fc-01M12QPF8BXSCJJM4XWWW35D54` | Read-only user event transitions session to running | Webhook waits in Modal | Initial run-start wakeup |
| 16:08:20.305–48.528 | Modal cold-start scheduling, 28.223 s | Anthropic retains session/work state | Queue item remains claimable | No sandbox is required until local tool fulfillment |
| 16:08:48.632–51.853 | Dispatcher container `ta-01M12QPFVR3PFN6S737WNQYACR` polls | — | Claims work/session at 50.585; ACK 51.158; final drain 51.853 | One session work item, not a tool call |
| 16:08:51.881 | Initial webhook execution finishes successfully | — | Work is no longer on the queue | HTTP success still precedes session completion |
| 16:08:55.319–56.642 | Warm HTTP call `fc-01M12QQJ37Q716VGJP1AB3WJZ1`, 1.309 s | Probably retry of the slow initial delivery | First poll empty | Timing fits a caller timeout plus Anthropic's documented minimum retry backoff; event ID unavailable |
| 16:08:55.508–55.906 | Session sandbox `ta-01M12QQEJ761DNTE8ZRC7X6CWS` starts runner, heartbeat, SSE and history reconciliation | First persisted local tool use is ready | Existing lease active | `handle_item()` and tool runner are reached |
| 16:08:56.220 | Sandbox submits first local tool result | Session can resume model reasoning | Existing work remains leased | Result event, not webhook, advances the tool loop |
| 16:08:56.738–57.651 | Warm HTTP call `fc-01M12QQKFT4RCY05K8RKZGPB3J`, 0.914 s | Run-start transition after result | Empty first poll | Legitimate no-op wakeup while sandbox owns session |
| 16:08:57.945–58.240 | Sandbox executes and submits second local tool result | Session resumes again | Existing work remains leased | Second local loop iteration |
| 16:08:58.686–09:02.191 | Warm HTTP call `fc-01M12QQNCH6XXASNTRSY4FKAZR`, 3.510 s | Next run-start/end-turn cycle | Empty first poll | Legitimate no-op wakeup; session becomes idle `end_turn` |
| 16:09:26–10:57 | Sandbox heartbeats about every 30 s | Session remains idle/end-turn | Lease deliberately retained for resume grace | HTTP function has already returned |
| 16:11:02.706–11:03.036 | Sandbox worker logs 120-second idle expiry | — | `work.stop(force=True)` returns 200 | Complete normal cleanup path |

The fresh run produced four Modal HTTP inputs: one initial event, one probable retry of that slow delivery, and two later run-transition events. It produced two local tool executions inside one sandbox and only one environment work claim. This directly disproves any one-POST-per-tool or one-poll-per-tool model.

### Fresh MCP connectivity run — Aug 27, 2026 PDT

| Time | Anthropic Console / control plane | Modal / self-hosted environment | Interpretation |
|---|---|---|---|
| 16:28:44 | Session `sesn_01ERaNc2AaTXEX3KL8EZNmT4` created with the exact Clevin environment and `Clevin MCP Credentials` vault | — | Both configured servers changed from `Needs vault` to `Connected via Clevin MCP Credentials` before launch |
| 16:29:06 | Session enters running; model requests one minimal read-only identity call from each MCP server | Run-start webhook is emitted | No ticket or repository content was requested |
| 16:29:08–09 | Linear `get_user` and GitHub `get_me` each produce `agent.mcp_tool_use` and `agent.mcp_tool_result` | Dispatcher later claims the session work; the session sandbox starts its runner | MCP calls are control-plane integrations even though the self-hosted session still has a leased worker |
| 16:29:09 | Tools inspector: Linear 1 call / 0 failed; GitHub 1 call / 0 failed; 2 completed, none denied | No `executing tool` line for either MCP operation | Authentication, routing and a minimal read-only operation passed for both integrations |
| 16:29:14.244–16.711 | — | Poll claims the session work at 16:29:14.788, ACKs at 16:29:15.198, then drains | The webhook/queue path remains session-scoped; it did not carry the MCP calls |
| 16:29:19.233 onward | Session is already idle with reason `budget_reached` | Sandbox tool runner starts, opens the event stream and heartbeats; it performs no local MCP execution | The sandbox observes/owns the self-hosted session lifecycle, while Anthropic owns MCP transport and execution |
| 16:32:40 | — | Investigator stops only the controlled test container; graceful cancellation posts work `/stop` (200), then logs `worker cancelled` | Avoids an unnecessary one-hour `budget_reached` sandbox tail; no pre-existing container or session was touched |

The `$0.25` cap was exceeded by one model turn to `$0.34`, which the Console explicitly allows. The cost was dominated by a 54,229-token prompt-cache write; the two MCP calls themselves completed in about one second. The cap prevented a final prose response, but not the two tool calls or their result events, so it does not weaken the connectivity finding.

### Historical requested windows — Aug 23–24, 2026 PDT

Sub-second values are rounded where the UI did not expose a request ID.

| Time | Modal request | Webhook/session event | Trigger session | Poll result | Claimed work/session | Session status/evidence |
|---|---|---|---|---|---|---|
| 23:45:11.868 | — | Session created | `sesn_01Ln…G5Eh` | — | — | Environment exactly matches target |
| 23:45:12.345 | Enqueued 23:45:12; cold start 5.317 s; execution 1.712 s | First `session.status_running` / run-start delivery | same | Claim, then final drain | Claimed 23:45:19.298; work ID equals session ID | Sandbox launched; first model request 23:45:14.039 |
| 23:45:23–23:45:39 | Five warm POSTs, each about 0.33–0.38 s | Distinct run cycles after tool results | same | Empty/drained | None | Existing sandbox owns session |
| 23:50:09.207 | — | Tool result submitted | same | — | Existing claim | Tool loop active |
| 23:50:10.477 | POST execution 0.509 s | `session.status_running` | same | Poll 23:50:11.160; 200 then drained 23:50:11.373 | None | Next agent tool use 23:50:12.877 |
| 23:50:13.208–15.909 | POST enqueued 23:50:13, cold-started 23:50:15; execution 0.687 s | Tool result, then another `session.status_running` at 23:50:13.294 | same | Empty/drained | None | Heartbeat for original work at 23:50:25.899 |
| 23:50:26.55–27.875 | Warm POST about 0.472 s | Edit/bash tool uses and results, then run-start | same | Empty/drained | None | Local sandbox execution visible |
| 00:13:09.367 | Cold-start POST, execution 0.750 s | `session.status_running` | same | Empty/drained | None | Session remains owned |
| 00:13:37.638–38.452 | POST about 0.647 s | Tool calls, results, then `session.status_running` | same | Empty/drained | None | One-for-one run-cycle correlation |
| 00:13:47.832–48.278 | POST about 0.592 s | Tool use, result, then `session.status_running` | same | Empty/drained | None | Same |
| 00:14:01.267–02.049 | POST about 0.602 s | Tool uses/results, then `session.status_running` | same | Empty/drained | None | Same |
| 00:14:05.369–05.470 | POST about 0.720 s | Tool result, then `session.status_running` | same | Empty/drained | None | Same |
| 00:17:45.495 | — | Final status idle, reason `budget_reached` | same | — | Work remains active | 79 local tool uses/results complete; no next model request |
| 00:44:59.234 | — | — | same | — | Last recorded work heartbeat | About 27m14s after budget pause; near one-hour sandbox limit |
| 00:51:28.086 | — | — | same | — | Work state becomes `stopped` | 33m43s after budget pause; 6m29s after last heartbeat |

The webhook event and Modal request ID columns remain unavailable at delivery-ID granularity. The timestamp sequence nevertheless differentiates distinct model-run transitions from retries of one event.

## Anthropic Console findings

### Environment

- Display name: `Clevin Modal Self-Hosted Environment`
- ID: `env_0152FZKRpy9f8uVw38Guzosy`
- Type: Self-hosted
- UI showed “Updated Aug 23”; exact creation timestamp was not exposed.
- Current queue/API stats: depth 0, pending 0, processing 0, oldest queued item absent, workers polling 0, idle workers 0.
- No current environment error was visible.
- Historical queue and worker counts were not exposed.

### Agent

- Name: `Clevin Native Ticket-to-Green-PR Agent`
- ID: `agent_01Eef1xLtkWW2cDg1shFUpms`
- State/version: Active, v5
- Model: `claude-opus-5`, Medium effort
- Capabilities summarized: Anthropic agent toolset plus configured GitHub and Linear MCP access. Sensitive prompt/configuration content was not retained.
- Session creation code explicitly supplies the environment ID; no Anthropic deployment ID was attached to inspected sessions.

### GitHub and Linear MCP verification

- **Linear: pass (high confidence).** With `Clevin MCP Credentials` attached, the Console Tools inspector recorded `linear · get_user`: one allowed call, zero failures, about 1.0 second, with a matching result event.
- **GitHub: pass (high confidence).** The same run recorded `github · get_me`: one allowed call, zero failures, about 1.0 second, with a matching result event.
- The test deliberately did not read ticket or repository contents and did not attempt writes. It therefore verifies vault attachment, authentication, MCP routing, permission admission and a minimal read-only operation. It does **not** by itself prove every one of the 22 Linear or 83 GitHub tool permissions, repository-specific authorization, or write access.
- An earlier no-vault smoke session emitted MCP authentication errors because the Console-created session omitted the vault. Clevin's normal repository path supplies `vault_ids=[self.settings.vault_id]`; the successful vault-attached run proves the earlier errors were a test-construction artifact, not a production integration failure.
- Anthropic Managed Agents supplies the MCP client/orchestration, injects the selected vault into the configured servers and records `agent.mcp_tool_use` / `agent.mcp_tool_result`. Clevin declares the server URLs, permission policy and vault association in its provisioned agent/session configuration; Modal does not execute these remote MCP calls.

### Representative sessions

| Session | Created–updated UTC | Final condition | Model/tool evidence | Environment |
|---|---|---|---|---|
| `sesn_01FyD7NAz3KtQ3bWbDfERnqM` | 05:34:32–05:36:57 | `end_turn` | 8 agent tool uses, 8 results, 2 MCP uses | Exact match |
| `sesn_01JhebLpY3ehzdZvp8GxrJUH` | 05:47:44–05:51:48 | `end_turn` | 9 agent tool uses, 9 results, 36 MCP uses | Exact match |
| `sesn_01RSGpuNxTZFZnTTNb9WvkZX` | 06:04:31–06:29:38 | `end_turn` | 56 agent tool uses, 55 results, 9 MCP uses | Exact match |
| `sesn_01LnksfmKUCjwTJASMVsG5Eh` | 06:45:11.868–07:17:45.792 | `budget_reached` | 79 agent tool uses, 79 results, 8 MCP uses | Exact match |
| `sesn_01BuA8fgCHoWtr479aY7H4ne` | Aug 27 23:07:56–23:09:02 | `end_turn` | 2 local `bash` uses/results; no MCP call | Exact match |

The Console labels final sessions “Idle,” but the event reason matters: three representative sessions ended normally; HUM-6 ended because it reached its `$5` session budget. “Idle” and a 200 webhook response are not synonymous with task success.

The HUM-6 event stream showed 65 `session.status_running` transitions. Those transitions interleaved with model requests, tool use, `session.status_idle`/`requires_action`, tool results and subsequent model runs. This explains the large number of webhook invocations for one session.

The environment work-list endpoint adds a second, independent lifecycle view:

| Session/work | Work created | Latest ACK | Last heartbeat | Stopped | State |
|---|---|---|---|---|---|
| `sesn_01Fy…ERnqM` | 05:34:32.215 UTC | 05:36:40.120 | 05:38:44.512 | 05:38:57.483 | stopped |
| `sesn_01Jhe…xrJUH` | 05:47:44.713 UTC | 06:04:38.222 | 06:09:17.631 | 06:09:42.142 | stopped |
| `sesn_01RSG…WvkZX` | 06:04:31.576 UTC | 06:21:04.545 | 06:31:17.862 | 06:31:39.122 | stopped |
| `sesn_01Lnk…G5Eh` | 06:45:11.975 UTC | 06:45:19.454 | 07:44:59.234 | 07:51:28.086 | stopped |

The API currently returns these observed work IDs equal to their session IDs. Its schema nevertheless treats work ID and session ID as separate fields. The HUM-5 record also shows that a session's work resource can be reactivated/re-ACKed (a later `user.interrupt` was processed), so work history is not equivalent to one immutable “first claim” timestamp.

### Webhook

- Name/ID: `Clevin Webhook`, `wep_01VYcp1ZCwCzKo4KMEX7sfT5`
- Endpoint: `https://hrabbani-clevin--clevin-webhook.modal.run`
- State: Enabled; created Aug 23
- Subscription: exactly one event, `session.status_run_started`
- Anthropic documents at-least-once delivery: the same event can arrive more than once with one stable top-level `event.id`; up to three attempts use jittered exponential backoff from 5 to 120 seconds. Ordering is not guaranteed and webhooks are not a durable log.
- The fresh run's first Modal input took 28.223 seconds to start and 3.353 seconds to execute. A second input was created at 16:08:55.319, a timing pattern consistent with a retry after a caller-side timeout and the documented minimum backoff. Per-delivery event IDs were not exposed, so this remains a medium-confidence inference rather than a proven duplicate.
- No signing material was inspected.

## Modal findings

- App/deployment: `clevin`, app ID `ap-rntdxyiE8GU1aYtc3PwBiN`.
- HTTP function: `webhook`, ID `fu-4umqt1TZoqEH0dIUmgJhKc`.
- Production deployment: v6, Aug 23 23:20:03 PDT, Modal client 1.4.3.
- Older v1–v5 entries are deployment history; only v6 was marked Production. No second active Clevin deployment was found.
- Current state: zero live webhook containers, zero running calls and no live sandboxes.
- Local decorator explicitly sets HTTP timeout 300 seconds and does not set retries, concurrency or autoscaling. Therefore platform defaults apply; their effective historical numeric values were not exposed. No application retry wrapper exists.
- Session sandboxes explicitly use a 3,600-second timeout and enable termination grace.
- Historical requests were mostly 0.18–0.75 seconds of execution. Cold starts usually added about 3.5–5.3 seconds of queue/start latency. The first HUM-6 invocation was the exception: 1.712 seconds of execution because it claimed work and launched the sandbox.
- The fresh Aug 27 call table showed four POSTs. The initial call was enqueued at 16:08:19, waited 28.212 seconds for a cold container and executed 3.353 seconds while claiming/ACKing the work and launching a detached sandbox. The three warm calls executed in 1.309 seconds, 0.914 seconds and 3.510 seconds and each found no claimable work.
- The initial webhook function container and the tool-running sandbox had different Modal container IDs. The sandbox opened SSE, reconciled history, executed two local tools and posted two results while the webhook container handled subsequent empty drains. This is direct platform-level evidence of the dispatcher/sandbox split.
- In the MCP-only verification run, Modal claimed and ACKed the session work and started the sandbox session runner, but logged no `executing tool` entry for Linear or GitHub. The Console simultaneously recorded two completed MCP calls. This directly confirms that remote MCP execution is handled by Anthropic's control plane, whereas Modal is responsible only for self-hosted tools/filesystem and the leased session worker.
- Logs showed one HUM-6 work claim, continuing sandbox heartbeats and local tool executions, and many empty webhook polls while that sandbox was active.
- No always-on worker was present in source or live deployment state. No evidence showed another app sharing this environment, although Anthropic Console does not provide a global reverse index of every possible external consumer.
- Work-history timestamps show normal `end_turn` sandboxes stopping about 120 seconds after the final idle event, exactly matching the configured resume grace. HUM-6 instead heartbeated for another 27 minutes after `budget_reached`, reaching the one-hour sandbox boundary before its work eventually stopped.
- A Modal webhook poller is not the same thing as a Modal session sandbox. Because every POST constructs a fresh SDK poller with a generated worker ID, Anthropic's rolling `workers_polling` metric can reflect many recent dispatcher invocations even when only one long-lived sandbox owns work.

The call drawer exposed non-secret function-call and input IDs plus execution timelines, but not the sanitized webhook body fields needed to compare top-level event IDs. Delivery-attempt identity therefore remains unresolved. The endpoint is nevertheless high-confidence Anthropic webhook traffic: it is the sole configured endpoint, verifies Anthropic signatures, filters the subscribed event type, and invocation timestamps match Anthropic run-start transitions.

## Code findings

1. **Based on the documented webhook pattern:** Yes, with a Modal-specific sandbox-per-session handoff.
2. **Requires `session.status_run_started`:** Yes; all other verified event types return `ignored`.
3. **Signature verification:** Yes, before event filtering or polling.
4. **Await semantics:** The HTTP handler awaits `_drain_work()` and sandbox creation/reuse, but not `handle_item()`; the Modal Sandbox is a detached platform resource, not an untracked asyncio background task.
5. **Container termination after response:** The HTTP function container may scale down after returning. That does not terminate the separately created Modal Sandbox.
6. **`block_ms=None`:** Explicit.
7. **Retry after empty poll:** None. The SDK retries transient poll/ack failures, but `drain=True` returns immediately on an ordinary empty result.
8. **Claim logging:** SDK logs work ID/type. Application return data includes work/session/sandbox IDs, but Clevin does not emit a durable structured claim/launch-completion log itself.
9. **Stop ownership:** `auto_stop=False` delegates it; `EnvironmentWorker.handle_item()` owns force-stop/cleanup.
10. **Exception observation:** Sandbox launch errors are caught and sanitized by type. The sandbox entrypoint awaits the worker task, so its exceptions are observable. There is no orphaned handler task.
11. **Graceful shutdown:** Sandbox SIGINT/SIGTERM cancels and awaits the tracked worker task; SDK teardown shields final cleanup. The app does not explicitly log final session status.
12. **Multiple replicas:** Multiple webhook replicas can call the same shared queue concurrently, but queue claiming is exclusive. One may claim while others drain empty. This is possible by design, but not needed to explain the observed repetition.
13. **Budget pause:** The SDK idle watchdog arms only for `end_turn`; `budget_reached` leaves the runner attached and heartbeating. HUM-6 remained alive until the Modal timeout boundary.
14. **Launch-failure ownership:** Because ACK precedes the yielded item and Clevin sets `auto_stop=False`, `_drain_work()` owns cleanup as soon as it receives `work`. Its sandbox-launch exception path does not currently issue `work.stop`.
15. **Delivery idempotency:** The handler verifies signatures but does not persist the top-level webhook event ID. Anthropic retries use the same event ID; exclusive claiming preserves correctness, but duplicate deliveries still consume a Modal invocation/poll.
16. **Tool delivery semantics:** Stream/history reconciliation is robust to disconnects, but a successfully executed side effect whose result POST is not confirmed can be executed again. Local tool execution is not an exactly-once transaction with Anthropic's event store.

The installed SDK additionally shows that the poller acknowledges a claim before yielding it, logs `claimed work`, and makes one final empty check with `drain=True`. `handle_item()` starts heartbeats, obtains context/resources, runs tools one at a time through the session event runner, submits results, syncs memory and force-stops the work on clean exit unless the lease was lost.

## Hypothesis matrix

| Hypothesis | Supporting evidence | Contradicting evidence | Confidence / minimum unresolved evidence |
|---|---|---|---|
| A. Duplicate webhook delivery | Fresh initial POST spent 28.212 s cold-starting plus 3.353 s executing; another POST was enqueued 35.724 s after the first and about five seconds after a plausible 30 s caller timeout. Anthropic documents retries with 5–120 s jittered backoff and stable event IDs. | The two later fresh POSTs follow two tool-result submissions and fit distinct run-start transitions; the historical steady-state pattern also maps to run cycles. | **Medium for one fresh POST; low as primary/steady-state cause.** Compare top-level event IDs to prove the individual retry. |
| B. Multiple sessions share one environment | All four inspected sessions target the same environment; the poller drains a shared queue. | The key 23:45–00:17 trace is one HUM-6 session. | **True generally, low relevance** to repeated HUM-6 polls. Need overlapping session-creation timestamps to attribute a specific cross-claim. |
| C. Another worker/deployment claims first | Shared queues permit this in principle. | Exactly one claim was logged; same session sandbox heartbeated/executed; one production deployment found. | **Low.** A global worker registry or historical claim audit would close the residual gap. |
| D. Concurrent Modal replicas race | The fresh initial request and first warm request used the same HTTP container; platform concurrency could still overlap calls. Exclusive claiming makes losers drain empty. | The session tool loop ran in a distinct sandbox container, and all later dispatcher polls began after the first ACK. No competing claim appeared. | **Low causal importance.** Per-call replica/concurrency metadata would close the residual question. |
| E. Wrong environment | Would produce empty polls. | Agent session metadata and handler configuration exactly match `env_0152…zosy`; initial work was claimed there. | **Ruled out** for inspected sessions. |
| F. Webhook precedes queue visibility | Non-blocking initial checks could theoretically race propagation. | Fresh initial webhook was created at 16:08:19 and, despite a 28 s Modal cold start, successfully claimed the queued item at 16:08:50. Historical first webhook also claimed. Later empties occur while work is already ACKed. | **Low/unobserved.** Only a genuinely stranded first-run session would justify a targeted timing test. |
| G. `block_ms=None` causes timing race | It turns an empty state into an immediate return and supplies no grace period. | No lost initial work in either correlated run; fresh later empties are explained by an active lease and probable duplicate/run-transition wakeups. | **Medium as noise/cost amplifier; low as failure cause.** Do not change without evidence of stranded work. |
| H. Claim succeeds but handoff fails | ACK happens before sandbox launch; Clevin catches launch errors but, with `auto_stop=False`, does not explicitly stop the already-ACKed item. | Sandbox heartbeat, tools and results prove successful handoff for HUM-6; no launch error occurred there. | **Ruled out for the trace; medium design risk generally.** Inject a local launch failure in a non-deployed test or observe the work-state recovery interval. |
| I. `handle_item()`/tool runner fails | Possible class of failure. | 79/79 tool-use/result pairs and sustained heartbeats. | **Ruled out** for HUM-6. Inspect final exception/stop logs for any different failed session. |
| J. Handler returns before background work completes | POSTs are sub-second while sessions run many minutes. | None; source confirms detached Sandbox architecture. | **High—true by design**, not a bug. |
| K. Modal timeout/container lifecycle interrupts session | HUM-6 continued heartbeating until almost exactly the 3,600-second sandbox lifetime after `budget_reached`. | Its productive tool loop completed before the timeout; no tool call was shown interrupted. | **High for cleanup, low for productive-loop failure.** Modal's timeout ended an otherwise idle budget-paused worker. Container termination logs would establish the exact signal path. |
| L. POSTs are not Anthropic webhooks | Raw Inputs were unavailable. | Verified-signature handler, sole matching subscription, and exact run-transition timing. | **High confidence they are Anthropic webhooks.** Per-request sanitized event ID/type would make this conclusive. |
| M. Budget-paused worker is not released | SDK idle watchdog keys only on `end_turn`; HUM-6 heartbeated 1,633.739 s after `budget_reached`, unlike ~120 s cleanup for `end_turn`. | A later work stop did occur after the heartbeat ceased. | **High—confirmed lifecycle inefficiency.** Add final-reason and sandbox-exit logs to distinguish graceful cancellation from lease-expiry cleanup. |

## Answers to key questions

1. **Are the Modal POSTs Anthropic webhooks?** High confidence yes; exact raw request inspection was intentionally avoided/unavailable.
2. **What event type?** `session.status_run_started`, the only subscribed and accepted type.
3. **Duplicates or different sessions?** Both mechanisms are possible and were probably present in the fresh burst. Two POSTs followed distinct tool-result/run transitions for the same session. One early empty POST is probably a retry of the slow initial delivery after a 28.2-second cold start; event IDs are required to prove it. Other periods can also contain different sessions because the queue is shared.
4. **Does the queue receive session work?** Yes. HUM-6 was claimed once as `work_type=session`.
5. **Is another worker claiming sessions?** No evidence. The observed session was claimed by its Clevin sandbox.
6. **Does each session target the expected environment?** Yes for all four inspected sessions.
7. **Webhook/queue timing race?** Not observed. Fresh initial work remained visible through a long Modal cold start and was claimed. Later empties are explained by ownership and, for one call, a probable delivery retry.
8. **Is `block_ms=None` contributing?** It makes redundant wakeups immediately drain; it is not shown to cause lost work.
9. **Is `handle_item()` reached?** Yes, proved by heartbeats, local tools and submitted results.
10. **Where does the actual loop run?** Claude/model reasoning runs in Anthropic; the per-session event/tool runner and tool processes run in the detached Modal Sandbox.
11. **Are HTTP requests held open?** Only while Modal queues/starts the HTTP container and while Clevin polls and launches a sandbox; not for the session duration. The fresh initial request waited 28.2 seconds for a cold start but executed only 3.35 seconds and returned long before the sandbox's 120-second idle cleanup.
12. **Are sessions successful?** The fresh test and three historical sessions ended at `end_turn`; HUM-6 executed normally but paused at `budget_reached`. Its Managed Agents session stopped making model requests, but its self-hosted work lease and Modal sandbox remained alive until the sandbox lifetime expired. HTTP 200 is not the success criterion.
13. **Why repeatedly drained?** Every run-start webhook—and any retry—checks the shared queue. After the first call ACKs the session item, later handlers see nothing claimable because the detached sandbox owns it. The initial handler itself also logs `queue drained` after its successful claim because `drain=True` performs a final empty check.
14. **Correct, inefficient or broken?** The queue behavior is correct. Re-polling for every legitimate run transition is noisy/inefficient, and cold-start-driven webhook retries can add more no-op polls. Initial handoff and tool execution are operational. Budget-paused sandbox cleanup is separately inefficient/broken: HUM-6 retained its lease and sandbox for roughly 27 minutes after the budget pause, until the one-hour Modal boundary.

## Recommended observability

Add sanitized structured logs at these boundaries:

- After successful verification: webhook event ID, event type, triggering session ID, delivery-attempt number if the SDK exposes it, and a Modal invocation/replica ID supplied by the platform.
- Before/after `_drain_work()`: environment ID, poll start/end monotonic timestamps, claim count and outcome (`claimed`, `first_poll_empty`, `drained_after_claim`, `error`).
- Per claim: work ID, work type, claimed session ID, sandbox ID, `created` versus `reused`, and launch duration.
- Sandbox entry/exit: work/session/environment IDs, `handle_item` start/end, total duration, terminal reason and sanitized exception class/message.
- Tool execution: tool type, opaque tool-call ID if non-sensitive, start/end/duration and success/failure. Do not log arguments or output.
- Cleanup: final session idle reason, last heartbeat time/TTL, final memory-sync result, force-stop attempted/result, lease-lost indication, sandbox signal/exit code and work-state timestamps.
- Poller identity: explicit stable dispatcher/container ID. Do not let SDK-generated per-request worker IDs masquerade as persistent worker replicas in metrics.

Never log prompts, tool arguments/results, raw bodies, headers, environment/work/webhook secrets, GitHub tokens or authorization material. Use a shared correlation schema across HTTP-function and Sandbox logs; this is more useful than free-form messages.

## Recommended fixes

1. **P1 — Stop a claimed worker when the session becomes budget-paused.** Do not wait for the one-hour sandbox timeout. Before implementation, confirm whether the desired product behavior is to release immediately on `budget_reached` or retain a short grace for a budget update. A Clevin-side session-event watcher can cancel `handle_item()` after that grace; cancellation already runs shielded flush/cleanup. Current upstream runner behavior is also `end_turn`-only, so upgrading alone is not a sufficient plan.
2. **P1 — Explicitly stop an ACKed item when sandbox launch fails.** `_drain_work()` owns stop responsibility after `auto_stop=False`; its exception path currently records an error but leaves the ACKed item without a launched heartbeat owner. Send a best-effort `work.stop(force=True)` on launch failure, treating 409 as already stopped. This should be covered with a local mocked test before deployment.
3. **P1 — Add structured correlation logging.** Include webhook event ID/trigger session, poller ID, claim/ACK outcome, sandbox ID, session idle reason, work state and cleanup. This will resolve delivery retry and signal-path questions without logging content.
4. **P2 — Make externally mutating custom tools idempotent.** The SDK's event reconciliation prevents ordinary duplicate dispatch, but a side effect followed by an unconfirmed result can be retried. Key custom-tool side effects by tool-use/event ID. Clevin's standard file/bash tools cannot generally guarantee exactly once, so design workflows to tolerate replay.
5. **P2 — Add webhook event-ID idempotency for actual retries.** Store only event ID, expiry and outcome. The fresh cold-start trace likely contained one retry, so this now has observed value. It will not eliminate legitimate distinct run-start wakeups.
6. **P2 — Supply an intentional poller `worker_id`.** Use a non-secret Modal invocation/container correlation ID so `workers_polling` can be interpreted. Avoid reusing one ID across truly concurrent pollers.
7. **P2 — Coalesce environment drains if invocation cost/noise matters.** A short-lived per-environment dispatcher lock can let one webhook drain while concurrent handlers return `drain_in_progress`. Preserve a subsequent wakeup so newly enqueued sessions cannot be stranded.
8. **P3 — Consider a session-aware fast path.** If the triggering session maps to a healthy live sandbox, the handler could return `already_owned`. Because a webhook is only a wakeup for a shared queue, never skip the global drain without a design that guarantees another trigger for unrelated queued sessions.
9. **P3 — Measure cold-start retries before buying warmth.** The fresh dispatcher cold start was 28.2 seconds and likely triggered one retry. Log event IDs and Modal startup duration, then measure the duplicate rate. Consider a warm dispatcher or a different durable handoff only if measured retry latency/cost justifies continuous Modal spend.
10. **Do not change `block_ms` or the two-second unacknowledged reclaim window based on the empty-poll evidence.** A blocking poll would make redundant requests longer and more expensive. Neither setting controls an already-ACKed active session lease.
11. **Do not adopt an always-on worker solely to suppress these logs.** That changes the cost and failure model. Evaluate it only against measured webhook overhead.

## Controlled-test proposal

Two controlled tests were executed; no further test is currently justified.

| Field | Recorded value |
|---|---|
| Purpose | Trace a minimal read-only session from Console creation through Modal work stop |
| Correlation ID/title | `clevin-loop-20260827T230745Z` |
| Session/work ID | `sesn_01BuA8fgCHoWtr479aY7H4ne` |
| Environment | `env_0152FZKRpy9f8uVw38Guzosy`, exact match |
| Start/end | Created 16:07:56 PDT; user run 16:08:19; session idle/end-turn by 16:09:02; work stopped 16:11:03 |
| Budget/cost | `$0.25` cap; `$0.08` Anthropic list cost |
| Billing safeguards | Anthropic auto-reload off; existing credits used. Modal included credits available; no displayed credit reduction. No purchase/settings change. |
| Webhook event ID | Not exposed; input/function-call IDs recorded instead |
| Modal function calls | Initial `fc-01M12QPF8BXSCJJM4XWWW35D54`; later `fc-01M12QQJ37Q716VGJP1AB3WJZ1`, `fc-01M12QQKFT4RCY05K8RKZGPB3J`, `fc-01M12QQNCH6XXASNTRSY4FKAZR` |
| Work claimed | Yes, once, at 16:08:50.585; ACK 16:08:51.158 |
| `handle_item()` / runner | Yes; runner started 16:08:55.546, first heartbeat 16:08:55.691 |
| Tools | Two successful local `bash` calls and two tool-result submissions; no MCP call |
| Final session status | Idle with `end_turn` |
| Cleanup | Heartbeats continued during resume grace; idle watchdog fired after 120 s; stop returned 200 |
| Conclusion | One session item feeds the whole local tool loop. Subsequent webhook drains do not represent tool calls. One early empty delivery was probably a cold-start-induced retry; two later empties were distinct run-transition wakeups. |

The session did not loop, exceed budget, or attempt out-of-scope actions, so cancellation was unnecessary. The Console emitted two MCP authentication-error events because the deliberately minimal test session did not attach credential vaults; the smoke path made no MCP call, so these did not affect the self-hosted tool-loop result.

The second test isolated the configured MCP integrations:

| Field | Recorded value |
|---|---|
| Purpose | Verify GitHub and Linear vault attachment, authentication and one minimal read-only call each |
| Correlation ID/title | `clevin-mcp-20260827T232804Z` |
| Session/work ID | `sesn_01ERaNc2AaTXEX3KL8EZNmT4` |
| Environment | `env_0152FZKRpy9f8uVw38Guzosy`, exact match |
| Vault | `Clevin MCP Credentials`; both servers showed connected before launch |
| Start/result | Created 16:28:44 PDT; run 16:29:06; both MCP results 16:29:09 |
| Budget/cost | `$0.25` cap; `$0.34` list cost after the documented one-turn overrun; 54,229 cache-write tokens dominated cost |
| Work claimed | Yes, 16:29:14.788; ACK 16:29:15.198; final queue drain 16:29:16.711 |
| `handle_item()` / runner | Yes; sandbox runner started 16:29:19.233 and heartbeated; it did not execute the MCP tools |
| Linear | `get_user`, one allowed call, zero failed, matching result event — pass |
| GitHub | `get_me`, one allowed call, zero failed, matching result event — pass |
| Content/write boundary | No ticket or repository content requested; no write operation attempted; returned identity data was inspected only to confirm a result existed and is omitted here |
| Final session status | Idle with `budget_reached` after the completed MCP results; no final prose turn |
| Controlled termination | Continued heartbeats showed the known budget-reached cleanup gap. The test's sole Modal container was stopped at 16:32:40 PDT; graceful cancellation posted work `/stop` successfully and no active Clevin container remained. |
| Conclusion | Both integrations are operational for authenticated minimal read-only use. MCP execution belongs to Anthropic's control plane, not the Modal sandbox. |

The second session did not loop or attempt an out-of-scope action. Its result was complete, but the `budget_reached` worker continued heartbeating past the normal 120-second `end_turn` grace. The investigator therefore stopped only the test's verified Modal container to prevent needless spend. Modal's SIGINT path invoked Clevin's graceful cancellation: Anthropic work stop returned 200 and the worker exited. No pre-existing session, worker or deployment was changed.

If delivery-level duplicate proof becomes necessary, the minimum next test is not another agent run. First add the safe event-ID logging recommended above, deploy it under separate approval, and then repeat the same `$0.25` smoke test. Without that instrumentation, another run may reproduce timing but still cannot compare event IDs.

## Open questions

- **Was the 16:08:55 fresh POST a retry of the initial event?** Its timing strongly fits a slow-delivery timeout plus the documented retry backoff, but delivery event IDs/attempt numbers were not accessible. Minimum action: log the verified top-level event ID and compare it across calls.
- **What is Anthropic's webhook request timeout?** The first Modal input completed about 32.3 seconds after creation and was followed by a new input at 35.7 seconds. The webhook docs specify retry counts/backoff but not the per-attempt HTTP deadline. Minimum action: obtain the documented timeout or compare event IDs and delivery-attempt timestamps.
- **Which Modal replica handled each historical POST?** The fresh run exposed function-call and container IDs, but historical UI did not. Minimum action: retain platform invocation/container ID in structured logs.
- **Did any unrelated external worker use the same environment?** One Clevin deployment and one representative claim were found, but no global reverse index exists. Minimum action: inspect an Anthropic historical worker/claim audit if offered.
- **Why does one HUM-5 session have 56 recorded agent tool uses but 55 user tool results?** It still ended `end_turn`; asynchronous/multi-call event grouping or a terminal call may explain the count. Minimum action: reconcile sanitized tool-call IDs for only that session.
- **What were effective historical Modal concurrency/autoscaling defaults?** They were omitted in code and not exposed numerically. Minimum action: read the deployment's resolved function specification.
- **How exactly did HUM-6 leave Modal at the one-hour boundary?** Work history proves the last heartbeat and later stop, but historical sandbox signal/exit logs were not retrievable through the CLI. Minimum action: retain sandbox exit code/signal and `handle_item` finally logs.
- **What is Anthropic's server-side lease-expiry interval after the last heartbeat?** HUM-6 stopped 388.852 seconds after its last heartbeat, but the last server-reported TTL was not logged. Minimum action: log the non-secret heartbeat TTL and work state, never the credential.
- **What short grace, if any, should Clevin allow after `budget_reached`?** Raising a session budget can resume it automatically, so immediate destruction trades cost for resume latency. Minimum action: choose a product policy and encode it explicitly rather than inheriting the one-hour Modal timeout.
- **Should Clevin spend money to suppress cold-start retries?** A warm dispatcher or different durable handoff could reduce first-delivery latency, but the observed retry was harmless and warming has ongoing cost. Minimum action: measure p95/p99 cold-start and duplicate-event rates after event-ID logging before changing Modal scaling.

None of these questions changes the high-confidence causal explanation for the observed empty polls.

## Evidence appendix

### Non-secret identifiers

- Environment: `env_0152FZKRpy9f8uVw38Guzosy`
- Agent: `agent_01Eef1xLtkWW2cDg1shFUpms`
- Webhook: `wep_01VYcp1ZCwCzKo4KMEX7sfT5`
- Modal app: `ap-rntdxyiE8GU1aYtc3PwBiN`
- Modal function: `fu-4umqt1TZoqEH0dIUmgJhKc`
- Representative session/work: `sesn_01LnksfmKUCjwTJASMVsG5Eh`
- Controlled session/work: `sesn_01BuA8fgCHoWtr479aY7H4ne`
- MCP verification session/work: `sesn_01ERaNc2AaTXEX3KL8EZNmT4`
- Controlled Modal HTTP calls: `fc-01M12QPF8BXSCJJM4XWWW35D54`, `fc-01M12QQJ37Q716VGJP1AB3WJZ1`, `fc-01M12QQKFT4RCY05K8RKZGPB3J`, `fc-01M12QQNCH6XXASNTRSY4FKAZR`
- Controlled dispatcher/sandbox containers: `ta-01M12QPFVR3PFN6S737WNQYACR`, `ta-01M12QQEJ761DNTE8ZRC7X6CWS`

### UI pages inspected

- Anthropic Console: Agents, agent detail/version, Environments, environment detail/queue overview, Sessions, session event views, Webhooks.
- Modal: deployed Clevin app Overview, Deployments, Functions, Sandboxes and App Logs/function-call history.

### Primary documentation

- [Anthropic: Self-hosted sandboxes](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes)
- [Anthropic: Subscribe to webhooks](https://platform.claude.com/docs/en/managed-agents/webhooks)
- [Anthropic: Session event stream](https://platform.claude.com/docs/en/managed-agents/events-and-streaming)
- [Anthropic: Self-hosted sandbox security model](https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes-security)
- [Anthropic API: Poll for work](https://platform.claude.com/docs/en/api/http/beta/environments/work/poll)
- [Anthropic API: Acknowledge work](https://platform.claude.com/docs/en/api/beta/environments/work/ack)
- [Anthropic Python SDK: self-hosted runner helpers](https://github.com/anthropics/anthropic-sdk-python/blob/main/helpers.md#self-hosted-environment-runner)
- [Anthropic Python SDK: session tool runner](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/lib/tools/_beta_session_runner.py)

### Key evidentiary timestamps

- First HUM-6 run-start: 2026-08-24 06:45:12.345 UTC.
- Work claim: 2026-08-23 23:45:19.298 PDT.
- Heartbeat while later polls drained: 2026-08-23 23:50:25.899 PDT.
- Repeated correlated run starts: 2026-08-24 00:13:09.367, 00:13:38.452, 00:13:48.278, 00:14:02.049 and 00:14:05.470 PDT.
- HUM-6 final budget status: 2026-08-24 07:17:45.792 UTC / 00:17:45.792 PDT.
- HUM-6 work last heartbeat: 2026-08-24 07:44:59.234 UTC / 00:44:59.234 PDT.
- HUM-6 work stopped: 2026-08-24 07:51:28.086 UTC / 00:51:28.086 PDT.
- Healthy `end_turn` comparisons: smoke work stopped 120.416 seconds after final idle; long HUM-5 work stopped 120.506 seconds after final idle.
- Investigation-time environment stats: queue depth 0, pending 0, processing 0, workers polling 0.
- Controlled input created: 2026-08-27 16:08:19.595 PDT.
- Controlled initial cold start: scheduled 16:08:20.305; execution began 16:08:48.528.
- Controlled claim/ACK: 16:08:50.585 / 16:08:51.158 PDT.
- Controlled sandbox runner/first heartbeat: 16:08:55.546 / 16:08:55.691 PDT.
- Controlled local result submissions: 16:08:56.220 and 16:08:58.240 PDT.
- Controlled empty webhook invocations: enqueued 16:08:55.319, 16:08:56.738 and 16:08:58.686 PDT.
- Controlled idle cleanup/work stop: 16:11:02.706 / 16:11:03.036 PDT.
- MCP verification: created 16:28:44; MCP uses 16:29:08; two result events and idle `budget_reached` 16:29:09; Modal claim/ACK 16:29:14.788 / 16:29:15.198; sandbox runner start 16:29:19.233; controlled container termination and successful work stop 16:32:40 PDT.
- Billing check: Anthropic `$88.64` before and `$88.56` after, auto-reload off; Modal `$29.89` included credit before and after at displayed precision.

No screenshots are attached because the UI views included potentially sensitive session content adjacent to the safe metadata. No credentials, headers, signing material, raw prompt text, tool arguments or tool output were retained in this report.
