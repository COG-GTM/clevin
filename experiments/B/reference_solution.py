"""Reference solution for the workstream-B workload.

Not part of any session: it exists so the objective grader is provably reachable
(``python3 reference_solution.py <dir>`` then ``python3 grade.py`` -> PASS). A
workload whose PASS state was never demonstrated cannot distinguish a weak agent
from an impossible task, which is the measurement the arms depend on.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture_gen  # noqa: E402

MARKER = "# clevin-b: MAUVE-42"

FILES: dict[str, str] = {
    "src/acme_billing/money.py": '''{marker}
"""Money value type and the single rounding rule for the package."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
TAX_RATE = Decimal("0.0825")


def quantize(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be a Decimal")

    @classmethod
    def of(cls, value: Decimal, currency: str = "USD") -> "Money":
        return cls(quantize(value), currency)

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def _check(self, other: "Money") -> None:
        if other.currency != self.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
''',
    "src/acme_billing/catalog.py": '''{marker}
"""Product catalog. Prices are Decimal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import Money


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit_price: Decimal
    taxable: bool = True


CATALOG = {
    "WID-1": Product("WID-1", "Widget", Decimal("19.99")),
    "WID-2": Product("WID-2", "Widget Pro", Decimal("49.95")),
    "GAD-1": Product("GAD-1", "Gadget", Decimal("7.25")),
    "GAD-2": Product("GAD-2", "Gadget Mini", Decimal("3.10")),
    "SRV-1": Product("SRV-1", "Support plan", Decimal("120.00"), taxable=False),
    "KIT-1": Product("KIT-1", "Starter kit", Decimal("53.50")),
}


def lookup(sku: str) -> Product:
    try:
        return CATALOG[sku]
    except KeyError as exc:
        raise KeyError(f"unknown sku {sku}") from exc


def unit_price(sku: str) -> Money:
    return Money.of(lookup(sku).unit_price)
''',
    "src/acme_billing/cart.py": '''{marker}
"""Shopping cart totals, sharing one tax rate with invoice.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import catalog
from .money import TAX_RATE, Money, quantize

__all__ = ["TAX_RATE", "Cart", "Line"]


@dataclass
class Line:
    sku: str
    quantity: int


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)

    def subtotal(self) -> Money:
        total = Decimal("0")
        for line in self.lines:
            total += catalog.lookup(line.sku).unit_price * line.quantity
        return Money.of(total)

    def add(self, sku: str, quantity: int = 1) -> None:
        self.lines.append(Line(sku, quantity))

    def tax(self) -> Money:
        taxed = Decimal("0")
        for line in self.lines:
            product = catalog.lookup(line.sku)
            if product.taxable:
                taxed += product.unit_price * line.quantity
        return Money.of(taxed * TAX_RATE)

    def total(self) -> Money:
        return Money(quantize(self.subtotal().amount + self.tax().amount))
''',
    "src/acme_billing/discounts.py": '''{marker}
"""Discount rules. Monetary maths is Decimal; the rule table stays float."""

from __future__ import annotations

from decimal import Decimal

from .money import quantize


def percentage_off(amount: Decimal, percent: Decimal | float) -> Decimal:
    return quantize(Decimal(amount) * (Decimal("1") - Decimal(str(percent)) / Decimal("100")))


def flat_off(amount: Decimal, discount: Decimal) -> Decimal:
    return quantize(max(Decimal(amount) - Decimal(discount), Decimal("0")))


def bulk_rule(quantity: int) -> float:
    """Return the percentage discount for a given quantity."""
    if quantity >= 100:
        return 15.0
    if quantity >= 25:
        return 7.5
    if quantity >= 10:
        return 2.5
    return 0.0
''',
    "src/acme_billing/invoice.py": '''{marker}
"""Invoice rendering, using the shared tax rate."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import catalog, discounts
from .money import TAX_RATE, Money, quantize

__all__ = ["TAX_RATE", "Invoice", "InvoiceLine"]


@dataclass
class InvoiceLine:
    sku: str
    quantity: int
    discount_percent: Decimal = Decimal("0")


@dataclass
class Invoice:
    number: str
    lines: list[InvoiceLine]

    def line_amount(self, line: InvoiceLine) -> Money:
        gross = catalog.lookup(line.sku).unit_price * line.quantity
        return Money(discounts.percentage_off(gross, line.discount_percent))

    def subtotal(self) -> Money:
        total = Decimal("0")
        for line in self.lines:
            total += self.line_amount(line).amount
        return Money(quantize(total))

    def tax(self) -> Money:
        taxable = Decimal("0")
        for line in self.lines:
            if catalog.lookup(line.sku).taxable:
                taxable += self.line_amount(line).amount
        return Money.of(taxable * TAX_RATE)

    def total(self) -> Money:
        return Money(quantize(self.subtotal().amount + self.tax().amount))

    def render(self) -> str:
        rows = [f"INVOICE {self.number}"]
        for line in self.lines:
            product = catalog.lookup(line.sku)
            rows.append(f"{product.name} x{line.quantity}  {self.line_amount(line).amount:.2f}")
        rows.append(f"subtotal {self.subtotal().amount:.2f}")
        rows.append(f"tax {self.tax().amount:.2f}")
        rows.append(f"total {self.total().amount:.2f}")
        return "\\n".join(rows)
''',
    "src/acme_billing/reports.py": '''{marker}
"""Aggregate reporting over invoices."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .invoice import Invoice
from .money import Money, quantize


def revenue_by_sku(invoices: list[Invoice]) -> dict[str, Money]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for invoice in invoices:
        for line in invoice.lines:
            totals[line.sku] += invoice.line_amount(line).amount
    return {sku: Money(quantize(value)) for sku, value in totals.items()}


def grand_total(invoices: list[Invoice]) -> Money:
    total = Decimal("0")
    for invoice in invoices:
        total += invoice.total().amount
    return Money(quantize(total))
''',
    "src/acme_billing/legacy.py": '''{marker}
"""Legacy helpers kept for the old storefront: float in, float out."""

from __future__ import annotations

from . import cart


def legacy_total(items: list[tuple[str, int]]) -> float:
    basket = cart.Cart()
    for sku, quantity in items:
        basket.add(sku, quantity)
    return float(basket.total().amount)


def legacy_tax_only(items: list[tuple[str, int]]) -> float:
    basket = cart.Cart()
    for sku, quantity in items:
        basket.add(sku, quantity)
    return float(basket.tax().amount)
''',
    "src/acme_billing/cli.py": '''{marker}
"""Tiny CLI wrapper used by the ops team."""

from __future__ import annotations

import sys

from .cart import Cart


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    basket = Cart()
    for token in argv:
        sku, _, qty = token.partition(":")
        basket.add(sku, int(qty or 1))
    print(f"subtotal {basket.subtotal().amount:.2f}")
    print(f"tax {basket.tax().amount:.2f}")
    print(f"total {basket.total().amount:.2f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
''',
}


def apply(root: Path) -> None:
    for name, body in FILES.items():
        (root / name).write_text(body.replace("{marker}", MARKER, 1))
    for name, body in fixture_gen.target_files().items():
        (root / name).write_text(body)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    apply(root)
    return subprocess.run([sys.executable, "grade.py"], cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
