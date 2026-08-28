"""C0: is the production Modal webhook path already serving new sessions?

Creates one smoke-test session, starts no local worker, and reports what the
platform did on its own (webhook -> EnvironmentWorker -> Modal sandbox).
"""

from __future__ import annotations
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chaos

PROMPT = (
    "CLEVIN_SMOKE_TEST workstream C baseline. Run exactly one harmless check: "
    "`echo c0-baseline && uname -a` with bash, report the output, then stop."
)


def main() -> None:
    sid = chaos.create_session(
        PROMPT,
        title="clevin-swarm-C c0 baseline",
        metadata={"workstream": "C", "experiment": "c0"},
    )
    print("session", sid, flush=True)
    deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 150)
    seen = 0
    c = chaos.client()
    while time.time() < deadline:
        lines = chaos.summarize(sid)
        if len(lines) > seen:
            for line in lines[seen:]:
                print(" ", line, flush=True)
            seen = len(lines)
        s = c.beta.sessions.retrieve(sid)
        if s.status in ("idle", "terminated", "failed") and seen > 2:
            print("status", s.status, flush=True)
        time.sleep(10)
    print("final status", c.beta.sessions.retrieve(sid).status)
    print("artifact", chaos.dump_events(sid, "c0-baseline"))


if __name__ == "__main__":
    main()
