"""Durable order intent journal — ATOS-P0-EXEC-002.

Invariant:

    The ledger knows a real order may exist before the network call that could
    create one.

The crash boundaries below are the six the ULTRAPLAN enumerates. Each simulates
the process dying at a different instant and then asks recovery the only
question that matters: *does anything tell us the broker might be holding an
order?* A boundary passes when the answer is yes and the client order ID is
there to resolve it with.
"""

from __future__ import annotations

import sqlite3
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
from core.order_intent import (  # noqa: E402
    IntentPersistenceError,
    OrderIntent,
    OrderIntentJournal,
)

pytestmark = pytest.mark.adversarial


@pytest.fixture
def journal_path(tmp_path):
    return str(tmp_path / "intents.sqlite")


@pytest.fixture
def journal(journal_path):
    return OrderIntentJournal(journal_path)


class ScriptedBroker:
    def __init__(self, script):
        self.script = list(script)
        self.submissions = []

    def submit_order(self, order):
        self.submissions.append(order.client_order_id)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def live_engine(journal, script):
    broker = ScriptedBroker(script)
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=journal,
    )
    return engine, broker


def an_intent(**overrides):
    base = dict(
        client_order_id="atos-test-1",
        internal_order_id="ord-1",
        session_id="sess-1",
        instrument="AAPL",
        side="buy",
        quantity=10.0,
        order_type="market",
        price=100.0,
    )
    base.update(overrides)
    return OrderIntent(**base)


# ---------------------------------------------------------------------------
# The journal itself
# ---------------------------------------------------------------------------

def test_intent_survives_a_new_process(journal_path):
    """Durability means a different journal object sees the same record."""
    OrderIntentJournal(journal_path).record_intent(an_intent())

    reopened = OrderIntentJournal(journal_path)
    found = reopened.get("atos-test-1")
    assert found is not None
    assert found.quantity == 10.0
    assert found.instrument == "AAPL"
    assert found.status == "intent_persisted"


def test_unresolved_intents_is_the_recovery_worklist(journal):
    journal.record_intent(an_intent(client_order_id="a", internal_order_id="1"))
    journal.record_intent(an_intent(client_order_id="b", internal_order_id="2"))
    journal.record_intent(an_intent(client_order_id="c", internal_order_id="3"))
    journal.record_transition("b", "filled", "broker confirmed")
    journal.record_transition("c", "rejected", "broker refused")

    unresolved = [i.client_order_id for i in journal.unresolved_intents()]
    assert unresolved == ["a"], "only provably terminal orders leave the worklist"


def test_unknown_status_keeps_an_intent_unresolved(journal):
    journal.record_intent(an_intent())
    journal.record_transition("atos-test-1", "unknown", "transport failure")

    assert [i.client_order_id for i in journal.unresolved_intents()] == ["atos-test-1"]
    assert journal.get("atos-test-1").is_unresolved


def test_transitions_are_append_only(journal):
    journal.record_intent(an_intent())
    journal.record_transition("atos-test-1", "submitting", "in flight")
    journal.record_transition("atos-test-1", "partial", "4 filled", filled_quantity=4.0)
    journal.record_transition("atos-test-1", "filled", "complete", filled_quantity=10.0)

    history = journal.transitions_for("atos-test-1")
    assert [h["to_status"] for h in history] == [
        "intent_persisted", "submitting", "partial", "filled",
    ]
    assert history == sorted(history, key=lambda h: h["seq"]), "history must be ordered"
    assert journal.get("atos-test-1").filled_quantity == 10.0


def test_same_client_id_with_different_economics_is_refused(journal):
    """EXEC-003: one client ID must never describe two different orders."""
    journal.record_intent(an_intent(quantity=10.0))
    with pytest.raises(IntentPersistenceError, match="different order"):
        journal.record_intent(an_intent(quantity=25.0))
    assert journal.get("atos-test-1").quantity == 10.0


def test_rerecording_an_identical_intent_is_idempotent(journal):
    """A retry after an ambiguous write must not fail or duplicate."""
    journal.record_intent(an_intent())
    journal.record_intent(an_intent())
    assert len(journal.all_intents()) == 1


