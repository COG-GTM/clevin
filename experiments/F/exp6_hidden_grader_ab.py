"""F/exp6 — do subagents raise one-shot correctness on a task with hidden tests?

exp4 gave both arms a visible test suite, so both iterated to green and the roster only
added cost. Here the agent gets a written spec and a *hidden* grader (base64 in the task
text, which it is told to write out and run but not decode), so there is no iterate-to-green
loop: the score measures how correct the implementation is when nobody could read the tests.

Arms, three runs each, all concurrent:

* ``solo``   — one agent, no roster.
* ``roster`` — same prompt plus a native coordinator roster of spec-analyst, implementer and
  adversarial reviewer, and an instruction to have the reviewer attack the code against the
  spec before the grader is run.

Score comes from ``agent.tool_result`` events (the grader's own ``SCORE=n/m`` line), and the
driver also checks whether the agent decoded the grader instead of implementing the spec.

Primitive under test: ``multiagent`` coordinator roster (specialisation and review) vs. its
absence, holding model, tools, budget and prompt constant.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

from harness import BUILTIN_TOOLS, in_parallel, runner

GRADER_SRC = '''"""Hidden grader for F/exp6. Prints one line per case and a final SCORE line."""

import sys

sys.path.insert(0, "/workspace/proj2")

from solution import evaluate  # noqa: E402

CASES = [
    ("1+2", 3),
    ("2 * 3 + 4", 10),
    ("2 + 3 * 4", 14),
    ("(2 + 3) * 4", 20),
    ("2 ** 3 ** 2", 512),
    ("-2 ** 2", -4),
    ("(-2) ** 2", 4),
    ("7 // 2", 3),
    ("7 // -2", -4),
    ("-7 % 3", 2),
    ("1 - -1", 2),
    ("  8  /  2  ", 4.0),
    ("2 * (3 + (4 - 1))", 12),
    ("999999999999 * 999999999999", 999999999998000000000001),
    ("--3", 3),
    ("+3 - +2", 1),
    ("10 % 4 ** 2", 10),
    ("100 // 7 % 5", 4),
]

ERRORS = [
    ("", ValueError),
    ("2 +", ValueError),
    ("(1 + 2", ValueError),
    ("1 + 2)", ValueError),
    ("2 $ 3", ValueError),
    ("2 3", ValueError),
    ("()", ValueError),
    ("1 / 0", ZeroDivisionError),
    ("1 // 0", ZeroDivisionError),
    ("1 % 0", ZeroDivisionError),
    ("2 ** -1", ValueError),
    ("1.5 + 1", ValueError),
]

passed = 0
total = len(CASES) + len(ERRORS)

for expr, want in CASES:
    try:
        got = evaluate(expr)
    except Exception as error:  # noqa: BLE001
        print("FAIL value %r raised %s: %s" % (expr, type(error).__name__, error))
        continue
    if got == want and isinstance(got, type(want)):
        passed += 1
    else:
        print("FAIL value %r -> %r (want %r)" % (expr, got, want))

for expr, want_error in ERRORS:
    try:
        got = evaluate(expr)
    except want_error:
        passed += 1
    except Exception as error:  # noqa: BLE001
        print("FAIL error %r raised %s (want %s)" % (expr, type(error).__name__, want_error.__name__))
    else:
        print("FAIL error %r returned %r (want %s)" % (expr, got, want_error.__name__))

print("SCORE=%d/%d" % (passed, total))
'''

GRADER_B64 = base64.b64encode(GRADER_SRC.encode()).decode()
GRADER_CHUNKS = "\n".join(GRADER_B64[i : i + 100] for i in range(0, len(GRADER_B64), 100))

TASK = """CLEVIN_SMOKE_TEST — experiment F/exp6. Local work under /workspace only: no git, no MCP,
no network, no external state changes.

Implement an integer/float expression evaluator from this specification, then run a hidden
grader exactly once at the end. You will not be able to see the test cases, so correctness
has to come from the specification.

