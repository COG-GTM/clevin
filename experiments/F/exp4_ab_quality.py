"""F/exp4 — do subagents improve outcome quality, or only cost?

Two arms on the same graded coding task, two runs each, all four sessions concurrent:

* ``solo``   — one agent, no roster.
* ``roster`` — identical task and identical top-level prompt, plus a native coordinator
  roster of planner / implementer / test-debugger / adversarial reviewer.

Grading is platform-recorded, not self-reported: the score comes from ``agent.tool_result``
events containing the unittest run, and the sha256 of the fixed test file is checked in the
same tool result so a rewritten test file cannot pass unnoticed.

Primitive under test: ``multiagent`` coordinator roster vs. the same agent without one.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from harness import BUILTIN_TOOLS, in_parallel, runner

TEST_SRC = '''import unittest

from util import chunk, parse_duration, retry


class ParseDuration(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_duration("45s"), 45)
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_compound_and_case(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("1H"), 3600)
        self.assertEqual(parse_duration("0s"), 0)

    def test_invalid(self):
        for bad in ("", "1h30", "h", "1x", "-5s", "1.5h", "1m1m"):
            with self.assertRaises(ValueError):
                parse_duration(bad)


class Chunk(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(chunk([1, 2, 3, 4, 5, 6, 7], 3), [[1, 2, 3], [4, 5, 6], [7]])
        self.assertEqual(chunk([], 3), [])
        self.assertEqual(chunk([1], 5), [[1]])

    def test_invalid_size(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                chunk([1, 2], bad)

    def test_does_not_mutate(self):
        data = [1, 2, 3]
        chunk(data, 2)
        self.assertEqual(data, [1, 2, 3])


class Retry(unittest.TestCase):
    def test_returns_first_success(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        self.assertEqual(retry(fn, attempts=3, on=(ValueError,)), "ok")
        self.assertEqual(len(calls), 1)

    def test_retries_listed_exception(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("nope")
            return "late"

        self.assertEqual(retry(fn, attempts=3, on=(ValueError,)), "late")
        self.assertEqual(len(calls), 3)

    def test_reraises_unlisted_immediately(self):
        calls = []

        def fn():
            calls.append(1)
            raise KeyError("boom")

        with self.assertRaises(KeyError):
            retry(fn, attempts=3, on=(ValueError,))
        self.assertEqual(len(calls), 1)

    def test_raises_last_after_exhaustion(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("fail%d" % len(calls))

        with self.assertRaises(ValueError) as ctx:
            retry(fn, attempts=2, on=(ValueError,))
        self.assertEqual(str(ctx.exception), "fail2")
        self.assertEqual(len(calls), 2)

    def test_attempts_must_be_positive(self):
        with self.assertRaises(ValueError):
            retry(lambda: None, attempts=0, on=(ValueError,))


if __name__ == "__main__":
    unittest.main()
'''

TEST_SHA = hashlib.sha256(TEST_SRC.encode()).hexdigest()

TASK_HEADER = """CLEVIN_SMOKE_TEST — experiment F/exp4. Local work under /workspace only: no git, no
MCP, no network, no external state changes.

Build a small module and make a fixed test suite pass.

Step 1. Create /workspace/proj/test_util.py with EXACTLY this content, byte for byte, using
the write tool. Do not reformat it, and never modify it again afterwards:

<<<TEST_FILE
{test_src}
TEST_FILE>>>

Step 2. Write /workspace/proj/util.py implementing three functions, with no imports beyond
the standard library:

* `parse_duration(text)` -> int seconds. Accepts a compound duration of unit-suffixed
  non-negative integers, units d/h/m/s, e.g. "45s", "90m", "1h30m", "1d2h3m4s". Case
  insensitive. Raises ValueError on anything else, including empty input, a trailing
  number with no unit, a unit with no number, unknown units, negatives, decimals, and a
  repeated unit.
* `chunk(items, size)` -> list of lists of at most `size` items, in order, without
  mutating `items`. Raises ValueError if size < 1.
* `retry(fn, attempts, on)` -> calls `fn()` up to `attempts` times, retrying only when it
  raises one of the exception types in `on`, re-raising anything else immediately, and
  raising the last exception if every attempt fails. Raises ValueError if attempts < 1.

Step 3. Verify by running exactly this command with bash, and never a modified version of it:

    cd /workspace/proj && sha256sum test_util.py && python3 -m unittest test_util 2>&1 | tail -4

Iterate on util.py only until that command reports OK. Run the exact command again after
every change to util.py, so the final tool result in this session is the real state.

