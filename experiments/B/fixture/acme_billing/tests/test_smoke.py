"""Pre-existing behaviour that must keep working (regression detector)."""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from acme_billing import cli, discounts, legacy  # noqa: E402


class SmokeTest(unittest.TestCase):
    def test_legacy_total_matches_published_value(self) -> None:
        self.assertEqual(legacy.legacy_total([("GAD-2", 3), ("WID-1", 1)]), 31.71)

    def test_legacy_tax_only(self) -> None:
        self.assertEqual(legacy.legacy_tax_only([("WID-1", 1)]), 1.65)

    def test_bulk_rule_thresholds(self) -> None:
        self.assertEqual(discounts.bulk_rule(9), 0.0)
        self.assertEqual(discounts.bulk_rule(10), 2.5)
        self.assertEqual(discounts.bulk_rule(25), 7.5)
        self.assertEqual(discounts.bulk_rule(100), 15.0)

    def test_cli_output(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["GAD-2:3", "WID-1:1"])
        self.assertEqual(code, 0)
        self.assertEqual(
            buffer.getvalue().splitlines(),
            ["subtotal 29.29", "tax 2.42", "total 31.71"],
        )


if __name__ == "__main__":
    unittest.main()
