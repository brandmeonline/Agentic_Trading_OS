"""Durable loss and drawdown anchors — ATOS-P1-RISK-002.

Invariant:

    A daily or total drawdown trip survives a restart.

The test that matters most is the simplest one: trip the limit, throw the
process away, come back, and check the limit is still tripped. A loss limit a
crash can clear is not a loss limit.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_anchors import (  # noqa: E402
    DurableRiskAnchors,
    RiskAnchors,
    RiskAnchorStore,
    utc_trading_date,
)

pytestmark = pytest.mark.adversarial

DAY = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store_path(tmp_path):
    return str(tmp_path / "anchors.sqlite")


def anchors_at(store_path, daily=0.05, total=0.20):
    return DurableRiskAnchors(
        RiskAnchorStore(store_path),
        max_daily_drawdown=daily,
        max_total_drawdown=total,
    )


# ---------------------------------------------------------------------------
# The UTC trading day
# ---------------------------------------------------------------------------

def test_the_trading_day_is_utc():
    """A local-midnight rollover is a different limit on every host."""
    just_before = datetime(2026, 9, 5, 23, 59, 0, tzinfo=timezone.utc)
    just_after = datetime(2026, 9, 6, 0, 1, 0, tzinfo=timezone.utc)
    assert utc_trading_date(just_before) == date(2026, 9, 5)
    assert utc_trading_date(just_after) == date(2026, 9, 6)


def test_a_naive_datetime_is_treated_as_utc():
    assert utc_trading_date(datetime(2026, 9, 5, 12, 0, 0)) == date(2026, 9, 5)


# ---------------------------------------------------------------------------
# The restart guarantee
# ---------------------------------------------------------------------------

def test_a_daily_drawdown_trip_survives_a_restart(store_path):
    """The headline test."""
    risk = anchors_at(store_path, daily=0.05)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-600.0, now=DAY)
    risk.observe_equity(9_400.0, now=DAY)

    assert risk.tripped
    assert "daily_drawdown" in risk.active_trips

    # The process dies and comes back.
    recovered = anchors_at(store_path, daily=0.05)
    assert recovered.tripped, "a restart cleared the daily loss limit"
    assert "daily_drawdown" in recovered.active_trips
    allowed, reason = recovered.may_trade()
    assert not allowed
    assert "daily_drawdown" in reason


def test_a_total_drawdown_trip_survives_a_restart(store_path):
    risk = anchors_at(store_path, total=0.20)
    risk.observe_equity(10_000.0, now=DAY)
    risk.observe_equity(7_500.0, now=DAY)
    assert "total_drawdown" in risk.active_trips

    recovered = anchors_at(store_path, total=0.20)
    assert "total_drawdown" in recovered.active_trips


def test_the_day_opening_anchor_survives_a_restart(store_path):
    """Re-anchoring at restart would reset the loss measurement to zero."""
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-400.0, now=DAY)
    risk.observe_equity(9_600.0, now=DAY)

    recovered = anchors_at(store_path)
    assert recovered.anchors.day_opening_equity == 10_000.0, (
        "the opening anchor was recomputed from the restarted equity"
    )
    assert recovered.anchors.daily_realized_pnl == -400.0
    assert recovered.anchors.daily_drawdown_fraction() == pytest.approx(0.04)


def test_the_high_water_mark_survives_a_restart(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.observe_equity(15_000.0, now=DAY)
    risk.observe_equity(14_000.0, now=DAY)

    recovered = anchors_at(store_path)
    assert recovered.anchors.high_water_equity == 15_000.0, (
        "the account's best is not its most recent"
    )


def test_the_loss_streak_survives_a_restart(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    for _ in range(3):
        risk.record_realized_pnl(-10.0, now=DAY)
    assert risk.anchors.loss_streak == 3

    assert anchors_at(store_path).anchors.loss_streak == 3


# ---------------------------------------------------------------------------
# The opening anchor is captured once
# ---------------------------------------------------------------------------

def test_the_opening_anchor_is_not_moved_by_later_observations(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.observe_equity(9_000.0, now=DAY)
    risk.observe_equity(8_000.0, now=DAY)
    assert risk.anchors.day_opening_equity == 10_000.0


def test_a_rising_account_does_not_move_the_opening_anchor(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.observe_equity(20_000.0, now=DAY)
    assert risk.anchors.day_opening_equity == 10_000.0
    assert risk.anchors.high_water_equity == 20_000.0


def test_setting_the_opening_anchor_is_recorded_once(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.observe_equity(9_000.0, now=DAY)
    events = [e["event"] for e in risk.store.events()]
    assert events.count("day_opening_equity_set") == 1


# ---------------------------------------------------------------------------
# Day rollover
# ---------------------------------------------------------------------------

def test_a_new_day_resets_the_daily_measurement(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-400.0, now=DAY)
    assert risk.anchors.daily_realized_pnl == -400.0

    tomorrow = DAY + timedelta(days=1)
    risk.observe_equity(9_600.0, now=tomorrow)
    assert risk.anchors.trading_date == utc_trading_date(tomorrow)
    assert risk.anchors.daily_realized_pnl == 0.0
    assert risk.anchors.day_opening_equity == 9_600.0


def test_the_high_water_mark_carries_across_days(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(15_000.0, now=DAY)

    tomorrow = DAY + timedelta(days=1)
    risk.observe_equity(14_000.0, now=tomorrow)
    assert risk.anchors.high_water_equity == 15_000.0, (
        "total drawdown is measured from the account's best ever"
    )


def test_a_total_drawdown_trip_still_fires_across_days(store_path):
    risk = anchors_at(store_path, total=0.20)
    risk.observe_equity(10_000.0, now=DAY)

    tomorrow = DAY + timedelta(days=1)
    risk.observe_equity(7_000.0, now=tomorrow)
    assert "total_drawdown" in risk.active_trips


def test_yesterdays_daily_trip_does_not_bind_today(store_path):
    """A daily limit is daily. A total one is not."""
    risk = anchors_at(store_path, daily=0.05, total=0.90)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-600.0, now=DAY)
    risk.observe_equity(9_400.0, now=DAY)
    assert "daily_drawdown" in risk.active_trips

    tomorrow = DAY + timedelta(days=1)
    risk.observe_equity(9_400.0, now=tomorrow)
    assert "daily_drawdown" not in risk.active_trips


def test_a_reopened_day_restores_that_days_trips(store_path):
    risk = anchors_at(store_path, daily=0.05, total=0.90)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-600.0, now=DAY)
    risk.observe_equity(9_400.0, now=DAY)

    tomorrow = DAY + timedelta(days=1)
    risk.observe_equity(9_400.0, now=tomorrow)
    risk.roll_to(utc_trading_date(DAY))
    assert "daily_drawdown" in risk.active_trips


# ---------------------------------------------------------------------------
# Trips are sticky and explicit
# ---------------------------------------------------------------------------

def test_a_trip_is_idempotent(store_path):
    risk = anchors_at(store_path)
    risk.trip("manual", "operator halted trading")
    first = risk.anchors.active_trips["manual"]["at"]
    risk.trip("manual", "a different reason")
    assert risk.anchors.active_trips["manual"]["at"] == first
    assert risk.anchors.active_trips["manual"]["reason"] == "operator halted trading"


def test_clearing_a_trip_requires_a_reason(store_path):
    risk = anchors_at(store_path)
    risk.trip("manual", "halt")
    with pytest.raises(ValueError):
        risk.clear_trip("manual", "")
    assert risk.tripped


def test_clearing_a_trip_is_recorded(store_path):
    risk = anchors_at(store_path)
    risk.trip("manual", "halt")
    assert risk.clear_trip("manual", "operator reviewed and resumed")
    assert not risk.tripped

    events = [e["event"] for e in risk.store.events()]
    assert "trip:manual" in events
    assert "trip_cleared:manual" in events


def test_clearing_an_absent_trip_is_a_no_op(store_path):
    risk = anchors_at(store_path)
    assert risk.clear_trip("nothing", "reason") is False


def test_a_cleared_trip_stays_cleared_across_a_restart(store_path):
    risk = anchors_at(store_path)
    risk.trip("manual", "halt")
    risk.clear_trip("manual", "reviewed")
    assert not anchors_at(store_path).tripped


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_daily_loss_is_zero_when_up():
    anchors = RiskAnchors(trading_date=date(2026, 9, 5), daily_realized_pnl=500.0)
    assert anchors.daily_loss == 0.0


def test_daily_loss_is_positive_when_down():
    anchors = RiskAnchors(trading_date=date(2026, 9, 5), daily_realized_pnl=-500.0)
    assert anchors.daily_loss == 500.0


def test_drawdown_fractions_are_none_without_anchors():
    anchors = RiskAnchors(trading_date=date(2026, 9, 5))
    assert anchors.daily_drawdown_fraction() is None
    assert anchors.total_drawdown_fraction(1000.0) is None


def test_total_drawdown_is_never_negative():
    anchors = RiskAnchors(trading_date=date(2026, 9, 5), high_water_equity=1000.0)
    assert anchors.total_drawdown_fraction(1500.0) == 0.0


def test_the_limit_fires_exactly_at_the_threshold(store_path):
    risk = anchors_at(store_path, daily=0.05)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-500.0, now=DAY)
    risk.observe_equity(9_500.0, now=DAY)
    assert "daily_drawdown" in risk.active_trips


def test_the_limit_does_not_fire_below_the_threshold(store_path):
    risk = anchors_at(store_path, daily=0.05)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-499.0, now=DAY)
    risk.observe_equity(9_501.0, now=DAY)
    assert not risk.tripped


def test_report_serialises_for_the_dashboard(store_path):
    risk = anchors_at(store_path)
    risk.observe_equity(10_000.0, now=DAY)
    risk.trip("manual", "halt")
    payload = risk.report()
    assert payload["tripped"] is True
    assert payload["active_trips"] == ["manual"]
    assert payload["anchors"]["day_opening_equity"] == 10_000.0
    assert payload["anchors"]["trading_date"] == "2026-09-05"


# ---------------------------------------------------------------------------
# Integration with RiskManager
# ---------------------------------------------------------------------------

def risk_manager(capital=10_000.0):
    from core.risk import RiskConfig, RiskManager
    return RiskManager(RiskConfig(initial_capital=capital))


def test_risk_manager_without_anchors_keeps_local_behaviour():
    manager = risk_manager()
    assert manager.durable_anchors is None
    assert manager.active_risk_trips() == []
    assert manager.check_risk_limits("AAPL") is True


def test_a_durable_trip_blocks_a_freshly_restarted_risk_manager(store_path):
    """The whole point: the counters reset, the trip did not."""
    risk = anchors_at(store_path, daily=0.05)
    risk.observe_equity(10_000.0, now=DAY)
    risk.record_realized_pnl(-600.0, now=DAY)
    risk.observe_equity(9_400.0, now=DAY)
    assert risk.tripped

    # A brand new process: in-memory daily_pnl is 0 and would allow trading.
    manager = risk_manager()
    assert manager.daily_pnl == 0.0
    assert manager.check_risk_limits("AAPL") is True, (
        "without durable anchors the restart really does clear the limit"
    )

    manager.attach_durable_anchors(anchors_at(store_path, daily=0.05))
    assert manager.check_risk_limits("AAPL") is False
    assert "daily_drawdown" in manager.active_risk_trips()


def test_clearing_the_trip_lets_the_manager_trade_again(store_path):
    risk = anchors_at(store_path)
    risk.trip("manual", "operator halt")

    manager = risk_manager()
    manager.attach_durable_anchors(risk)
    assert manager.check_risk_limits("AAPL") is False

    risk.clear_trip("manual", "operator reviewed and resumed")
    assert manager.check_risk_limits("AAPL") is True
