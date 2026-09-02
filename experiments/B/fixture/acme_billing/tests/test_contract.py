"""Target contract for the Decimal migration. DO NOT MODIFY THIS FILE.

Rounding rule for every monetary result: quantize to two decimal places with
ROUND_HALF_UP. Floats must not appear anywhere in the money path.

Written against stdlib ``unittest`` so the sandbox needs no third-party
packages: ``python3 -m unittest discover -s tests`` is the only runner.
"""

from __future__ import annotations

import pathlib
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from acme_billing import cart, catalog, invoice, legacy, reports  # noqa: E402
from acme_billing.money import Money  # noqa: E402


class ContractTest(unittest.TestCase):
    def test_unit_price_is_money(self) -> None:
        price = catalog.unit_price("WID-1")
        self.assertIsInstance(price, Money)
        self.assertEqual(price.amount, Decimal("19.99"))

    def test_catalog_prices_are_decimal(self) -> None:
        for product in catalog.CATALOG.values():
            self.assertIsInstance(product.unit_price, Decimal, product.sku)

    def test_cart_totals_are_money(self) -> None:
        basket = cart.Cart()
        basket.add("GAD-2", 3)
        basket.add("WID-1", 1)
        self.assertEqual(basket.subtotal(), Money(Decimal("29.29")))
        self.assertEqual(basket.tax(), Money(Decimal("2.42")))
        self.assertEqual(basket.total(), Money(Decimal("31.71")))

    def test_untaxed_products_are_excluded(self) -> None:
        basket = cart.Cart()
        basket.add("SRV-1", 1)
        self.assertEqual(basket.tax(), Money(Decimal("0.00")))
        self.assertEqual(basket.total(), Money(Decimal("120.00")))

    def test_invoice_uses_half_up_rounding(self) -> None:
        doc = invoice.Invoice("INV-1", [invoice.InvoiceLine("KIT-1", 1, Decimal("5"))])
        self.assertEqual(doc.line_amount(doc.lines[0]), Money(Decimal("50.83")))
        self.assertEqual(doc.subtotal(), Money(Decimal("50.83")))

    def test_invoice_tax_and_total(self) -> None:
        doc = invoice.Invoice("INV-2", [invoice.InvoiceLine("WID-2", 2)])
        self.assertEqual(doc.subtotal(), Money(Decimal("99.90")))
        self.assertEqual(doc.tax(), Money(Decimal("8.24")))
        self.assertEqual(doc.total(), Money(Decimal("108.14")))

    def test_single_source_of_tax_rate(self) -> None:
        self.assertIsInstance(cart.TAX_RATE, Decimal)
        self.assertEqual(cart.TAX_RATE, Decimal("0.0825"))
        self.assertIs(cart.TAX_RATE, invoice.TAX_RATE)

    def test_reports_return_money(self) -> None:
        doc = invoice.Invoice("INV-3", [invoice.InvoiceLine("WID-1", 2)])
        revenue = reports.revenue_by_sku([doc])
        self.assertEqual(revenue["WID-1"], Money(Decimal("39.98")))
        self.assertEqual(reports.grand_total([doc]), Money(Decimal("43.28")))

    def test_legacy_signature_still_returns_float(self) -> None:
        value = legacy.legacy_total([("GAD-2", 3), ("WID-1", 1)])
        self.assertIsInstance(value, float)
        self.assertAlmostEqual(value, 31.71, places=2)

    def test_money_rejects_floats(self) -> None:
        with self.assertRaises(TypeError):
            Money(31.71)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
