"""Tests for the workstream J gauntlet fixture.

`test_quoted_description_does_not_shift_columns` failed on the fixture's initial
state: that red check was the gauntlet's starting condition. It passes now that
field splitting is delegated to the standard library `csv` reader.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).resolve().parents[1]
if str(FIXTURE_ROOT) not in sys.path:
    sys.path.insert(0, str(FIXTURE_ROOT))

from reportkit.aggregate import (  # noqa: E402
    amount_of,
    format_amount,
    month_of,
    monthly_average,
    monthly_totals,
    months,
)
from reportkit.parser import Row, parse_rows  # noqa: E402

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


# ---------------------------------------------------------------------------
# Parsing: delimiters and quoting
# ---------------------------------------------------------------------------

QUOTED_ESCAPE = '''date,region,description,amount
2026-01-04,emea,"renewal, ""gold"" tier",120.10
'''

BLANK_LINES = """date,region,description,amount

2026-01-04,emea,seat expansion,120.10

2026-02-02,emea,renewal,50.00
"""

NO_HEADER = """2026-01-04,emea,seat expansion,120.10
2026-01-19,amer,renewal,80.20
"""

SHORT_ROW = """date,region,description,amount
2026-01-04,emea,seat expansion
"""


def test_doubled_quotes_inside_quoted_field() -> None:
    rows = parse_rows(QUOTED_ESCAPE)
    assert len(rows) == 1
    assert rows[0].description == 'renewal, "gold" tier'
    assert rows[0].amount == "120.10"


def test_blank_lines_are_ignored() -> None:
    rows = parse_rows(BLANK_LINES)
    assert [row.amount for row in rows] == ["120.10", "50.00"]


def test_headerless_input_keeps_its_first_row() -> None:
    # The old parser always dropped the first line, silently losing a row of
    # revenue when the export arrived without a header.
    rows = parse_rows(NO_HEADER)
    assert len(rows) == 2
    assert rows[0].description == "seat expansion"


def test_row_with_wrong_field_count_is_not_silently_dropped() -> None:
    with pytest.raises(ValueError, match="expected 4 fields"):
        parse_rows(SHORT_ROW)


def test_empty_input_parses_to_no_rows() -> None:
    assert parse_rows("") == []
    assert parse_rows("date,region,description,amount\n") == []


# ---------------------------------------------------------------------------
# Money: exact decimal accumulation, never binary float
# ---------------------------------------------------------------------------

REPEATED_CENTS = """date,region,description,amount
2026-01-04,emea,a,0.07
2026-01-05,emea,b,0.07
2026-01-06,emea,c,0.07
"""


def test_totals_are_exact_decimals_not_floats() -> None:
    totals = monthly_totals(parse_rows(REPEATED_CENTS))
    assert isinstance(totals["2026-01"], Decimal)
    assert totals["2026-01"] == Decimal("0.07") * 3


def test_total_avoids_the_binary_float_drift() -> None:
    rows = parse_rows(REPEATED_CENTS)
    exact = monthly_totals(rows)["2026-01"]
    assert exact == Decimal("0.21")
    # The old implementation accumulated into a float and produced this instead.
    drifted = 0.0
    for row in rows:
        drifted += float(row.amount)
    assert drifted != 0.21
    assert Decimal(repr(drifted)) != exact


def test_sub_cent_input_is_preserved_exactly() -> None:
    rows = [Row("2026-01-04", "emea", "a", "0.005"),
            Row("2026-01-05", "emea", "b", "0.005")]
    assert monthly_totals(rows)["2026-01"] == Decimal("0.010")


def test_amount_of_rejects_non_numeric_and_non_finite() -> None:
    for bad in ("", "abc", "1,200.00", "NaN", "Infinity", "-inf"):
        with pytest.raises(ValueError):
            amount_of(Row("2026-01-04", "emea", "d", bad))


def test_amount_of_tolerates_surrounding_whitespace() -> None:
    assert amount_of(Row("2026-01-04", "emea", "d", " 120.10 ")) == Decimal("120.10")


# ---------------------------------------------------------------------------
# Presentation: the single place rounding happens (2dp, ROUND_HALF_EVEN)
# ---------------------------------------------------------------------------


def test_format_amount_uses_two_places_and_bankers_rounding() -> None:
    assert format_amount(Decimal("1")) == "1.00"
    assert format_amount(Decimal("120.10")) == "120.10"
    # Ties go to the even digit rather than always up.
    assert format_amount(Decimal("0.125")) == "0.12"
    assert format_amount(Decimal("0.135")) == "0.14"
    assert format_amount(Decimal("-0.125")) == "-0.12"


def test_aggregation_itself_never_rounds() -> None:
    rows = [Row("2026-01-04", "emea", "a", "0.125")]
    assert monthly_totals(rows)["2026-01"] == Decimal("0.125")
    assert format_amount(monthly_totals(rows)["2026-01"]) == "0.12"


# ---------------------------------------------------------------------------
# Grouping: one pass, not quadratic
# ---------------------------------------------------------------------------


class _CountingRows(list):
    """A row list that records how many times it has been iterated."""

    def __init__(self, items: list[Row]) -> None:
        super().__init__(items)
        self.passes = 0

    def __iter__(self) -> Iterator[Row]:
        self.passes += 1
        return super().__iter__()


def _rows_over_months(count: int) -> list[Row]:
    return [
        Row(f"2026-{(i % 12) + 1:02d}-0{(i % 9) + 1}", "emea", "x", "1.00")
        for i in range(count)
    ]


def test_monthly_totals_scans_the_rows_exactly_once() -> None:
    # The old implementation called months() and then rescanned every row once
    # per month, i.e. 1 + len(months) passes. Any regression to a per-group
    # rescan makes this fail deterministically, without timing.
    rows = _CountingRows(_rows_over_months(120))
    totals = monthly_totals(rows)
    assert rows.passes == 1
    assert len(totals) == 12


def test_months_scans_the_rows_exactly_once() -> None:
    rows = _CountingRows(_rows_over_months(120))
    assert len(months(rows)) == 12
    assert rows.passes == 1


def test_grouping_preserves_first_appearance_order() -> None:
    rows = [Row("2026-03-01", "emea", "a", "1.00"),
            Row("2026-01-02", "emea", "b", "2.00"),
            Row("2026-03-05", "emea", "c", "3.00")]
    assert months(rows) == ["2026-03", "2026-01"]
    assert list(monthly_totals(rows)) == ["2026-03", "2026-01"]
    assert monthly_totals(rows)["2026-03"] == Decimal("4.00")


# ---------------------------------------------------------------------------
# Empty and missing months do not blow up
# ---------------------------------------------------------------------------


def test_no_rows_at_all_is_not_an_error() -> None:
    assert monthly_totals([]) == {}
    assert months([]) == []
    assert monthly_average([], "2026-01") is None


def test_average_of_a_missing_month_is_none_not_zero_division() -> None:
    rows = parse_rows(SIMPLE)
    # The old implementation raised ZeroDivisionError here.
    assert monthly_average(rows, "2026-09") is None


def test_average_is_exact_for_a_populated_month() -> None:
    rows = parse_rows(SIMPLE)
    assert monthly_average(rows, "2026-02") == Decimal("50.00")
    assert monthly_average(rows, "2026-01") == Decimal("100.15")


# ---------------------------------------------------------------------------
# Dates: a malformed date must not create a junk money bucket
# ---------------------------------------------------------------------------


def test_month_of_accepts_valid_dates() -> None:
    assert month_of(Row("2026-01-04", "emea", "d", "1.00")) == "2026-01"
    assert month_of(Row("2026-12", "emea", "d", "1.00")) == "2026-12"
    assert month_of(Row(" 2026-01-04 ", "emea", "d", "1.00")) == "2026-01"


def test_month_of_rejects_malformed_dates() -> None:
    for bad in ("", "2026", "2026-13-01", "2026-00-01", "2026/01/04", "not-a-date"):
        with pytest.raises(ValueError, match="valid YYYY-MM"):
            month_of(Row(bad, "emea", "d", "1.00"))


# ---------------------------------------------------------------------------
# Real-world export quirks
# ---------------------------------------------------------------------------

BOM_CSV = "\ufeffdate,region,description,amount\n2026-01-04,emea,seat expansion,120.10\n"
CRLF_CSV = "date,region,description,amount\r\n2026-01-04,emea,a,120.10\r\n"
MULTILINE_CSV = 'date,region,description,amount\n2026-01-04,emea,"line one\nline two",120.10\n'


def test_utf8_bom_does_not_turn_the_header_into_a_data_row() -> None:
    # Spreadsheet exports routinely carry a BOM. Without stripping it the header
    # failed to match, was parsed as data, and then blew up on the date.
    rows = parse_rows(BOM_CSV)
    assert len(rows) == 1
    assert rows[0].date == "2026-01-04"
    assert monthly_totals(rows) == {"2026-01": Decimal("120.10")}


def test_crlf_line_endings() -> None:
    rows = parse_rows(CRLF_CSV)
    assert len(rows) == 1
    assert rows[0].amount == "120.10"


def test_newline_inside_a_quoted_description() -> None:
    rows = parse_rows(MULTILINE_CSV)
    assert len(rows) == 1
    assert rows[0].description == "line one\nline two"
    assert rows[0].amount == "120.10"


def test_amount_grammar_is_narrower_than_decimal() -> None:
    # Decimal() alone would accept all of these and produce a plausible number.
    for bad in ("1_000", "１２", "NaN", "-NaN", "Infinity", "-Infinity", "0x10"):
        with pytest.raises(ValueError, match="decimal number|finite"):
            amount_of(Row("2026-01-04", "emea", "d", bad))


def test_amount_grammar_accepts_ordinary_money() -> None:
    assert amount_of(Row("2026-01-04", "e", "d", "-50.00")) == Decimal("-50.00")
    assert amount_of(Row("2026-01-04", "e", "d", "+5.00")) == Decimal("5.00")
    assert amount_of(Row("2026-01-04", "e", "d", "0")) == Decimal("0")
    assert amount_of(Row("2026-01-04", "e", "d", ".50")) == Decimal("0.50")
