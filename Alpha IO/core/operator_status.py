"""Operator status bar and dashboard trust hierarchy — ATOS-P2-UI-001.

Invariant (INV-ATOS-012):

    A dashboard never renders demo, stale or absent data as healthy live
    state, and every page says what mode the system is in.

A trading dashboard is an instrument, and an instrument that reads plausibly
when it has lost its input is worse than one that reads nothing. The DeFi
panel in this repository fabricates quotes with ``random.uniform``; the
marketplace and leaderboard return ``[]`` on any error, which looks exactly
like "no strategies" rather than "the query failed". Rendered next to a real
account balance, in the same typeface, both read as fact.

So every surface declares what its numbers are, the status bar shows the worst
declaration on the page, and the defaults are the pessimistic ones. A field
nobody has set says UNKNOWN, not OK: an operator status assembled from an
empty system must never claim the system is fit to trade.

The severity ordering is deliberate. DEMO ranks *worse* than UNAVAILABLE,
which surprises people. An unavailable panel is visibly broken and nobody
trades on it; a demo panel shows confident numbers that are not about
anything. Fabricated plausibility is the more dangerous failure, so it wins
the badge.

The second half of the issue is separation: dangerous controls must not sit
among unauthenticated read-only analytics. A surface declares whether it
carries a control that changes what the system may do, and analytics surfaces
are checked to carry none.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class DataTrust(Enum):
    """What a number on the screen actually is."""

    #: A real observation, recent enough to act on.
    LIVE = "live"
    #: A real observation, too old to act on.
    STALE = "stale"
    #: An observation that failed validation (crossed book, negative size).
    INVALID = "invalid"
    #: Fabricated. Useful for a walkthrough, never for a decision.
    DEMO = "demo"
    #: Nothing was obtained at all.
    UNAVAILABLE = "unavailable"


#: Higher is worse. DEMO outranks UNAVAILABLE on purpose: see the module
#: docstring. INVALID outranks STALE because a wrong number is worse than an
#: old one, and STALE outranks LIVE because it is the mildest of the failures.
_TRUST_SEVERITY = {
    DataTrust.LIVE: 0,
    DataTrust.STALE: 1,
    DataTrust.UNAVAILABLE: 2,
    DataTrust.INVALID: 3,
    DataTrust.DEMO: 4,
}

#: Trust levels that must not be presented as a basis for a trading decision.
UNTRADEABLE_TRUST = frozenset({
    DataTrust.STALE,
    DataTrust.INVALID,
    DataTrust.DEMO,
    DataTrust.UNAVAILABLE,
})


class OperatingMode(Enum):
    """The MODE badge. UNKNOWN exists because a default must not read PAPER."""

    UNKNOWN = "unknown"
    BACKTEST = "backtest"
    RESEARCH = "research"
    PAPER = "paper"
    LIVE_ARMED = "live-armed"
    LIVE = "live"
    FROZEN = "frozen"
    RECOVERY_REQUIRED = "recovery required"


#: Modes in which real capital is committed or one step away from it.
LIVE_MODES = frozenset({OperatingMode.LIVE_ARMED, OperatingMode.LIVE})

#: Modes in which the operator has to do something before trading resumes.
BLOCKED_MODES = frozenset({
    OperatingMode.FROZEN,
    OperatingMode.RECOVERY_REQUIRED,
})


class BrokerKind(Enum):
    UNKNOWN = "unknown"
    DISCONNECTED = "disconnected"
    PAPER = "paper"
    LIVE = "live"


class ExecutionState(Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class ReconciliationState(Enum):
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"
    MATCHED = "matched"


#: How old a broker heartbeat may be before the badge stops saying LIVE.
DEFAULT_BROKER_FRESHNESS = timedelta(seconds=30)

#: How old a quote may be before the badge stops saying LIVE.
DEFAULT_DATA_FRESHNESS = timedelta(seconds=60)


def worst_trust(levels: Iterable[DataTrust]) -> DataTrust:
    """The most alarming trust level in a collection.

    An empty collection is UNAVAILABLE, not LIVE: a page that declared nothing
    has not established anything.
    """
    levels = list(levels)
    if not levels:
        return DataTrust.UNAVAILABLE
    return max(levels, key=lambda level: _TRUST_SEVERITY[level])


def classify_freshness(
    age: Optional[timedelta],
    window: timedelta,
) -> DataTrust:
    """LIVE inside the window, STALE outside it, UNAVAILABLE if unknown.

    ``None`` is the case that matters. A missing age is not a young age; it
    means nothing has been observed, and the honest badge is UNAVAILABLE.
    """
    if age is None:
        return DataTrust.UNAVAILABLE
    if age < timedelta(0):
        # A future timestamp is a clock problem, not freshness. Do not let it
        # read as the freshest possible observation.
        return DataTrust.INVALID
    return DataTrust.LIVE if age <= window else DataTrust.STALE


def account_fingerprint(account_id: Optional[str]) -> str:
    """A stable short tag for an account that is not the account number.

    Operators need to see *which* account at a glance — the whole point of the
    badge is catching a live key behind a paper label — but the dashboard is
    shared, screenshotted and demoed, so the identifier itself does not belong
    on it.
    """
    if not account_id:
        return "unidentified"
    digest = hashlib.sha256(str(account_id).encode("utf-8")).hexdigest()
    return digest[:8]


# ---------------------------------------------------------------------------
# Frozen feeds
# ---------------------------------------------------------------------------

#: How long a quote may stay at exactly the same value before the feed is
#: treated as frozen rather than quiet. Long enough that a genuinely still
#: market does not trip it, short enough to notice a socket replaying its
#: last tick.
DEFAULT_FREEZE_WINDOW = timedelta(minutes=10)


@dataclass
class FeedHealth:
    """Tracks whether a feed is arriving *and* changing.

    Freshness measured as "when did we last receive something" answers the
    wrong question. The common feed failure is not silence - it is a
    disconnected socket replaying its last tick, a cached upstream, or a
    proxy answering from memory. Every one of those keeps the arrival
    timestamp current while the number stops meaning anything, and a bar that
    reads LIVE because the clock moved is exactly the false green light the
    trust hierarchy exists to prevent.

    So two clocks: when a value last *arrived*, and when it last *changed*.
    """

    symbol: str
    last_value: Optional[float] = None
    last_seen_at: Optional[datetime] = None
    last_change_at: Optional[datetime] = None
    repeats: int = 0

    def observe(self, value: float, at: datetime) -> None:
        if self.last_value is None or value != self.last_value:
            self.last_change_at = at
            self.repeats = 0
        else:
            self.repeats += 1
        self.last_value = value
        self.last_seen_at = at

    def trust(
        self,
        now: datetime,
        arrival_window: timedelta = DEFAULT_DATA_FRESHNESS,
        freeze_window: timedelta = DEFAULT_FREEZE_WINDOW,
    ) -> DataTrust:
        """LIVE only if it is both arriving and moving."""
        arrival = classify_freshness(
            None if self.last_seen_at is None else now - self.last_seen_at,
            arrival_window,
        )
        if arrival is not DataTrust.LIVE:
            return arrival
        if self.last_change_at is None:
            return DataTrust.UNAVAILABLE
        if now - self.last_change_at > freeze_window:
            return DataTrust.STALE
        return DataTrust.LIVE

    def frozen(self, now: datetime,
               freeze_window: timedelta = DEFAULT_FREEZE_WINDOW) -> bool:
        """Arriving, but not moving."""
        if self.last_seen_at is None or self.last_change_at is None:
            return False
        return (now - self.last_change_at) > freeze_window

    def to_dict(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        return {
            "symbol": self.symbol,
            "last_value": self.last_value,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_change_at": (
                self.last_change_at.isoformat() if self.last_change_at else None
            ),
            "repeats": self.repeats,
            "trust": self.trust(now).value,
            "frozen": self.frozen(now),
        }


class FeedMonitor:
    """FeedHealth for every symbol, and the worst of them."""

    def __init__(self, freeze_window: timedelta = DEFAULT_FREEZE_WINDOW,
                 arrival_window: timedelta = DEFAULT_DATA_FRESHNESS) -> None:
        self.freeze_window = freeze_window
        self.arrival_window = arrival_window
        self.feeds: Dict[str, FeedHealth] = {}

    def observe(self, symbol: str, value: float,
                at: Optional[datetime] = None) -> None:
        at = at or datetime.now(timezone.utc)
        self.feeds.setdefault(symbol, FeedHealth(symbol)).observe(value, at)

    def trust(self, now: Optional[datetime] = None) -> DataTrust:
        now = now or datetime.now(timezone.utc)
        return worst_trust([
            feed.trust(now, self.arrival_window, self.freeze_window)
            for feed in self.feeds.values()
        ])

    def frozen_symbols(self, now: Optional[datetime] = None) -> List[str]:
        now = now or datetime.now(timezone.utc)
        return sorted(
            symbol for symbol, feed in self.feeds.items()
            if feed.frozen(now, self.freeze_window)
        )

    def report(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        return {
            "trust": self.trust(now).value,
            "frozen": self.frozen_symbols(now),
            "feeds": [feed.to_dict(now) for feed in
                      sorted(self.feeds.values(), key=lambda f: f.symbol)],
        }


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Surface:
    """One panel, and what its contents are.

    ``dangerous_control`` marks a surface that can change what the system is
    permitted to do — arming live, flattening, clearing a freeze. Those must
    not be rendered inside read-only analytics.
    """

    name: str
    trust: DataTrust = DataTrust.UNAVAILABLE
    reason: str = "no trust declared"
    dangerous_control: bool = False
    observed_at: Optional[datetime] = None

    @property
    def tradeable(self) -> bool:
        return self.trust is DataTrust.LIVE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "trust": self.trust.value,
            "reason": self.reason,
            "dangerous_control": self.dangerous_control,
            "observed_at": (
                self.observed_at.isoformat() if self.observed_at else None
            ),
        }


def stamp(payload: Any, trust: DataTrust, reason: str = "",
          source: str = "") -> Dict[str, Any]:
    """Wrap an API payload with its trust level.

    Returning the stamp alongside the data rather than expecting the template
    to know which endpoint fabricates things keeps the knowledge next to the
    code that produced it, which is the only place it stays correct.
    """
    return {
        "data": payload,
        "trust": trust.value,
        "trust_reason": reason,
        "source": source,
        "tradeable": trust is DataTrust.LIVE,
    }


def read_trust(payload: Any) -> DataTrust:
    """Recover the trust level of a stamped payload.

    Anything unstamped is UNAVAILABLE. Not LIVE — an unstamped payload is one
    whose author did not say, and a default of LIVE would make forgetting the
    stamp the silent, comfortable path.
    """
    if isinstance(payload, dict) and "trust" in payload:
        try:
            return DataTrust(payload["trust"])
        except ValueError:
            return DataTrust.INVALID
    return DataTrust.UNAVAILABLE


def dangerous_controls(surfaces: Sequence[Surface]) -> List[str]:
    return [s.name for s in surfaces if s.dangerous_control]


def separation_problems(
    surfaces: Sequence[Surface],
    page_is_analytics: bool,
    authenticated: bool = True,
) -> List[str]:
    """Reasons this page mixes things that must stay apart."""
    problems: List[str] = []
    controls = dangerous_controls(surfaces)
    if controls and page_is_analytics:
        problems.append(
            "read-only analytics page carries dangerous control(s): "
            + ", ".join(sorted(controls))
        )
    if controls and not authenticated:
        problems.append(
            "dangerous control(s) rendered on an unauthenticated page: "
            + ", ".join(sorted(controls))
        )
    return problems


# ---------------------------------------------------------------------------
# The status bar itself
# ---------------------------------------------------------------------------


@dataclass
class OperatorStatus:
    """Everything the ULTRAPLAN requires on every page.

    Every default is the pessimistic one. Constructing this with no arguments
    must describe a system that is not fit to trade, because a status object
    assembled from a half-wired dashboard is exactly the case where an
    optimistic default becomes a false green light.
    """

    mode: OperatingMode = OperatingMode.UNKNOWN
    broker: BrokerKind = BrokerKind.UNKNOWN
    broker_account: str = "unidentified"
    execution: ExecutionState = ExecutionState.DISABLED
    reconciliation: ReconciliationState = ReconciliationState.UNKNOWN

    broker_connection_age: Optional[timedelta] = None
    market_data_age: Optional[timedelta] = None
    broker_freshness_window: timedelta = DEFAULT_BROKER_FRESHNESS
    data_freshness_window: timedelta = DEFAULT_DATA_FRESHNESS

    effective_capital_at_risk: float = 0.0
    capital_tier_limit: Optional[float] = None
    reserved_capital: float = 0.0

    open_order_count: int = 0
    unknown_order_count: int = 0

    active_risk_trips: tuple = ()
    last_persistence_ok_at: Optional[datetime] = None
    persistence_freshness_window: timedelta = timedelta(minutes=5)

    surfaces: List[Surface] = field(default_factory=list)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # -- derived badges ---------------------------------------------------

    @property
    def broker_trust(self) -> DataTrust:
        if self.broker is BrokerKind.DISCONNECTED:
            return DataTrust.UNAVAILABLE
        return classify_freshness(
            self.broker_connection_age, self.broker_freshness_window
        )

    @property
    def data_trust(self) -> DataTrust:
        """The DATA badge: the worst of the feed age and every panel."""
        feed = classify_freshness(
            self.market_data_age, self.data_freshness_window
        )
        return worst_trust([feed] + [s.trust for s in self.surfaces])

    @property
    def is_live(self) -> bool:
        return self.mode in LIVE_MODES

    @property
    def capital_headroom(self) -> Optional[float]:
        if self.capital_tier_limit is None:
            return None
        return self.capital_tier_limit - self.effective_capital_at_risk

    @property
    def capital_breached(self) -> bool:
        headroom = self.capital_headroom
        return headroom is not None and headroom < 0

    @property
    def persistence_healthy(self) -> bool:
        if self.last_persistence_ok_at is None:
            return False
        age = self.generated_at - self.last_persistence_ok_at
        return timedelta(0) <= age <= self.persistence_freshness_window

    # -- the judgement ----------------------------------------------------

    def problems(self) -> List[str]:
        """Everything wrong, in the words an operator needs to read.

        Ordered by how much it should worry them, most first.
        """
        problems: List[str] = []

        if self.mode is OperatingMode.UNKNOWN:
            problems.append(
                "operating mode is not established; the dashboard cannot say "
                "what this system is doing"
            )
        elif self.mode in BLOCKED_MODES:
            problems.append(f"system is {self.mode.value.upper()}")

        demo = [s.name for s in self.surfaces if s.trust is DataTrust.DEMO]
        if demo:
            problems.append(
                "demo data on screen: " + ", ".join(sorted(demo))
            )
            if self.is_live:
                problems.append(
                    "demo panels are rendered while real capital is committed"
                )

        if self.unknown_order_count:
            problems.append(
                f"{self.unknown_order_count} order(s) in an unknown state"
            )

        if self.reconciliation is ReconciliationState.MISMATCH:
            problems.append("broker reconciliation reports a mismatch")
        elif self.reconciliation is ReconciliationState.UNKNOWN and self.is_live:
            problems.append(
                "no reconciliation result while live; broker agreement is "
                "unestablished"
            )

        if self.capital_breached:
            problems.append(
                f"capital at risk {self.effective_capital_at_risk:,.2f} "
                f"exceeds the tier limit {self.capital_tier_limit:,.2f}"
            )
        elif self.capital_tier_limit is None and self.is_live:
            problems.append("no capital tier limit is configured while live")

        if self.active_risk_trips:
            problems.append(
                "active risk trip(s): " + ", ".join(self.active_risk_trips)
            )

        broker_trust = self.broker_trust
        if broker_trust is not DataTrust.LIVE:
            problems.append(f"broker connection is {broker_trust.value}")

        data_trust = self.data_trust
        if data_trust is not DataTrust.LIVE and not demo:
            # The demo case is already reported above, and reporting it twice
            # buries the other findings.
            problems.append(f"market data is {data_trust.value}")

        if not self.persistence_healthy:
            problems.append(
                "no recent successful persistence health check"
                if self.last_persistence_ok_at
                else "persistence health has never been confirmed"
            )

        if self.execution is ExecutionState.ENABLED:
            if data_trust in UNTRADEABLE_TRUST:
                problems.append(
                    f"execution is enabled on {data_trust.value} data"
                )

        return problems

    @property
    def fit_to_trade(self) -> bool:
        """Whether the bar may show green. Never true by default."""
        return not self.problems()

    def banner(self) -> Dict[str, str]:
        """The one line that goes across the top of every page."""
        problems = self.problems()
        if not problems:
            return {
                "level": "ok",
                "text": f"{self.mode.value.upper()} · all checks pass",
            }
        level = "critical" if (
            self.is_live
            or self.mode in BLOCKED_MODES
            or self.unknown_order_count
            or self.capital_breached
        ) else "warning"
        return {
            "level": level,
            "text": f"{self.mode.value.upper()} · {problems[0]}",
            "problem_count": str(len(problems)),
        }

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """The wire form the status bar renders, one key per required field."""

        def seconds(value: Optional[timedelta]) -> Optional[float]:
            return value.total_seconds() if value is not None else None

        return {
            "mode": self.mode.value.upper(),
            "broker": self.broker.value.upper(),
            "broker_account": self.broker_account,
            "data": self.data_trust.value.upper(),
            "execution": self.execution.value.upper(),
            "reconciliation": self.reconciliation.value.upper(),
            "broker_connection_age_seconds": seconds(self.broker_connection_age),
            "broker_trust": self.broker_trust.value.upper(),
            "market_data_age_seconds": seconds(self.market_data_age),
            "effective_capital_at_risk": self.effective_capital_at_risk,
            "capital_tier_limit": self.capital_tier_limit,
            "capital_headroom": self.capital_headroom,
            "capital_breached": self.capital_breached,
            "reserved_capital": self.reserved_capital,
            "open_order_count": self.open_order_count,
            "unknown_order_count": self.unknown_order_count,
            "active_risk_trips": list(self.active_risk_trips),
            "last_persistence_ok_at": (
                self.last_persistence_ok_at.isoformat()
                if self.last_persistence_ok_at else None
            ),
            "persistence_healthy": self.persistence_healthy,
            "surfaces": [s.to_dict() for s in self.surfaces],
            "problems": self.problems(),
            "fit_to_trade": self.fit_to_trade,
            "banner": self.banner(),
            "generated_at": self.generated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Building one from the running system
# ---------------------------------------------------------------------------


#: How a runtime state maps onto the badge the ULTRAPLAN asks for. The runtime
#: machine has more states than the bar does; LIVE_RECONCILING is shown as
#: LIVE-ARMED because from an operator's point of view both mean "real money,
#: not yet cleared to acquire".
_RUNTIME_TO_MODE = {
    "backtest": OperatingMode.BACKTEST,
    "research": OperatingMode.RESEARCH,
    "paper": OperatingMode.PAPER,
    "paper_live_data": OperatingMode.PAPER,
    "live_armed": OperatingMode.LIVE_ARMED,
    "live_reconciling": OperatingMode.LIVE_ARMED,
    "live_active": OperatingMode.LIVE,
    "frozen": OperatingMode.FROZEN,
    "recovery_required": OperatingMode.RECOVERY_REQUIRED,
    "halted": OperatingMode.FROZEN,
}


def mode_from_runtime(state_value: Optional[str]) -> OperatingMode:
    """Translate a runtime state value into a badge.

    An unrecognised state is UNKNOWN rather than a guess. A new state added to
    the machine should make the bar say "I do not know what this is", which is
    true, instead of silently inheriting the nearest label.
    """
    if not state_value:
        return OperatingMode.UNKNOWN
    return _RUNTIME_TO_MODE.get(str(state_value).lower(), OperatingMode.UNKNOWN)


def build_operator_status(
    runtime_state: Optional[str] = None,
    broker: BrokerKind = BrokerKind.UNKNOWN,
    broker_account_id: Optional[str] = None,
    execution_enabled: bool = False,
    reconciliation: ReconciliationState = ReconciliationState.UNKNOWN,
    broker_seen_at: Optional[datetime] = None,
    market_data_at: Optional[datetime] = None,
    effective_capital_at_risk: float = 0.0,
    capital_tier_limit: Optional[float] = None,
    reserved_capital: float = 0.0,
    open_order_count: int = 0,
    unknown_order_count: int = 0,
    active_risk_trips: Sequence[str] = (),
    last_persistence_ok_at: Optional[datetime] = None,
    surfaces: Optional[Sequence[Surface]] = None,
    now: Optional[datetime] = None,
) -> OperatorStatus:
    """Assemble the bar from whatever the caller could actually establish.

    Every argument has a pessimistic default so a caller that cannot obtain a
    value gets an honest badge rather than an omission.
    """
    now = now or datetime.now(timezone.utc)

    def age(at: Optional[datetime]) -> Optional[timedelta]:
        if at is None:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return now - at

    return OperatorStatus(
        mode=mode_from_runtime(runtime_state),
        broker=broker,
        broker_account=account_fingerprint(broker_account_id),
        execution=(
            ExecutionState.ENABLED if execution_enabled
            else ExecutionState.DISABLED
        ),
        reconciliation=reconciliation,
        broker_connection_age=age(broker_seen_at),
        market_data_age=age(market_data_at),
        effective_capital_at_risk=effective_capital_at_risk,
        capital_tier_limit=capital_tier_limit,
        reserved_capital=reserved_capital,
        open_order_count=open_order_count,
        unknown_order_count=unknown_order_count,
        active_risk_trips=tuple(active_risk_trips),
        last_persistence_ok_at=last_persistence_ok_at,
        surfaces=list(surfaces or []),
        generated_at=now,
    )


def reconciliation_from_report(report: Any) -> ReconciliationState:
    """Read a reconciliation report without trusting it to exist.

    ``None`` is UNKNOWN, not MATCHED. The absence of a mismatch is not the
    presence of agreement.
    """
    if report is None:
        return ReconciliationState.UNKNOWN
    may_acquire = getattr(report, "may_acquire", None)
    if may_acquire is None:
        return ReconciliationState.UNKNOWN
    return (
        ReconciliationState.MATCHED if may_acquire
        else ReconciliationState.MISMATCH
    )
