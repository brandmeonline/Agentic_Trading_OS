"""Broker-authoritative exposure — ATOS-P1-RISK-001.

Invariant:

    effective_exposure = broker_position_exposure
                       + outstanding_acquisition_order_exposure

and pre-trade risk is evaluated against that, not against local bookkeeping.

The six scenarios the ULTRAPLAN lists are all here: a manual broker-side
position appearing, an open order consuming the cap, local disagreeing with
broker, a price move causing a concentration breach, concurrent orders racing
for the same capacity, and stale data blocking new risk.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exposure import (  # noqa: E402
    BrokerAuthoritativeExposure,
    ExposureView,
    view_from_reconciliation,
)

pytestmark = pytest.mark.adversarial


def limiter(live=True, capital=10_000.0, asset_pct=0.20, portfolio_pct=0.50):
    return BrokerAuthoritativeExposure(
        max_asset_concentration=asset_pct,
        max_portfolio_exposure=portfolio_pct,
        configured_capital=capital,
        live=live,
    )


def view(positions=None, outstanding=None, equity=10_000.0,
         complete=True, reconciled=True):
    return ExposureView(
        positions=dict(positions or {}),
        outstanding_acquisitions=dict(outstanding or {}),
        equity=equity,
        complete=complete,
        reconciled=reconciled,
    )


# ---------------------------------------------------------------------------
# The formula
# ---------------------------------------------------------------------------

def test_effective_exposure_is_position_plus_outstanding():
    v = view(positions={"AAPL": 1000.0}, outstanding={"AAPL": 500.0})
    assert v.position_exposure("AAPL") == 1000.0
    assert v.outstanding_exposure("AAPL") == 500.0
    assert v.effective_exposure("AAPL") == 1500.0


def test_reserved_and_filled_stay_distinguishable():
    """The previous asset_exposure dict collapsed these into one number."""
    v = view(positions={"AAPL": 300.0}, outstanding={"AAPL": 700.0})
    assert v.position_exposure("AAPL") != v.effective_exposure("AAPL")
    payload = v.to_dict()["by_instrument"]["AAPL"]
    assert payload["position"] == 300.0
    assert payload["outstanding"] == 700.0
    assert payload["effective"] == 1000.0


def test_a_short_consumes_capacity_like_a_long():
    v = view(positions={"AAPL": -1000.0})
    assert v.position_exposure("AAPL") == 1000.0
    assert v.gross_exposure == 1000.0
    assert v.net_exposure == -1000.0


def test_gross_and_net_differ_when_hedged():
    v = view(positions={"AAPL": 1000.0, "SPY": -1000.0})
    assert v.gross_exposure == 2000.0, "a hedge consumes twice the capacity"
    assert v.net_exposure == 0.0


# ---------------------------------------------------------------------------
# 1: a manual broker-side position appears
# ---------------------------------------------------------------------------

def test_a_position_we_never_opened_consumes_the_cap():
    """Local believes nothing is held; the broker disagrees."""
    limits = limiter()
    limits.update(view(positions={"AAPL": 1900.0}))

    assert limits.asset_limit() == 2000.0
    assert limits.headroom("AAPL") == pytest.approx(100.0)

    allowed, reason = limits.may_acquire("AAPL", 500.0)
    assert not allowed
    assert "headroom" in reason


def test_a_manual_position_above_the_cap_is_a_breach_not_a_small_order():
    """The distinction this issue exists for."""
    limits = limiter()
    limits.update(view(positions={"AAPL": 5000.0}))

    breaches = limits.breaches()
    assert breaches, "exposure above the cap must be reported, not clamped away"
    assert breaches[0].scope == "asset:AAPL"
    assert breaches[0].actual == 5000.0
    assert breaches[0].excess == pytest.approx(3000.0)

    allowed, reason = limits.may_acquire("AAPL", 1.0)
    assert not allowed
    assert "already breached" in reason


def test_headroom_is_zero_but_that_is_not_the_whole_story():
    """Zero headroom cannot distinguish "full" from "over"."""
    full = limiter()
    full.update(view(positions={"AAPL": 2000.0}))
    over = limiter()
    over.update(view(positions={"AAPL": 9000.0}))

    assert full.headroom("AAPL") == 0.0
    assert over.headroom("AAPL") == 0.0
    assert full.breaches() == []
    assert over.breaches(), "only breaches() tells the two apart"


# ---------------------------------------------------------------------------
# 2: an open order pushes effective exposure to the cap
# ---------------------------------------------------------------------------

def test_an_open_order_consumes_capacity_before_it_fills():
    limits = limiter()
    limits.update(view(positions={"AAPL": 1000.0}, outstanding={"AAPL": 1000.0}))

    assert limits.headroom("AAPL") == 0.0
    allowed, reason = limits.may_acquire("AAPL", 100.0)
    assert not allowed, "an unfilled order still reserves the capacity"


def test_closing_orders_do_not_consume_acquisition_capacity():
    """Counting reduce-only orders would block risk reduction."""
    limits = limiter()
    limits.update(view(positions={"AAPL": 1000.0}, outstanding={}))
    assert limits.headroom("AAPL") == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# 3: local says X, broker says more
# ---------------------------------------------------------------------------

def test_unreconciled_exposure_cannot_authorise_new_risk():
    limits = limiter()
    limits.update(view(positions={"AAPL": 100.0}, reconciled=False))

    allowed, reason = limits.may_acquire("AAPL", 100.0)
    assert not allowed
    assert "not reconciled" in reason


def test_incomplete_exposure_cannot_authorise_new_risk():
    limits = limiter()
    limits.update(view(positions={}, complete=False))

    allowed, reason = limits.may_acquire("AAPL", 100.0)
    assert not allowed
    assert "incomplete" in reason


def test_paper_mode_does_not_require_reconciliation():
    """Paper has no broker to reconcile against."""
    limits = limiter(live=False)
    limits.update(view(positions={}, reconciled=False, complete=True))
    allowed, _ = limits.may_acquire("AAPL", 100.0)
    assert allowed


# ---------------------------------------------------------------------------
# 4: a price move causes a concentration breach
# ---------------------------------------------------------------------------

def test_a_price_move_can_create_a_breach_without_any_trade():
    limits = limiter()
    positions = {"AAPL": 10.0}

    limits.update(view_from_reconciliation(
        positions, prices={"AAPL": 150.0}, equity=10_000.0, reconciled=True
    ))
    assert limits.breaches() == [], "1500 is inside the 2000 cap"

    # The position did not change. The price did.
    limits.update(view_from_reconciliation(
        positions, prices={"AAPL": 250.0}, equity=10_000.0, reconciled=True
    ))
    assert limits.breaches(), "2500 is outside the 2000 cap"


def test_an_unpriced_position_makes_the_view_incomplete_not_zero():
    """Valuing an unmarked position at zero would understate risk."""
    v = view_from_reconciliation(
        {"AAPL": 10.0, "MYSTERY": 5.0},
        prices={"AAPL": 100.0},
        equity=10_000.0,
        reconciled=True,
    )
    assert not v.complete
    assert "MYSTERY" not in v.instruments
    assert not v.usable

    limits = limiter()
    limits.update(v)
    allowed, reason = limits.may_acquire("AAPL", 1.0)
    assert not allowed
    assert "incomplete" in reason


# ---------------------------------------------------------------------------
# 5: concurrent orders racing for the same capacity
# ---------------------------------------------------------------------------

def test_two_orders_cannot_both_claim_the_last_of_the_headroom():
    """Whichever is reserved first must show up in the second's view."""
    limits = limiter()
    limits.update(view(positions={"AAPL": 1500.0}))
    assert limits.headroom("AAPL") == pytest.approx(500.0)

    first_allowed, _ = limits.may_acquire("AAPL", 500.0)
    assert first_allowed

    # The first order is now outstanding.
    limits.update(view(positions={"AAPL": 1500.0}, outstanding={"AAPL": 500.0}))
    second_allowed, reason = limits.may_acquire("AAPL", 500.0)
    assert not second_allowed, "both orders would together double the cap"
    assert "headroom" in reason


