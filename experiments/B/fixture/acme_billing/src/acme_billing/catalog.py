"""Product catalog. Prices are still stored as floats."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit_price: float
    taxable: bool = True


CATALOG = {
    "WID-1": Product("WID-1", "Widget", 19.99),
    "WID-2": Product("WID-2", "Widget Pro", 49.95),
    "GAD-1": Product("GAD-1", "Gadget", 7.25),
    "GAD-2": Product("GAD-2", "Gadget Mini", 3.10),
    "SRV-1": Product("SRV-1", "Support plan", 120.00, taxable=False),
    "KIT-1": Product("KIT-1", "Starter kit", 53.50),
}


def lookup(sku: str) -> Product:
    try:
        return CATALOG[sku]
    except KeyError as exc:
        raise KeyError(f"unknown sku {sku}") from exc


def unit_price(sku: str) -> float:
    return lookup(sku).unit_price
