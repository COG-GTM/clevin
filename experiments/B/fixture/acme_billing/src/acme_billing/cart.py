"""Shopping cart totals. Duplicates the tax rules found in invoice.py."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import catalog

TAX_RATE = 0.0825


@dataclass
class Line:
    sku: str
    quantity: int


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)

    def add(self, sku: str, quantity: int = 1) -> None:
        self.lines.append(Line(sku, quantity))

    def subtotal(self) -> float:
        total = 0.0
        for line in self.lines:
            total += catalog.unit_price(line.sku) * line.quantity
        return round(total, 2)

    def tax(self) -> float:
        taxed = 0.0
        for line in self.lines:
            product = catalog.lookup(line.sku)
            if product.taxable:
                taxed += product.unit_price * line.quantity
        return round(taxed * TAX_RATE, 2)

    def total(self) -> float:
        return round(self.subtotal() + self.tax(), 2)