def test_the_portfolio_limit_binds_across_instruments():
    limits = limiter()
    limits.update(view(positions={"AAPL": 1900.0, "SPY": 1900.0, "QQQ": 1200.0}))
    assert limits.view.gross_exposure == 5000.0
    assert limits.portfolio_limit() == 5000.0

    allowed, reason = limits.may_acquire("TSLA", 100.0)
    assert not allowed
    assert "headroom" in reason


def test_portfolio_breach_is_reported_separately_from_asset_breach():
    limits = limiter()
    limits.update(view(positions={"AAPL": 3000.0, "SPY": 3000.0}))
    scopes = {b.scope for b in limits.breaches()}
    assert "asset:AAPL" in scopes
    assert "asset:SPY" in scopes
    assert "portfolio" in scopes


# ---------------------------------------------------------------------------
# 6: percentage limits use broker equity, not configured capital
# ---------------------------------------------------------------------------

def test_live_limits_are_taken_against_broker_equity():
    """Sizing against initial_capital after the account moved is wrong."""
    limits = limiter(capital=10_000.0)
    limits.update(view(equity=4_000.0))
    assert limits.capital_base() == 4_000.0
    assert limits.asset_limit() == pytest.approx(800.0), (
        "the limit must shrink with the account, not stay at the configured number"
    )


