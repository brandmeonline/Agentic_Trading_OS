"""Proving an order reduces risk — ATOS-P1-RISK-003.

Invariant:

    Risk-reducing privilege implies non-increasing absolute exposure:

        abs(post_trade_exposure) <= abs(pre_trade_exposure)

"It is a sell, so it reduces risk" is false whenever the sell is larger than
the position, or a second sell against a position already being closed, or
sized against a stale reading. Each of those is a way to acquire exposure
through a path that skips the acquisition checks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.exposure import ExposureView, ReduceOnlyGate  # noqa: E402

pytestmark = pytest.mark.adversarial


def gate(allow_shorting=False, live=True, complete=True, reconciled=True):
    return ReduceOnlyGate(
        ExposureView(complete=complete, reconciled=reconciled),
        allow_shorting=allow_shorting,
        live=live,
    )


# ---------------------------------------------------------------------------
# The genuine cases
# ---------------------------------------------------------------------------

def test_selling_part_of_a_long_reduces():
    d = gate().evaluate("AAPL", "sell", 40.0, held_quantity=100.0)
    assert d.allowed
    assert d.pre_trade_exposure == 100.0
    assert d.post_trade_exposure == 60.0


def test_selling_all_of_a_long_reduces_to_flat():
    d = gate().evaluate("AAPL", "sell", 100.0, held_quantity=100.0)
    assert d.allowed
    assert d.post_trade_exposure == 0.0


def test_buying_back_a_short_reduces():
    d = gate().evaluate("AAPL", "buy", 40.0, held_quantity=-100.0)
    assert d.allowed
    assert d.pre_trade_exposure == 100.0
    assert d.post_trade_exposure == 60.0


def test_the_broker_reduce_only_flag_is_noted():
    d = gate().evaluate("AAPL", "sell", 10.0, held_quantity=100.0,
                        broker_reduce_only=True)
    assert d.allowed
    assert "reduce-only" in d.reason


# ---------------------------------------------------------------------------
# Overselling opens a short
# ---------------------------------------------------------------------------

def test_selling_more_than_is_held_is_refused():
    """The most common accidental short."""
    d = gate().evaluate("AAPL", "sell", 150.0, held_quantity=100.0)
    assert not d.allowed
    assert "would open a short" in d.reason
    assert d.post_trade_exposure == 50.0
    assert d.max_reducible_quantity == 100.0


def test_selling_against_a_flat_book_is_an_opening_trade():
    d = gate().evaluate("AAPL", "sell", 10.0, held_quantity=0.0)
    assert not d.allowed
    assert "would open a position, not reduce one" in d.reason


def test_shorting_enabled_still_routes_through_acquisition_checks():
    """A short is an acquisition; this gate is not where it gets approved."""
    d = gate(allow_shorting=True).evaluate("AAPL", "sell", 150.0,
                                           held_quantity=100.0)
    assert not d.allowed
    assert "acquisition checks" in d.reason


# ---------------------------------------------------------------------------
# Outstanding closes prevent selling the same position twice
# ---------------------------------------------------------------------------

def test_an_outstanding_close_reduces_what_is_still_closeable():
    d = gate().evaluate("AAPL", "sell", 60.0, held_quantity=100.0,
                        outstanding_close_quantity=50.0)
    assert not d.allowed
    assert "50 already working" in d.reason
    assert d.max_reducible_quantity == 50.0


def test_selling_exactly_the_remaining_closeable_quantity_is_allowed():
    d = gate().evaluate("AAPL", "sell", 50.0, held_quantity=100.0,
                        outstanding_close_quantity=50.0)
    assert d.allowed


def test_two_full_closes_would_open_a_short():
    """Each sell looks fine alone; together they flip the position."""
    first = gate().evaluate("AAPL", "sell", 100.0, held_quantity=100.0)
    assert first.allowed

    second = gate().evaluate("AAPL", "sell", 100.0, held_quantity=100.0,
                             outstanding_close_quantity=100.0)
    assert not second.allowed
    assert second.max_reducible_quantity == 0.0


# ---------------------------------------------------------------------------
# Wrong direction
# ---------------------------------------------------------------------------

def test_buying_more_of_a_long_is_not_reducing():
    d = gate().evaluate("AAPL", "buy", 10.0, held_quantity=100.0)
    assert not d.allowed
    assert "increases absolute exposure" in d.reason


def test_selling_more_of_a_short_is_not_reducing():
    d = gate().evaluate("AAPL", "sell", 10.0, held_quantity=-100.0)
    assert not d.allowed
    assert "increases absolute exposure" in d.reason


# ---------------------------------------------------------------------------
# Stale state cannot grant the privilege
# ---------------------------------------------------------------------------

def test_an_incomplete_view_cannot_prove_a_reduction():
    d = gate(complete=False).evaluate("AAPL", "sell", 10.0, held_quantity=100.0)
    assert not d.allowed
    assert "incomplete" in d.reason


def test_an_unreconciled_view_cannot_prove_a_reduction():
    d = gate(reconciled=False).evaluate("AAPL", "sell", 10.0, held_quantity=100.0)
    assert not d.allowed
    assert "not reconciled" in d.reason


def test_paper_mode_does_not_require_reconciliation():
    d = gate(live=False, reconciled=False).evaluate(
        "AAPL", "sell", 10.0, held_quantity=100.0
    )
    assert d.allowed


# ---------------------------------------------------------------------------
# Input validation and the arithmetic invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("quantity", [0.0, -10.0])
def test_a_nonpositive_quantity_is_refused(quantity):
    d = gate().evaluate("AAPL", "sell", quantity, held_quantity=100.0)
    assert not d.allowed


def test_an_unrecognised_side_is_refused():
    d = gate().evaluate("AAPL", "hodl", 10.0, held_quantity=100.0)
    assert not d.allowed
    assert "unrecognised side" in d.reason


def test_every_allowed_decision_satisfies_the_invariant():
    """The arithmetic proof, over a grid of cases."""
    g = gate()
    for held in (-100.0, -1.0, 1.0, 100.0):
        for side in ("buy", "sell"):
            for qty in (0.5, 1.0, 50.0, 100.0, 200.0):
                d = g.evaluate("AAPL", side, qty, held_quantity=held)
                if d.allowed:
                    assert d.post_trade_exposure <= d.pre_trade_exposure + 1e-9, (
                        f"allowed {side} {qty} against {held} increased exposure "
                        f"from {d.pre_trade_exposure} to {d.post_trade_exposure}"
                    )


def test_every_decision_explains_itself():
    g = gate()
    for side, qty, held in (("sell", 10.0, 100.0), ("sell", 999.0, 100.0),
                            ("buy", 10.0, 100.0), ("sell", 10.0, 0.0)):
        d = g.evaluate("AAPL", side, qty, held_quantity=held)
        assert d.reason, "a decision without a reason cannot be audited"


def test_decision_serialises():
    d = gate().evaluate("AAPL", "sell", 40.0, held_quantity=100.0)
    payload = d.to_dict()
    assert payload["allowed"] is True
    assert payload["pre_trade_exposure"] == 100.0
    assert payload["post_trade_exposure"] == 60.0
