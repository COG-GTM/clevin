# Revised Claude Managed Agents Swarm Plan

## Core objective

> **Build the most capable cloud agent possible exclusively by composing and extending Claude Managed Agents paradigms. Custom code is permitted only when it lives inside, connects directly to, or empirically tests one of those paradigms. Where a capability cannot be achieved this way, treat that as a Managed Agents limitation rather than filling the gap with unrelated platform code.**

The constraint is stronger than "minimize custom code":

> **Do not write custom code unless that code directly configures, implements, extends, observes, or tests a Claude Managed Agents primitive.**

The swarm must not build a parallel platform around Managed Agents. If a native paradigm cannot support a capability, the default outcome is to **document the limitation**, not recreate the missing capability independently.

The paradigms in scope are:

- Managed Agent configuration
- Agent versioning
- Sessions and server-side state
- Context compaction
- Memory Stores
- Built-in subagents
- Built-in tools and MCPs
- Skills
- Self-hosted `EnvironmentWorker` sandboxes
- Deployments
- SSE event streams, usage events, and lifecycle webhooks
- Browser and Computer Use where available
- Console and `ant` CLI workflows

Repository/credential lifecycle and security/enterprise-readiness testing are explicitly **out of scope for now**.

---

# 0. Custom-code eligibility rule

Custom code is in scope only when all of the following are clear:

1. **The Managed Agents primitive being leveraged**
    - Agent/version configuration
    - Session API
    - `EnvironmentWorker`
    - Self-hosted sandbox
    - Tool or MCP
    - Skill
    - Memory Store
    - Built-in subagent
    - Deployment
    - SSE event stream
    - Lifecycle webhook
    - Usage event
2. **How Managed Agents invokes or consumes the code**
3. **Why configuration alone is insufficient**
4. **What the experiment teaches us about that primitive**
5. **Why the implementation is no larger than necessary to answer the question**

A useful shorthand:

> **If the code would still be a meaningful product component without Claude Managed Agents, it is probably out of scope.**

## Allowed custom code

| Code | Why it is allowed |
| --- | --- |
| Clevin agent-as-code configuration | Directly creates and versions Managed Agents resources |
| `EnvironmentWorker` or sandbox changes | Implements the native self-hosted runtime paradigm |
| Tools called by the Managed Agent | Extends the native tool paradigm |
| MCP servers configured on the agent | Extends the native MCP paradigm |
| Scripts packaged into a skill | Tests the native skill paradigm |
| Memory Store interaction through supported interfaces | Tests the native memory paradigm |
| Built-in subagent definitions and prompts | Tests the native subagent paradigm |
| Lifecycle webhook consumers | Directly observes Managed Agents lifecycle events |
| SSE clients and event instrumentation | Directly observes native session execution |
| Deployment configuration or handlers | Exercises the native Deployment paradigm |
| Benchmark and failure-injection code | Allowed only when its purpose is to test Managed Agents behavior |
| A thin UI or terminal client for Managed Agent sessions | Directly invokes and streams native sessions |

## Disallowed custom code

- A replacement agent loop
- A separate context-management or compaction system
- A custom top-level session orchestrator
- An external planning or delegation engine
- A custom memory database or RAG layer
- A scheduler built to replace Deployments
- A separate job queue replacing the Managed Agents work queue
- A parallel session-state system
- A custom repository or credential platform
- An independent automation platform
- A standalone observability product
- Any feature added mainly to make Clevin look more complete without testing a Managed Agents primitive

Worked examples:

- If native Deployments cannot support event-driven automation, **document that limitation**. Do not build a generic automation service.
- If native Memory Stores cannot support dynamic scoping, **document that limitation**. Do not add a vector database.
- If built-in subagents cannot provide isolated parallel execution, **document that limitation**. Do not build custom top-level session orchestration.
- Session recovery work remains within prompts, tool design, `EnvironmentWorker`, session APIs, and lifecycle events. Do not add an independent workflow engine.

