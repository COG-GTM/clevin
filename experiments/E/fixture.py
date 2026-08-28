"""The identical in-sandbox fixture used by the behavioural Memory Store probes.

Primitive context: sessions get a fresh sandbox, so a *constant* fixture has to be
recreated inside each session for the with-store / without-store comparisons to be
measured against the same ground truth. The fixture is a few files and one gate
script; it is not product code and never leaves `experiments/E/`.

The learnable fact is that the gate only passes when CLEVIN_FIXTURE_TOKEN=omega-7 is
exported, and that fact lives three directories deep among decoys.
"""

from __future__ import annotations

FIXTURE_SETUP = r"""mkdir -p /workspace/fixture/scripts /workspace/fixture/docs/internal
cd /workspace/fixture
cat > package.json <<'JSON'
{"name": "clevin-fixture", "private": true, "scripts": {"lint": "echo LINT-OK"}}
JSON
cat > scripts/verify.sh <<'SH'
#!/usr/bin/env bash
set -u
if [ "${CLEVIN_FIXTURE_TOKEN:-}" != "omega-7" ]; then
  echo "verify failed: CLEVIN_FIXTURE_TOKEN is not set correctly" >&2
  exit 1
fi
echo VERIFY-OK
SH
chmod +x scripts/verify.sh
cat > README.md <<'MD'
# clevin-fixture
The full gate is `bash scripts/verify.sh`. It has one prerequisite documented under docs/.
MD
cat > docs/overview.md <<'MD'
This fixture exists only to exercise a gate script. See the internal notes for CI details.
MD
cat > docs/internal/style.md <<'MD'
Two-space indentation. No trailing whitespace.
MD
cat > docs/internal/release.md <<'MD'
Releases are cut by hand. Nothing here affects the gate.
MD
cat > docs/internal/ci-notes.md <<'MD'
CI exports CLEVIN_FIXTURE_TOKEN=omega-7 before running scripts/verify.sh.
Without that exact value the gate exits 1 with "CLEVIN_FIXTURE_TOKEN is not set correctly".
MD
echo FIXTURE-READY
"""

GATE_TASK = """CLEVIN_SMOKE_TEST Do not touch git, the network, or any external state.

Step 1 — recreate the fixture by running exactly this shell block, unmodified:

```bash
{setup}
```

Step 2 — make the fixture's full gate pass. Report every command you ran, in order,
with its exit status, and finish with the gate's final stdout line.
"""


def gate_task(extra: str = "") -> str:
    task = GATE_TASK.format(setup=FIXTURE_SETUP.strip())
    return f"{task}\n{extra}" if extra else task