def test_transition_for_an_unknown_order_is_refused(journal):
    """A lifecycle event for an order we never wrote down is a real problem."""
    with pytest.raises(IntentPersistenceError, match="never wrote down"):
        journal.record_transition("never-seen", "filled", "phantom")


def test_intent_records_the_risk_context_that_approved_it(journal):
    journal.record_intent(an_intent(
        expected_max_exposure_delta=1000.0,
        risk_approval_hash="cfg-abc123",
        strategy="momentum",
        signal_id="sig-9",
    ))
    found = journal.get("atos-test-1")
    assert found.expected_max_exposure_delta == 1000.0
    assert found.risk_approval_hash == "cfg-abc123"
    assert found.strategy == "momentum"
    assert found.signal_id == "sig-9"


# ---------------------------------------------------------------------------
# Engine integration: no durable intent, no order
# ---------------------------------------------------------------------------

def test_live_submission_requires_a_journal():
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=ScriptedBroker([{"status": "filled", "filled_quantity": 1.0}]),
    )
    order = engine.create_order("AAPL", OrderSide.BUY, 1.0, OrderType.MARKET)
    result = engine.submit_order(order)

    assert not result.success
    assert "intent journal" in result.message
    assert engine.broker_adapter.submissions == [], "nothing may reach the broker"


def test_intent_is_written_before_the_broker_is_called(journal):
    """The journal must be able to prove the order existed first."""
    seen = {}

    class RecordingBroker:
        submissions = []

        def submit_order(self, order):
            # At this instant the WAL must already know about the order.
            seen["at_submit_time"] = journal.get(order.client_order_id)
            return {"status": "filled", "filled_quantity": 5.0, "avg_fill_price": 10.0}

    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=RecordingBroker(),
        intent_journal=journal,
    )
    order = engine.create_order("AAPL", OrderSide.BUY, 5.0, OrderType.MARKET)
    engine.submit_order(order)

    assert seen["at_submit_time"] is not None, (
        "the broker was called before the intent was durable"
    )
    # By the moment of the call the journal has already advanced to
    # "submitting", which is what recovery wants to see: it says a network
    # call was in flight, not merely that an order was contemplated.
    assert seen["at_submit_time"].status == "submitting"
    assert seen["at_submit_time"].quantity == 5.0
    assert seen["at_submit_time"].instrument == "AAPL"


def test_persistence_failure_prevents_submission(journal, monkeypatch):
    """If we cannot write it down, we do not send it."""
    def explode(_intent):
        raise IntentPersistenceError("disk full")

    monkeypatch.setattr(journal, "record_intent", explode)
    engine, broker = live_engine(journal, [{"status": "filled", "filled_quantity": 1.0}])
    order = engine.create_order("AAPL", OrderSide.BUY, 1.0, OrderType.MARKET)
    result = engine.submit_order(order)

    assert not result.success
    assert broker.submissions == [], "no order may be sent when the WAL write failed"
    assert order.status is OrderStatus.REJECTED
    assert engine.intent_persistence_failures == 1
    assert "durably recorded" in result.message


def test_every_lifecycle_transition_reaches_the_journal(journal):
    engine, _ = live_engine(journal, [
        {"status": "partially_filled", "filled_quantity": 4.0, "avg_fill_price": 100.0,
         "order_id": "brk-77"},
    ])
    order = engine.create_order("AAPL", OrderSide.BUY, 10.0, OrderType.MARKET)
    engine.submit_order(order)

    statuses = [h["to_status"] for h in journal.transitions_for(order.client_order_id)]
    assert statuses[0] == "intent_persisted"
    assert "partial" in statuses, f"the fill never reached the journal: {statuses}"

    stored = journal.get(order.client_order_id)
    assert stored.filled_quantity == 4.0
    assert stored.broker_order_id == "brk-77"


# ---------------------------------------------------------------------------
# The six crash boundaries
# ---------------------------------------------------------------------------

def test_crash_1_before_intent_persist_leaves_nothing(journal_path):
    """Nothing was written and nothing was sent, so nothing is owed."""
    journal = OrderIntentJournal(journal_path)
    # The process dies here, before any write.
    recovered = OrderIntentJournal(journal_path)
    assert recovered.unresolved_intents() == []
    assert journal.all_intents() == []