## Outcome classification

Every capability investigated must end in exactly one of these classes:

- **A. Achievable entirely through Managed Agents configuration.**
- **B. Achievable through a native Managed Agents extension point.**
- **C. Partially achievable through a native extension point.**
- **D. Not achievable within the Managed Agents model.**

Class D is a legitimate, valuable result. It must never be converted into class B by building unrelated platform code.

---

# 1. Native-first scope

The investigation centers on these native surfaces:

- **Managed Agent configuration**
    - System prompts
    - Agent versions
    - Skills
    - Tools
    - Built-in subagents
    - Memory Stores
    - Deployment configuration
- **Existing Clevin capabilities**
    - Provisioner
    - Terminal client
    - Modal bridge
    - Sandbox runtime
    - Browser capabilities
- **Supported management surfaces**
    - Anthropic Console
    - `ant` CLI
    - Modal Console
    - Existing APIs
- **Thin experimental instrumentation**
    - Logging
    - Correlation identifiers
    - Benchmark drivers
    - Failure injection
    - Result collection
- **Native extension points**
    - Tools, MCPs, skills, `EnvironmentWorker`, Deployment handlers, session/SSE/webhook consumers, and instrumentation of native primitives

These categories define the available design space rather than a mandatory sequence of work. Agents may choose whichever methods best test their hypotheses while remaining within the custom-code eligibility rule.

Custom additions are evaluated by their specific Managed Agents primitive, invocation path, reason configuration was insufficient, evidence produced about the primitive, size, and removability if Anthropic supplies a missing capability.

The goal is to produce a **maximally native Clevin**, not a custom platform that merely happens to call Managed Agents. If the native design space cannot carry a capability, it remains a class D limitation rather than becoming a replacement subsystem.

---

# 2. Swarm roles

## Research Director

The Research Director is the integrative research role. Its goals are to preserve a coherent view of the investigation, synthesize evidence across independent sessions, keep the work aligned with the Managed Agents-only scope, and translate validated discoveries into a maximally native Clevin configuration.

The role is evaluated by depth of investigation, information gain, native Managed Agents leverage, reproducibility, functional capability, and Managed Agents provenance. Cost optimization is **not** a goal.

## Research session roles

Roles describe areas of responsibility, not mandatory procedures. A session may combine roles or approach its goal in any way consistent with the scope of this plan.

### Investigator

The Investigator’s goal is to establish how a Managed Agents capability actually behaves and identify the most important open questions.

### Builder

The Builder’s goal is to find the strongest implementation available through native configuration or an eligible native extension point.

### Breaker

The Breaker’s goal is to challenge claimed capabilities, expose edge cases, and identify failure boundaries.

### Verifier

The Verifier’s goal is to determine whether important findings are reproducible and attributable to Managed Agents rather than accidental state or undocumented setup.

---

# 3. Coordination freedom

The swarm has no mandatory reporting cadence, status format, steering protocol, role sequence, or experiment sequence. Research sessions may communicate, coordinate, specialize, combine roles, and revise their approaches as they judge useful.

Findings need only carry enough evidence and context to support synthesis, reproduction, outcome classification, and attribution to a Managed Agents primitive. The document specifies what the investigation is trying to learn and what remains in scope; it does not prescribe how individual agents reason about or pursue that work.

---

# 4. Autonomy and spending authorization

All research sessions have broad autonomy to:

- Start as many Managed Agent sessions as useful
- Create and test agent versions
- Update Clevin configuration
- Use the Anthropic Console
- Use the Modal Console
- Start, stop, restart, and inspect Modal sandboxes
- Run long-duration workloads
- Use browser and Computer Use
- Invoke tools and MCPs
- Generate substantial model usage
- Consume Modal compute
- Repeat experiments as many times as needed
- Use all currently available credits when doing so produces useful evidence

