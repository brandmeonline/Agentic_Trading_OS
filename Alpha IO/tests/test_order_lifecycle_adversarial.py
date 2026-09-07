"""Adversarial order lifecycle tests — ATOS-P0-EXEC-001.

Core invariant under test:

    Every economically live remainder remains represented as exposure until
    broker truth proves a terminal state.

The scenarios below are the twelve the ULTRAPLAN enumerates for EXEC-001, plus
the monotonicity rules of the state machine itself. Each one asks the same
question in a different way: after this failure, does the system still believe
the broker might be holding an order for us?

Marked ``adversarial`` so CI can select the suite (ATOS-P2-CI-001).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import (  # noqa: E402
    AMBIGUOUS_ORDER_STATES,
    BrokerRejection,
    ExecutionConfig,
    ExecutionEngine,
    InvalidOrderTransition,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from core.order_intent import OrderIntentJournal  # noqa: E402

pytestmark = pytest.mark.adversarial


class ScriptedBroker:
    """A broker adapter that replays a fixed script of responses.

    Each entry is either a payload dict to return or an exception to raise.
    It records what it was asked to submit so tests can assert that no
    duplicate order was created.
    """

    def __init__(self, script):
        self.script = list(script)
        self.submissions = []
        self.cancels = []

    def submit_order(self, order):
        self.submissions.append(order.client_order_id)
        if not self.script:
            raise AssertionError("broker asked for more responses than scripted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def cancel_order(self, order_id):
        self.cancels.append(order_id)
        return True


def engine_with(script, journal=True):
    """A live-mode engine wired to a scripted broker.

    Live mode requires a durable intent journal (ATOS-P0-EXEC-002), so one is
    provided on a throwaway path unless a test is specifically exercising its
    absence.
    """
    broker = ScriptedBroker(script)
    intent_journal = None
    if journal:
        intent_journal = OrderIntentJournal(
            str(Path(tempfile.mkdtemp()) / "intents.sqlite")
        )
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=intent_journal,
    )
    return engine, broker


def new_order(engine, quantity=10.0):
    return engine.create_order(
        asset="AAPL",
        side=OrderSide.BUY,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


# ---------------------------------------------------------------------------
# 1-4: ordinary lifecycles that must still work
# ---------------------------------------------------------------------------

def test_immediate_full_fill():
    engine, _ = engine_with([
        {"status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.0,
         "order_id": "brk-1"},
    ])
    order = new_order(engine)
    result = engine.submit_order(order)

    assert result.success
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10.0
    assert order.remaining_quantity == 0.0
    assert order.broker_order_id == "brk-1"
    assert not order.is_active


def test_accepted_but_not_yet_filled_stays_live():
    engine, _ = engine_with([{"status": "open", "filled_quantity": 0.0, "order_id": "brk-2"}])
    order = new_order(engine)
    result = engine.submit_order(order)

    assert result.success
    assert order.status is OrderStatus.OPEN
    assert order.is_active, "an open order still carries exposure"
    assert order.remaining_quantity == 10.0


def test_partial_then_full_accumulates_without_double_counting():
    engine, _ = engine_with([{"status": "partially_filled", "filled_quantity": 4.0,
                              "avg_fill_price": 100.0, "order_id": "brk-3"}])
    order = new_order(engine)
    engine.submit_order(order)

    assert order.status is OrderStatus.PARTIAL
    assert order.filled_quantity == 4.0
    assert order.remaining_quantity == 6.0
    assert order.is_active

    # The rest arrives as a later lifecycle event carrying a cumulative total.
    engine._apply_live_execution_response(
        order, {"status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.5}
    )
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10.0, "cumulative total, not 4 + 10"
    assert order.remaining_quantity == 0.0


def test_partial_then_canceled_keeps_the_filled_part():
    engine, _ = engine_with([{"status": "partially_filled", "filled_quantity": 3.0,
                              "avg_fill_price": 100.0, "order_id": "brk-4"}])
    order = new_order(engine)
    engine.submit_order(order)

    engine._apply_live_execution_response(
        order, {"status": "canceled", "filled_quantity": 3.0, "avg_fill_price": 100.0}
    )
    assert order.status is OrderStatus.CANCELLED
    assert order.filled_quantity == 3.0, "the executed part does not vanish on cancel"
    assert not order.is_active


# ---------------------------------------------------------------------------
# 5-6: the timeout pair — the defect this issue exists for
# ---------------------------------------------------------------------------

def test_timeout_before_acceptance_is_unknown_not_rejected():
    """A lost response is not proof the broker refused the order."""
    engine, _ = engine_with([TimeoutError("read timed out")])
    order = new_order(engine)
    result = engine.submit_order(order)

    assert not result.success
    assert order.status is OrderStatus.UNKNOWN, (
        "calling a timeout REJECTED would release the reservation while the "
        "broker may be working a live order"
    )
    assert order.is_active, "an unknown order still carries exposure"
    assert order.is_ambiguous
    assert "reconcile" in result.message.lower()
    assert engine.rejected_orders == 0, "an unknown order is not a rejection"


def test_timeout_after_acceptance_leaves_exposure_tracked():
    """The broker accepted; the response was lost. Exposure must survive."""
    engine, broker = engine_with([ConnectionError("connection reset by peer")])
    order = new_order(engine)
    engine.submit_order(order)

    assert order.status is OrderStatus.UNKNOWN
    assert order.is_active

    # Reconciliation later finds the order really was filled.
    order.reconcile_to(OrderStatus.FILLED, "broker lookup by client order ID")
    assert order.status is OrderStatus.FILLED
    assert any("RECONCILIATION" in t["reason"] for t in order.transitions), (
        "a broker-authoritative correction must leave an explicit event"
    )
    assert len(broker.submissions) == 1, "no duplicate submission"


def test_explicit_broker_rejection_is_a_real_rejection():
    """Only a positive assertion from the adapter proves no exposure."""
    engine, _ = engine_with([BrokerRejection("insufficient buying power")])
    order = new_order(engine)
    result = engine.submit_order(order)

    assert not result.success
    assert order.status is OrderStatus.REJECTED
    assert not order.is_active
    assert engine.rejected_orders == 1


# ---------------------------------------------------------------------------
# 7-8: unreadable broker answers
# ---------------------------------------------------------------------------

def test_malformed_broker_response_does_not_imply_acceptance():
    engine, _ = engine_with([{"unexpected": "shape", "no_status": True}])
    order = new_order(engine)
    engine.submit_order(order)

    # No status field at all falls back to the default, which is an
    # acknowledgement claim we have not earned unless the payload says so.
    assert order.status in {OrderStatus.SUBMITTED, OrderStatus.UNKNOWN}
    assert order.is_active
    assert order.filled_quantity == 0.0


def test_unknown_broker_status_stays_unknown():
    """The old default mapped anything unfamiliar to SUBMITTED."""
    engine, _ = engine_with([{"status": "quantum_superposition", "filled_quantity": 0.0}])
    order = new_order(engine)
    engine.submit_order(order)

    assert order.status is OrderStatus.UNKNOWN
    assert order.is_ambiguous
    assert order.is_active


def test_none_status_is_not_silently_accepted():
    engine, _ = engine_with([{"status": None, "filled_quantity": 0.0}])
    order = new_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN


# ---------------------------------------------------------------------------
# 9-10: redelivery and reordering
# ---------------------------------------------------------------------------

def test_duplicate_lifecycle_event_is_idempotent():
    engine, _ = engine_with([{"status": "partially_filled", "filled_quantity": 5.0,
                              "avg_fill_price": 100.0}])
    order = new_order(engine)
    engine.submit_order(order)

    volume_after_first = engine.total_volume
    position_after_first = engine.get_position("AAPL")

    # The broker redelivers the identical event.
    engine._apply_live_execution_response(
        order, {"status": "partially_filled", "filled_quantity": 5.0, "avg_fill_price": 100.0}
    )

    assert order.filled_quantity == 5.0
    assert engine.total_volume == volume_after_first, "duplicate must not add volume"
    assert engine.get_position("AAPL") == position_after_first, (
        "duplicate must not move the position twice"
    )


def test_out_of_order_event_cannot_rewind_cumulative_fill():
    engine, _ = engine_with([{"status": "partially_filled", "filled_quantity": 7.0,
                              "avg_fill_price": 100.0}])
    order = new_order(engine)
    engine.submit_order(order)

    # A stale event from earlier in the sequence arrives late.
    engine._apply_live_execution_response(
        order, {"status": "partially_filled", "filled_quantity": 2.0, "avg_fill_price": 100.0}
    )

    assert order.filled_quantity == 7.0, "a late event must not unwind known fill"
    assert order.remaining_quantity == 3.0
    assert any("stale event ignored" in t["reason"] for t in order.transitions)


# ---------------------------------------------------------------------------
# 11-12: broker truth contradicts local belief
# ---------------------------------------------------------------------------

def test_overfill_freezes_instead_of_clamping():
    """The old code clamped with min(), hiding exposure above the intent."""
    engine, _ = engine_with([{"status": "filled", "filled_quantity": 15.0,
                              "avg_fill_price": 100.0}])
    order = new_order(engine, quantity=10.0)
    result = engine.submit_order(order)

    assert not result.success
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED
    assert order.filled_quantity == 15.0, (
        "the real broker quantity must be recorded, not clamped to the request"
    )
    assert order.is_overfilled
    assert order.is_active, "a frozen order still carries exposure"
    assert "reconciliation" in result.message.lower()


def test_rejected_after_partial_fill_is_a_reconciliation_error():
    engine, _ = engine_with([{"status": "partially_filled", "filled_quantity": 4.0,
                              "avg_fill_price": 100.0}])
    order = new_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.PARTIAL

    result = engine._apply_live_execution_response(
        order, {"status": "rejected", "filled_quantity": 4.0}
    )

    assert not result.success
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED, (
        "a broker cannot un-fill 4 shares by declaring the order rejected"
    )
    assert order.filled_quantity == 4.0


# ---------------------------------------------------------------------------
# State machine rules
# ---------------------------------------------------------------------------

def test_terminal_states_do_not_move_backwards():
    order = Order(asset="AAPL", quantity=1.0)
    order.transition_to(OrderStatus.SUBMITTED, "ack")
    order.transition_to(OrderStatus.FILLED, "fill")
    for backwards in (OrderStatus.OPEN, OrderStatus.PARTIAL, OrderStatus.SUBMITTED):
        with pytest.raises(InvalidOrderTransition):
            order.transition_to(backwards, "impossible")
    assert order.status is OrderStatus.FILLED


def test_refused_transition_is_still_audited():
    order = Order(asset="AAPL", quantity=1.0)
    order.transition_to(OrderStatus.SUBMITTED, "ack")
    order.transition_to(OrderStatus.FILLED, "fill")
    with pytest.raises(InvalidOrderTransition):
        order.transition_to(OrderStatus.OPEN, "impossible")
    assert any(t["reason"].startswith("REFUSED") for t in order.transitions), (
        "a refused transition is evidence and must be recorded"
    )


def test_reconciliation_correction_requires_a_reason():
    order = Order(asset="AAPL", quantity=1.0)
    with pytest.raises(ValueError):
        order.reconcile_to(OrderStatus.FILLED, "")


def test_every_ambiguous_state_counts_as_active():
    for status in AMBIGUOUS_ORDER_STATES:
        order = Order(asset="AAPL", quantity=1.0)
        order.status = status
        assert order.is_active, f"{status.name} must retain exposure"
        assert order.is_ambiguous


def test_local_expiry_cannot_retire_an_ambiguous_order():
    """A local clock must not turn "we do not know" into a terminal state."""
    from datetime import datetime, timedelta

    engine, _ = engine_with([TimeoutError("lost")])
    order = new_order(engine)
    order.expires_at = datetime.now() - timedelta(seconds=1)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN

    expired = engine.check_expired_orders()
    assert order not in expired
    assert order.status is OrderStatus.UNKNOWN, (
        "an unknown order stays unknown until the broker resolves it"
    )
    assert order.is_active


def test_client_order_id_is_stable_and_collision_resistant():
    """EXEC-003 depends on this ID being generated before any network call."""
    ids = {Order(asset="AAPL", quantity=1.0).client_order_id for _ in range(500)}
    assert len(ids) == 500, "client order IDs must not collide"

    order = Order(asset="AAPL", quantity=1.0)
    original = order.client_order_id
    order.transition_to(OrderStatus.SUBMITTED, "ack")
    order.mark_ambiguous("timeout")
    assert order.client_order_id == original, "the ID must survive the lifecycle"
    assert len(original) > 32, "short IDs risk collision across restarts"


def test_a_retry_reuses_the_same_client_order_id():
    """No retry may create a second economic order."""
    engine, broker = engine_with([TimeoutError("lost"), {"status": "open", "filled_quantity": 0.0}])
    order = new_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN

    # A caller that retries the same Order object must not mint a new ID.
    engine._execute_live(order)
    assert len(set(broker.submissions)) == 1, (
        "the broker saw two different client order IDs; that is a duplicate order"
    )
