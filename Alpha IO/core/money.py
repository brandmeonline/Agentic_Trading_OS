"""Decimal boundary for capital-affecting arithmetic — ATOS-P1-NUM-001.

Invariant:

    Money and quantity are exact at the boundaries where capital is decided.

Binary floats cannot represent most decimal fractions. ``0.1 + 0.2`` is not
``0.3``, and a hundred fills of 0.01 do not sum to 1.00. That is tolerable in
a Sharpe ratio and intolerable in a cash balance, a reservation, or a
reconciliation comparison, because the error accumulates in exactly the
quantities that decide whether an order is allowed.

Three rules this module enforces rather than documents:

* **Never build a Decimal from an uncontrolled float.** ``Decimal(0.1)`` is
  0.1000000000000000055511151231257827021181583404541015625, which is worse
  than the float it came from because it looks exact. :func:`to_decimal`
  accepts strings, integers and Decimals. Floats must go through
  :func:`from_float`, which quantizes and says in its name what it is doing.

* **Tolerance comes from the venue's grid, not an epsilon.** Two quantities
  are equal when they are the same number of ticks. A hand-picked ``1e-9``
  is either too tight for a venue with coarse increments or too loose for one
  with fine ones, and nobody remembers which.

* **Serialise as strings.** A Decimal round-tripped through a float column has
  stopped being a Decimal.

Analytics, numpy and model features stay on float. This module is for the
boundary, not for the whole program.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

Numeric = Union[str, int, Decimal]

#: Working precision for intermediate arithmetic. Generous: the quantization
#: step, not the precision, is what makes a value venue-legal.
CALCULATION_PRECISION = 28

ZERO = Decimal("0")


class MoneyError(ValueError):
    """A value that cannot be trusted as exact money or quantity."""


def to_decimal(value: Numeric, field: str = "value") -> Decimal:
    """Convert to Decimal, refusing raw floats.

    A float argument raises rather than converting, because the caller almost
    certainly has a more exact source available - the string the API returned,
    or the integer of minor units - and silently accepting the float would
    bake its error in while looking precise.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise MoneyError(f"{field}: a boolean is not a monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        raise MoneyError(
            f"{field}: refusing to build a Decimal from the float {value!r}. "
            "Use the exact source (the API's string, or minor units as an "
            "integer), or call from_float() if the imprecision is understood."
        )
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise MoneyError(f"{field}: empty string is not a monetary value")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise MoneyError(f"{field}: {value!r} is not a valid decimal") from exc
    raise MoneyError(f"{field}: cannot convert {type(value).__name__} to Decimal")