They do **not** need to minimize token usage or compute spend.

However, they must not:

- Purchase additional credits
- Enable automatic credit purchases or auto-recharge
- Upgrade a plan
- Add or change a payment method
- Create spend beyond the currently available prepaid balance
- Make an organization-level billing change
- Continue once the available balance is exhausted

The full existing balance is available for useful research, but the swarm is not authorized to increase that balance.

---

# 5. Research workstreams

## A. Control plane and session semantics

### Questions

- What exactly is stored server-side?
- What is pinned to the agent version versus session?
- What changes can an active session observe?
- How do cancel, pause, resume, timeout, and retry behave?
- What event-ordering and delivery guarantees exist?
- When and how does context compaction occur?
- Does prompt caching occur?
- What is retained when the Modal worker or sandbox is replaced?

### Experiments

- Change model, system prompt, skills, tools, and subagent config after a session starts.
- Compare active sessions against newly created sessions.
- Roll forward and roll back agent versions.
- Disconnect and reconnect the SSE client.
- Delay, duplicate, and reorder lifecycle webhook handling.
- Cancel sessions during model generation and tool execution.
- Run sessions until several compactions occur.
- Plant important constraints early and test whether they survive compaction.
- Repeat identical prompts and configurations while inspecting usage events.
- Compare Anthropic-side history with sandbox filesystem state.

### Deliverable

A precise state and lifecycle model showing:

- Anthropic-owned state
- Sandbox-owned state
- Version-pinned state
- Session-specific state
- Recoverable state
- Irrecoverable state

---

## B. Long-horizon agent quality

### Goal

Determine whether Managed Agents can support the type of long-running, minimally supervised work expected from a real cloud agent platform.

### Workloads

- Large multi-file refactor
- Dependency upgrade
- Framework migration
- Test-suite debugging
- Browser-plus-code workflow
- Repeated review and revision
- Task with changing requirements
- Task requiring information introduced several hours earlier
- Task with multiple false starts
- Task that spans several context-compaction events

### Variants

Run with:

- One session versus multiple resumptions
- Memory Store enabled versus disabled
- Built-in subagents enabled versus disabled
- Different agent configurations
- Different system-prompt strategies
- Planned worker interruption
- Tool failure and recovery

### Metrics

- End-to-end task completion
- Human interventions
- Constraint retention
- Repeated mistakes
- Plan stability
- Tool reliability
- Regression rate
- Elapsed time
- Token usage
- Compute usage
- Variance across runs

---

## C. Runtime reliability and recovery

### Goal

Push the combination of the Anthropic-managed loop and self-hosted Modal sandboxes until its actual recovery semantics are clear.

### Failure injection

- Kill the worker mid-command.
- Restart the worker during reasoning.
- Restart after files are written but before the tool reports success.
- Temporarily detach the persistent volume.
- Fill the sandbox disk.
- Interrupt network connectivity.
- Time out a tool.
- Return malformed tool output.
- Return extremely large tool output.
- Crash the browser process.
- Redeploy the Modal app.
- Expire the sandbox.
- Delay worker startup.
- Duplicate a tool response.
- Run several sessions against constrained compute.

### Questions

For every failure:

1. Does Anthropic notice?
2. Does Anthropic retry?
3. Is the retry safe?
4. Does the session remain usable?
5. Does the sandbox state survive?
6. Does the agent correctly understand what happened?
7. Can the agent recover without custom orchestration?
8. What minimal configuration or prompt change improves recovery?

### Constraint

Recovery improvements must come from prompts, tool design, tool schemas, `EnvironmentWorker` behavior, session APIs, and lifecycle events. Do not add an independent workflow engine or reconciliation service. If recovery cannot be achieved through those native surfaces, record it as a class D limitation.

---

## D. Agent-as-code and configuration lifecycle

### Goal

Determine whether Managed Agents’ versioning and configuration paradigms can support a scalable agent-development lifecycle without building a second control plane.

