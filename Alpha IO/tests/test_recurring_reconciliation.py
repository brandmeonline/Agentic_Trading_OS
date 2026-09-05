"""Continuous reconciliation — ATOS-P2-REC-001.

Invariant:

    Reconciliation runs throughout a live session, and any material mismatch
    freezes acquisition. A check that happened long enough ago is not evidence
    about now.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.reconciliation import (  # noqa: E402
    Mismatch,
    MismatchClass,
    ReconciliationReport,
)
from core.recurring_reconciliation import (  # noqa: E402
    RecurringReconciler,
    ReconciliationTrigger,
)

pytestmark = pytest.mark.adversarial

START = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def matched_report():
    return ReconciliationReport(matched=True)


def mismatched_report(critical=True):
    kind = (MismatchClass.POSITION_MISMATCH if critical
            else MismatchClass.CASH_MISMATCH)
    return ReconciliationReport(
        matched=False,
        mismatches=[Mismatch(kind, "AAPL", "differs", local_value=0.0,
                             broker_value=40.0)],
    )


def reconciler(reports=None, clock=None, **kwargs):
    clock = clock or FakeClock()
    queue = list(reports or [matched_report()])

    def reconcile():
        if not queue:
            return matched_report()
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return RecurringReconciler(reconcile, clock=clock, **kwargs), clock


# ---------------------------------------------------------------------------
# Nothing has run yet
# ---------------------------------------------------------------------------

def test_a_reconciler_that_has_never_run_blocks_acquisition():
    rec, _ = reconciler()
    allowed, reason = rec.may_acquire()
    assert not allowed
    assert "no reconciliation has completed" in reason


def test_the_first_run_is_always_due():
    rec, _ = reconciler()
    assert rec.is_due()


# ---------------------------------------------------------------------------
# A successful, matched run permits acquisition
# ---------------------------------------------------------------------------

def test_a_matched_run_permits_acquisition():
    rec, _ = reconciler()
    run = rec.run(ReconciliationTrigger.STARTUP)
    assert run.succeeded
    assert run.matched
    assert rec.may_acquire()[0]


def test_a_mismatched_run_blocks_acquisition():
    rec, _ = reconciler(reports=[mismatched_report()])
    rec.run(ReconciliationTrigger.STARTUP)
    allowed, reason = rec.may_acquire()
    assert not allowed
    assert "position_mismatch" in reason


def test_a_non_critical_mismatch_still_blocks():
    """An unexplained difference does not get to authorise new positions."""
    rec, _ = reconciler(reports=[mismatched_report(critical=False)])
    rec.run(ReconciliationTrigger.STARTUP)
    assert not rec.may_acquire()[0]


# ---------------------------------------------------------------------------
# Staleness: the rule systems forget
# ---------------------------------------------------------------------------

def test_a_stale_success_stops_permitting_acquisition():
    """Agreement twenty minutes ago is not agreement now."""
    rec, clock = reconciler(freshness_window=timedelta(minutes=15))
    rec.run(ReconciliationTrigger.STARTUP)
    assert rec.may_acquire()[0]

    clock.advance(minutes=20)
    allowed, reason = rec.may_acquire()
    assert not allowed
    assert "freshness window" in reason
    assert "not evidence about now" in reason


def test_freshness_is_measured_from_success_not_attempt():
    """A failing run must not refresh the clock."""
    rec, clock = reconciler(
        reports=[matched_report(), ConnectionError("broker down")],
        freshness_window=timedelta(minutes=15),
    )
    rec.run(ReconciliationTrigger.STARTUP)
    clock.advance(minutes=20)
    rec.run(ReconciliationTrigger.SCHEDULED)  # this one raises

    allowed, reason = rec.may_acquire()
    assert not allowed, (
        "a broken broker connection looked like continuous agreement"
    )
    assert "freshness window" in reason


def test_a_fresh_run_restores_permission():
    rec, clock = reconciler(
        reports=[matched_report(), matched_report()],
        freshness_window=timedelta(minutes=15),
    )
    rec.run(ReconciliationTrigger.STARTUP)
    clock.advance(minutes=20)
    assert not rec.may_acquire()[0]

    rec.run(ReconciliationTrigger.SCHEDULED)
    assert rec.may_acquire()[0]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------

def test_a_raising_reconciliation_is_recorded_as_failed():
    rec, _ = reconciler(reports=[ConnectionError("broker unreachable")])
    run = rec.run(ReconciliationTrigger.SCHEDULED)
    assert not run.succeeded
    assert "ConnectionError" in run.error
    assert not run.matched


def test_a_raising_reconciliation_does_not_propagate():
    """The scheduler must survive a broker outage."""
    rec, _ = reconciler(reports=[RuntimeError("boom")])
    rec.run(ReconciliationTrigger.SCHEDULED)  # must not raise
    assert rec.metrics()["failed_runs"] == 1


def test_a_failure_after_a_success_keeps_the_last_good_report():
    rec, _ = reconciler(reports=[matched_report(), RuntimeError("boom")])
    rec.run(ReconciliationTrigger.STARTUP)
    rec.run(ReconciliationTrigger.SCHEDULED)
    assert rec.last_report is not None
    assert rec.last_report.matched


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def test_run_if_due_respects_the_interval():
    rec, clock = reconciler(
        reports=[matched_report(), matched_report()],
        interval=timedelta(minutes=5),
    )
    assert rec.run_if_due() is not None
    assert rec.run_if_due() is None, "ran again before the interval elapsed"

    clock.advance(minutes=5)
    assert rec.run_if_due() is not None


def test_the_interval_is_measured_from_the_attempt_not_the_success():
    """A failing broker must not be retried in a tight loop."""
    rec, clock = reconciler(
        reports=[RuntimeError("down"), RuntimeError("down")],
        interval=timedelta(minutes=5),
    )
    rec.run_if_due()
    assert rec.run_if_due() is None
    clock.advance(minutes=5)
    assert rec.run_if_due() is not None


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trigger", list(ReconciliationTrigger))
def test_every_trigger_can_drive_a_run(trigger):
    """The ULTRAPLAN's trigger list must all be reachable."""
    rec, _ = reconciler()
    run = rec.run(trigger)
    assert run.trigger is trigger
    assert rec.history()[-1]["trigger"] == trigger.value


