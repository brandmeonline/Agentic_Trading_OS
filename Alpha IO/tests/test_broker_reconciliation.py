"""Broker reconciliation — ATOS-P0-REC-002.

Invariant:

    Broker truth is authoritative over local bookkeeping, and any disagreement
    stops new risk.

The tests are organised around the ten mismatch classes the ULTRAPLAN names,
plus the two rules that are easy to get wrong: an incomplete snapshot is a
mismatch rather than a pass, and adopting broker truth leaves an audit event
behind.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.reconciliation import (  # noqa: E402
    BrokerSnapshot,
    LocalSnapshot,
    MismatchClass,
    ReconciliationEngine,
)

pytestmark = pytest.mark.adversarial

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
ACCOUNT = "acct-fingerprint-abc"


def engine(**kwargs):
    kwargs.setdefault("expected_account_fingerprint", ACCOUNT)
    return ReconciliationEngine(**kwargs)


def local(**overrides):
    base = dict(account_fingerprint=ACCOUNT, taken_at=NOW, cash=10_000.0,
                positions={}, open_orders={}, unknown_orders={})
    base.update(overrides)
    return LocalSnapshot(**base)


def broker(**overrides):
    base = dict(account_fingerprint=ACCOUNT, taken_at=NOW, cash=10_000.0,
                positions={}, open_orders={})
    base.update(overrides)
    return BrokerSnapshot(**base)


def reconcile(local_snap=None, broker_snap=None, eng=None, now=NOW):
    return (eng or engine()).reconcile(
        local_snap or local(), broker_snap or broker(), now=now
    )


# ---------------------------------------------------------------------------
# The matched case
# ---------------------------------------------------------------------------

def test_identical_state_matches():
    report = reconcile(
        local(positions={"AAPL": 10.0}, cash=5000.0),
        broker(positions={"AAPL": 10.0}, cash=5000.0),
    )
    assert report.matched
    assert report.may_acquire
    assert report.mismatches == []
    assert report.summary() == "MATCHED"


def test_quantities_within_tolerance_match():
    report = reconcile(
        local(positions={"AAPL": 10.0}),
        broker(positions={"AAPL": 10.0 + 1e-9}),
    )
    assert report.may_acquire


# ---------------------------------------------------------------------------
# ACCOUNT_ID_MISMATCH — reconciling against the wrong book
# ---------------------------------------------------------------------------

def test_a_different_account_is_a_mismatch():
    """A clean comparison against the wrong account would read as MATCHED."""
    report = reconcile(broker_snap=broker(account_fingerprint="someone-elses"))
    assert not report.may_acquire
    assert report.of_kind(MismatchClass.ACCOUNT_ID_MISMATCH)
    assert report.requires_operator


def test_a_missing_account_fingerprint_is_a_mismatch():
    report = reconcile(broker_snap=broker(account_fingerprint=""))
    assert not report.may_acquire
    assert report.of_kind(MismatchClass.ACCOUNT_ID_MISMATCH)


# ---------------------------------------------------------------------------
# INCOMPLETE_BROKER_SNAPSHOT — absence of evidence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", [
    "account_complete", "positions_complete",
    "open_orders_complete", "fills_complete",
])
def test_an_incomplete_snapshot_never_matches(flag):
    """A fetch that failed proves nothing, however empty the result looks."""
    report = reconcile(broker_snap=broker(**{flag: False}))
    assert not report.may_acquire, (
        f"{flag} was False yet reconciliation reported agreement"
    )
    assert report.of_kind(MismatchClass.INCOMPLETE_BROKER_SNAPSHOT)


def test_an_empty_but_incomplete_position_fetch_does_not_prove_flat():
    """The exact way a system convinces itself it holds nothing."""
    report = reconcile(
        local(positions={"AAPL": 25.0}),
        broker(positions={}, positions_complete=False),
    )
    assert not report.may_acquire
    kinds = {m.kind for m in report.mismatches}
    assert MismatchClass.INCOMPLETE_BROKER_SNAPSHOT in kinds
    assert MismatchClass.POSITION_MISMATCH in kinds


def test_a_stale_snapshot_is_a_mismatch():
    old = broker(taken_at=NOW - timedelta(minutes=30))
    report = reconcile(broker_snap=old)
    assert not report.may_acquire
    assert report.of_kind(MismatchClass.INCOMPLETE_BROKER_SNAPSHOT)
    assert report.snapshot_age_seconds == pytest.approx(1800)


def test_a_fresh_snapshot_within_the_age_limit_is_fine():
    fresh = broker(taken_at=NOW - timedelta(seconds=30))
    assert reconcile(broker_snap=fresh).may_acquire


# ---------------------------------------------------------------------------
# Order mismatches
# ---------------------------------------------------------------------------

def test_an_order_the_broker_has_and_we_do_not():
    """Somebody traded this account, or one of our orders escaped our books."""
    report = reconcile(
        broker_snap=broker(open_orders={"atos-ghost": {"quantity": 5.0, "side": "buy"}})
    )
    assert not report.may_acquire
    found = report.of_kind(MismatchClass.UNKNOWN_BROKER_ORDER)
    assert found and found[0].subject == "atos-ghost"
    assert report.requires_operator


def test_an_order_we_have_and_the_broker_does_not():
    report = reconcile(
        local(open_orders={"atos-1": {"quantity": 5.0, "side": "buy"}})
    )
    assert not report.may_acquire
    found = report.of_kind(MismatchClass.MISSING_BROKER_ORDER)
    assert found and found[0].subject == "atos-1"


def test_an_unknown_local_order_always_reconciles_as_unresolved():
    report = reconcile(
        local(unknown_orders={"atos-lost": {"quantity": 3.0}}),
        broker(open_orders={"atos-lost": {"quantity": 3.0}}),
    )
    assert not report.may_acquire
    found = report.of_kind(MismatchClass.UNKNOWN_BROKER_ORDER)
    assert found and found[0].subject == "atos-lost"
    assert found[0].broker_value is not None, "both sides must be recorded"


def test_same_client_id_different_quantity():
    report = reconcile(
        local(open_orders={"atos-1": {"quantity": 5.0, "side": "buy", "symbol": "AAPL"}}),
        broker(open_orders={"atos-1": {"quantity": 50.0, "side": "buy", "symbol": "AAPL"}}),
    )
    found = report.of_kind(MismatchClass.QUANTITY_MISMATCH)
    assert found and "quantity differs" in found[0].detail


def test_same_client_id_different_side():
    report = reconcile(
        local(open_orders={"atos-1": {"quantity": 5.0, "side": "buy"}}),
        broker(open_orders={"atos-1": {"quantity": 5.0, "side": "sell"}}),
    )
    found = report.of_kind(MismatchClass.QUANTITY_MISMATCH)
    assert found and "side differs" in found[0].detail


def test_a_duplicated_client_id_is_flagged():
    """A retry created a second economic order."""
    report = reconcile(
        local(open_orders={"atos-1": {"quantity": 5.0}}),
        broker(open_orders={"atos-1": {"quantity": 5.0}},
               duplicate_client_ids=["atos-1"]),
    )
    found = report.of_kind(MismatchClass.DUPLICATE_CLIENT_ID)
    assert found and "duplicate exposure" in found[0].detail
    assert report.requires_operator


# ---------------------------------------------------------------------------
# POSITION_MISMATCH
# ---------------------------------------------------------------------------

def test_a_position_the_broker_has_and_we_do_not():
    report = reconcile(broker_snap=broker(positions={"TSLA": 40.0}))
    found = report.of_kind(MismatchClass.POSITION_MISMATCH)
    assert found and found[0].subject == "TSLA"
    assert found[0].local_value == 0.0
    assert found[0].broker_value == 40.0
    assert report.requires_operator


def test_a_position_we_have_and_the_broker_does_not():
    report = reconcile(local(positions={"TSLA": 40.0}))
    found = report.of_kind(MismatchClass.POSITION_MISMATCH)
    assert found and found[0].broker_value == 0.0


def test_a_short_against_a_long_is_a_mismatch():
    report = reconcile(
        local(positions={"AAPL": 10.0}),
        broker(positions={"AAPL": -10.0}),
    )
    found = report.of_kind(MismatchClass.POSITION_MISMATCH)
    assert found and "-20" in found[0].detail


# ---------------------------------------------------------------------------
# CASH_MISMATCH and CAPITAL_LIMIT_BREACH
# ---------------------------------------------------------------------------

def test_cash_within_tolerance_is_fine():
    assert reconcile(local(cash=1000.0), broker(cash=1000.005)).may_acquire


def test_cash_beyond_tolerance_is_a_mismatch():
    report = reconcile(local(cash=1000.0), broker(cash=850.0))
    found = report.of_kind(MismatchClass.CASH_MISMATCH)
    assert found and found[0].broker_value == 850.0
    assert not report.may_acquire
    assert not report.requires_operator, "cash alone is not an operator escalation"


def test_broker_exposure_above_the_capital_tier_is_a_breach():
    """Capital that appeared without our asking."""
    report = reconcile(
        local(capital_tier_limit=10.0),
        broker(positions={"AAPL": 40.0}),
    )
    found = report.of_kind(MismatchClass.CAPITAL_LIMIT_BREACH)
    assert found
    assert found[0].broker_value == 40.0
    assert report.requires_operator


def test_exposure_within_the_tier_is_not_a_breach():
    report = reconcile(
        local(capital_tier_limit=100.0, positions={"AAPL": 40.0}),
        broker(positions={"AAPL": 40.0}),
    )
    assert report.of_kind(MismatchClass.CAPITAL_LIMIT_BREACH) == []


# ---------------------------------------------------------------------------
# Corrections are events
# ---------------------------------------------------------------------------

def test_adopting_broker_positions_records_an_event():
    local_snap = local(positions={"AAPL": 0.0})
    broker_snap = broker(positions={"AAPL": 40.0})
    eng = engine()
    report = eng.reconcile(local_snap, broker_snap, now=NOW)

    corrections = eng.apply_position_corrections(
        local_snap, broker_snap, report, reason="startup reconciliation"
    )

    assert local_snap.positions["AAPL"] == 40.0
    assert len(corrections) == 1
    event = corrections[0]
    assert event["before"] == 0.0
    assert event["after"] == 40.0
    assert event["delta"] == 40.0
    assert event["reason"] == "startup reconciliation"
    assert report.corrections == corrections, (
        "the report must carry the corrections for persistence"
    )


def test_a_correction_without_a_reason_is_refused():
    local_snap = local(positions={"AAPL": 0.0})
    broker_snap = broker(positions={"AAPL": 40.0})
    eng = engine()
    report = eng.reconcile(local_snap, broker_snap, now=NOW)
    with pytest.raises(ValueError):
        eng.apply_position_corrections(local_snap, broker_snap, report, reason="")


def test_correcting_does_not_retroactively_make_the_report_matched():
    """The fact that a position was wrong outlives the corrected number."""
    local_snap = local(positions={"AAPL": 0.0})
    broker_snap = broker(positions={"AAPL": 40.0})
    eng = engine()
    report = eng.reconcile(local_snap, broker_snap, now=NOW)
    eng.apply_position_corrections(local_snap, broker_snap, report, reason="fix")

    assert not report.may_acquire, (
        "this reconciliation found a break; the next one has to prove agreement"
    )
    assert report.of_kind(MismatchClass.POSITION_MISMATCH)


def test_a_second_reconciliation_after_correction_matches():
    local_snap = local(positions={"AAPL": 0.0})
    broker_snap = broker(positions={"AAPL": 40.0})
    eng = engine()
    first = eng.reconcile(local_snap, broker_snap, now=NOW)
    eng.apply_position_corrections(local_snap, broker_snap, first, reason="fix")

    second = eng.reconcile(local_snap, broker_snap, now=NOW)
    assert second.may_acquire


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_multiple_mismatches_are_all_reported():
    report = reconcile(
        local(positions={"AAPL": 10.0}, cash=1000.0,
              open_orders={"atos-1": {"quantity": 1.0}}),
        broker(positions={"TSLA": 5.0}, cash=2000.0,
               open_orders={"atos-ghost": {"quantity": 2.0}}),
    )
    kinds = {m.kind for m in report.mismatches}
    assert MismatchClass.POSITION_MISMATCH in kinds
    assert MismatchClass.CASH_MISMATCH in kinds
    assert MismatchClass.MISSING_BROKER_ORDER in kinds
    assert MismatchClass.UNKNOWN_BROKER_ORDER in kinds
    assert not report.may_acquire


def test_report_serialises_for_the_dashboard_and_alerts():
    report = reconcile(broker_snap=broker(positions={"TSLA": 40.0}))
    payload = report.to_dict()
    assert payload["matched"] is False
    assert payload["may_acquire"] is False
    assert payload["requires_operator"] is True
    assert "position_mismatch" in payload["summary"]
    assert payload["mismatches"][0]["broker_value"] == 40.0


def test_every_mismatch_class_is_reachable():
    """A class nothing can produce is a gap in the engine, not a spare."""
    produced = set()

    produced.update(m.kind for m in reconcile(
        broker_snap=broker(account_fingerprint="other")).mismatches)
    produced.update(m.kind for m in reconcile(
        broker_snap=broker(positions_complete=False)).mismatches)
    produced.update(m.kind for m in reconcile(
        broker_snap=broker(open_orders={"g": {"quantity": 1.0}})).mismatches)
    produced.update(m.kind for m in reconcile(
        local(open_orders={"m": {"quantity": 1.0}})).mismatches)
    produced.update(m.kind for m in reconcile(
        local(positions={"A": 1.0})).mismatches)
    produced.update(m.kind for m in reconcile(
        local(cash=1.0), broker(cash=999.0)).mismatches)
    produced.update(m.kind for m in reconcile(
        local(open_orders={"q": {"quantity": 1.0, "side": "buy"}}),
        broker(open_orders={"q": {"quantity": 9.0, "side": "buy"}})).mismatches)
    produced.update(m.kind for m in reconcile(
        local(open_orders={"d": {"quantity": 1.0}}),
        broker(open_orders={"d": {"quantity": 1.0}},
               duplicate_client_ids=["d"])).mismatches)
    produced.update(m.kind for m in reconcile(
        local(capital_tier_limit=1.0), broker(positions={"A": 99.0})).mismatches)

    expected = set(MismatchClass) - {MismatchClass.FILL_MISMATCH}
    assert expected <= produced, f"unreachable: {expected - produced}"


def test_fill_mismatch_class_exists_for_the_recurring_engine():
    """Documented gap: fill-level comparison arrives with ATOS-P2-REC-001.

    The class is defined so the recurring reconciler can raise it without a
    schema change, and this test records that it is not yet produced rather
    than letting the omission pass unnoticed.
    """
    report = reconcile(
        local(fills=[{"client_order_id": "a", "quantity": 1.0}]),
        broker(recent_fills=[{"client_order_id": "a", "quantity": 5.0}]),
    )
    assert report.of_kind(MismatchClass.FILL_MISMATCH) == []