### Experiments

- Fully reconstruct an agent from Clevin’s declarative configuration.
- Compare code-managed configuration with Console-managed configuration.
- Make a Console change and detect drift.
- Reconcile drift back into agent-as-code.
- Create development, staging, and production-style versions.
- Canary two versions across the same benchmark.
- Roll back a broken version.
- Share common skills, tools, and subagent definitions.
- Manage a large number of agent variants.
- Determine which resources can and cannot be managed declaratively.
- Test active-session behavior during version changes.
- Test whether version pinning is sufficient for reproducibility.

### Key question

Can an enterprise-scale agent fleet be managed primarily through native agent versions plus thin configuration-as-code—or does the builder eventually need a custom control plane?

### Constraint

Configuration-as-code that directly creates and versions Managed Agents resources is allowed. A second control plane that stores its own agent state, or reimplements versioning, is not. If native versioning cannot support the lifecycle, document that as a class D limitation.

---

## E. Native Memory Store investigation

This workstream should focus **only on the Memory Store / Knowledge paradigm built into Claude Managed Agents**.

Do not build a custom vector database, retrieval service, memory API, or repo-scoped memory platform.

### Goal

Determine how far the native Memory Store can be pushed as the long-term memory layer for an advanced cloud agent.

### Setup

Create several native Memory Stores through the supported Managed Agents flows:

- General operating knowledge
- Coding conventions
- User preferences
- Prior task learnings
- Known failure patterns
- Environment knowledge
- Project-specific knowledge where the native paradigm allows it

Attach different combinations of stores to agent versions and sessions.

### Experiments

- Have the agent update an existing Memory Store over many sessions.
- Test whether the agent recognizes what is worth remembering.
- Test whether it can correct or supersede stale information.
- Introduce contradictory entries.
- Introduce subtly incorrect entries.
- Store lessons from failed runs and see whether future sessions improve.
- Compare repeated tasks with and without the store attached.
- Test memory behavior across agent versions.
- Test memory behavior across compaction.
- Test multiple subagents using the same attached memory.
- Test whether subagents produce conflicting updates.
- Test whether memory grows noisy over time.
- Determine how retrieval is triggered and whether it is predictable.
- Determine whether memory provenance is visible.
- Determine what lifecycle actions still require manual Console work.
- Test the maximum practical size and structure of a store.

### Questions

- Does native memory improve repeated-task performance?
- Can the agent maintain it without constant human cleanup?
- How quickly does it become stale or contradictory?
- Can good naming and structure approximate missing scoping?
- Can the system prompt reliably control when memory is read or written?
- Is Memory Store good enough for production agent learning?
- What capability is fundamentally blocked by the inability to create or scope stores dynamically?

### Constraint

If native Memory Stores cannot support a desired behavior, document the limitation. Do not solve it by building a replacement memory system, a vector database, or a RAG layer. Code that reads or writes the Memory Store through supported Managed Agents interfaces is allowed; code that stores knowledge anywhere else is not.

---

## F. Built-in Managed Agents subagents

This workstream should use **only the built-in Managed Agents subagent paradigm** for the target agent.

External isolated research sessions remain in scope. The target Clevin agent, however, remains limited to built-in Managed Agents subagents rather than a custom top-level session orchestrator.

### Goal

Push built-in subagents as far as possible toward the planning, delegation, parallelism, specialization, and review capabilities of a sophisticated cloud agent.

### Subagent configurations to test

- Repository explorer
- Planner
- Implementer
- Test debugger
- Reviewer
- Browser researcher
- Documentation writer
- Performance investigator
- Skeptical verifier
- Failure-recovery specialist

### Delegation patterns

- Planner → implementer
- Implementer → reviewer
- Parallel exploration followed by synthesis
- One subagent per failing test
- One subagent per hypothesis
- Competing implementation proposals
- Implementer plus adversarial reviewer
- Browser researcher feeding a coding subagent
- Hierarchical delegation if supported
- Repeated delegation over a long session

