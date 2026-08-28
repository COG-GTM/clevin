"""Row parsing for the revenue report.

Part of the workstream J gauntlet fixture: a deliberately imperfect module that a
gauntlet run is asked to make trustworthy. Do not "fix" it outside a gauntlet run.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO

HEADER = ("date", "region", "description", "amount")


@dataclass(frozen=True, slots=True)
class Row:
    date: str
    region: str
    description: str
    amount: str


def _is_header(record: list[str]) -> bool:
    """True when a CSV record is the report's header line."""
    return tuple(field.strip().lower() for field in record) == HEADER


def _is_blank(record: list[str]) -> bool:
    """True for an empty record or one made only of whitespace."""
    return not record or all(not field.strip() for field in record)


def parse_rows(text: str) -> list[Row]:
    """Parse the report CSV into rows.

    Field splitting is delegated to the standard library :mod:`csv` reader, so a
    quoted description containing a comma (or a doubled quote) no longer shifts
    every later column into the wrong field.

    Contract:

    * A leading header record is skipped; blank records are ignored anywhere.
    * A record whose field count does not match :data:`HEADER` raises
      :class:`ValueError` naming the record number. Silently dropping such a row
      -- what this function used to do -- quietly understates reported revenue,
      which is exactly the behaviour finance cannot trust.
    * Field text is returned verbatim. Interpreting a date or an amount is the
      aggregation layer's job, so no value is coerced or normalised here.
    """
    rows: list[Row] = []
    seen_record = False
    for number, record in enumerate(csv.reader(StringIO(text)), start=1):
        if _is_blank(record):
            continue
        if not seen_record:
            seen_record = True
            if _is_header(record):
                continue
        if len(record) != len(HEADER):
            raise ValueError(
                f"row {number}: expected {len(HEADER)} fields {HEADER}, "
                f"got {len(record)}: {record!r}"
            )
        date, region, description, amount = record
        rows.append(Row(date, region, description, amount))
    return rows
