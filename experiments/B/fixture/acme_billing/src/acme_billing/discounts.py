"""Discount rules, also float-based."""

from __future__ import annotations


def percentage_off(amount: float, percent: float) -> float:
    return round(amount * (1.0 - percent / 100.0), 2)


def flat_off(amount: float, discount: float) -> float:
    return round(max(amount - discount, 0.0), 2)


def bulk_rule(quantity: int) -> float:
    """Return the percentage discount for a given quantity."""
    if quantity >= 100:
        return 15.0
    if quantity >= 25:
        return 7.5
    if quantity >= 10:
        return 2.5
    return 0.0
