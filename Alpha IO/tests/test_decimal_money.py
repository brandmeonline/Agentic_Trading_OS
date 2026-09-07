"""Decimal boundary — ATOS-P1-NUM-001.

Invariant:

    Money and quantity are exact where capital is decided.

The tests below are mostly demonstrations that float is wrong in the specific
ways that matter here, followed by proof that this module is not.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.money import (  # noqa: E402
    InstrumentGrid,
    MoneyError,
    add,
    as_float,
    coerce_legacy,
    from_float,
    notional,
    to_decimal,
    to_str,
    weighted_average,
)

pytestmark = pytest.mark.adversarial


# ---------------------------------------------------------------------------
# Why this module exists
# ---------------------------------------------------------------------------

def test_float_addition_is_wrong_and_decimal_is_not():
    assert 0.1 + 0.2 != 0.3, "the premise of this module"
    assert add("0.1", "0.2") == Decimal("0.3")


def test_a_hundred_small_fills_sum_exactly():
    """Accumulated fill error becomes cash error becomes a wrong limit."""
    float_total = sum(0.01 for _ in range(100))
    assert float_total != 1.0

    assert add(*["0.01"] * 100) == Decimal("1.00")


def test_cost_basis_does_not_drift_across_partial_fills():
    fills = [("0.1", "100.10")] * 30
    average = weighted_average(fills)
    assert average == Decimal("100.10"), "thirty partials must not move the basis"


# ---------------------------------------------------------------------------
# Refusing uncontrolled floats
# ---------------------------------------------------------------------------

def test_a_raw_float_is_refused():
    with pytest.raises(MoneyError, match="refusing to build a Decimal"):
        to_decimal(0.1)


def test_the_refusal_explains_the_alternative():
    with pytest.raises(MoneyError) as excinfo:
        to_decimal(1.23, field="price")
    assert "price" in str(excinfo.value)
    assert "exact source" in str(excinfo.value)


def test_strings_and_integers_are_exact():
    assert to_decimal("1.23") == Decimal("1.23")
    assert to_decimal(5) == Decimal(5)
    assert to_decimal(Decimal("2.50")) == Decimal("2.50")


def test_a_boolean_is_not_money():
    with pytest.raises(MoneyError):
        to_decimal(True)


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1.2.3"])
def test_junk_strings_are_refused(bad):
    with pytest.raises(MoneyError):
        to_decimal(bad)


def test_from_float_does_not_expand_the_binary_error():
    """Decimal(0.1) is worse than the float: it looks exact."""
    assert str(Decimal(0.1)).startswith("0.1000000000000000055")
    assert from_float(0.1) == Decimal("0.1")


def test_from_float_quantizes_so_the_error_stops_here():
    assert from_float(1 / 3, places=4) == Decimal("0.3333")


def test_from_float_rejects_a_non_number():
    with pytest.raises(MoneyError):
        from_float("1.5")


# ---------------------------------------------------------------------------
# The venue grid
# ---------------------------------------------------------------------------

def test_prices_quantize_to_the_tick():
    grid = InstrumentGrid.of("AAPL", tick_size="0.01")
    assert grid.quantize_price("100.234") == Decimal("100.23")
    assert grid.quantize_price("100.236") == Decimal("100.24")


def test_quantities_round_down_never_up():
    """Rounding up would submit more exposure than was asked for."""
    grid = InstrumentGrid.of("BTC/USD", lot_size="0.001")
    assert grid.quantize_quantity("1.0009") == Decimal("1.000")
    assert grid.quantize_quantity("1.9999") == Decimal("1.999")


def test_a_coarse_lot_size_rounds_down_to_whole_units():
    grid = InstrumentGrid.of("AAPL", lot_size="1")
    assert grid.quantize_quantity("9.99") == Decimal("9")


def test_tolerance_comes_from_the_grid_not_an_epsilon():
    """The same difference is equal on one venue and not on another."""
    coarse = InstrumentGrid.of("AAPL", lot_size="1")
    fine = InstrumentGrid.of("BTC/USD", lot_size="0.00000001")

    assert coarse.quantities_equal("10", "10.4"), "sub-lot on a whole-share venue"
    assert not fine.quantities_equal("10", "10.4")


def test_prices_equal_within_one_tick():
    grid = InstrumentGrid.of("AAPL", tick_size="0.01")
    assert grid.prices_equal("100.00", "100.005")
    assert not grid.prices_equal("100.00", "100.02")


# ---------------------------------------------------------------------------
# Venue acceptance
# ---------------------------------------------------------------------------

def test_an_off_grid_quantity_is_not_tradeable():
    grid = InstrumentGrid.of("AAPL", lot_size="1", tick_size="0.01")
    ok, reason = grid.is_tradeable("1.5", "100.00")
    assert not ok
    assert "lot size" in reason


def test_an_off_tick_price_is_not_tradeable():
    grid = InstrumentGrid.of("AAPL", lot_size="1", tick_size="0.01")
    ok, reason = grid.is_tradeable("1", "100.005")
    assert not ok
    assert "tick size" in reason


def test_below_minimum_notional_is_not_tradeable():
    grid = InstrumentGrid.of("BTC/USD", lot_size="0.001", tick_size="0.01",
                             min_notional="10")
    ok, reason = grid.is_tradeable("0.001", "100.00")
    assert not ok
    assert "notional" in reason


def test_below_minimum_quantity_is_not_tradeable():
    grid = InstrumentGrid.of("BTC/USD", lot_size="0.001", min_quantity="0.01")
    ok, reason = grid.is_tradeable("0.001", "100.00")
    assert not ok
    assert "minimum" in reason


def test_a_conforming_order_is_tradeable():
    grid = InstrumentGrid.of("AAPL", lot_size="1", tick_size="0.01",
                             min_notional="1")
    ok, reason = grid.is_tradeable("10", "100.00")
    assert ok, reason


def test_a_nonpositive_quantity_is_not_tradeable():
    grid = InstrumentGrid.of("AAPL")
    ok, _ = grid.is_tradeable("0", "100.00")
    assert not ok


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_notional_is_exact():
    assert notional("3", "0.1") == Decimal("0.3")


def test_weighted_average_of_nothing_is_none():
    assert weighted_average([]) is None
    assert weighted_average([("0", "100")]) is None


def test_weighted_average_across_different_prices():
    assert weighted_average([("1", "100"), ("1", "200")]) == Decimal("150")


def test_weighted_average_is_quantity_weighted():
    assert weighted_average([("3", "100"), ("1", "200")]) == Decimal("125")


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def test_serialisation_avoids_exponent_notation():
    """A value stored as 1E-8 is a parsing problem waiting to happen."""
    assert "E" not in to_str(Decimal("0.00000001"))
    assert to_str(Decimal("0.00000001")) == "0.00000001"


def test_serialisation_round_trips():
    for text in ("0.1", "100.00", "0.00000001", "123456789.123456"):
        assert to_decimal(to_str(to_decimal(text))) == to_decimal(text)


def test_integral_values_serialise_without_trailing_zeros():
    assert to_str(Decimal("100.00")) == "100"


# ---------------------------------------------------------------------------
# The migration seam
# ---------------------------------------------------------------------------

def test_coerce_legacy_accepts_a_float():
    assert coerce_legacy(0.1) == Decimal("0.1")


def test_coerce_legacy_prefers_exactness_when_offered():
    assert coerce_legacy("0.1") == Decimal("0.1")
    assert coerce_legacy(5) == Decimal(5)


def test_as_float_is_available_for_analytics():
    assert as_float(Decimal("1.5")) == 1.5


# ---------------------------------------------------------------------------
# The grid replaces the epsilon in reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_uses_the_venue_grid_when_it_knows_one():
    """A sub-lot difference on a whole-share venue is not a break."""
    from datetime import datetime, timezone

    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine

    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    account = "acct-1"

    def snapshots(local_qty, broker_qty):
        return (
            LocalSnapshot(account_fingerprint=account, taken_at=now,
                          positions={"AAPL": local_qty}),
            BrokerSnapshot(account_fingerprint=account, taken_at=now,
                           positions={"AAPL": broker_qty}),
        )

    # Without a grid, the tight float tolerance calls this a mismatch.
    without = ReconciliationEngine(expected_account_fingerprint=account)
    local, broker = snapshots(10.0, 10.4)
    assert not without.reconcile(local, broker, now=now).may_acquire

    # With a whole-share grid, the venue cannot express 0.4 of a share.
    with_grid = ReconciliationEngine(
        expected_account_fingerprint=account,
        grids={"AAPL": InstrumentGrid.of("AAPL", lot_size="1")},
    )
    local, broker = snapshots(10.0, 10.4)
    assert with_grid.reconcile(local, broker, now=now).may_acquire


def test_a_real_break_is_still_a_break_on_the_grid():
    from datetime import datetime, timezone

    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine

    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    account = "acct-1"
    engine = ReconciliationEngine(
        expected_account_fingerprint=account,
        grids={"AAPL": InstrumentGrid.of("AAPL", lot_size="1")},
    )
    report = engine.reconcile(
        LocalSnapshot(account_fingerprint=account, taken_at=now,
                      positions={"AAPL": 10.0}),
        BrokerSnapshot(account_fingerprint=account, taken_at=now,
                       positions={"AAPL": 40.0}),
        now=now,
    )
    assert not report.may_acquire


def test_a_fine_grid_catches_what_a_coarse_one_forgives():
    from datetime import datetime, timezone

    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine

    now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
    account = "acct-1"
    engine = ReconciliationEngine(
        expected_account_fingerprint=account,
        grids={"BTC/USD": InstrumentGrid.of("BTC/USD", lot_size="0.00000001")},
    )
    report = engine.reconcile(
        LocalSnapshot(account_fingerprint=account, taken_at=now,
                      positions={"BTC/USD": 1.0}),
        BrokerSnapshot(account_fingerprint=account, taken_at=now,
                       positions={"BTC/USD": 1.4}),
        now=now,
    )
    assert not report.may_acquire, "0.4 BTC is a real break"
