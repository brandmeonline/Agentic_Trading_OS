"""Backtest fill realism — ATOS-P3-BT-001.

Invariant:

    A decision made from bar *t* fills no earlier than bar *t+1*, at a price
    that is worse than the mid, for no more size than the bar could have
    absorbed.

Three separate optimisms compound in a naive backtest, and each one alone is
enough to turn a losing strategy into a winning chart:

* **The impossible fill.** A signal computed from the close of bar *t*, filled
  at the close of bar *t*. The close is not knowable until the bar is over,
  and by then it is not tradeable. This is the single most common way a
  backtest lies, and it is what ``core/backtest_engine.py`` did.

* **The free round trip.** No spread, so buying and immediately selling costs
  nothing. Real round trips start underwater, and a strategy whose edge is
  smaller than the spread is a loss-making strategy that backtests flat.

* **Infinite liquidity.** Position size limited by capital rather than by what
  the bar actually traded. A backtest that fills 40% of the day's volume at
  the touch has modelled a market that does not exist.

The model here is intentionally simple - a participation cap and a linear
impact term - because a simple model an author understands beats a
sophisticated one they cannot argue with. What matters is that all three
costs are present and that none of them can be zero by accident: the defaults
charge, and setting them to zero has to be typed on purpose.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class FillTiming(Enum):
    """When a decision made during bar t is allowed to fill."""

    #: The open of the following bar. The honest default for a signal derived
    #: from a completed bar.
    NEXT_BAR_OPEN = "next_bar_open"
    #: The close of the following bar. Appropriate for an order worked over
    #: the session rather than sent at the open.
    NEXT_BAR_CLOSE = "next_bar_close"
    #: The close of the deciding bar. Present so it can be named and refused,
    #: not so it can be used.
    SAME_BAR_CLOSE = "same_bar_close"


#: Timings that let a decision fill at a price it helped produce.
IMPOSSIBLE_TIMINGS = frozenset({FillTiming.SAME_BAR_CLOSE})


class ImpossibleFill(ValueError):
    """A fill that could not have happened in a real market."""


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar, as the fill model needs it."""

    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class FillCosts:
    """The cost model. Defaults charge; zeroing one is a deliberate act."""

    #: Half-spread paid on every fill, as a fraction of price.
    half_spread_pct: float = 0.0005
    #: Commission per side, as a fraction of notional.
    commission_pct: float = 0.0005
    #: Slippage beyond the spread, as a fraction of price.
    slippage_pct: float = 0.0005
    #: The most of a bar's volume this system is willing to assume it could
    #: have taken. 10% is already generous for a single participant.
    max_participation: float = 0.10
    #: Linear impact charged on the participation actually used, so taking the
    #: whole cap costs more per share than taking a tenth of it.
    impact_pct_at_full_participation: float = 0.002

    def free(self) -> bool:
        """Whether this model charges nothing. Worth being able to assert on."""
        return (
            self.half_spread_pct == 0.0
            and self.commission_pct == 0.0
            and self.slippage_pct == 0.0
            and self.impact_pct_at_full_participation == 0.0
        )


@dataclass
class Fill:
    """What the model says actually happened."""

    price: float
    quantity: float
    commission: float
    #: Price paid above (buy) or below (sell) the reference, per unit.
    slippage_per_unit: float
    requested_quantity: float
    capped_by_liquidity: bool
    timing: FillTiming

    @property
    def notional(self) -> float:
        return self.price * self.quantity

    @property
    def filled(self) -> bool:
        return self.quantity > 0

    @property
    def unfilled_quantity(self) -> float:
        return max(0.0, self.requested_quantity - self.quantity)


class FillModel:
    """Turns an intent into a fill, charging for the privilege."""

    def __init__(
        self,
        costs: Optional[FillCosts] = None,
        timing: FillTiming = FillTiming.NEXT_BAR_OPEN,
    ) -> None:
        if timing in IMPOSSIBLE_TIMINGS:
            raise ImpossibleFill(
                f"{timing.value} lets a signal derived from a bar fill at a "
                "price from that same bar. A backtest configured this way "
                "reports returns nobody could have earned."
            )
        self.costs = costs or FillCosts()
        self.timing = timing

    # -- pricing ----------------------------------------------------------

    def reference_price(self, fill_bar: Bar) -> float:
        if self.timing is FillTiming.NEXT_BAR_OPEN:
            return fill_bar.open
        return fill_bar.close

    def available_quantity(self, fill_bar: Bar, requested: float) -> float:
        """How much of the request the bar could plausibly have absorbed.

        A bar with no recorded volume is treated as having no liquidity rather
        than infinite liquidity. Missing data is not permission.
        """
        if self.costs.max_participation <= 0:
            return requested
        capacity = fill_bar.volume * self.costs.max_participation
        return max(0.0, min(requested, capacity))

    def fill(
        self,
        side: str,
        requested_quantity: float,
        fill_bar: Bar,
        decision_bar: Optional[Bar] = None,
    ) -> Fill:
        """Price a buy or sell of ``requested_quantity`` on ``fill_bar``.

        ``decision_bar`` is accepted so the caller's intent is visible in the
        call and can be checked: passing the same object for both is the
        impossible fill, and it is refused rather than priced.
        """
        if decision_bar is not None and decision_bar is fill_bar:
            raise ImpossibleFill(
                "the decision bar and the fill bar are the same bar; a signal "
                "cannot be filled at a price it was derived from"
            )

        side = side.lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"unknown side {side!r}")

        quantity = self.available_quantity(fill_bar, requested_quantity)
        reference = self.reference_price(fill_bar)

        if quantity <= 0:
            return Fill(
                price=reference, quantity=0.0, commission=0.0,
                slippage_per_unit=0.0,
                requested_quantity=requested_quantity,
                capped_by_liquidity=requested_quantity > 0,
                timing=self.timing,
            )

        participation = 0.0
        if fill_bar.volume > 0:
            participation = min(1.0, quantity / fill_bar.volume
                                / max(self.costs.max_participation, 1e-12))

        adverse = (
            self.costs.half_spread_pct
            + self.costs.slippage_pct
            + self.costs.impact_pct_at_full_participation * participation
        )
        # Always against the trader. A cost model that can help you is not a
        # cost model.
        direction = 1.0 if side == "buy" else -1.0
        price = reference * (1.0 + direction * adverse)
        commission = abs(price * quantity) * self.costs.commission_pct

        return Fill(
            price=price,
            quantity=quantity,
            commission=commission,
            slippage_per_unit=abs(price - reference),
            requested_quantity=requested_quantity,
            capped_by_liquidity=quantity + 1e-12 < requested_quantity,
            timing=self.timing,
        )

    # -- round trip -------------------------------------------------------

    def round_trip_cost_pct(self) -> float:
        """What a buy followed immediately by a sell costs, as a fraction.

        The number a strategy's edge has to clear before it is an edge.
        """
        one_way = self.costs.half_spread_pct + self.costs.slippage_pct
        return 2.0 * (one_way + self.costs.commission_pct)