### Questions

- When does the parent decide to delegate?
- How much context does the child receive?
- How reliably does the child report back?
- Can the parent synthesize conflicting results?
- Do subagents share filesystem and process state correctly?
- Do they interfere with one another in the same VM?
- What happens when one subagent hangs or fails?
- Can the parent cancel or redirect subagent work?
- Are subagent results preserved through compaction?
- Does adding subagents improve success rate or just increase cost?
- What is the useful maximum number of concurrent subagents?
- Can role-specific prompts produce consistently specialized behavior?

### Stress tests

- Several subagents editing overlapping files.
- Subagents returning conflicting conclusions.
- One subagent deliberately returning a low-quality result.
- Resource contention inside the shared VM.
- Very large child outputs.
- Child tool failure.
- Parent compaction while children are active.
- Recursive or repeated delegation.
- Long-running child work.

### Constraint

Do not work around subagent limitations by spawning custom top-level Managed Agent sessions inside Clevin, and do not build an external planning or delegation engine. Subagent definitions, prompts, and tool grants are the only levers. The purpose is to identify the maximum capability of the native subagent paradigm; anything it cannot do is a class D limitation.

---

## G. Tools, MCPs, browser, and Computer Use

**Cancelled.** `browser_toolset_20260801` and `computer_toolset_20260801` are rejected as invalid
Managed Agents tool types, so browser and Computer Use are recorded as class D. The tool and MCP
half of this workstream moved into workstream C. Retained below for the original questions only.

### Goal

Determine how far native tools and configured integrations can reproduce the broad action surface of a cloud agent platform.

### Experiments

- Use browser capabilities for research and application workflows.
- Combine browser activity with coding inside Modal.
- Test browser recovery after crash or timeout.
- Test long-lived browser state.
- Exercise hosted MCPs under long-running sessions.
- Test tool retries and malformed outputs.
- Test large tool responses and compaction.
- Change tool configuration between agent versions.
- Use subagents with different tool access.
- Compare a generalist agent with role-specific tool configurations.
- Inspect where tools execute and how results reach the sandbox.
- Determine which capabilities can be added through configuration alone.

### Constraint

Prioritize built-in tools and standard MCP configuration. Custom tools and MCP servers are allowed because they extend a native paradigm—but only when the Managed Agent itself invokes them and the experiment answers a question about the tool or MCP paradigm. A service the agent does not call is out of scope.

---

## H. Deployments and automation

### Goal

Push the native Deployment model as far as possible without immediately replacing it with a custom automation service.

### Experiments

- Run high-frequency scheduled deployments.
- Use deployments for recurring maintenance.
- Have deployments inspect and update Memory Stores.
- Trigger workflows that delegate to built-in subagents.
- Test overlapping deployment runs.
- Test failed and delayed runs.
- Test version changes between scheduled runs.
- Determine whether a deployment can continue prior work.
- Test whether external activity can be polled rather than received through a custom webhook.
- Use existing lifecycle webhooks only for observation where possible.
- Determine whether event-driven behavior is expressible as a Deployment configuration or handler.

### Questions

- How much useful automation can be expressed natively?
- Can polling plus persistent session/memory approximate event-driven behavior?
- What controls exist for concurrency and overlapping runs?
- Is the Deployment model merely limited, or genuinely unusable for advanced workflows?

### Constraint

Deployment configuration and handlers are allowed. A scheduler, job queue, or automation service that replaces Deployments is not. If native Deployments cannot support event-driven automation, document that as a class D limitation.

---

## I. Observability and economics

### Goal

Determine whether native events, usage data, session history, Console views, and Modal logs provide enough visibility to operate an advanced agent.

### Build only thin instrumentation

Correlate:

