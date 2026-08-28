"""Fixture CI check for the K4 experiment: passes only when VALUE == 42."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from value import VALUE  # noqa: E402

if VALUE != 42:
    raise SystemExit(f"k4-ci-fixture FAILED: expected VALUE == 42, found {VALUE}")
print("k4-ci-fixture PASSED")