def test_paper_limits_fall_back_to_configured_capital():
    limits = limiter(live=False, capital=10_000.0)
    limits.update(view(equity=None))
    assert limits.capital_base() == 10_000.0


def test_live_without_broker_equity_has_no_honest_limit():
    limits = limiter()
    limits.update(view(equity=None))

    assert limits.capital_base() is None
    assert limits.asset_limit() is None
    breaches = limits.breaches()
    assert breaches and "no usable capital base" in breaches[0].detail

    allowed, reason = limits.may_acquire("AAPL", 1.0)
    assert not allowed


def test_a_shrinking_account_can_create_a_breach_on_an_unchanged_position():
    limits = limiter(capital=10_000.0)
    limits.update(view(positions={"AAPL": 1500.0}, equity=10_000.0))
    assert limits.breaches() == []

    # Losses elsewhere halve the account. The position did not move.
    limits.update(view(positions={"AAPL": 1500.0}, equity=5_000.0))
    assert limits.breaches(), "1500 is outside 20% of a 5000 account"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_a_nonpositive_notional_is_refused():
    limits = limiter()
    limits.update(view())
    for amount in (0.0, -100.0):
        allowed, reason = limits.may_acquire("AAPL", amount)
        assert not allowed
        assert "non-positive" in reason


def test_every_refusal_explains_itself():
    limits = limiter()
    for v in (view(complete=False), view(reconciled=False),
              view(positions={"AAPL": 9999.0}), view(equity=None)):
        limits.update(v)
        allowed, reason = limits.may_acquire("AAPL", 100.0)
        assert not allowed
        assert reason, "a refusal without a reason is indistinguishable from a bug"


def test_report_serialises_for_the_dashboard():
    limits = limiter()
    limits.update(view(positions={"AAPL": 5000.0}, outstanding={"SPY": 100.0}))
    payload = limits.report()
    assert payload["live"] is True
    assert payload["capital_base"] == 10_000.0
    assert payload["breaches"]
    assert payload["view"]["by_instrument"]["AAPL"]["effective"] == 5000.0
    assert payload["view"]["by_instrument"]["SPY"]["outstanding"] == 100.0


def test_a_clean_view_permits_a_fitting_order():
    limits = limiter()
    limits.update(view(positions={"AAPL": 500.0}))
    allowed, reason = limits.may_acquire("AAPL", 1000.0)
    assert allowed, reason
    assert reason == ""


# ---------------------------------------------------------------------------
# Integration with RiskManager
# ---------------------------------------------------------------------------

def risk_manager(capital=10_000.0):
    from core.risk import RiskConfig, RiskManager
    return RiskManager(RiskConfig(
        initial_capital=capital,
        max_position_concentration=0.20,
        max_portfolio_exposure=0.50,
    ))


def test_risk_manager_without_a_broker_view_keeps_local_behaviour():
    """Backtests and paper runs must not be disturbed."""
    manager = risk_manager()
    assert manager.broker_exposure is None
    assert manager.reserve_exposure("AAPL", 500.0) is True
    assert manager.exposure_breaches() == []


def test_risk_manager_refuses_when_broker_exposure_breaches_the_cap():
    """A manual trade at the broker blocks our next order."""
    manager = risk_manager()
    limits = limiter()
    limits.update(view(positions={"AAPL": 5000.0}))
    manager.attach_broker_exposure(limits)

    assert manager.reserve_exposure("AAPL", 100.0) is False
    assert manager.exposure_breaches(), "the breach must be visible, not just refused"


def test_risk_manager_refuses_when_the_broker_view_is_unreconciled():
    manager = risk_manager()
    limits = limiter()
    limits.update(view(positions={}, reconciled=False))
    manager.attach_broker_exposure(limits)

    assert manager.reserve_exposure("AAPL", 100.0) is False


def test_risk_manager_allows_an_order_that_fits_broker_truth():
    manager = risk_manager()
    limits = limiter()
    limits.update(view(positions={"AAPL": 500.0}))
    manager.attach_broker_exposure(limits)

    assert manager.reserve_exposure("AAPL", 1000.0) is True


def test_local_projection_alone_can_no_longer_grant_permission():
    """The headline change: local belief stopped being the authority.

    Local sees nothing held, so the old code would have allowed this. The
    broker holds a position that consumes the whole cap.
    """
    manager = risk_manager()
    assert manager.asset_exposure == {}, "local believes nothing is held"

    limits = limiter()
    limits.update(view(positions={"AAPL": 2000.0}))
    manager.attach_broker_exposure(limits)

    assert manager.reserve_exposure("AAPL", 500.0) is False, (
        "local emptiness must not authorise an order the broker has no room for"
    )