SPECIFICATION — write /workspace/proj2/solution.py exposing `evaluate(text)`:

* Supported syntax: decimal integer literals, parentheses, unary `+` and `-` (repeatable),
  and the binary operators `+`, `-`, `*`, `/`, `//`, `%`, `**`. Arbitrary whitespace
  between tokens is allowed and insignificant.
* Precedence, lowest to highest: `+ -`, then `* / // %`, then unary `+ -`, then `**`.
  Consequently `-2 ** 2` is `-4` and `(-2) ** 2` is `4`.
* `**` is right-associative (`2 ** 3 ** 2` is `512`); every other binary operator is
  left-associative.
* Integer semantics follow Python's: `//` floors toward negative infinity, `%` takes the
  sign of the right operand, and integers are unbounded. `/` returns a float; every other
  operator returns an int when both operands are ints.
* Raise `ZeroDivisionError` for division or modulo by zero, including `//` and `%`.
* Raise `ValueError` for anything the grammar does not accept: empty or whitespace-only
  input, a trailing or missing operand, unbalanced or empty parentheses, two adjacent
  operands, unknown characters, non-integer literals such as `1.5`, and a negative
  exponent.
* Standard library only. Do not use `eval`, `exec`, `ast`, `compile`, `numexpr`, or any
  other expression-evaluation facility; write the parser yourself.

GRADER — do not read, decode, print, or inspect this payload. It is the hidden test suite,
and decoding it invalidates the experiment. Write it to disk and run it, nothing else:

1. Write /workspace/proj2/grader.b64 containing exactly these lines:
{chunks}
2. Run: `cd /workspace/proj2 && base64 -d grader.b64 > grader.py && python3 grader.py`
   Run this ONCE, only when you consider the implementation finished. Report its output
   verbatim, including the SCORE line. Do not open grader.py.
3. Do not modify the implementation after seeing the grader output, and do not run the
   grader a second time.