Step 4. Finish with one fenced ```json block:
{{"final_command_output": "<verbatim last run>", "attempts": <int>,
  "edge_cases_you_handled": ["..."], "anything_still_failing": "<or none>"}}"""

SOLO_SYSTEM = """You are a careful Python engineer working inside a Managed Agents session.
Work only under /workspace. Be rigorous about edge cases and never claim a test passed
unless a tool result shows it. Do not modify a fixed test file."""

COORD_SYSTEM = (
    SOLO_SYSTEM
    + """

You have a subagent roster: a planner, an implementer, a test-debugger and an adversarial
reviewer. Use them deliberately for this task:
1. Ask the planner for the spec's edge cases before any code is written.
2. Hand the implementation to the implementer.
3. Have the adversarial reviewer attack the implementation against the written spec, and the
   test-debugger diagnose any failing test.
4. You own the final result: run the verification command yourself and only stop when a tool
   result you obtained shows OK."""
)

PLANNER = """You are a planning subagent. Given a spec, enumerate the exact edge cases and
the decisions an implementer must get right, as a terse numbered list (max 15 items). Read
files under /workspace if useful. Do not write code."""

IMPLEMENTER = """You are an implementation subagent. Write or fix the requested file under
/workspace with the write/edit tools, standard library only, then report in under 60 words
what you wrote. Never modify any test file. Do not run the full test suite unless asked."""

DEBUGGER = """You are a test-debugging subagent. Reproduce the failing tests with bash,
identify the precise cause of each failure in the implementation (never in the tests), fix
the implementation, re-run, and report the before/after command output verbatim."""

REVIEWER = """You are an adversarial reviewer subagent. Assume the implementation is subtly
wrong. Read the spec and the code, then hunt for cases where the code disagrees with the
spec. Probe your suspicions with actual bash/python runs, never assertions from reading
alone. Report a terse list of confirmed defects with the reproducing input, then say
VERDICT=DEFECTS or VERDICT=CLEAN. Never edit files."""

GRADE_RE = re.compile(r"^(OK|FAILED)\b.*$", re.MULTILINE)


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    out: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(str(block.get("text", "")))
    return "\n".join(out)


def grade(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Score from native tool_result events only (never the agent's own claims)."""
    runs: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "agent.tool_result":
            continue
        body = text_of(event.get("content"))
        if "unittest" in body or "Ran " in body or GRADE_RE.search(body):
            verdicts = GRADE_RE.findall(body)
            if not verdicts:
                continue
            runs.append(
                {
                    "thread": event.get("session_thread_id"),
                    "verdict": verdicts[-1],
                    "test_sha_intact": TEST_SHA in body,
                    "excerpt": body[-400:],
                }
            )
    return {
        "verification_runs": len(runs),
        "final_verdict": runs[-1]["verdict"] if runs else "NO_VERIFICATION_RUN",
        "final_test_sha_intact": runs[-1]["test_sha_intact"] if runs else False,
        "passed": bool(runs) and runs[-1]["verdict"] == "OK" and runs[-1]["test_sha_intact"],
        "runs": runs,
    }


def main() -> None:
    task = TASK_HEADER.format(test_src=TEST_SRC)
    with runner("exp4_ab_quality") as run:
        run.note("expected_test_sha256", TEST_SHA)
        solo = run.create_agent(
            "solo", system=SOLO_SYSTEM, model="claude-opus-5", tools=BUILTIN_TOOLS
        )
        roster = [
            run.create_agent("planner", system=PLANNER),
            run.create_agent("implementer", system=IMPLEMENTER),
            run.create_agent("test-debugger", system=DEBUGGER),
            run.create_agent("adversarial-reviewer", system=REVIEWER),
        ]
        coordinator = run.create_agent(
            "coordinator-quality",
            system=COORD_SYSTEM,
            model="claude-opus-5",
            tools=BUILTIN_TOOLS,
            multiagent={"type": "coordinator", "agents": [a.id for a in roster]},
        )

        def arm(label: str, agent_id: str) -> dict[str, Any]:
            session = run.create_session(
                agent_id=agent_id, prompt=task, label=label, max_list_cost="250"
            )
            status = run.wait(session.id, timeout_s=3600)
            snapshot = run.collect(session.id)
            return {
                "session_id": session.id,
                "status": status,
                "grade": grade(snapshot["events"]),
                "summary": snapshot["summary"],
            }

        arms = in_parallel(
            [
                ("solo_run1", lambda: arm("solo_run1", solo.id)),
                ("solo_run2", lambda: arm("solo_run2", solo.id)),
                ("roster_run1", lambda: arm("roster_run1", coordinator.id)),
                ("roster_run2", lambda: arm("roster_run2", coordinator.id)),
            ]
        )
        scoreboard: dict[str, Any] = {}
        for label, value in arms.items():
            run.note(label, value)
            if isinstance(value, dict) and "grade" in value:
                summary = value.get("summary", {})
                scoreboard[label] = {
                    "passed": value["grade"]["passed"],
                    "final_verdict": value["grade"]["final_verdict"],
                    "verification_runs": value["grade"]["verification_runs"],
                    "session_list_cost": summary.get("session_list_cost"),
                    "threads": summary.get("thread_count"),
                    "peak_concurrent_children": (summary.get("concurrency") or {}).get(
                        "peak_concurrent_children"
                    ),
                }
            else:
                scoreboard[label] = {"error": str(value)[:300]}
        run.note("scoreboard", scoreboard)
        print(json.dumps(scoreboard, indent=2))


if __name__ == "__main__":
    main()