def test_the_ultraplan_triggers_are_all_present():
    names = {t.value for t in ReconciliationTrigger}
    assert {
        "startup", "scheduled", "order_ambiguity", "broker_reconnect",
        "persistence_recovery", "risk_trip", "capital_promotion", "shutdown",
    } <= names


def test_the_mismatch_callback_fires_only_on_a_mismatch():
    seen = []
    rec, _ = reconciler(
        reports=[matched_report(), mismatched_report()],
        on_mismatch=seen.append,
    )
    rec.run(ReconciliationTrigger.STARTUP)
    assert seen == []

    rec.run(ReconciliationTrigger.SCHEDULED)
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metrics_cover_the_required_fields():
    rec, _ = reconciler(reports=[mismatched_report()])
    rec.run(ReconciliationTrigger.STARTUP)
    metrics = rec.metrics()

    for key in ("last_reconciliation_age_seconds", "last_duration_seconds",
                "mismatch_count", "unknown_order_count",
                "broker_vs_local_exposure_delta", "broker_vs_local_cash_delta",
                "unresolved_intents"):
        assert key in metrics, f"missing required metric {key}"


def test_the_exposure_delta_reflects_the_mismatch():
    rec, _ = reconciler(reports=[mismatched_report()])
    rec.run(ReconciliationTrigger.STARTUP)
    assert rec.metrics()["broker_vs_local_exposure_delta"] == 40.0


def test_the_cash_delta_is_signed():
    report = ReconciliationReport(
        matched=False,
        mismatches=[Mismatch(MismatchClass.CASH_MISMATCH, "cash", "differs",
                             local_value=1000.0, broker_value=850.0)],
    )
    rec, _ = reconciler(reports=[report])
    rec.run(ReconciliationTrigger.STARTUP)
    assert rec.metrics()["broker_vs_local_cash_delta"] == -150.0


def test_metrics_before_any_run_are_honest_about_it():
    rec, _ = reconciler()
    metrics = rec.metrics()
    assert metrics["last_reconciliation_age_seconds"] is None
    assert metrics["is_fresh"] is False
    assert metrics["may_acquire"] is False


def test_history_is_bounded_and_ordered():
    rec, clock = reconciler(reports=[matched_report() for _ in range(30)])
    for _ in range(30):
        rec.run(ReconciliationTrigger.SCHEDULED)
        clock.advance(seconds=1)
    history = rec.history(limit=5)
    assert len(history) == 5
    assert history == sorted(history, key=lambda h: h["started_at"])