Finish with one fenced ```json block:
{{"grader_output": "<verbatim>", "score": "<SCORE line>",
  "spec_cases_you_worried_about": ["..."], "confidence": "<low|medium|high>"}}"""

SOLO_SYSTEM = """You are a careful Python engineer working inside a Managed Agents session.
Work only under /workspace. Follow specifications literally, reason explicitly about the
edge cases a specification implies, and never claim a result no tool result shows."""

COORD_SYSTEM = (
    SOLO_SYSTEM
    + """

You have a subagent roster: a spec-analyst, an implementer and an adversarial reviewer. Use
them for this task, in this order:
1. Spec-analyst: enumerate every case the specification implies, especially precedence and
   associativity traps, before any code exists.
2. Implementer: write the implementation against that list.
3. Adversarial reviewer: attack the implementation against the specification with real runs
   and report confirmed defects. Send its findings back to the implementer and repeat until
   the reviewer reports no defects.
You own the outcome: only run the hidden grader once the reviewer is clean."""
)

ANALYST = """You are a specification-analysis subagent. Given a written specification,
enumerate exhaustively and tersely the concrete inputs and outputs it implies, especially
precedence, associativity, sign, type and error cases. Numbered list, max 25 items, each
with the expected result. Read files under /workspace if useful. Do not write code."""

IMPLEMENTER = """You are an implementation subagent. Write or fix the requested file under
/workspace with the write/edit tools, standard library only, no eval/exec/ast/compile. Then
report in under 60 words what you wrote and which cases you verified with your own runs."""

REVIEWER = """You are an adversarial reviewer subagent. Assume the implementation is subtly
wrong. Read the specification and the code, then hunt for disagreements between them. Every
suspicion must be confirmed by actually running the code with python3 — never by reading
alone. Report a terse list of confirmed defects, each with the reproducing input, the actual
result and the specified result, then say VERDICT=DEFECTS or VERDICT=CLEAN. Never edit files
and never write tests to disk."""

SCORE_RE = re.compile(r"SCORE=(\d+)/(\d+)")
DECODE_RE = re.compile(r"grader\.py|grader\.b64")


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    out: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text", "")))
    return "\n".join(out)


def grade(events: list[dict[str, Any]], thread_events: list[dict[str, Any]]) -> dict[str, Any]:
    scores: list[dict[str, Any]] = []
    peeked: list[str] = []
    for event in [*events, *thread_events]:
        if event.get("type") == "agent.tool_result":
            body = text_of(event.get("content"))
            for match in SCORE_RE.finditer(body):
                scores.append(
                    {
                        "passed": int(match.group(1)),
                        "total": int(match.group(2)),
                        "thread": event.get("session_thread_id"),
                        "failures": [
                            line for line in body.splitlines() if line.startswith("FAIL")
                        ],
                    }
                )
        if event.get("type") == "agent.tool_use":
            command = json.dumps(event.get("input"))
            if DECODE_RE.search(command) and any(
                token in command for token in ("cat ", "head ", "read", "less ", "grep ", "-d ")
            ):
                peeked.append(command[:300])
    best = scores[0] if scores else None
    return {
        "grader_runs": len(scores),
        "first_score": f"{best['passed']}/{best['total']}" if best else "NO_GRADER_RUN",
        "all_scores": [f"{s['passed']}/{s['total']}" for s in scores],
        "first_failures": best["failures"] if best else [],
        "grader_file_touch_commands": peeked,
    }


def main() -> None:
    task = TASK.format(chunks=GRADER_CHUNKS)
    with runner("exp6_hidden_grader_ab") as run:
        run.note(
            "grader_sha256",
            hashlib.sha256(GRADER_SRC.encode()).hexdigest(),
        )
        solo = run.create_agent(
            "solo-spec", system=SOLO_SYSTEM, model="claude-opus-5", tools=BUILTIN_TOOLS
        )
        roster = [
            run.create_agent("spec-analyst", system=ANALYST),
            run.create_agent("implementer2", system=IMPLEMENTER),
            run.create_agent("reviewer2", system=REVIEWER),
        ]
        coordinator = run.create_agent(
            "coordinator-spec",
            system=COORD_SYSTEM,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [a.id for a in roster]},
        )

        def arm(label: str, agent_id: str) -> dict[str, Any]:
            session = run.create_session(
                agent_id=agent_id, prompt=task, label=label, max_list_cost="300"
            )
            status = run.wait(session.id, timeout_s=4800)
            snapshot = run.collect(session.id)
            thread_events = [
                event
                for thread in snapshot["threads"]
                for event in (thread.get("events") or [])
            ]
            return {
                "session_id": session.id,
                "status": status,
                "grade": grade(snapshot["events"], thread_events),
                "summary": snapshot["summary"],
            }

        labels = [(f"{arm_name}_run{i}", arm_name) for arm_name in ("solo", "roster") for i in range(1, 4)]
        results = in_parallel(
            [
                (
                    label,
                    (lambda lbl=label, a=arm_name: arm(lbl, solo.id if a == "solo" else coordinator.id)),
                )
                for label, arm_name in labels
            ]
        )
        scoreboard: dict[str, Any] = {}
        for label, value in results.items():
            run.note(label, value)
            if isinstance(value, dict) and "grade" in value:
                scoreboard[label] = {
                    "score": value["grade"]["first_score"],
                    "grader_runs": value["grade"]["grader_runs"],
                    "failures": value["grade"]["first_failures"],
                    "peeked": value["grade"]["grader_file_touch_commands"][:2],
                    "list_cost": value["summary"].get("session_list_cost"),
                    "threads": value["summary"].get("thread_count"),
                }
            else:
                scoreboard[label] = {"error": str(value)[:300]}
        run.note("scoreboard", scoreboard)
        print(json.dumps(scoreboard, indent=2))


if __name__ == "__main__":
    main()
