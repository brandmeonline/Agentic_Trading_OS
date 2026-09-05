"""Continuous broker reconciliation — ATOS-P2-REC-001.

Invariant:

    Reconciliation is not a startup ritual. It runs throughout a live session,
    and any material mismatch freezes acquisition.

REC-002 built the comparison and REC-001 runs it once, at startup. That
establishes agreement at a single instant and says nothing about the hours
afterwards, which is when a manual trade gets placed, a fill arrives on a
socket nobody read, or an order quietly expires at the venue.

So reconciliation gets a schedule and a set of triggers. The schedule catches
drift; the triggers catch the specific moments when local state is most likely
to have diverged — after an ambiguous order, after a reconnect, after a
persistence recovery, after a risk trip, and before a capital promotion.

The staleness rule is the part that is easy to get wrong. A reconciliation
that succeeded twenty minutes ago is not evidence about now. If the last
successful run is older than the freshness window, acquisition stops until a
new one succeeds — the absence of a recent check is itself a reason not to
trade, not a neutral state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.reconciliation import ReconciliationReport

logger = logging.getLogger(__name__)


class ReconciliationTrigger(Enum):
    """Why a reconciliation ran. Recorded so drift can be attributed."""

    STARTUP = "startup"
    SCHEDULED = "scheduled"
    ORDER_AMBIGUITY = "order_ambiguity"
    BROKER_RECONNECT = "broker_reconnect"
    PERSISTENCE_RECOVERY = "persistence_recovery"
    RISK_TRIP = "risk_trip"
    CAPITAL_PROMOTION = "capital_promotion"
    SHUTDOWN = "shutdown"
    MANUAL = "manual"


#: How long a successful reconciliation remains evidence about the present.
DEFAULT_FRESHNESS_WINDOW = timedelta(minutes=15)

#: How often to reconcile when nothing else triggers one.
DEFAULT_INTERVAL = timedelta(minutes=5)


@dataclass
class ReconciliationRun:
    """One execution of the reconciler."""

    trigger: ReconciliationTrigger
    started_at: datetime
    finished_at: datetime
    matched: bool
    mismatch_count: int
    critical_count: int
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def succeeded(self) -> bool:
        """Whether the run completed, regardless of what it found.

        A run that found mismatches succeeded: it did its job. A run that
        raised did not, and cannot be counted as a check having happened.
        """
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger": self.trigger.value,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "matched": self.matched,
            "mismatch_count": self.mismatch_count,
            "critical_count": self.critical_count,
            "error": self.error,
            "succeeded": self.succeeded,
        }


class RecurringReconciler:
    """Runs reconciliation on a schedule and on demand, and gates acquisition.

    ``reconcile`` is injected: it takes no arguments and returns a
    :class:`ReconciliationReport`. Keeping the data-gathering outside makes
    this testable without a broker and keeps the scheduling logic honest about
    what it actually knows.
    """

    def __init__(
        self,
        reconcile: Callable[[], ReconciliationReport],
        interval: timedelta = DEFAULT_INTERVAL,
        freshness_window: timedelta = DEFAULT_FRESHNESS_WINDOW,
        on_mismatch: Optional[Callable[[ReconciliationReport], None]] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._reconcile = reconcile
        self.interval = interval
        self.freshness_window = freshness_window
        self.on_mismatch = on_mismatch
        self._clock = clock
        self._lock = threading.Lock()

        self.runs: List[ReconciliationRun] = []
        self.last_report: Optional[ReconciliationReport] = None
        self.last_success_at: Optional[datetime] = None
        self.last_attempt_at: Optional[datetime] = None

    # -- running ---------------------------------------------------------

    def run(self, trigger: ReconciliationTrigger) -> ReconciliationRun:
        """Reconcile now, recording why and what happened."""
        with self._lock:
            started = self._clock()
            self.last_attempt_at = started
            report: Optional[ReconciliationReport] = None
            error: Optional[str] = None

            try:
                report = self._reconcile()
            except Exception as exc:
                # A reconciliation that raised is not a reconciliation. It
                # must not refresh the freshness clock, or a permanently
                # broken broker connection would look like continuous
                # agreement.
                logger.exception("Reconciliation (%s) failed", trigger.value)
                error = f"{type(exc).__name__}: {exc}"

            finished = self._clock()

            if report is not None:
                self.last_report = report
                self.last_success_at = finished

            run = ReconciliationRun(
                trigger=trigger,
                started_at=started,
                finished_at=finished,
                matched=bool(report.may_acquire) if report else False,
                mismatch_count=len(report.mismatches) if report else 0,
                critical_count=len(report.critical_mismatches) if report else 0,
                error=error,
            )
            self.runs.append(run)

        if report is not None and not report.may_acquire:
            logger.warning(
                "Reconciliation (%s) found %d mismatch(es): %s",
                trigger.value, len(report.mismatches), report.summary(),
            )
            if self.on_mismatch:
                self.on_mismatch(report)

        return run

    def run_if_due(self) -> Optional[ReconciliationRun]:
        """Run only if the interval has elapsed since the last attempt."""
        if not self.is_due():
            return None
        return self.run(ReconciliationTrigger.SCHEDULED)

    def is_due(self) -> bool:
        if self.last_attempt_at is None:
            return True
        return self._clock() - self.last_attempt_at >= self.interval

    # -- gating ----------------------------------------------------------

    def age(self) -> Optional[timedelta]:
        """Time since the last *successful* reconciliation."""
        if self.last_success_at is None:
            return None
        return self._clock() - self.last_success_at

    def is_fresh(self) -> bool:
        age = self.age()
        return age is not None and age <= self.freshness_window

    def may_acquire(self) -> tuple:
        """Whether reconciliation state permits new risk.

        Three ways to refuse, and the middle one is the one systems forget:
        never reconciled; reconciled too long ago; reconciled recently and
        found a mismatch.
        """
        if self.last_report is None:
            return False, "no reconciliation has completed in this session"

        if not self.is_fresh():
            age = self.age()
            seconds = age.total_seconds() if age else float("inf")
            return False, (
                f"the last successful reconciliation was {seconds:.0f}s ago, "
                f"beyond the {self.freshness_window.total_seconds():.0f}s "
                "freshness window; an old check is not evidence about now"
            )

        if not self.last_report.may_acquire:
            return False, f"reconciliation found: {self.last_report.summary()}"

        return True, ""

    # -- reporting -------------------------------------------------------

    def metrics(self) -> Dict[str, Any]:
        """The ULTRAPLAN's required reconciliation metrics."""
        age = self.age()
        recent = self.runs[-1] if self.runs else None
        return {
            "last_reconciliation_age_seconds": (
                age.total_seconds() if age is not None else None
            ),
            "last_duration_seconds": recent.duration_seconds if recent else None,
            "last_trigger": recent.trigger.value if recent else None,
            "mismatch_count": (
                len(self.last_report.mismatches) if self.last_report else None
            ),
            "unknown_order_count": self._count_kind("unknown_broker_order"),
            "broker_vs_local_exposure_delta": self._position_delta(),
            "broker_vs_local_cash_delta": self._cash_delta(),
            "unresolved_intents": self._count_kind("unknown_broker_order"),
            "total_runs": len(self.runs),
            "failed_runs": sum(1 for r in self.runs if not r.succeeded),
            "is_fresh": self.is_fresh(),
            "may_acquire": self.may_acquire()[0],
        }

    def _count_kind(self, kind_value: str) -> int:
        if self.last_report is None:
            return 0
        return sum(
            1 for m in self.last_report.mismatches if m.kind.value == kind_value
        )

    def _position_delta(self) -> float:
        if self.last_report is None:
            return 0.0
        total = 0.0
        for mismatch in self.last_report.mismatches:
            if mismatch.kind.value != "position_mismatch":
                continue
            local = mismatch.local_value or 0.0
            broker = mismatch.broker_value or 0.0
            total += abs(float(broker) - float(local))
        return total

    def _cash_delta(self) -> float:
        if self.last_report is None:
            return 0.0
        for mismatch in self.last_report.mismatches:
            if mismatch.kind.value == "cash_mismatch":
                local = mismatch.local_value or 0.0
                broker = mismatch.broker_value or 0.0
                return float(broker) - float(local)
        return 0.0

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [run.to_dict() for run in self.runs[-limit:]]
