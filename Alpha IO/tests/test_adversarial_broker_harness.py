"""Adversarial broker harness — ATOS-P2-FAULT-001.

Every scenario in the ULTRAPLAN's list is driven through the real
ExecutionEngine, and each one asserts the same four properties:

1. no untracked exposure;
2. no duplicate order;
3. the reservation stays conservative while state is unknown;
4. acquisition is frozen on material uncertainty.

The point is not that each individual behaviour is correct - the earlier
issues tested those. The point is that the *whole set* holds simultaneously,
so a fix for one failure mode has not quietly broken another.
"""

from __future__ import annotations

import sys
import tempfile
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
from core.fake_broker import (  # noqa: E402
    AdversarialBroker,
    BrokerScenario,
    assert_no_untracked_exposure,
    cancel_outcome,
)
from core.order_intent import OrderIntentJournal  # noqa: E402

pytestmark = pytest.mark.adversarial


def engine_for(broker, tmp_path=None):
    base = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    return ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=OrderIntentJournal(str(base / "intents.sqlite")),
    )


def submit(engine, quantity=10.0, symbol="AAPL"):
    order = engine.create_order(symbol, OrderSide.BUY, quantity, OrderType.MARKET)
    return order, engine.submit_order(order)


# ---------------------------------------------------------------------------
# Every scenario, one invariant set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", list(BrokerScenario))
def test_every_scenario_preserves_the_core_invariants(scenario, tmp_path):
    """The whole ULTRAPLAN fault list, against the same four assertions."""
    broker = AdversarialBroker(script=[scenario])
    engine = engine_for(broker, tmp_path)

    order, result = submit(engine)

    violations = assert_no_untracked_exposure(engine, broker)
    assert not violations, f"{scenario.value}: " + "; ".join(violations)

    # Whatever happened, the order is in a state the system can describe.
    assert isinstance(order.status, OrderStatus)
    assert order.transitions, "no lifecycle transition was recorded"

    # And it never claims success while ambiguous.
    if order.is_ambiguous:
        assert not result.success
        assert order.is_active


@pytest.mark.parametrize("scenario", list(BrokerScenario))
def test_no_scenario_submits_twice(scenario, tmp_path):
    broker = AdversarialBroker(script=[scenario])
    engine = engine_for(broker, tmp_path)
    submit(engine)
    assert len(broker.submissions) == len(set(broker.submissions))
    assert len(broker.submissions) <= 1


# ---------------------------------------------------------------------------
# The dangerous one, in detail
# ---------------------------------------------------------------------------

def test_timeout_after_accept_leaves_a_resolvable_order(tmp_path):
    """The broker holds a real order and we never heard about it."""
    broker = AdversarialBroker(script=[BrokerScenario.TIMEOUT_AFTER_ACCEPT])
    engine = engine_for(broker, tmp_path)
    order, result = submit(engine)

    assert not result.success
    assert order.status is OrderStatus.UNKNOWN
    assert order.is_active, "the reservation was released on a live order"
    assert broker.state.silently_accepted == [order.client_order_id]

    # And it is resolvable by the ID we generated before the call.
    resolution = engine.resolve_ambiguous_order(order)
    assert broker.lookups == [order.client_order_id]
    assert order.status is OrderStatus.OPEN
    assert resolution.success


def test_timeout_before_accept_is_resolvable_as_absent(tmp_path):
    broker = AdversarialBroker(script=[BrokerScenario.TIMEOUT_BEFORE_ACCEPT])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)
    assert order.status is OrderStatus.UNKNOWN

    engine.resolve_ambiguous_order(order)
    assert order.status is OrderStatus.INTENT_PERSISTED, (
        "the broker positively reported no such order"
    )
    assert len(broker.submissions) == 1


def test_a_lost_response_never_produces_a_second_order(tmp_path):
    """The failure this whole plan exists to prevent."""
    broker = AdversarialBroker(script=[BrokerScenario.TIMEOUT_AFTER_ACCEPT])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)

    # A caller that does not know better tries again.
    retry = engine.submit_order(order)
    assert not retry.success
    assert "Refusing to resubmit" in retry.message
    assert len(broker.submissions) == 1, "a duplicate economic order was created"


# ---------------------------------------------------------------------------
# Transport and HTTP faults
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", [
    BrokerScenario.RATE_LIMITED,
    BrokerScenario.SERVER_ERROR,
    BrokerScenario.AUTH_FAILURE,
])
def test_http_errors_do_not_become_rejections(scenario, tmp_path):
    """A 429 is not the broker saying no; it is the broker saying nothing."""
    broker = AdversarialBroker(script=[scenario])
    engine = engine_for(broker, tmp_path)
    order, result = submit(engine)

    assert not result.success
    assert order.status is OrderStatus.UNKNOWN, (
        f"{scenario.value} was treated as a definitive answer"
    )
    assert order.is_active
    assert engine.rejected_orders == 0


def test_malformed_json_does_not_imply_acceptance(tmp_path):
    broker = AdversarialBroker(script=[BrokerScenario.MALFORMED_JSON])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)
    assert order.filled_quantity == 0.0
    assert order.is_active


def test_an_unknown_status_stays_unknown(tmp_path):
    broker = AdversarialBroker(script=[BrokerScenario.UNKNOWN_STATUS])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)
    assert order.status is OrderStatus.UNKNOWN
    assert order.is_active