def from_float(value: float, places: int = 8, field: str = "value") -> Decimal:
    """Convert a float, acknowledging what that costs.

    Goes through ``repr`` so the result is the shortest decimal that
    round-trips to the same float, rather than the float's full binary
    expansion - ``from_float(0.1)`` is ``0.1``, not ``0.1000000000000000055…``.
    Then quantizes, so the imprecision stops here instead of propagating.

    Use this at the edge of legacy float code. Do not use it for values that
    arrived as strings.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MoneyError(f"{field}: from_float expects a float, got {value!r}")
    try:
        exact = Decimal(repr(float(value)))
    except InvalidOperation as exc:
        raise MoneyError(f"{field}: {value!r} is not a finite number") from exc
    if not exact.is_finite():
        raise MoneyError(f"{field}: {value!r} is not finite")
    return exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def to_str(value: Decimal) -> str:
    """Serialise deterministically, without exponent notation."""
    return format(value.normalize() if value == value.to_integral_value()
                  else value, "f")


@dataclass(frozen=True)
class InstrumentGrid:
    """The increments a venue actually accepts for one instrument.

    ``tick_size`` is the price increment, ``lot_size`` the quantity increment.
    An order off the grid is rejected by the venue, so quantizing to it is not
    cosmetic - it is the difference between an order and a rejection.
    """

    symbol: str
    tick_size: Decimal = Decimal("0.01")
    lot_size: Decimal = Decimal("1")
    min_quantity: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")

    @classmethod
    def of(
        cls,
        symbol: str,
        tick_size: Numeric = "0.01",
        lot_size: Numeric = "1",
        min_quantity: Numeric = "0",
        min_notional: Numeric = "0",
    ) -> "InstrumentGrid":
        return cls(
            symbol=symbol,
            tick_size=to_decimal(tick_size, "tick_size"),
            lot_size=to_decimal(lot_size, "lot_size"),
            min_quantity=to_decimal(min_quantity, "min_quantity"),
            min_notional=to_decimal(min_notional, "min_notional"),
        )

    def quantize_price(self, price: Numeric) -> Decimal:
        """Round a price to the nearest tick."""
        value = to_decimal(price, "price")
        if self.tick_size <= 0:
            return value
        return (value / self.tick_size).quantize(
            Decimal(1), rounding=ROUND_HALF_EVEN
        ) * self.tick_size

    def quantize_quantity(self, quantity: Numeric) -> Decimal:
        """Round a quantity **down** to a whole lot.

        Down, not nearest: rounding up would submit more than the caller
        asked for, and "slightly more exposure than intended" is the wrong
        direction for every caller of this function.
        """
        value = to_decimal(quantity, "quantity")
        if self.lot_size <= 0:
            return value
        return (value / self.lot_size).quantize(
            Decimal(1), rounding=ROUND_DOWN
        ) * self.lot_size

    def is_tradeable(self, quantity: Numeric, price: Numeric) -> tuple:
        """Whether the venue would accept this order. Returns (ok, reason)."""
        qty = to_decimal(quantity, "quantity")
        px = to_decimal(price, "price")
        if qty <= 0:
            return False, "quantity must be positive"
        if self.min_quantity and qty < self.min_quantity:
            return False, (
                f"quantity {to_str(qty)} is below the minimum "
                f"{to_str(self.min_quantity)}"
            )
        if self.quantize_quantity(qty) != qty:
            return False, (
                f"quantity {to_str(qty)} is not a multiple of the lot size "
                f"{to_str(self.lot_size)}"
            )
        if self.quantize_price(px) != px:
            return False, (
                f"price {to_str(px)} is not a multiple of the tick size "
                f"{to_str(self.tick_size)}"
            )
        if self.min_notional and qty * px < self.min_notional:
            return False, (
                f"notional {to_str(qty * px)} is below the minimum "
                f"{to_str(self.min_notional)}"
            )
        return True, ""

    def quantities_equal(self, left: Numeric, right: Numeric) -> bool:
        """Equal within one lot: the venue cannot express a finer difference."""
        a = to_decimal(left, "left")
        b = to_decimal(right, "right")
        if self.lot_size <= 0:
            return a == b
        return abs(a - b) < self.lot_size

    def prices_equal(self, left: Numeric, right: Numeric) -> bool:
        """Equal within one tick."""
        a = to_decimal(left, "left")
        b = to_decimal(right, "right")
        if self.tick_size <= 0:
            return a == b
        return abs(a - b) < self.tick_size


def notional(quantity: Numeric, price: Numeric) -> Decimal:
    """Exact quantity x price."""
    with localcontext() as ctx:
        ctx.prec = CALCULATION_PRECISION
        return to_decimal(quantity, "quantity") * to_decimal(price, "price")


def add(*values: Numeric) -> Decimal:
    """Exact sum. A hundred additions of 0.01 make exactly 1.00."""
    with localcontext() as ctx:
        ctx.prec = CALCULATION_PRECISION
        total = ZERO
        for index, value in enumerate(values):
            total += to_decimal(value, f"values[{index}]")
        return total


def weighted_average(pairs) -> Optional[Decimal]:
    """Exact quantity-weighted average price, or None with no quantity.

    This is cost basis and average fill price. Computed in float it drifts
    with every partial fill, and cost basis drift becomes P&L error.
    """
    with localcontext() as ctx:
        ctx.prec = CALCULATION_PRECISION
        total_qty = ZERO
        total_value = ZERO
        for quantity, price in pairs:
            qty = to_decimal(quantity, "quantity")
            total_qty += qty
            total_value += qty * to_decimal(price, "price")
        if total_qty == 0:
            return None
        return total_value / total_qty


def as_float(value: Decimal) -> float:
    """Escape hatch to float, for analytics and display only.

    Never round-trip a capital-affecting value through this. It is here so
    that Decimal values can reach numpy and the dashboard without every call
    site writing its own conversion.
    """
    return float(value)


def coerce_legacy(value: Any, places: int = 8, field: str = "value") -> Decimal:
    """Best-effort conversion at the boundary with existing float code.

    Accepts what to_decimal accepts, and additionally tolerates floats by
    routing them through from_float. Use only where the caller genuinely
    cannot supply an exact source yet - it is the migration seam, not the
    destination.
    """
    if isinstance(value, float):
        return from_float(value, places=places, field=field)
    return to_decimal(value, field=field)
