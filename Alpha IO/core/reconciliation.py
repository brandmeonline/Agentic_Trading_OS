"""Broker reconciliation — ATOS-P0-REC-002.

Invariant:

    Broker truth is authoritative over local bookkeeping, and any disagreement
    stops new risk until a human or an explicit correction resolves it.

Reconciliation is the act of asking the broker what it actually holds and
comparing that against what we believe. The comparison is the easy part. The
two rules that make it safe are less obvious:

* **An incomplete snapshot is a mismatch.** If we could not fetch positions,
  we have not proved they match — we have proved nothing. Treating a failed
  fetch as "no differences found" is how a system convinces itself it is flat.

* **Adopting broker truth is an event, not an assignment.** When the broker
  says we hold 40 shares and we thought we held 0, overwriting the local
  number silently destroys the only evidence that something went wrong. Every
  correction is recorded and returned.

The engine itself is pure: it takes two snapshots and produces a report. It
does not mutate anything, which makes it safe to run continuously and easy to
test against adversarial broker states.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Absolute tolerance for quantity comparison. ATOS-P1-NUM-001 replaces this
#: with a venue-grid-aware Decimal comparison; until then it is deliberately
#: tight so a real break is not absorbed as rounding.
QUANTITY_TOLERANCE = 1e-6

#: Cash can legitimately differ by rounding and unsettled fees. This is a
#: currency tolerance, not a licence for drift.
CASH_TOLERANCE = 0.01

#: A snapshot older than this cannot support a live activation decision.
DEFAULT_MAX_SNAPSHOT_AGE = timedelta(minutes=5)


class MismatchClass(Enum):
    """The ways local belief and broker truth can disagree."""

    UNKNOWN_BROKER_ORDER = "unknown_broker_order"
    MISSING_BROKER_ORDER = "missing_broker_order"
    POSITION_MISMATCH = "position_mismatch"
    CASH_MISMATCH = "cash_mismatch"
    FILL_MISMATCH = "fill_mismatch"
    QUANTITY_MISMATCH = "quantity_mismatch"
    DUPLICATE_CLIENT_ID = "duplicate_client_id"
    CAPITAL_LIMIT_BREACH = "capital_limit_breach"
    ACCOUNT_ID_MISMATCH = "account_id_mismatch"
    INCOMPLETE_BROKER_SNAPSHOT = "incomplete_broker_snapshot"


#: Mismatches that mean we may have lost track of real exposure. These need an
#: operator, not a retry.
CRITICAL_MISMATCHES = frozenset({
    MismatchClass.UNKNOWN_BROKER_ORDER,
    MismatchClass.POSITION_MISMATCH,
    MismatchClass.QUANTITY_MISMATCH,
    MismatchClass.DUPLICATE_CLIENT_ID,
    MismatchClass.CAPITAL_LIMIT_BREACH,
    MismatchClass.ACCOUNT_ID_MISMATCH,
})


@dataclass
class Mismatch:
    """One specific disagreement, with both sides recorded."""

    kind: MismatchClass
    subject: str
    detail: str
    local_value: Any = None
    broker_value: Any = None

    @property
    def is_critical(self) -> bool:
        return self.kind in CRITICAL_MISMATCHES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "subject": self.subject,
            "detail": self.detail,
            "local_value": self.local_value,
            "broker_value": self.broker_value,
            "critical": self.is_critical,
        }


@dataclass
class BrokerSnapshot:
    """What the broker says it holds, and how much of it we actually got.

    The completeness flags matter as much as the data. A snapshot where
    ``positions_complete`` is False cannot be used to prove positions match,
    no matter what the positions list contains.
    """

    account_fingerprint: str = ""
    taken_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    cash: Optional[float] = None
    equity: Optional[float] = None
    buying_power: Optional[float] = None

    #: instrument -> signed quantity
    positions: Dict[str, float] = field(default_factory=dict)
    #: client_order_id -> order payload
    open_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: recent fills, newest first
    recent_fills: List[Dict[str, Any]] = field(default_factory=list)
    fees: float = 0.0
    #: Client order IDs the broker returned more than once. open_orders is a
    #: mapping, so a duplicate would otherwise be silently collapsed - and a
    #: duplicated client ID is exactly the evidence that a retry created a
    #: second order.
    duplicate_client_ids: List[str] = field(default_factory=list)

    account_complete: bool = True
    positions_complete: bool = True
    open_orders_complete: bool = True
    fills_complete: bool = True

    @property
    def is_complete(self) -> bool:
        return (
            self.account_complete
            and self.positions_complete
            and self.open_orders_complete
            and self.fills_complete
        )

    def age(self, now: Optional[datetime] = None) -> timedelta:
        reference = now or datetime.now(timezone.utc)
        taken = self.taken_at
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        return reference - taken

    def incomplete_parts(self) -> List[str]:
        parts = []
        if not self.account_complete:
            parts.append("account")
        if not self.positions_complete:
            parts.append("positions")
        if not self.open_orders_complete:
            parts.append("open_orders")
        if not self.fills_complete:
            parts.append("fills")
        return parts


@dataclass
class LocalSnapshot:
    """What we believe we hold."""

    account_fingerprint: str = ""
    taken_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    cash: Optional[float] = None
    equity: Optional[float] = None

    #: instrument -> signed quantity
    positions: Dict[str, float] = field(default_factory=dict)
    #: client_order_id -> order payload, for orders we believe are live
    open_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: client_order_id -> order payload, for orders whose state we lost
    unknown_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    reserved_quantity: Dict[str, float] = field(default_factory=dict)
    reserved_notional: float = 0.0
    fills: List[Dict[str, Any]] = field(default_factory=list)
    fees: float = 0.0
    strategy_attribution: Dict[str, str] = field(default_factory=dict)
    risk_state: Dict[str, Any] = field(default_factory=dict)

    #: The hard ceiling on capital at risk, from the promotion ladder.
    capital_tier_limit: Optional[float] = None


@dataclass
class ReconciliationReport:
    """The outcome of one comparison."""

    matched: bool
    mismatches: List[Mismatch] = field(default_factory=list)
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snapshot_age_seconds: float = 0.0

    @property
    def may_acquire(self) -> bool:
        """Whether new risk is permitted.

        Any mismatch at all blocks acquisition. A non-critical mismatch such
        as a cash difference may be resolvable without an operator, but it is
        still an unexplained difference and unexplained differences do not get
        to authorise new positions.
        """
        return self.matched and not self.mismatches

    @property
    def critical_mismatches(self) -> List[Mismatch]:
        return [m for m in self.mismatches if m.is_critical]

    @property
    def requires_operator(self) -> bool:
        return bool(self.critical_mismatches)

    def of_kind(self, kind: MismatchClass) -> List[Mismatch]:
        return [m for m in self.mismatches if m.kind is kind]

    def summary(self) -> str:
        if self.may_acquire:
            return "MATCHED"
        kinds = ", ".join(sorted({m.kind.value for m in self.mismatches}))
        return f"MISMATCH ({len(self.mismatches)}): {kinds}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched": self.matched,
            "may_acquire": self.may_acquire,
            "requires_operator": self.requires_operator,
            "summary": self.summary(),
            "checked_at": self.checked_at.isoformat(),
            "snapshot_age_seconds": self.snapshot_age_seconds,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "corrections": list(self.corrections),
        }


class ReconciliationEngine:
    """Compares local belief against broker truth.

    Pure: it reads two snapshots and returns a report. Applying a correction
    is a separate, explicit call that records what it changed.
    """

    def __init__(
        self,
        quantity_tolerance: float = QUANTITY_TOLERANCE,
        cash_tolerance: float = CASH_TOLERANCE,
        max_snapshot_age: timedelta = DEFAULT_MAX_SNAPSHOT_AGE,
        expected_account_fingerprint: Optional[str] = None,
    ) -> None:
        self.quantity_tolerance = quantity_tolerance
        self.cash_tolerance = cash_tolerance
        self.max_snapshot_age = max_snapshot_age
        self.expected_account_fingerprint = expected_account_fingerprint

    def reconcile(
        self,
        local: LocalSnapshot,
        broker: BrokerSnapshot,
        now: Optional[datetime] = None,
    ) -> ReconciliationReport:
        now = now or datetime.now(timezone.utc)
        mismatches: List[Mismatch] = []

        mismatches += self._check_account_identity(local, broker)
        mismatches += self._check_completeness(broker, now)
        mismatches += self._check_orders(local, broker)
        mismatches += self._check_positions(local, broker)
        mismatches += self._check_cash(local, broker)
        mismatches += self._check_capital_tier(local, broker)

        report = ReconciliationReport(
            matched=not mismatches,
            mismatches=mismatches,
            checked_at=now,
            snapshot_age_seconds=broker.age(now).total_seconds(),
        )
        if not report.may_acquire:
            logger.warning("Reconciliation: %s", report.summary())
        return report

    # -- individual comparisons ------------------------------------------

    def _check_account_identity(
        self, local: LocalSnapshot, broker: BrokerSnapshot
    ) -> List[Mismatch]:
        """Reconciling against the wrong account is worse than not reconciling.

        A clean comparison against somebody else's account reads as MATCHED
        and authorises trading on a book we have never seen.
        """
        expected = self.expected_account_fingerprint or local.account_fingerprint
        if not expected or not broker.account_fingerprint:
            return [Mismatch(
                MismatchClass.ACCOUNT_ID_MISMATCH,
                "account",
                "account fingerprint is missing on one side; the snapshot "
                "cannot be attributed to a known account",
                local_value=expected or None,
                broker_value=broker.account_fingerprint or None,
            )]
        if expected != broker.account_fingerprint:
            return [Mismatch(
                MismatchClass.ACCOUNT_ID_MISMATCH,
                "account",
                "the broker snapshot belongs to a different account",
                local_value=expected,
                broker_value=broker.account_fingerprint,
            )]
        return []

    def _check_completeness(
        self, broker: BrokerSnapshot, now: datetime
    ) -> List[Mismatch]:
        """A partial snapshot proves nothing, and a stale one proves less."""
        mismatches = []
        missing = broker.incomplete_parts()
        if missing:
            mismatches.append(Mismatch(
                MismatchClass.INCOMPLETE_BROKER_SNAPSHOT,
                "snapshot",
                "could not fetch: " + ", ".join(missing)
                + "; absence of a difference is not evidence of agreement",
                broker_value=missing,
            ))
        age = broker.age(now)
        if age > self.max_snapshot_age:
            mismatches.append(Mismatch(
                MismatchClass.INCOMPLETE_BROKER_SNAPSHOT,
                "snapshot",
                f"snapshot is {age.total_seconds():.0f}s old, older than the "
                f"{self.max_snapshot_age.total_seconds():.0f}s limit",
                broker_value=age.total_seconds(),
            ))
        return mismatches

    def _check_orders(
        self, local: LocalSnapshot, broker: BrokerSnapshot
    ) -> List[Mismatch]:
        mismatches = []

        # An order the broker is working that we know nothing about. Somebody
        # traded this account, or one of our submissions escaped our books.
        for client_id, payload in broker.open_orders.items():
            if client_id in local.open_orders or client_id in local.unknown_orders:
                continue
            mismatches.append(Mismatch(
                MismatchClass.UNKNOWN_BROKER_ORDER,
                client_id,
                "the broker is working an order we have no record of",
                broker_value=payload,
            ))

        # An order we believe is live that the broker does not have. It may
        # have filled or been cancelled while we were not looking.
        for client_id, payload in local.open_orders.items():
            if client_id in broker.open_orders:
                continue
            mismatches.append(Mismatch(
                MismatchClass.MISSING_BROKER_ORDER,
                client_id,
                "we believe this order is live but the broker does not list it",
                local_value=payload,
            ))

        # An order whose state we lost. Present or absent at the broker, it is
        # unresolved until something says which.
        for client_id, payload in local.unknown_orders.items():
            broker_side = broker.open_orders.get(client_id)
            mismatches.append(Mismatch(
                MismatchClass.UNKNOWN_BROKER_ORDER,
                client_id,
                "local order state is unknown and must be resolved before "
                "adding risk",
                local_value=payload,
                broker_value=broker_side,
            ))

        # Same client ID, different economics.
        for client_id, broker_payload in broker.open_orders.items():
            local_payload = local.open_orders.get(client_id)
            if not local_payload:
                continue
            detail = self._order_economics_differ(local_payload, broker_payload)
            if detail:
                mismatches.append(Mismatch(
                    MismatchClass.QUANTITY_MISMATCH,
                    client_id,
                    detail,
                    local_value=local_payload,
                    broker_value=broker_payload,
                ))

        # The same client ID on two distinct broker orders means a retry
        # created a second economic order. open_orders is keyed by client ID
        # so a duplicate collapses on the way in; the fetcher reports it
        # separately rather than letting it disappear.
        for client_id in broker.duplicate_client_ids:
            mismatches.append(Mismatch(
                MismatchClass.DUPLICATE_CLIENT_ID,
                client_id,
                "one client order ID maps to more than one broker order; a "
                "retry created duplicate exposure",
                broker_value=broker.open_orders.get(client_id),
            ))

        return mismatches

    def _order_economics_differ(
        self, local_payload: Dict[str, Any], broker_payload: Dict[str, Any]
    ) -> Optional[str]:
        local_qty = self._as_float(local_payload.get("quantity"))
        broker_qty = self._as_float(broker_payload.get("quantity"))
        if local_qty is not None and broker_qty is not None:
            if abs(local_qty - broker_qty) > self.quantity_tolerance:
                return f"quantity differs: local {local_qty}, broker {broker_qty}"
        local_side = str(local_payload.get("side", "")).lower()
        broker_side = str(broker_payload.get("side", "")).lower()
        if local_side and broker_side and local_side != broker_side:
            return f"side differs: local {local_side}, broker {broker_side}"
        local_symbol = local_payload.get("symbol") or local_payload.get("instrument")
        broker_symbol = broker_payload.get("symbol") or broker_payload.get("instrument")
        if local_symbol and broker_symbol and local_symbol != broker_symbol:
            return f"instrument differs: local {local_symbol}, broker {broker_symbol}"
        return None

    def _check_positions(
        self, local: LocalSnapshot, broker: BrokerSnapshot
    ) -> List[Mismatch]:
        mismatches = []
        for instrument in sorted(set(local.positions) | set(broker.positions)):
            local_qty = float(local.positions.get(instrument, 0.0))
            broker_qty = float(broker.positions.get(instrument, 0.0))
            if abs(local_qty - broker_qty) <= self.quantity_tolerance:
                continue
            mismatches.append(Mismatch(
                MismatchClass.POSITION_MISMATCH,
                instrument,
                f"position differs by {broker_qty - local_qty:+g}",
                local_value=local_qty,
                broker_value=broker_qty,
            ))
        return mismatches

    def _check_cash(
        self, local: LocalSnapshot, broker: BrokerSnapshot
    ) -> List[Mismatch]:
        if local.cash is None or broker.cash is None:
            return []
        if abs(local.cash - broker.cash) <= self.cash_tolerance:
            return []
        return [Mismatch(
            MismatchClass.CASH_MISMATCH,
            "cash",
            f"cash differs by {broker.cash - local.cash:+.2f}",
            local_value=local.cash,
            broker_value=broker.cash,
        )]

    def _check_capital_tier(
        self, local: LocalSnapshot, broker: BrokerSnapshot
    ) -> List[Mismatch]:
        """Broker-side exposure above the authorised tier is a breach.

        This catches capital that appeared without our asking - a manual trade,
        a stale position, a fill we never saw - which local bookkeeping alone
        cannot see.
        """
        if local.capital_tier_limit is None:
            return []
        exposure = sum(abs(qty) for qty in broker.positions.values())
        if exposure <= local.capital_tier_limit:
            return []
        return [Mismatch(
            MismatchClass.CAPITAL_LIMIT_BREACH,
            "capital",
            f"broker exposure {exposure:g} exceeds the authorised tier "
            f"{local.capital_tier_limit:g}",
            local_value=local.capital_tier_limit,
            broker_value=exposure,
        )]

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -- applying corrections --------------------------------------------

    def apply_position_corrections(
        self,
        local: LocalSnapshot,
        broker: BrokerSnapshot,
        report: ReconciliationReport,
        reason: str,
    ) -> List[Dict[str, Any]]:
        """Adopt broker positions, recording every change as an event.

        The ULTRAPLAN is explicit that broker truth must never silently
        overwrite local state. This returns the correction events so the
        caller can persist them; the fact that a position was wrong is often
        more important than the corrected number.
        """
        if not reason:
            raise ValueError("a reconciliation correction must state its reason")

        corrections = []
        for mismatch in report.of_kind(MismatchClass.POSITION_MISMATCH):
            instrument = mismatch.subject
            before = local.positions.get(instrument, 0.0)
            after = float(broker.positions.get(instrument, 0.0))
            local.positions[instrument] = after
            event = {
                "type": "position_correction",
                "instrument": instrument,
                "before": before,
                "after": after,
                "delta": after - before,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            corrections.append(event)
            report.corrections.append(event)
            logger.warning(
                "Reconciliation correction: %s %s -> %s (%s)",
                instrument, before, after, reason,
            )
        return corrections
