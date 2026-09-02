"""Row parsing for the revenue report.

Part of the workstream J gauntlet fixture: a deliberately imperfect module that a
gauntlet run is asked to make trustworthy. Do not "fix" it outside a gauntlet run.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = ("date", "region", "description", "amount")


@dataclass(frozen=True, slots=True)
class Row:
    date: str
    region: str
    description: str
    amount: str


def parse_rows(text: str) -> list[Row]:
    """Parse the report CSV into rows.

    Known-imperfect: fields are split on every comma, so a quoted description
    containing a comma shifts every later column.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    rows: list[Row] = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < len(HEADER):
            continue
        rows.append(Row(parts[0], parts[1], parts[2], parts[3]))
    return rows
