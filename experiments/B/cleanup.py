"""Archive the temporary agents and delete the uploaded seed files this workstream created.

Sessions are deliberately *not* archived: they are the evidence for the findings and
native session history is the only durable record of a run (see ``rescue.py``).

Usage: ``python cleanup.py [--dry-run]``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

LEDGER = harness.ARTIFACTS / "cleanup-ledger.json"


def main() -> int:
    dry = "--dry-run" in sys.argv
    client = harness.client()
    entries = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    for entry in entries:
        if entry.get("cleanup") not in (None, "failed"):
            continue
        kind, rid = entry["kind"], entry["id"]
        if kind == "session":
            entry["cleanup"] = "kept: evidence"
            continue
        try:
            if dry:
                entry["cleanup"] = f"would archive ({kind})"
            elif kind == "agent":
                client.beta.agents.archive(rid)
                entry["cleanup"] = "archived"
            elif kind == "environment":
                client.beta.environments.archive(rid)
                entry["cleanup"] = "archived"
            elif kind == "file":
                client.beta.files.delete(rid)
                entry["cleanup"] = "deleted"
            else:
                entry["cleanup"] = "skipped: unknown kind"
        except Exception as error:  # recorded, never hidden
            entry["cleanup"] = f"failed: {type(error).__name__}: {error}"[:200]
        print(f"{kind} {rid}: {entry['cleanup']}", flush=True)
    LEDGER.write_text(json.dumps(entries, indent=2))
    print(f"ledger -> {LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