def test_a_missing_order_id_still_tracks_the_order(tmp_path):
    broker = AdversarialBroker(script=[BrokerScenario.MISSING_ORDER_ID])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)
    assert order.is_active
    assert order.client_order_id, "the client ID is ours and always exists"


# ---------------------------------------------------------------------------
# Venue rejections are real rejections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", [
    BrokerScenario.INSUFFICIENT_BUYING_POWER,
    BrokerScenario.MARKET_CLOSED,
    BrokerScenario.HALTED,
    BrokerScenario.QUANTITY_PRECISION_REJECTED,
    BrokerScenario.NOTIONAL_INVALID,
])
def test_venue_rejections_are_terminal_and_carry_no_exposure(scenario, tmp_path):
    broker = AdversarialBroker(script=[scenario])
    engine = engine_for(broker, tmp_path)
    order, result = submit(engine)

    assert not result.success
    assert order.status is OrderStatus.REJECTED
    assert not order.is_active, "a rejected order holds no exposure"
    assert order.filled_quantity == 0.0


# ---------------------------------------------------------------------------
# The cancel race, end to end
# ---------------------------------------------------------------------------

def test_a_fill_after_a_cancel_request_is_applied(tmp_path):
    broker = AdversarialBroker(script=[
        BrokerScenario.OPEN_ORDER,
        BrokerScenario.FILL_AFTER_CANCEL_REQUEST,
    ])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine, quantity=10.0)

    assert engine.cancel_order(order.id) is True
    assert order.status is OrderStatus.CANCEL_REQUESTED
    assert order.is_active

    engine.confirm_cancellation(
        order.id,
        cancel_outcome(BrokerScenario.FILL_AFTER_CANCEL_REQUEST, 10.0),
    )
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10.0


def test_a_cancel_with_a_partial_fill_keeps_the_filled_part(tmp_path):
    broker = AdversarialBroker(script=[
        BrokerScenario.OPEN_ORDER,
        BrokerScenario.CANCELED_WITH_PARTIAL_FILL,
    ])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine, quantity=10.0)
    engine.cancel_order(order.id)

    engine.confirm_cancellation(
        order.id,
        cancel_outcome(BrokerScenario.CANCELED_WITH_PARTIAL_FILL, 10.0),
    )
    assert order.status is OrderStatus.CANCELLED
    assert order.filled_quantity == 5.0


# ---------------------------------------------------------------------------
# External interference
# ---------------------------------------------------------------------------

def test_a_position_appearing_behind_our_back_is_visible(tmp_path):
    """Somebody traded the account. Reconciliation has to be able to see it."""
    broker = AdversarialBroker(script=[BrokerScenario.POSITION_CHANGED_EXTERNALLY])
    engine = engine_for(broker, tmp_path)
    submit(engine, quantity=10.0)

    broker_positions = {p["symbol"]: p["qty"] for p in broker.get_positions()}
    assert broker_positions["AAPL"] == 999.0
    assert engine.get_position("AAPL") != broker_positions["AAPL"], (
        "the local projection cannot see an external trade, which is why "
        "reconciliation reads the broker"
    )


# ---------------------------------------------------------------------------
# Sequences, not just single faults
# ---------------------------------------------------------------------------

def test_a_run_of_faults_leaves_no_untracked_exposure(tmp_path):
    """Faults compose. A fix for one must not break another."""
    script = [
        BrokerScenario.RATE_LIMITED,
        BrokerScenario.TIMEOUT_AFTER_ACCEPT,
        BrokerScenario.PARTIAL_FILL,
        BrokerScenario.SERVER_ERROR,
        BrokerScenario.UNKNOWN_STATUS,
        BrokerScenario.FULL_FILL,
    ]
    broker = AdversarialBroker(script=script)
    engine = engine_for(broker, tmp_path)

    for index in range(len(script)):
        submit(engine, quantity=1.0, symbol=f"SYM{index}")

    violations = assert_no_untracked_exposure(engine, broker)
    assert not violations, "; ".join(violations)
    assert len(broker.submissions) == len(set(broker.submissions))


def test_the_harness_itself_detects_untracked_exposure(tmp_path):
    """Guard the guard: the invariant checker must be able to fail."""
    broker = AdversarialBroker(script=[BrokerScenario.TIMEOUT_AFTER_ACCEPT])
    engine = engine_for(broker, tmp_path)
    order, _ = submit(engine)

    assert assert_no_untracked_exposure(engine, broker) == []

    # Simulate the bug the invariant exists to catch: an ambiguous order
    # quietly marked terminal, releasing its reservation.
    order.status = OrderStatus.CANCELLED
    violations = assert_no_untracked_exposure(engine, broker)
    assert violations, "the checker cannot see a released reservation"
    assert "releasing its reservation" in violations[0]


def test_the_scenario_list_covers_the_ultraplan():
    """Every fault the plan names must be scriptable."""
    names = {s.value for s in BrokerScenario}
    required = {
        "full_fill", "open_order", "partial_fill",
        "fill_after_cancel_request", "canceled_with_partial_fill",
        "timeout_before_accept", "timeout_after_accept", "duplicate_response",
        "stale_status", "out_of_order_event", "rate_limited", "server_error",
        "malformed_json", "missing_order_id", "unknown_status",
        "insufficient_buying_power", "market_closed", "halted",
        "quantity_precision_rejected", "notional_invalid", "auth_failure",
        "account_mismatch", "position_changed_externally", "stream_disconnect",
    }
    assert required <= names, f"missing scenarios: {required - names}"
