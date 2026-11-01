"""Money helpers. All financial calculations use exact Decimal values."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

CENTS = Decimal("0.01")


def money(value: Decimal | str | int) -> Decimal:
    """Parse and normalize a monetary amount to cents."""
    if isinstance(value, Decimal):
        amount = value
    else:
        amount = Decimal(str(value).replace(",", ""))
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def sum_money(values: Iterable[Decimal | str | int]) -> Decimal:
    return money(sum((money(value) for value in values), Decimal("0")))
