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
    print(f"subtotal {basket.subtotal():.2f}")
    print(f"tax {basket.tax():.2f}")
    print(f"total {basket.total():.2f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
