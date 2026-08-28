"""Legacy helpers kept for the old storefront. Still float-only.

Callers outside this repository import ``legacy_total``; its *signature* must
not change, but its arithmetic may.
"""

from __future__ import annotations

from . import cart


def legacy_total(items: list[tuple[str, int]]) -> float:
    basket = cart.Cart()
    for sku, quantity in items:
        basket.add(sku, quantity)
    return basket.total()


def legacy_tax_only(items: list[tuple[str, int]]) -> float:
    basket = cart.Cart()
    for sku, quantity in items:
        basket.add(sku, quantity)
    return basket.tax()
