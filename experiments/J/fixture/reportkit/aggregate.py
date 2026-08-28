"""Monthly aggregation for the revenue report.

Part of the workstream J gauntlet fixture. Deliberately imperfect: quadratic
grouping, binary-float accumulation, and an unhandled empty-group case.
"""

from __future__ import annotations

from .parser import Row


def months(rows: list[Row]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        month = row.date[:7]
        if month not in seen:
            seen.append(month)
    return seen


def monthly_totals(rows: list[Row]) -> dict[str, float]:
    """Total amount per YYYY-MM.

    Known-imperfect: O(n * m) scan per month and float accumulation.
    """
    totals: dict[str, float] = {}
    for month in months(rows):
        total = 0.0
        for row in rows:
            if row.date[:7] == month:
                total += float(row.amount)
        totals[month] = total
    return totals


def monthly_average(rows: list[Row], month: str) -> float:
    """Mean amount for one month.

    Known-imperfect: divides by zero when the month has no rows.
    """
    amounts = [float(row.amount) for row in rows if row.date[:7] == month]
    return sum(amounts) / len(amounts)