```
Agent version
→ session
→ model events
→ tool request
→ EnvironmentWorker
→ Modal sandbox
→ browser/tool activity
→ filesystem changes
→ tool response
→ subagent activity
→ compaction
→ final result
```

### Track

- Session phase
- Model usage
- Prompt-caching indicators
- Context-compaction events
- Tool latency
- Tool retries
- Worker restarts
- Subagent activity
- Browser actions
- Errors and recovery
- Files changed
- Cost per experiment
- Cost per successful task

### Constraint

Instrumentation is allowed only where it consumes native session APIs, SSE events, usage events, or lifecycle webhooks. Do not build a standalone observability product. Add only enough instrumentation to determine what the native system exposes and where the blind spots are; the blind spots themselves are the finding.

---

## J. Integrated “maximally native Clevin” gauntlet

After individual workstreams, combine the strongest native configurations into one agent version.

The gauntlet should require the agent to:

1. Receive a broad, ambiguous coding task.
2. Inspect the existing project.
3. Build and maintain a long-running plan.
4. Use native Memory Stores for prior knowledge.
5. Delegate investigation to built-in subagents.
6. Use browser capabilities where relevant.
7. Make changes in the Modal sandbox.
8. Recover from an injected worker or tool failure.
9. Survive context compaction.
10. Run tests and review its own work.
11. Revisit its implementation after feedback.
12. Complete with minimal human intervention.

Run the gauntlet repeatedly across multiple agent configurations.

The final implementation should be scored on **Managed Agents provenance**: every component must trace to a specific native primitive, and nothing in the configuration should be a standalone product component that would still make sense without Managed Agents.

---

# 6. Metrics

## Capability metrics

- Task success rate
- Human-intervention rate
- Long-horizon constraint retention
- Failure-recovery rate
- Memory usefulness
- Memory contamination or staleness
- Subagent delegation quality
- Subagent synthesis quality
- Browser/tool success rate
- Reproducibility across agent versions
- Deployment reliability
- Observability completeness

## Managed Agents provenance metric

This replaces the older "minimum custom code" metric. For every capability, record which native primitive carried it and how far it got:

| Capability | Managed Agents primitive | Config only | Native extension | Result | Native limitation |
| --- | --- | --- | --- | --- | --- |
| Long-term memory | Memory Store | Yes/No | Yes/No | Pass/Partial/Fail | Description |
| Parallel delegation | Built-in subagents | Yes/No | Yes/No | Pass/Partial/Fail | Description |
| Runtime recovery | `EnvironmentWorker` • sessions | Yes/No | Yes/No | Pass/Partial/Fail | Description |
| Automation | Deployments | Yes/No | Yes/No | Pass/Partial/Fail | Description |
| Browser workflows | Tool/MCP/browser | Yes/No | Yes/No | Pass/Partial/Fail | Description |
| Observability | SSE + lifecycle events | Yes/No | Yes/No | Pass/Partial/Fail | Description |

Also track:

- Outcome class (A/B/C/D) per capability
- Number of capabilities achieved through configuration alone
- Number achieved through a native extension point
- Number of irreducible native limitations
- Number of manual Console steps
- Lines of code written, each attributed to a specific primitive
- Lines of code written with **no** attributable primitive — this number should be zero

A capability achieved through a better Memory Store structure, subagent definition, or system prompt is strictly more valuable than one achieved with custom infrastructure. A capability achieved with code that has no Managed Agents provenance does not count at all.

---

# 7. Candidate research specializations

The swarm may draw on any combination of these specializations:

- **Control-plane mapper**
- **Session-persistence investigator**
- **Context-compaction investigator**
- **Prompt-caching investigator**
- **Failure-injection investigator**
- **Agent-versioning and drift investigator**
- **Native Memory Store investigator**
- **Memory Store breaker**
- **Built-in subagent investigator**
- **Subagent contention and failure investigator**
- **Browser and tool investigator**
- **Deployment investigator**
- **Anthropic/Modal observability investigator**
- **Native-first skeptic**

