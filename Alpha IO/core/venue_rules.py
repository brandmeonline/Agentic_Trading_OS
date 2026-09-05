"""Asset-class execution semantics — ATOS-P3-EXEC-001.

Invariant:

    A backtest, a paper run and a live order obey the same venue rules, and
    an asset class whose semantics are not implemented is refused rather than
    approximated.

A fill model that charges a spread is not the same thing as an execution model
that knows what a venue will accept. The gap between them is where a strategy
that backtests beautifully meets its first rejection:

* an equity order at 15:59:59 that the venue never saw because the market was
  already closed;
* 3.7 shares of a symbol that does not do fractions;
* a crypto order for $4 against a $10 minimum notional;
* a short in a name with no borrow;
* a price with four decimals on a venue that quantises to two.

None of these lose money in a backtest. All of them are a silent divergence
between the tested system and the running one, and the divergence is always in
the flattering direction, because the backtest fills orders the venue would
have rejected.

The other half of this module is the refusals. The README claimed "execution
via futures, options, spreads" and the code behind that claim returns a
hardcoded option spread with no multiplier, no expiry, no margin and no
assignment handling. Section 26 is explicit: do not claim support until the
semantics exist. So the semantics are named, the claim is withdrawn, and the
asset classes are refused in live mode by a list that says exactly what is
missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Tuple

logger = logging.getLogger(__name__)


class AssetClass(Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"


#: What is missing before each unsupported class may be traded live. Written
#: out rather than left as a boolean, because "unsupported" invites someone to
#: flip a flag and "no margin model" does not.
MISSING_SEMANTICS: Dict[AssetClass, Tuple[str, ...]] = {
    AssetClass.FUTURE: (
        "contract multipliers",
        "expiries and roll handling",
        "initial and maintenance margin",
        "liquidation risk",
        "reduce-only and position-side semantics",
        "open interest and liquidity limits",
    ),
    AssetClass.OPTION: (
        "contract multipliers",
        "expiries",
        "assignment and exercise",
        "Greeks and delta exposure",
        "margin for short options",
        "open interest and liquidity limits",
    ),
    AssetClass.FX: (
        "settlement and rollover",
        "venue-specific lot conventions",
    ),
}

#: The two this system's execution path actually models end to end.
SUPPORTED_FOR_LIVE: FrozenSet[AssetClass] = frozenset({
    AssetClass.EQUITY, AssetClass.CRYPTO,
})


class UnsupportedAssetClass(PermissionError):
    """An asset class whose execution semantics do not exist here."""


def live_support_problems(asset_class: AssetClass) -> List[str]:
    """Why this asset class may not be traded live, if it may not."""
    if asset_class in SUPPORTED_FOR_LIVE:
        return []
    missing = MISSING_SEMANTICS.get(asset_class, ("its execution semantics",))
    return [
        f"{asset_class.value} is not supported for live execution; missing: "
        + ", ".join(missing)
    ]


def require_live_support(asset_class: AssetClass) -> None:
    problems = live_support_problems(asset_class)
    if problems:
        raise UnsupportedAssetClass("; ".join(problems))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionState(Enum):
    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"
    HALTED = "halted"

    @property
    def tradeable(self) -> bool:
        """Only the regular session by default.

        Extended hours are a deliberate opt-in: the spread is wider, the depth
        is thinner, and a strategy calibrated on regular-session data has not
        been calibrated for them.
        """
        return self is SessionState.REGULAR


#: US equity regular hours, in Eastern. Stored as UTC offsets from a fixed
#: reference rather than pulling in a timezone database: this is a session
#: model, not a calendar service, and the honest scope is "regular hours on a
#: normal weekday".
REGULAR_OPEN_ET = time(9, 30)
REGULAR_CLOSE_ET = time(16, 0)
PRE_MARKET_OPEN_ET = time(4, 0)
AFTER_HOURS_CLOSE_ET = time(20, 0)

#: Eastern is UTC-5, or UTC-4 during daylight saving. The offset is a
#: parameter rather than a constant because getting it wrong shifts every
#: session boundary by an hour, and an hour at the open is the whole day's
#: liquidity.
DEFAULT_ET_OFFSET_HOURS = -5


@dataclass
class EquityCalendar:
    """When an equity venue will accept an order.

    Deliberately small. It knows about weekends, regular hours, the extended
    sessions, and a set of dates somebody has told it are holidays. It does
    not know the exchange calendar - and says so, rather than pretending, so
    a caller can supply one.
    """

    holidays: FrozenSet[date] = frozenset()
    halted_symbols: FrozenSet[str] = frozenset()
    et_offset_hours: int = DEFAULT_ET_OFFSET_HOURS
    allow_extended_hours: bool = False

    def _eastern(self, moment: datetime) -> datetime:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc) + timedelta(
            hours=self.et_offset_hours
        )

    def state(self, moment: datetime, symbol: str = "") -> SessionState:
        if symbol and symbol in self.halted_symbols:
            return SessionState.HALTED

        eastern = self._eastern(moment)
        if eastern.weekday() >= 5:
            return SessionState.CLOSED
        if eastern.date() in self.holidays:
            return SessionState.CLOSED

        clock = eastern.time()
        if REGULAR_OPEN_ET <= clock < REGULAR_CLOSE_ET:
            return SessionState.REGULAR
        if PRE_MARKET_OPEN_ET <= clock < REGULAR_OPEN_ET:
            return SessionState.PRE_MARKET
        if REGULAR_CLOSE_ET <= clock < AFTER_HOURS_CLOSE_ET:
            return SessionState.AFTER_HOURS
        return SessionState.CLOSED

    def may_trade(self, moment: datetime, symbol: str = "") -> Tuple[bool, str]:
        state = self.state(moment, symbol)
        if state is SessionState.HALTED:
            return False, f"{symbol} is halted"
        if state is SessionState.REGULAR:
            return True, ""
        if state in (SessionState.PRE_MARKET, SessionState.AFTER_HOURS):
            if self.allow_extended_hours:
                return True, ""
            return False, (
                f"{state.value} trading is not enabled; the spread is wider "
                "and the depth thinner than the data this strategy was "
                "calibrated on"
            )
        return False, "the market is closed"


@dataclass
class CryptoCalendar:
    """Crypto trades continuously, which is not the same as always.

    A venue outage is the crypto equivalent of a halt, and it is more common
    than an equity halt rather than less. 24/7 means there is no session to
    hide behind when one happens.
    """

    venue_down: bool = False
    halted_symbols: FrozenSet[str] = frozenset()

    def state(self, moment: datetime, symbol: str = "") -> SessionState:
        if self.venue_down or (symbol and symbol in self.halted_symbols):
            return SessionState.HALTED
        return SessionState.REGULAR

    def may_trade(self, moment: datetime, symbol: str = "") -> Tuple[bool, str]:
        if self.venue_down:
            return False, "the venue is down"
        if symbol and symbol in self.halted_symbols:
            return False, f"{symbol} is halted at the venue"
        return True, ""


# ---------------------------------------------------------------------------
# Instrument rules
# ---------------------------------------------------------------------------


class OrderRefused(ValueError):
    """An order the venue would not have accepted."""


@dataclass(frozen=True)
class InstrumentRules:
    """What one venue will accept for one instrument.

    ``fractional`` is the field people forget. A backtest that buys 3.7 shares
    of a whole-share-only symbol has been running a strategy the venue would
    not have executed, and the error compounds: every position size is
    slightly wrong, in the direction of whatever the sizing formula wanted.
    """

    symbol: str
    asset_class: AssetClass
    tick_size: float = 0.01
    lot_size: float = 1.0
    fractional: bool = False
    min_quantity: float = 0.0
    min_notional: float = 0.0
    #: Decimal places the venue accepts for quantity. Crypto venues differ per
    #: pair, and a rejected order is indistinguishable from a missed signal.
    quantity_precision: int = 8
    shortable: bool = False
    borrow_available: bool = True

    def quantize_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return float(price)
        return round(round(float(price) / self.tick_size) * self.tick_size, 10)

    def quantize_quantity(self, quantity: float) -> float:
        value = float(quantity)
        if not self.fractional:
            return float(int(value))
        if self.lot_size > 0:
            value = int(value / self.lot_size) * self.lot_size
        return round(value, self.quantity_precision)

    def problems(self, quantity: float, price: float,
                 side: str = "buy") -> List[str]:
        """Every reason the venue would reject this order."""
        problems: List[str] = []
        quantity = float(quantity)
        price = float(price)

        if quantity <= 0:
            problems.append("quantity is not positive")
            return problems

        if not self.fractional and abs(quantity - round(quantity)) > 1e-9:
            problems.append(
                f"{self.symbol} does not accept fractional quantities and "
                f"{quantity} is not whole"
            )
        if self.min_quantity and quantity < self.min_quantity:
            problems.append(
                f"quantity {quantity} is below the {self.min_quantity} minimum"
            )
        if self.min_notional and quantity * price < self.min_notional:
            problems.append(
                f"notional {quantity * price:.2f} is below the "
                f"{self.min_notional:.2f} minimum for {self.symbol}"
            )
        if self.tick_size > 0:
            ticks = price / self.tick_size
            if abs(ticks - round(ticks)) > 1e-6:
                problems.append(
                    f"price {price} is not a multiple of the "
                    f"{self.tick_size} tick"
                )
        if side.lower() == "sell_short":
            if not self.shortable:
                problems.append(f"{self.symbol} is not shortable")
            elif not self.borrow_available:
                problems.append(f"no borrow available for {self.symbol}")

        return problems

    def require(self, quantity: float, price: float, side: str = "buy") -> None:
        problems = self.problems(quantity, price, side)
        if problems:
            raise OrderRefused("; ".join(problems))


# ---------------------------------------------------------------------------
# The check a backtest and a live path both run
# ---------------------------------------------------------------------------


@dataclass
class ExecutionContext:
    """Everything needed to say whether an order could actually be placed."""

    rules: InstrumentRules
    equity_calendar: EquityCalendar = field(default_factory=EquityCalendar)
    crypto_calendar: CryptoCalendar = field(default_factory=CryptoCalendar)
    live: bool = False

    def session_state(self, moment: datetime) -> SessionState:
        if self.rules.asset_class is AssetClass.CRYPTO:
            return self.crypto_calendar.state(moment, self.rules.symbol)
        return self.equity_calendar.state(moment, self.rules.symbol)

    def problems(
        self, quantity: float, price: float, moment: datetime,
        side: str = "buy",
    ) -> List[str]:
        """Every reason this order would not have happened.

        Run by the backtest and by the live path from the same code, because
        the whole point is that they agree. A backtest that fills an order the
        venue would reject has tested a different system.
        """
        problems: List[str] = []

        if self.live:
            problems.extend(live_support_problems(self.rules.asset_class))

        if self.rules.asset_class is AssetClass.CRYPTO:
            allowed, reason = self.crypto_calendar.may_trade(
                moment, self.rules.symbol
            )
        else:
            allowed, reason = self.equity_calendar.may_trade(
                moment, self.rules.symbol
            )
        if not allowed:
            problems.append(reason)

        problems.extend(self.rules.problems(quantity, price, side))
        return problems

    def may_place(self, quantity: float, price: float, moment: datetime,
                  side: str = "buy") -> Tuple[bool, str]:
        problems = self.problems(quantity, price, moment, side)
        return (not problems), "; ".join(problems)


# ---------------------------------------------------------------------------
# Common instruments
# ---------------------------------------------------------------------------


def us_equity(symbol: str, fractional: bool = False,
              shortable: bool = False) -> InstrumentRules:
    """A US equity on a penny tick, whole shares unless told otherwise."""
    return InstrumentRules(
        symbol=symbol, asset_class=AssetClass.EQUITY,
        tick_size=0.01,
        # A fractional symbol quantises to the venue's smallest increment,
        # not to one share. Leaving lot_size at 1.0 here rounded 3.7 down to
        # 3.0 even with fractional=True, which is the whole-share behaviour
        # the flag exists to switch off.
        lot_size=1e-6 if fractional else 1.0,
        fractional=fractional,
        min_quantity=0.0 if fractional else 1.0,
        quantity_precision=6 if fractional else 0,
        shortable=shortable,
    )


def crypto_pair(symbol: str, tick_size: float = 0.01,
                lot_size: float = 1e-8, min_notional: float = 10.0,
                quantity_precision: int = 8) -> InstrumentRules:
    """A crypto pair: fractional, precise, and with a minimum notional."""
    return InstrumentRules(
        symbol=symbol, asset_class=AssetClass.CRYPTO,
        tick_size=tick_size, lot_size=lot_size, fractional=True,
        min_notional=min_notional, quantity_precision=quantity_precision,
        shortable=True,
    )


def describe_support() -> Dict[str, Any]:
    """What this system actually supports, for the README and the dashboard.

    Generated rather than written down, so the claim and the code cannot
    drift apart the way they had.
    """
    return {
        "supported_for_live": sorted(a.value for a in SUPPORTED_FOR_LIVE),
        "unsupported": {
            asset.value: list(missing)
            for asset, missing in sorted(
                MISSING_SEMANTICS.items(), key=lambda kv: kv[0].value
            )
        },
    }
