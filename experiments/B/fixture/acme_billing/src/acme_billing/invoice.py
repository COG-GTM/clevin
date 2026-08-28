"""Invoice rendering. Re-implements the cart tax rules (drift risk)."""

from __future__ import annotations

from dataclasses import dataclass

from . import catalog, discounts

TAX_RATE = 0.0825


@dataclass
class InvoiceLine:
    sku: str
    quantity: int
    discount_percent: float = 0.0


@dataclass
class Invoice:
    number: str
    lines: list[InvoiceLine]

    def line_amount(self, line: InvoiceLine) -> float:
        gross = catalog.unit_price(line.sku) * line.quantity
        return discounts.percentage_off(gross, line.discount_percent)

    def subtotal(self) -> float:
        return round(sum(self.line_amount(line) for line in self.lines), 2)

    def tax(self) -> float:
        taxable = 0.0
        for line in self.lines:
            if catalog.lookup(line.sku).taxable:
                taxable += self.line_amount(line)
        return round(taxable * TAX_RATE, 2)

    def total(self) -> float:
        return round(self.subtotal() + self.tax(), 2)

    def render(self) -> str:
        rows = [f"INVOICE {self.number}"]
        for line in self.lines:
            product = catalog.lookup(line.sku)
            rows.append(f"{product.name} x{line.quantity}  {self.line_amount(line):.2f}")
        rows.append(f"subtotal {self.subtotal():.2f}")
        rows.append(f"tax {self.tax():.2f}")
        rows.append(f"total {self.total():.2f}")
        return "\n".join(rows)