The native-first skeptic’s general goal is to test whether conclusions genuinely exhaust and remain attributable to native Managed Agents paradigms, rather than hiding limitations behind custom infrastructure or weak experimental assumptions.

These are optional research perspectives, not a required launch order, staffing model, or workflow.

---

# 8. Research charter prompt

The operational launch prompt handed to each swarm session lives in `context/swarm-prompt.md`; it
supersedes the charter below, which is kept for provenance.

```
You are part of a long-running, highly autonomous investigation of Claude
Managed Agents.

The goal is to build the most capable cloud agent possible exclusively by
composing and extending Claude Managed Agents paradigms. The objective is not
to build the best possible Clevin by any means necessary, but to determine the
absolute ceiling of Clevin specifically through Claude Managed Agents.

The native design space includes Managed Agent configuration and versions,
sessions and server-side state, context compaction, native Memory Stores,
built-in subagents, tools, MCPs, browser and Computer Use, self-hosted
EnvironmentWorker sandboxes, Deployments, event streams, session history,
lifecycle webhooks, existing Clevin functionality, and thin instrumentation of
those primitives.

Repository and credential lifecycle are out of scope. Security and enterprise-
readiness testing are out of scope. The target Clevin agent uses native Memory
Stores and built-in Managed Agents subagents rather than replacement systems.

HARD IMPLEMENTATION CONSTRAINT

Custom code is in scope only when it directly configures, implements, extends,
observes, or empirically tests a Managed Agents primitive. Its relationship to
that primitive, invocation path, experimental value, and minimal scope must be
clear.

Do not build a replacement agent loop, context or compaction layer, top-level
session orchestrator, planning or delegation engine, memory or retrieval
system, scheduler, job queue, parallel session-state system, repository or
credential platform, independent automation platform, or standalone product
capability unrelated to a Managed Agents primitive.

If Managed Agents cannot carry a capability through configuration or an
intended extension point, treat that as a valid native limitation rather than
filling the gap with unrelated infrastructure.

Classify investigated capabilities as:

A. Achievable entirely through Managed Agents configuration.
B. Achievable through a native Managed Agents extension point.
C. Partially achievable through a native extension point.
D. Not achievable within the Managed Agents model.

Research may use isolated sessions and any useful combination of investigative,
building, adversarial, and verification perspectives. Agents are free to choose
their own strategies, coordination patterns, role combinations, reporting
cadence, and experimental order within this charter.

All existing prepaid Anthropic and Modal resources may be used when useful.
Do not purchase credits, enable auto-recharge, upgrade a plan, add or change a
payment method, make organization-level billing changes, spend beyond the
existing balance, or continue once that balance is exhausted.

Cost optimization is not a goal. Information gain, depth, reproducibility,
functional capability, and Managed Agents provenance are the goals. Do not
optimize for a favorable demo; determine the actual maximum capability of the
native Managed Agents model.
```

# 9. Final deliverables

The swarm should produce:

- A reproducible map of Managed Agents session and runtime semantics.
- A long-horizon benchmark suite.
- A failure/recovery matrix.
- A native Memory Store capability assessment.
- A built-in subagent capability assessment.
- A versioning and agent-as-code operating model.
- A tools/browser/MCP capability map.
- A Deployments assessment.
- An observability and economics report.
- A working “maximally native Clevin” configuration.
- A Managed Agents provenance ledger: every line of custom code introduced, attributed to the primitive it configures, implements, extends, observes, or tests.
- An A/B/C/D classification for every capability investigated.
- A list of capabilities achieved entirely through configuration.
- A list of irreducible Managed Agents limitations, including the capabilities we deliberately did **not** build around.
- A clear answer to:

> **What is the absolute ceiling of a cloud agent built through Claude Managed Agents’ configuration and intended extension points—not through a separately engineered platform?**
>