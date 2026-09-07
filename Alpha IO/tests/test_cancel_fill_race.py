"""Cancel/fill race — ATOS-P0-EXEC-004.

Invariant:

    cancel requested != canceled.

Between asking the broker to cancel and hearing back, the order is still live
on the exchange. It can fill entirely, fill partially, or be refused
cancellation because it already filled. Whoever the matching engine serves
first wins, and it is not us. So the only safe reading of a cancel request is:
nothing has changed yet.

Every test here asks whether the system keeps carrying the exposure across
that window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import (  # noqa: E402
    ExecutionConfig,
    ExecutionEngine,
    OrderSide,
    OrderStatus,
    OrderType,
)
from core.order_intent import OrderIntentJournal  # noqa: E402

pytestmark = pytest.mark.adversarial


class CancelBroker:
    """Broker whose cancel behaviour is scripted independently of submission."""

    def __init__(self, submit_response=None, cancel_result=True, cancel_error=None):
        self.submit_response = submit_response or {
            "status": "open", "filled_quantity": 0.0, "order_id": "brk-1",
        }
        self.cancel_result = cancel_result
        self.cancel_error = cancel_error
        self.submissions = []
        self.cancel_calls = []

    def submit_order(self, order):
        self.submissions.append(order.client_order_id)
        return self.submit_response

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        if self.cancel_error:
            raise self.cancel_error
        return self.cancel_result


def live_engine(broker, tmp_path):
    return ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=OrderIntentJournal(str(Path(tmp_path) / "intents.sqlite")),
    )


def open_order(engine, quantity=10.0):
    order = engine.create_order("AAPL", OrderSide.BUY, quantity, OrderType.MARKET)
    engine.submit_order(order)
    return order


# ---------------------------------------------------------------------------
# The request is not the outcome
# ---------------------------------------------------------------------------

def test_cancel_request_does_not_cancel_the_order(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)

    accepted = engine.cancel_order(order.id)

    assert accepted is True, "the request was accepted for processing"
    assert order.status is OrderStatus.CANCEL_REQUESTED
    assert order.status is not OrderStatus.CANCELLED
    assert order.is_active, "the order can still fill; the reservation stays"


def test_reservation_is_held_across_the_cancel_window(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine, quantity=10.0)
    engine.cancel_order(order.id)

    assert order.remaining_quantity == 10.0
    assert order in engine.get_active_orders()
    assert engine.has_material_ambiguity(), (
        "an outstanding cancel is unresolved state; new acquisition must be blocked"
    )


def test_confirmation_is_what_retires_the_order(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)
    engine.cancel_order(order.id)

    engine.confirm_cancellation(order.id, {"status": "canceled", "filled_quantity": 0.0})

    assert order.status is OrderStatus.CANCELLED
    assert not order.is_active
    assert not engine.has_material_ambiguity()


# ---------------------------------------------------------------------------
# The race itself
# ---------------------------------------------------------------------------

def test_a_fill_landing_after_the_cancel_request_is_applied(tmp_path):
    """The exchange filled it before our cancel arrived. That fill is real."""
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine, quantity=10.0)
    engine.cancel_order(order.id)
    assert order.status is OrderStatus.CANCEL_REQUESTED

    engine.confirm_cancellation(order.id, {
        "status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.0,
    })

    assert order.status is OrderStatus.FILLED, (
        "a cancel request cannot un-fill an order the exchange already matched"
    )
    assert order.filled_quantity == 10.0
    assert engine.get_position("AAPL") == 10.0


def test_a_partial_fill_after_the_cancel_request_is_kept(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine, quantity=10.0)
    engine.cancel_order(order.id)

    engine.confirm_cancellation(order.id, {
        "status": "canceled", "filled_quantity": 4.0, "avg_fill_price": 100.0,
    })

    assert order.status is OrderStatus.CANCELLED
    assert order.filled_quantity == 4.0, "the executed part survives the cancellation"
    assert engine.get_position("AAPL") == 4.0
    assert not order.is_active, "the unfilled 6 are genuinely gone"


def test_broker_refusing_the_cancel_leaves_the_order_unproven(tmp_path):
    """A refusal usually means it already filled — but we do not know that yet."""
    broker = CancelBroker(cancel_result=False)
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)

    accepted = engine.cancel_order(order.id)

    assert accepted is False
    assert order.is_ambiguous, "a refused cancel is not a confirmed live order"
    assert order.is_active
    assert engine.has_material_ambiguity()


def test_cancel_transport_failure_marks_the_order_unknown(tmp_path):
    """We do not know whether the broker even received the request."""
    broker = CancelBroker(cancel_error=TimeoutError("cancel timed out"))
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)

    accepted = engine.cancel_order(order.id)

    assert accepted is False
    assert order.status is OrderStatus.UNKNOWN
    assert order.is_active, "an unknown order keeps its exposure"
    assert "cancel request raised" in (order.ambiguity_reason or "")


def test_a_duplicate_cancel_request_is_harmless(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)

    engine.cancel_order(order.id)
    engine.cancel_order(order.id)

    assert order.status is OrderStatus.CANCEL_REQUESTED
    assert order.is_active
    # The second request is still forwarded - cancelling twice is safe - but
    # it must not corrupt the lifecycle.
    assert order.transitions[-1]["to"] == OrderStatus.CANCEL_REQUESTED.value


def test_cancelling_an_ambiguous_order_is_refused(tmp_path):
    """We cannot reason about cancelling what we cannot see."""
    class LostBroker(CancelBroker):
        def submit_order(self, order):
            self.submissions.append(order.client_order_id)
            raise TimeoutError("lost")

    broker = LostBroker()
    engine = live_engine(broker, tmp_path)
    order = engine.create_order("AAPL", OrderSide.BUY, 10.0, OrderType.MARKET)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN

    assert engine.cancel_order(order.id) is False
    assert broker.cancel_calls == [], "no cancel may be sent for an unseen order"
    assert order.status is OrderStatus.UNKNOWN


def test_cancelling_a_terminal_order_is_a_no_op(tmp_path):
    broker = CancelBroker(submit_response={
        "status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.0,
    })
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)
    assert order.status is OrderStatus.FILLED

    assert engine.cancel_order(order.id) is False
    assert broker.cancel_calls == []
    assert order.status is OrderStatus.FILLED


# ---------------------------------------------------------------------------
# Visibility for the risk engine
# ---------------------------------------------------------------------------

def test_pending_cancellations_are_enumerable(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    first = open_order(engine)
    second = open_order(engine)
    engine.cancel_order(first.id)

    pending = engine.orders_pending_cancellation()
    assert [o.id for o in pending] == [first.id]
    assert second.status is OrderStatus.OPEN


def test_ambiguous_orders_are_enumerable_for_the_risk_engine(tmp_path):
    broker = CancelBroker(cancel_error=TimeoutError("lost"))
    engine = live_engine(broker, tmp_path)
    order = open_order(engine)
    engine.cancel_order(order.id)

    ambiguous = engine.ambiguous_orders()
    assert [o.id for o in ambiguous] == [order.id]
    assert engine.has_material_ambiguity()


def test_no_ambiguity_when_everything_is_resolved(tmp_path):
    broker = CancelBroker(submit_response={
        "status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.0,
    })
    engine = live_engine(broker, tmp_path)
    open_order(engine)
    assert not engine.has_material_ambiguity()
    assert engine.ambiguous_orders() == []


def test_cancel_lifecycle_reaches_the_durable_journal(tmp_path):
    journal_path = str(Path(tmp_path) / "intents.sqlite")
    journal = OrderIntentJournal(journal_path)
    broker = CancelBroker()
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=journal,
    )
    order = open_order(engine)
    engine.cancel_order(order.id)

    recovered = OrderIntentJournal(journal_path).get(order.client_order_id)
    assert recovered.status == "cancel_requested"
    assert recovered.is_unresolved, (
        "a crash mid-cancel must not recover as a completed cancellation"
    )


def test_simulation_cancels_immediately_because_there_is_no_race():
    engine = ExecutionEngine(config=ExecutionConfig(simulation_mode=True))
    engine.set_price("AAPL", 100.0)
    order = engine.create_order("AAPL", OrderSide.BUY, 10.0, OrderType.LIMIT, price=1.0)
    engine.submit_order(order)
    if order.is_active:
        assert engine.cancel_order(order.id) is True
        assert order.status is OrderStatus.CANCELLED


def test_confirm_cancellation_for_an_unknown_order_id(tmp_path):
    broker = CancelBroker()
    engine = live_engine(broker, tmp_path)
    result = engine.confirm_cancellation("no-such-order", {"status": "canceled"})
    assert not result.success
    assert "No such order" in result.message
