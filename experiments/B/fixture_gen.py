"""Generate the wide tier of the workstream-B fixture.

The hand-written core (``acme_billing``) is finished by a Sonnet session in about
two minutes, which measures nothing about long-horizon behaviour. The wide tier
adds ``N_REGIONS`` mechanically similar but individually different pricing
modules plus a contract test over all of them, so a run needs tens of edits, a
maintained plan, and enough tool output to reach native compaction.

``region_source(i, decimal=False)`` is the starting (float) state; the same
function with ``decimal=True`` is the reference target, which is how the
workload is proven solvable.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

N_REGIONS = 18
MARKER = "# clevin-b: MAUVE-42"
SKUS = ["ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON"]


def code(index: int) -> str:
    return f"r{index:02d}"


def rate(index: int) -> Decimal:
    return (Decimal("0.0400") + Decimal(index) * Decimal("0.0035")).quantize(Decimal("0.0001"))


def prices(index: int) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for slot, sku in enumerate(SKUS):
        cents = 199 + index * 137 + slot * 561
        out[f"{sku}-{index:02d}"] = (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))
    return out


def variant(index: int) -> str:
    return ["plain", "exempt", "legacy", "shipping"][index % 4]


def exempt_sku(index: int) -> str:
    return f"{SKUS[index % len(SKUS)]}-{index:02d}"


def quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def expected_total(index: int, basket: list[tuple[str, int]]) -> Decimal:
    """Independent expected value: what a correct Decimal implementation must return."""
    subtotal = Decimal("0")
    taxable = Decimal("0")
    for sku, qty in basket:
        amount = prices(index)[sku] * qty
        subtotal += amount
        if not (variant(index) == "exempt" and sku == exempt_sku(index)):
            taxable += amount
    return quantize(subtotal + quantize(taxable * rate(index)))


def region_source(index: int, *, decimal: bool = False) -> str:
    name = code(index)
    table = prices(index)
    head = f'"""Pricing rules for region {name.upper()}."""\n\nfrom __future__ import annotations\n'
    if decimal:
        head = (
            f"{MARKER}\n"
            f'"""Pricing rules for region {name.upper()}."""\n\n'
            "from __future__ import annotations\n\n"
            "from decimal import Decimal\n\n"
            "from ..money import Money, quantize\n"
        )
    if decimal:
        rate_line = f'RATE = Decimal("{rate(index)}")'
        rows = ",\n".join(f'    "{sku}": Decimal("{amount}")' for sku, amount in table.items())
    else:
        rate_line = f"RATE = {rate(index)}"
        rows = ",\n".join(f'    "{sku}": {amount}' for sku, amount in table.items())
    body = [head, "", rate_line, "", "PRICES = {", rows, "}", ""]

    if decimal:
        body += [
            "",
            "def line_total(sku: str, quantity: int) -> Money:",
            "    return Money(quantize(PRICES[sku] * quantity))",
            "",
            "",
            "def subtotal(basket: list[tuple[str, int]]) -> Money:",
            '    total = Decimal("0")',
            "    for sku, quantity in basket:",
            "        total += PRICES[sku] * quantity",
            "    return Money(quantize(total))",
            "",
            "",
            "def tax(basket: list[tuple[str, int]]) -> Money:",
            '    taxable = Decimal("0")',
            "    for sku, quantity in basket:",
            (
                f'        if sku == "{exempt_sku(index)}":\n            continue'
                if variant(index) == "exempt"
                else "        pass"
            ),
            "        taxable += PRICES[sku] * quantity",
            "    return Money(quantize(taxable * RATE))",
            "",
            "",
            "def total(basket: list[tuple[str, int]]) -> Money:",
            "    return Money(quantize(subtotal(basket).amount + tax(basket).amount))",
        ]
    else:
        body += [
            "",
            "def line_total(sku: str, quantity: int) -> float:",
            "    return round(PRICES[sku] * quantity, 2)",
            "",
            "",
            "def subtotal(basket: list[tuple[str, int]]) -> float:",
            "    total = 0.0",
            "    for sku, quantity in basket:",
            "        total += PRICES[sku] * quantity",
            "    return round(total, 2)",
            "",
            "",
            "def tax(basket: list[tuple[str, int]]) -> float:",
            "    taxable = 0.0",
            "    for sku, quantity in basket:",
            (
                f'        if sku == "{exempt_sku(index)}":\n            continue'
                if variant(index) == "exempt"
                else "        pass"
            ),
            "        taxable += PRICES[sku] * quantity",
            "    return round(taxable * RATE, 2)",
            "",
            "",
            "def total(basket: list[tuple[str, int]]) -> float:",
            "    return round(subtotal(basket) + tax(basket), 2)",
        ]

    if variant(index) == "legacy":
        body += [
            "",
            "",
            "def legacy_total(basket: list[tuple[str, int]]) -> float:",
            '    """Old storefront API: must keep returning a plain float."""',
            (
                "    return float(total(basket).amount)"
                if decimal
                else "    return total(basket)"
            ),
        ]
    if variant(index) == "shipping":
        body += [
            "",
            "",
            (
                "def shipping(weight_kg: Decimal) -> Money:"
                if decimal
                else "def shipping(weight_kg: float) -> float:"
            ),
            (
                '    return Money(quantize(Decimal("4.95") + Decimal(weight_kg) * Decimal("1.35")))'
                if decimal
                else "    return round(4.95 + weight_kg * 1.35, 2)"
            ),
        ]
    return "\n".join(body).rstrip() + "\n"


def baskets(index: int) -> list[tuple[str, int]]:
    table = list(prices(index))
    return [(table[0], 3), (table[index % len(table)], 2), (table[-1], 1)]


def test_wide_source() -> str:
    lines = [
        '"""Contract for the regional pricing modules. DO NOT MODIFY THIS FILE."""',
        "",
        "from __future__ import annotations",
        "",
        "import importlib",
        "import pathlib",
        "import sys",
        "import unittest",
        "from decimal import Decimal",
        "",
        'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))',
        "",
        "from acme_billing.money import Money  # noqa: E402",
        "",
        "EXPECTED = {",
    ]
    for index in range(N_REGIONS):
        lines.append(
            f'    "{code(index)}": ("{rate(index)}", "{expected_total(index, baskets(index))}",'
            f" {baskets(index)!r}),"
        )
    lines += [
        "}",
        "",
        "",
        "class WideContractTest(unittest.TestCase):",
        "    def test_every_region_is_decimal_and_exact(self) -> None:",
        "        for name, (rate, total, basket) in EXPECTED.items():",
        '            module = importlib.import_module(f"acme_billing.regions.{name}")',
        "            with self.subTest(region=name):",
        "                self.assertIsInstance(module.RATE, Decimal)",
        "                self.assertEqual(module.RATE, Decimal(rate))",
        "                for price in module.PRICES.values():",
        "                    self.assertIsInstance(price, Decimal)",
        "                result = module.total(basket)",
        "                self.assertIsInstance(result, Money)",
        "                self.assertEqual(result.amount, Decimal(total))",
        "                self.assertIsInstance(module.subtotal(basket), Money)",
        "                self.assertIsInstance(module.tax(basket), Money)",
        "                first = basket[0]",
        "                self.assertIsInstance(module.line_total(*first), Money)",
        "",
        "    def test_legacy_regions_still_return_float(self) -> None:",
        "        for name in EXPECTED:",
        '            module = importlib.import_module(f"acme_billing.regions.{name}")',
        '            if not hasattr(module, "legacy_total"):',
        "                continue",
        "            basket = EXPECTED[name][2]",
        "            with self.subTest(region=name):",
        "                self.assertIsInstance(module.legacy_total(basket), float)",
        "",
        "",
        'if __name__ == "__main__":',
        "    unittest.main()",
        "",
    ]
    return "\n".join(lines)


def start_files() -> dict[str, bytes]:
    """The float starting state of the wide tier."""
    out: dict[str, bytes] = {
        "src/acme_billing/regions/__init__.py": (
            '"""Regional pricing modules (still float-based)."""\n\n'
            f"REGIONS = {[code(i) for i in range(N_REGIONS)]!r}\n"
        ).encode(),
        "tests/test_wide.py": test_wide_source().encode(),
    }
    for index in range(N_REGIONS):
        out[f"src/acme_billing/regions/{code(index)}.py"] = region_source(index).encode()
    return out


def target_files() -> dict[str, str]:
    """The Decimal target state, used only by ``reference_solution.py``."""
    out = {
        "src/acme_billing/regions/__init__.py": (
            f"{MARKER}\n"
            '"""Regional pricing modules."""\n\n'
            f"REGIONS = {[code(i) for i in range(N_REGIONS)]!r}\n"
        )
    }
    for index in range(N_REGIONS):
        out[f"src/acme_billing/regions/{code(index)}.py"] = region_source(index, decimal=True)
    return out
