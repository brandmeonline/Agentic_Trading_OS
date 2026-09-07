"""Persistence failure policy — ATOS-P1-PERSIST-001.

Invariant:

    A critical write that fails in live mode stops new risk.

Not every write matters equally. Losing a dashboard cache entry costs nothing;
losing an order intent costs the ability to know what the broker is holding.
The dangerous pattern is treating them the same — usually by wrapping both in
``except Exception: pass`` — because that turns a storage outage into silent
data loss while the strategy loop keeps sending orders.

So writes are classified. A non-critical failure is logged and life goes on. A
critical failure in live mode freezes the system: it may still reduce risk,
but it may not add any, because the durable record of what it is doing has
stopped being durable.

Paper mode degrades loudly instead of freezing. No real capital is exposed, and
halting paper on a disk hiccup would stop the evidence runs that promotion
depends on — but the failure is recorded, and a paper run that lost writes is
not clean evidence.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class WriteKind(Enum):
    """What is being written, and therefore how much its loss costs."""

    # Critical: losing any of these means losing track of real exposure,
    # real permission, or the audit trail that proves either.
    ORDER_INTENT = "order_intent"
    LIFECYCLE_TRANSITION = "lifecycle_transition"
    FILL = "fill"
    RESERVATION_CHANGE = "reservation_change"
    RECONCILIATION_REPORT = "reconciliation_report"
    LIVE_SESSION_STATE = "live_session_state"
    CAPITAL_TIER = "capital_tier"
    RISK_TRIP = "risk_trip"
    DAILY_LOSS_ANCHOR = "daily_loss_anchor"

    # Non-critical: useful, reconstructible, and not load-bearing for safety.
    ANALYTICS = "analytics"
    DASHBOARD_CACHE = "dashboard_cache"
    RESEARCH_TELEMETRY = "research_telemetry"
    COSMETIC_HISTORY = "cosmetic_history"


#: The ULTRAPLAN's critical list. Membership here is the whole policy.
CRITICAL_WRITES = frozenset({
    WriteKind.ORDER_INTENT,
    WriteKind.LIFECYCLE_TRANSITION,
    WriteKind.FILL,
    WriteKind.RESERVATION_CHANGE,
    WriteKind.RECONCILIATION_REPORT,
    WriteKind.LIVE_SESSION_STATE,
    WriteKind.CAPITAL_TIER,
    WriteKind.RISK_TRIP,
    WriteKind.DAILY_LOSS_ANCHOR,
})

NON_CRITICAL_WRITES = frozenset(set(WriteKind) - CRITICAL_WRITES)


def is_critical(kind: WriteKind) -> bool:
    return kind in CRITICAL_WRITES


class CriticalWriteFailed(RuntimeError):
    """A write that safety depends on did not reach durable storage."""

    def __init__(self, kind: WriteKind, detail: str) -> None:
        super().__init__(f"critical write {kind.value} failed: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class WriteFailure:
    """One recorded persistence failure."""

    kind: WriteKind
    detail: str
    live: bool
    froze_trading: bool
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "critical": is_critical(self.kind),
            "detail": self.detail,
            "live": self.live,
            "froze_trading": self.froze_trading,
            "at": self.at.isoformat(),
        }


class PersistencePolicy:
    """Decides what a failed write means.

    ``freeze`` is called with a reason when live trading must stop adding
    risk. In practice that is ``RuntimeStateMachine.freeze``; it is injected
    so the policy stays testable and has no import cycle with the runtime.
    """

    def __init__(
        self,
        live: bool = False,
        freeze: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.live = live
        self._freeze = freeze
        self.failures: List[WriteFailure] = []

    @property
    def critical_failures(self) -> List[WriteFailure]:
        return [f for f in self.failures if is_critical(f.kind)]

    @property
    def degraded(self) -> bool:
        """Whether any critical write has been lost in this session.

        A paper run in this state is not clean promotion evidence, even
        though it was allowed to continue.
        """
        return bool(self.critical_failures)

    def record_failure(self, kind: WriteKind, detail: str) -> WriteFailure:
        """Classify a failure and act on it.

        Returns the recorded failure. Raises nothing: the caller decides
        whether to propagate, and :meth:`guard` does propagate for critical
        writes so no code path can continue as though the write succeeded.
        """
        critical = is_critical(kind)
        should_freeze = critical and self.live

        if should_freeze and self._freeze is not None:
            self._freeze(
                f"critical persistence failure ({kind.value}): {detail}. "
                "No new risk until storage is healthy and state is reconciled."
            )

        failure = WriteFailure(
            kind=kind, detail=detail, live=self.live, froze_trading=should_freeze
        )
        self.failures.append(failure)

        if should_freeze:
            logger.error(
                "LIVE CRITICAL WRITE FAILED (%s): %s. Trading frozen.",
                kind.value, detail,
            )
        elif critical:
            logger.error(
                "Critical write failed in paper mode (%s): %s. Continuing, but "
                "this run is degraded and is not clean promotion evidence.",
                kind.value, detail,
            )
        else:
            logger.warning("Non-critical write failed (%s): %s", kind.value, detail)

        return failure

    @contextmanager
    def guard(self, kind: WriteKind):
        """Wrap a write so its failure is classified rather than swallowed.

        Critical failures re-raise as :class:`CriticalWriteFailed` after being
        recorded and after any freeze, so no caller can continue believing the
        write landed. Non-critical failures are absorbed, which is the only
        place in this module where swallowing is correct.
        """
        try:
            yield
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self.record_failure(kind, detail)
            if is_critical(kind):
                raise CriticalWriteFailed(kind, detail) from exc
            return

    def report(self) -> Dict[str, Any]:
        return {
            "live": self.live,
            "degraded": self.degraded,
            "failure_count": len(self.failures),
            "critical_failure_count": len(self.critical_failures),
            "failures": [f.to_dict() for f in self.failures],
        }