def test_crash_2_after_intent_persist_before_network_call(journal_path):
    """The order may not exist, but recovery must assume it might."""
    OrderIntentJournal(journal_path).record_intent(an_intent())
    # Crash before submit().

    recovered = OrderIntentJournal(journal_path)
    unresolved = recovered.unresolved_intents()
    assert len(unresolved) == 1
    assert unresolved[0].client_order_id == "atos-test-1", (
        "recovery needs the client order ID to ask the broker what happened"
    )
    assert unresolved[0].status == "intent_persisted"


def test_crash_3_broker_accepted_but_response_lost(journal_path):
    journal = OrderIntentJournal(journal_path)
    engine, _ = live_engine(journal, [TimeoutError("response lost")])
    order = engine.create_order("AAPL", OrderSide.BUY, 3.0, OrderType.MARKET)
    engine.submit_order(order)
    # Crash here.

    recovered = OrderIntentJournal(journal_path)
    unresolved = recovered.unresolved_intents()
    assert len(unresolved) == 1
    assert unresolved[0].client_order_id == order.client_order_id
    assert unresolved[0].status == "unknown"


def test_crash_4_after_ack_before_ledger_transition(journal_path):
    """The journal is written first, so an ack is never lost with the ledger."""
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())
    journal.record_transition("atos-test-1", "submitted", "broker acknowledged",
                              broker_order_id="brk-9")
    # Crash before the ledger projection catches up.

    recovered = OrderIntentJournal(journal_path).get("atos-test-1")
    assert recovered.status == "submitted"
    assert recovered.broker_order_id == "brk-9"
    assert recovered.is_unresolved


def test_crash_5_after_partial_fill(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(quantity=10.0))
    journal.record_transition("atos-test-1", "partial", "4 of 10", filled_quantity=4.0)
    # Crash.

    recovered = OrderIntentJournal(journal_path).get("atos-test-1")
    assert recovered.filled_quantity == 4.0
    assert recovered.is_unresolved, "6 units are still live at the broker"


def test_crash_6_during_cancel_race(journal_path):
    """A cancel request is not a cancellation; recovery must still resolve it."""
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())
    journal.record_transition("atos-test-1", "cancel_requested", "cancel sent")
    # Crash before the broker confirmed either way.

    recovered = OrderIntentJournal(journal_path).get("atos-test-1")
    assert recovered.status == "cancel_requested"
    assert recovered.is_unresolved, (
        "a cancel request must not be recovered as a completed cancellation"
    )


# ---------------------------------------------------------------------------
# Storage faults
# ---------------------------------------------------------------------------

def test_a_broken_journal_raises_rather_than_silently_dropping(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"this is not a database")
    with pytest.raises((IntentPersistenceError, sqlite3.DatabaseError)):
        OrderIntentJournal(str(path)).record_intent(an_intent())


def test_journal_rejects_a_nonpositive_quantity(journal):
    with pytest.raises(ValueError):
        journal.record_intent(an_intent(quantity=0.0))


def test_journal_rejects_a_missing_client_order_id(journal):
    with pytest.raises(ValueError):
        journal.record_intent(an_intent(client_order_id=""))


def test_recovery_worklist_survives_many_orders(journal_path):
    journal = OrderIntentJournal(journal_path)
    for n in range(50):
        journal.record_intent(an_intent(
            client_order_id=f"c-{n}", internal_order_id=f"o-{n}"
        ))
        if n % 2 == 0:
            journal.record_transition(f"c-{n}", "filled", "done")

    recovered = OrderIntentJournal(journal_path)
    unresolved = recovered.unresolved_intents()
    assert len(unresolved) == 25
    assert all(i.client_order_id.startswith("c-") for i in unresolved)
    assert unresolved == sorted(unresolved, key=lambda i: i.created_at)


def test_temp_dir_journal_is_usable():
    """Smoke: the default construction path works outside pytest tmp_path."""
    path = Path(tempfile.mkdtemp()) / "nested" / "dir" / "intents.sqlite"
    journal = OrderIntentJournal(str(path))
    journal.record_intent(an_intent())
    assert path.exists(), "the journal must create its parent directories"
