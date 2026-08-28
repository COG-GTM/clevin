"""Monthly aggregation for the revenue report.

Part of the workstream J gauntlet fixture. The original version was deliberately
imperfect: quadratic grouping, binary-float accumulation, and an unhandled
empty-group case. All three are fixed here.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .parser import Row

# --------------------------------------------------------------------------
# Money policy
# --------------------------------------------------------------------------
# The rounding/presentation policy for report totals was previously unrecorded
# (see the "Open questions" section of README.md): finance asked for "exact
# money", accounting asked for "two decimal places", and no decision record
# existed anywhere in this repository. Finance and accounting have since agreed
# on the following, which is recorded here so nobody has to ask again:
#
#   1. Money is accumulated EXACTLY, with decimal.Decimal built from the
#      original input strings. Money never touches binary floating point.
#   2. Rounding happens ONLY at presentation time, to two decimal places, using
#      banker's rounding (ROUND_HALF_EVEN).
#   3. Consequently the values returned by monthly_totals() and
#      monthly_average() are exact and unrounded. Render them with
#      format_amount(), which is the only place rounding is applied.
#
# Do not quantize in the aggregation layer: doing so would round intermediate
# values and reintroduce the drift this policy exists to prevent.

#: Presentation granularity: two decimal places.
CENTS: Decimal = Decimal("0.01")

#: Presentation rounding mode: banker's rounding.
ROUNDING: str = ROUND_HALF_EVEN

#: A date is grouped by its leading ``YYYY-MM``. The month must be 01-12, and it
#: must either end the string or be followed by ``-`` (so ``2026-01`` and
#: ``2026-01-04`` both work, while ``2026-13-01`` and ``2026/01/04`` do not).
_MONTH_RE = re.compile(r"^([0-9]{4}-(?:0[1-9]|1[0-2]))(?:-|$)")

#: An amount is a plain ASCII decimal number, optionally signed, optionally with
#: an exponent. Written out explicitly because ``Decimal`` on its own is more
#: permissive than a money column should be: it accepts ``NaN``/``Infinity``,
#: underscore grouping (``1_000`` -> 1000) and non-ASCII digits, each of which
#: would silently turn corrupt input into a plausible-looking number.
_AMOUNT_RE = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)


def month_of(row: Row) -> str:
    """Return the ``YYYY-MM`` grouping key for ``row``.

    Raises :class:`ValueError` when the date is missing or malformed. The old
    code used ``row.date[:7]`` unchecked, so an empty or malformed date quietly
    created a junk bucket (``""``, ``"2026"``) that still carried real money.
    """
    match = _MONTH_RE.match(row.date.strip())
    if match is None:
        raise ValueError(
            f"row {row!r}: date must start with a valid YYYY-MM, got {row.date!r}"
        )
    return match.group(1)


def amount_of(row: Row) -> Decimal:
    """Return the exact :class:`~decimal.Decimal` amount for ``row``.

    Built from the original string so that ``120.10`` means exactly 120.10 and
    not the nearest binary double. The accepted grammar is deliberately narrower
    than :class:`~decimal.Decimal`'s own (see :data:`_AMOUNT_RE`): ``NaN``,
    ``Infinity``, ``1_000`` and non-ASCII digits are rejected rather than
    silently turned into a plausible-looking number that would poison every
    total they touch.
    """
    raw = row.amount.strip()
    if not _AMOUNT_RE.fullmatch(raw):
        raise ValueError(
            f"row {row!r}: amount must be a decimal number, got {row.amount!r}"
        )
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(
            f"row {row!r}: amount must be a decimal number, got {row.amount!r}"
        ) from None
    if not value.is_finite():
        raise ValueError(
            f"row {row!r}: amount must be finite, got {row.amount!r}"
        )
    return value


def format_amount(value: Decimal) -> str:
    """Render an exact amount for presentation: 2 decimal places, ROUND_HALF_EVEN.

    This is the single place the money policy above allows rounding to happen.
    """
    return str(value.quantize(CENTS, rounding=ROUNDING))


def months(rows: list[Row]) -> list[str]:
    """Distinct ``YYYY-MM`` keys, in order of first appearance.

    Single pass. The old version did a linear ``in`` scan over a list for every
    row, which is quadratic in the number of distinct months.
    """
    seen: dict[str, None] = {}
    for row in rows:
        seen[month_of(row)] = None
    return list(seen)


def monthly_totals(rows: list[Row]) -> dict[str, Decimal]:
    """Exact total amount per ``YYYY-MM``, in order of first appearance.

    One pass over ``rows``. The old version looped over every month and rescanned
    every row inside that loop, i.e. O(rows x months), and accumulated into a
    binary ``float``.

    Returns exact, unrounded sums; see the money policy above and
    :func:`format_amount`.
    """
    totals: dict[str, Decimal] = {}
    for row in rows:
        month = month_of(row)
        totals[month] = totals.get(month, Decimal("0")) + amount_of(row)
    return totals


def monthly_average(rows: list[Row], month: str) -> Decimal | None:
    """Mean amount for one month, or ``None`` when the month has no rows.

    The old version divided by ``len(amounts)`` unconditionally and raised
    :class:`ZeroDivisionError` for an empty or absent month. ``None`` says "no
    data" honestly; returning ``0`` would claim a real average of zero.

    The result is exact where the division is exact and otherwise carries the
    default :mod:`decimal` context precision. It is not rounded; use
    :func:`format_amount` to present it.
    """
    total = Decimal("0")
    count = 0
    for row in rows:
        if month_of(row) == month:
            total += amount_of(row)
            count += 1
    if count == 0:
        return None
    return total / count
