"""Aggregate reporting over invoices."""

from __future__ import annotations

from collections import defaultdict

from .invoice import Invoice


def revenue_by_sku(invoices: list[Invoice]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for invoice in invoices:
        for line in invoice.lines:
            totals[line.sku] += invoice.line_amount(line)
    return {sku: round(value, 2) for sku, value in totals.items()}


def grand_total(invoices: list[Invoice]) -> float:
    return round(sum(invoice.total() for invoice in invoices), 2)
