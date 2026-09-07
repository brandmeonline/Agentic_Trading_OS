"""Order idempotency and timeout-after-accept — ATOS-P0-EXEC-003.

Invariant:

    Network retry cannot create duplicate economic exposure.

The scenario that motivates all of this: we submit, the broker accepts, and
the response is lost. Locally we know nothing. The wrong move is to retry,
because the broker is already working an order. The right move is to ask the
broker by the client order ID we generated *before* the call and attach to
whatever it says.

The tests below hold the line on both halves: the ID must be usable as an
idempotency key, and the code must refuse to resubmit until the question has
been answered.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import (  # noqa: E402
    AlpacaExecutionAdapter,
    ExecutionConfig,
    ExecutionEngine,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from core.order_intent import OrderIntentJournal  # noqa: E402

pytestmark = pytest.mark.adversarial


class LookupBroker:
    """A broker that can be asked about an order by client order ID.

    ``known`` maps client order ID to the order the broker holds. ``script``
    drives submit_order. ``lookup_error`` makes the question itself fail.
    """

    def __init__(self, script=None, known=None, lookup_error=None):
        self.script = list(script or [])
        self.known = dict(known or {})
        self.lookup_error = lookup_error
        self.submissions = []
        self.lookups = []

    def submit_order(self, order):
        self.submissions.append(order.client_order_id)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def get_order_by_client_order_id(self, client_order_id):
        self.lookups.append(client_order_id)
        if self.lookup_error:
            raise self.lookup_error
        return self.known.get(client_order_id)


def make_engine(broker, tmp_path=None):
    base = Path(tmp_path) if tmp_path else Path(tempfile.mkdtemp())
    return ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=OrderIntentJournal(str(base / "intents.sqlite")),
    )


def an_order(engine, quantity=10.0):
    return engine.create_order("AAPL", OrderSide.BUY, quantity, OrderType.MARKET)


# ---------------------------------------------------------------------------
# The identifier itself
# ---------------------------------------------------------------------------

def test_client_order_id_exists_before_any_network_call():
    order = Order(asset="AAPL", quantity=1.0)
    assert order.client_order_id
    assert order.client_order_id.startswith("atos-")
    assert len(order.client_order_id) > 32, (
        "a short ID risks colliding with another order across restarts"
    )


def test_client_order_ids_do_not_collide():
    ids = {Order(asset="AAPL", quantity=1.0).client_order_id for _ in range(2000)}
    assert len(ids) == 2000


def test_the_broker_is_given_the_stable_id_not_the_short_one():
    """The adapter previously sent order.id, an eight-character slice."""
    class RecordingClient:
        def __init__(self):
            self.placed = []

        def place_order(self, **kwargs):
            self.placed.append(kwargs)
            return {"id": "brk-1", "status": "accepted", "filled_qty": 0,
                    "client_order_id": kwargs["client_order_id"]}

    client = RecordingClient()
    adapter = AlpacaExecutionAdapter(client)
    order = Order(asset="AAPL", quantity=1.0, side=OrderSide.BUY,
                  order_type=OrderType.MARKET)
    adapter.submit_order(order)

    sent = client.placed[0]["client_order_id"]
    assert sent == order.client_order_id
    assert sent != order.id


def test_adapter_surfaces_the_broker_order_id():
    """The engine reads broker_order_id; the adapter only emitted external_order_id."""
    class Client:
        def place_order(self, **kwargs):
            return {"id": "brk-42", "status": "accepted", "filled_qty": 0}

    adapter = AlpacaExecutionAdapter(Client())
    order = Order(asset="AAPL", quantity=1.0, side=OrderSide.BUY,
                  order_type=OrderType.MARKET)
    response = adapter.submit_order(order)
    assert response["broker_order_id"] == "brk-42"
    assert response["external_order_id"] == "brk-42"


# ---------------------------------------------------------------------------
# 1: response lost after the broker accepted
# ---------------------------------------------------------------------------

def test_response_lost_then_lookup_attaches_to_the_existing_order(tmp_path):
    broker = LookupBroker(script=[TimeoutError("response lost")])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN

    # The broker did receive it, and it filled.
    broker.known[order.client_order_id] = {
        "status": "filled", "filled_quantity": 10.0, "avg_fill_price": 100.0,
        "symbol": "AAPL", "side": "buy", "quantity": 10.0, "order_id": "brk-7",
    }
    result = engine.resolve_ambiguous_order(order)

    assert result.success
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10.0
    assert order.broker_order_id == "brk-7"
    assert broker.lookups == [order.client_order_id]
    assert len(broker.submissions) == 1, "resolution must not submit a second order"


def test_lookup_finding_nothing_permits_a_resubmit(tmp_path):
    """Only a positive "no such order" clears the way to submit again."""
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)

    result = engine.resolve_ambiguous_order(order)
    assert not result.success
    assert order.status is OrderStatus.INTENT_PERSISTED
    assert not order.is_ambiguous
    assert "safe to resubmit" in result.message

    # And the resubmit reuses the same client order ID.
    broker.script = [{"status": "filled", "filled_quantity": 10.0,
                      "avg_fill_price": 100.0}]
    engine.submit_order(order)
    assert len(set(broker.submissions)) == 1, "the idempotency key must not change"


def test_a_failed_lookup_is_not_a_negative_answer(tmp_path):
    """"I could not ask" must never be read as "there is nothing there"."""
    broker = LookupBroker(
        script=[TimeoutError("lost")],
        lookup_error=ConnectionError("broker unreachable"),
    )
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)

    result = engine.resolve_ambiguous_order(order)
    assert not result.success
    assert order.status is OrderStatus.UNKNOWN, (
        "a failed question leaves the order exactly as unknown as before"
    )
    assert order.is_active
    assert "not a negative answer" in result.message


# ---------------------------------------------------------------------------
# 2: restart, then look the order up
# ---------------------------------------------------------------------------

def test_restart_then_lookup_by_recovered_client_id(tmp_path):
    """After a crash the ID comes from the journal, not from memory."""
    journal_path = str(tmp_path / "intents.sqlite")
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=OrderIntentJournal(journal_path),
    )
    order = an_order(engine)
    engine.submit_order(order)
    lost_client_id = order.client_order_id
    del engine, order  # the process dies here

    # A new process reads the worklist off disk.
    recovered_journal = OrderIntentJournal(journal_path)
    unresolved = recovered_journal.unresolved_intents()
    assert len(unresolved) == 1
    assert unresolved[0].client_order_id == lost_client_id, (
        "the idempotency key must survive the restart or the order is unresolvable"
    )

    # And it is enough to ask the broker with.
    broker.known[lost_client_id] = {"status": "open", "filled_quantity": 0.0}
    assert broker.get_order_by_client_order_id(lost_client_id) is not None


# ---------------------------------------------------------------------------
# 3: duplicate retry
# ---------------------------------------------------------------------------

def test_resubmitting_an_unknown_order_is_refused(tmp_path):
    """The headline guarantee: no retry while the broker state is unknown."""
    broker = LookupBroker(script=[TimeoutError("lost"), {"status": "filled",
                                                         "filled_quantity": 10.0}])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.UNKNOWN

    result = engine.submit_order(order)
    assert not result.success
    assert "Refusing to resubmit" in result.message
    assert len(broker.submissions) == 1, (
        "a second submission would create a second economic position"
    )


def test_resubmitting_a_terminal_order_is_refused(tmp_path):
    broker = LookupBroker(script=[{"status": "filled", "filled_quantity": 10.0,
                                   "avg_fill_price": 100.0}])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)
    assert order.status is OrderStatus.FILLED

    result = engine.submit_order(order)
    assert not result.success
    assert "already FILLED" in result.message
    assert len(broker.submissions) == 1


def test_resolution_attempts_are_counted_and_persisted(tmp_path):
    journal_path = str(tmp_path / "intents.sqlite")
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=OrderIntentJournal(journal_path),
    )
    order = an_order(engine)
    engine.submit_order(order)

    engine.resolve_ambiguous_order(order)
    engine.resolve_ambiguous_order(order)
    assert order.resolution_attempts == 2

    history = OrderIntentJournal(journal_path).transitions_for(order.client_order_id)
    attempts = [h for h in history if "lookup attempt" in h["reason"]]
    assert len(attempts) == 2, "resolution attempts must be durable, not in-memory only"


# ---------------------------------------------------------------------------
# 4: the same client ID describing a different order
# ---------------------------------------------------------------------------

def test_client_id_resolving_to_a_different_instrument_freezes(tmp_path):
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)

    broker.known[order.client_order_id] = {
        "status": "filled", "filled_quantity": 10.0, "symbol": "TSLA",
        "side": "buy", "quantity": 10.0,
    }
    result = engine.resolve_ambiguous_order(order)

    assert not result.success
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED
    assert "TSLA" in result.message
    assert order.is_active


def test_client_id_resolving_to_a_different_quantity_freezes(tmp_path):
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine, quantity=10.0)
    engine.submit_order(order)

    broker.known[order.client_order_id] = {
        "status": "filled", "filled_quantity": 50.0, "symbol": "AAPL",
        "side": "buy", "quantity": 50.0,
    }
    result = engine.resolve_ambiguous_order(order)
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED
    assert "quantity" in result.message


def test_client_id_resolving_to_the_opposite_side_freezes(tmp_path):
    broker = LookupBroker(script=[TimeoutError("lost")])
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)

    broker.known[order.client_order_id] = {
        "status": "filled", "filled_quantity": 10.0, "symbol": "AAPL",
        "side": "sell", "quantity": 10.0,
    }
    result = engine.resolve_ambiguous_order(order)
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED
    assert "side" in result.message


def test_journal_refuses_a_conflicting_reuse_of_a_client_id(tmp_path):
    """Two different orders must never share an idempotency key."""
    from core.order_intent import IntentPersistenceError, OrderIntent

    journal = OrderIntentJournal(str(tmp_path / "intents.sqlite"))
    journal.record_intent(OrderIntent(
        client_order_id="shared", internal_order_id="a", session_id="s",
        instrument="AAPL", side="buy", quantity=10.0, order_type="market",
    ))
    with pytest.raises(IntentPersistenceError):
        journal.record_intent(OrderIntent(
            client_order_id="shared", internal_order_id="b", session_id="s",
            instrument="TSLA", side="sell", quantity=99.0, order_type="market",
        ))


# ---------------------------------------------------------------------------
# 5: concurrency
# ---------------------------------------------------------------------------

def test_concurrent_submissions_cannot_share_a_client_id(tmp_path):
    import threading

    broker = LookupBroker(script=[{"status": "open", "filled_quantity": 0.0}] * 20)
    engine = make_engine(broker, tmp_path)
    orders = [an_order(engine, quantity=1.0) for _ in range(20)]

    errors = []

    def submit(order):
        try:
            engine.submit_order(order)
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=(o,)) for o in orders]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent submission raised: {errors}"
    assert len(set(broker.submissions)) == 20, "client order IDs were reused"


def test_an_adapter_without_lookup_cannot_silently_resolve(tmp_path):
    """No lookup capability means the order stays unresolved, not assumed safe."""
    class NoLookupBroker:
        def __init__(self):
            self.submissions = []

        def submit_order(self, order):
            self.submissions.append(order.client_order_id)
            raise TimeoutError("lost")

    broker = NoLookupBroker()
    engine = make_engine(broker, tmp_path)
    order = an_order(engine)
    engine.submit_order(order)

    result = engine.resolve_ambiguous_order(order)
    assert not result.success
    assert order.status is OrderStatus.RECONCILIATION_REQUIRED
    assert order.is_active
    assert "Resolve manually" in result.message
