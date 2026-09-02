"""Rebuild an arm report from a session that outlived its driver.

Everything in a run's report except the driver's own timeline is derivable from
native session state (``sessions.events.list`` + ``session.usage``), so a driver
that is killed -- or, as happened here, an account that runs out of credit
mid-arm -- does not lose the evidence. This is also the cleanest demonstration
that Anthropic-side session history is the durable record.

Usage: ``python rescue.py <arm> <session_id> [<arm> <session_id> ...]``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
import prompts  # noqa: E402


def rescue(arm: str, session_id: str) -> Path:
    run = harness.Run(harness.Arm(name=arm, system="", notes="rescued from session history"),
                      environment_id="", ledger=harness.Ledger())
    run.session_id = session_id
    events = run.events()
    messages = [harness.text_of(e) for e in events if e.get("type") == "agent.message"]
    report = run.finish(
        {
            "rescued": True,
            "supervision": {"nudges": None, "outcome": (run.stop_reason() or {}).get("type")},
            "recall_answer": messages[-1:],
            "codename_retained": any(prompts.CODENAME in m for m in messages[-3:]),
            "elapsed_s": None,
        }
    )
    print(f"[{arm}] rescued -> {report}", flush=True)
    return Path(report)


def main() -> int:
    args = sys.argv[1:]
    if not args or len(args) % 2:
        print(__doc__)
        return 2
    for arm, session_id in zip(args[::2], args[1::2], strict=True):
        rescue(arm, session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
