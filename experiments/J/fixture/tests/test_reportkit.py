"""Tests for the workstream J gauntlet fixture.

`test_quoted_description_does_not_shift_columns` fails on the fixture's initial
state: that red check is the gauntlet's starting condition.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[1]
if str(FIXTURE_ROOT) not in sys.path:
    sys.path.insert(0, str(FIXTURE_ROOT))

from reportkit.aggregate import monthly_totals  # noqa: E402
from reportkit.parser import parse_rows  # noqa: E402

SIMPLE = """date,region,description,amount
2026-01-04,emea,seat expansion,120.10
2026-01-19,amer,renewal,80.20
2026-02-02,emea,renewal,50.00
"""

QUOTED = """date,region,description,amount
2026-01-04,emea,"seat expansion, annual",120.10
2026-01-19,amer,renewal,80.20
"""


def test_parses_simple_rows() -> None:
    rows = parse_rows(SIMPLE)
    assert len(rows) == 3
    assert rows[0].region == "emea"
    assert rows[2].amount == "50.00"


def test_monthly_totals_groups_by_month() -> None:
    totals = monthly_totals(parse_rows(SIMPLE))
    assert sorted(totals) == ["2026-01", "2026-02"]
    assert round(totals["2026-02"], 2) == 50.00


def test_quoted_description_does_not_shift_columns() -> None:
    rows = parse_rows(QUOTED)
    assert len(rows) == 2
    assert rows[0].description == "seat expansion, annual"
    assert rows[0].amount == "120.10"
