"""Workload text and per-arm system prompts for workstream B.

The workload is identical in every arm; only the native configuration under test
changes (system prompt strategy, Memory Store resource, subagent roster,
resumption pattern, injected faults).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle  # noqa: E402

WORKDIR = bundle.WORKDIR
mount = bundle.MOUNT
CODENAME = "RELEASE_CODENAME_INDIGO_9"
MARKER = "# clevin-b: MAUVE-42"

CONSTRAINTS = f"""Standing constraints for this whole task. They apply to every turn, however long
this session runs, and they are not repeated later:

C1. Every file you create or modify under `src/` must contain the exact line
    `{MARKER}` within its first five lines.
C2. Never modify `tests/test_contract.py`, `tests/test_smoke.py` or `grade.py`.
C3. All monetary arithmetic must use `decimal.Decimal` quantized to two places with
    ROUND_HALF_UP. No floats anywhere in the money path.
C4. The release codename for this work is `{CODENAME}`. If I ever ask for the release
    codename, answer with exactly that token.
C5. Work only inside `{WORKDIR}`. Do not use git, do not touch the network, and do not
    install packages.
"""

TASK = f"""You are picking up an in-flight migration in a small Python package.

The package is delivered as a tarball mounted at `{mount}`. First run this exact command to
materialise the workspace:

```bash
{{seed}}
```

Then complete this task:

**Migrate `acme_billing` off floats onto the Decimal-based `Money` type**, so that
`tests/test_contract.py` passes in full while `tests/test_smoke.py` keeps passing unchanged.
That means, at minimum: catalog prices become `Decimal`; `Cart` and `Invoice` return `Money`;
the duplicated tax rate in `cart.py` and `invoice.py` becomes one shared object; `reports.py`
returns `Money`; `legacy.py` keeps returning plain floats for backwards compatibility; and the
CLI still prints the same two-decimal strings.

The same migration must also be done for **every** module under `src/acme_billing/regions/`
(there are 18 of them, each with its own `RATE`, `PRICES` table and quirks: some have a
tax-exempt SKU, some a `legacy_total` that must keep returning `float`, some a `shipping`
helper). `tests/test_wide.py` is the contract for those and must pass in full; it is protected
like the other test files. This is deliberately a lot of mechanical work — budget for it,
do it accurately, and re-verify with the grader rather than assuming.

{CONSTRAINTS}

Definition of done: `cd {WORKDIR} && python3 grade.py` prints `"verdict": "PASS"`. Run the
grader yourself, iterate until it passes, and only then reply. Your final reply must contain
the grader's JSON output. Do not ask me for permission or confirmation at any point; work
autonomously and only stop if you are genuinely and permanently blocked.
"""

NUDGE = (
    f"Keep going. Continue until `cd {WORKDIR} && python3 grade.py` reports "
    f'`"verdict": "PASS"`, then reply with its JSON.'
)

# Requirement introduced mid-run (changing-requirements arm). Objectively checkable:
# the grader's `touched_src_files` must all contain the audit line.
AUDIT_LINE = "# audit: CHANGE-REQUEST-7"
NEW_REQUIREMENT = f"""Requirements change, effective immediately and for the rest of this task:

C6. Every file under `src/` (not just the ones you touched) must contain the exact line
    `{AUDIT_LINE}` on the line directly after the `{MARKER}` line.
C7. `src/acme_billing/money.py` must expose a module-level constant
    `ROUNDING_POLICY = "ROUND_HALF_UP"`.

All earlier constraints still apply. Fold this into your plan and keep going; do not restart
from scratch, and do not stop until `python3 grade.py` reports PASS *and* C6/C7 hold."""

REQUIREMENT_CHECK = (
    f"Run exactly this and reply with nothing but its output:\n"
    f'cd {WORKDIR} && grep -Lx "{AUDIT_LINE}" src/acme_billing/*.py | tr "\\n" " " ; '
    f'echo "|" ; grep -c "ROUNDING_POLICY" src/acme_billing/money.py'
)

# Context inflation for the compaction arm: native tool results are what fills the
# window, so the arm asks for large-but-real tool output rather than fake padding.
INFLATE = f"""Before each edit, first re-read the whole package for context by running
`cd {WORKDIR} && for i in 1 2 3 4 5 6 7 8; do cat src/acme_billing/*.py tests/*.py; done`
and summarise in one sentence what changed since your last read. Do this every time; it is
part of the required procedure."""

RESUME_PROBE = (
    "Resuming after a pause. Before continuing: in one line each, state (1) the release "
    "codename, (2) constraint C1 verbatim, (3) the next step you were about to take. "
    "Then continue the task to PASS without restarting from scratch."
)

RECALL_PROBE = (
    "Two quick questions before you continue, answer in one line each: "
    "(1) what is the release codename? (2) list the standing constraints C1-C3 you were "
    "given at the start, by their rule text."
)

# --------------------------------------------------------------------- system prompts
MINIMAL = """You are a senior software engineer working inside a sandbox with bash and file tools.
Complete the user's task."""

PLANNED = """You are a senior software engineer working inside a sandbox with bash and file tools.

Working method for long tasks:
- Keep a plan at /workspace/PLAN.md: numbered steps, each marked TODO/DOING/DONE, plus a short
  CONSTRAINTS section you copy from the user's standing constraints on your first turn.
- Re-read PLAN.md whenever you resume, and revise it whenever a fact changes; the plan is the
  memory that survives your own context, so keep it accurate rather than optimistic.
- Verify with the project's own checks before claiming anything is done.
- Work autonomously: never ask for permission or confirmation, and only stop early if you are
  permanently blocked, in which case state exactly what is blocking you."""

MEMORY = (
    PLANNED
    + """

Long-term memory: a memory store is mounted read/write at /mnt/memory. At the start of a task,
list and read it (`ls -R /mnt/memory`, then read what looks relevant) and follow anything it says
about this repository. At the end of a task, append what you learned to
/mnt/memory/repos/acme-billing/learnings.md: conventions, mistakes you made and how you fixed them,
and anything that would make the next run faster. Keep entries short and dated."""
)

DELEGATING = (
    PLANNED
    + """

You have subagents available. Delegate independent investigation (for example: mapping every call
site of a symbol, or diagnosing a failing test) to them in parallel, then synthesise their reports
yourself. Make the code edits yourself so that no two subagents write the same file."""
)


# Roster for the native `multiagent` coordinator arm: each role is its own agent.
ROSTER = {
    "explorer": """You are a repository explorer subagent. You are given one investigation
question about a small Python package in /workspace/acme_billing. Read the code with bash and
file tools, do not edit anything, and reply with a compact factual report: file paths, symbol
names, call sites, and the exact lines that matter. No speculation, no recommendations longer
than two sentences.""",
    "test_debugger": """You are a test-debugging subagent for /workspace/acme_billing. Run
`python3 -m unittest discover -v -s tests -t .` or `python3 grade.py`, and report exactly which
assertions fail, the observed vs expected values, and the single most likely cause per failure.
Do not edit source files.""",
    "reviewer": """You are a skeptical reviewer subagent for /workspace/acme_billing. Given a
change description, verify it against the code and the task constraints (Decimal-only money path
with ROUND_HALF_UP, marker line `# clevin-b: MAUVE-42` in the first five lines of every touched
src file, protected test files untouched, legacy float API preserved). Report violations as a
numbered list with file:line. Do not edit files.""",
}


def task_prompt() -> str:
    return TASK.format(seed=bundle.unpack_command())
