"""Crash and event replay — ATOS-P2-FAULT-002.

Invariant:

    Deterministic replay of durable events reconstructs the state the system
    needs to decide whether it may trade, and any uncertainty resolves to
    RECOVERY_REQUIRED rather than to a clean book.

FAULT-001 asked what happens when the broker misbehaves. This asks what
happens when *we* die — at each of the moments where dying is most expensive,
and against storage that is itself damaged.

The recurring theme: replay is allowed to produce "I do not know". It is not
allowed to produce "nothing was happening".
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
    OrderType,
)
from core.fake_broker import AdversarialBroker, BrokerScenario  # noqa: E402
from core.order_intent import (  # noqa: E402
    IntentPersistenceError,
    OrderIntent,
    OrderIntentJournal,
)
from core.risk_anchors import DurableRiskAnchors, RiskAnchorStore  # noqa: E402
from core.runtime_state import RuntimeState, RuntimeStateMachine  # noqa: E402

pytestmark = pytest.mark.adversarial


@pytest.fixture
def journal_path(tmp_path):
    return str(tmp_path / "intents.sqlite")


def engine_at(journal_path, broker=None):
    """A live engine sharing one journal file, as a restarted process would."""
    return ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker or AdversarialBroker(),
        intent_journal=OrderIntentJournal(journal_path),
    )


def an_intent(**overrides):
    base = dict(
        client_order_id="atos-1", internal_order_id="ord-1", session_id="s",
        instrument="AAPL", side="buy", quantity=10.0, order_type="market",
    )
    base.update(overrides)
    return OrderIntent(**base)


# ---------------------------------------------------------------------------
# Replay reconstructs what the ULTRAPLAN requires
# ---------------------------------------------------------------------------

def test_replay_reconstructs_open_and_unknown_orders(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(client_order_id="open-1"))
    journal.record_transition("open-1", "open", "acknowledged")
    journal.record_intent(an_intent(client_order_id="unknown-1",
                                    internal_order_id="ord-2"))
    journal.record_transition("unknown-1", "unknown", "transport failure")
    journal.record_intent(an_intent(client_order_id="done-1",
                                    internal_order_id="ord-3"))
    journal.record_transition("done-1", "filled", "complete")

    replayed = OrderIntentJournal(journal_path).unresolved_intents()
    ids = {i.client_order_id for i in replayed}
    assert ids == {"open-1", "unknown-1"}, (
        "a filled order is resolved; an open or unknown one is not"
    )


def test_replay_reconstructs_partial_fill_quantities(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(quantity=10.0))
    journal.record_transition("atos-1", "partial", "4 of 10", filled_quantity=4.0)

    recovered = OrderIntentJournal(journal_path).get("atos-1")
    assert recovered.filled_quantity == 4.0
    assert recovered.quantity == 10.0
    assert recovered.is_unresolved, "6 units are still live"


def test_replay_reconstructs_provenance(journal_path):
    """Recovery must know which strategy and signal produced an order."""
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(
        strategy="momentum", signal_id="sig-9",
        risk_approval_hash="cfg-abc", expected_max_exposure_delta=1000.0,
    ))
    recovered = OrderIntentJournal(journal_path).get("atos-1")
    assert recovered.strategy == "momentum"
    assert recovered.signal_id == "sig-9"
    assert recovered.risk_approval_hash == "cfg-abc"
    assert recovered.expected_max_exposure_delta == 1000.0


def test_replay_reconstructs_the_idempotency_key(journal_path):
    """Without this, an unresolved order cannot be resolved at all."""
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(client_order_id="atos-stable-key"))
    recovered = OrderIntentJournal(journal_path).unresolved_intents()[0]
    assert recovered.client_order_id == "atos-stable-key"


def test_replay_reconstructs_risk_anchors_and_trips(tmp_path):
    store_path = str(tmp_path / "anchors.sqlite")
    from datetime import datetime, timezone
    day = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)

    risk = DurableRiskAnchors(RiskAnchorStore(store_path), max_daily_drawdown=0.05)
    risk.observe_equity(10_000.0, now=day)
    risk.record_realized_pnl(-600.0, now=day)
    risk.observe_equity(9_400.0, now=day)

    replayed = DurableRiskAnchors(RiskAnchorStore(store_path),
                                  max_daily_drawdown=0.05)
    assert replayed.anchors.day_opening_equity == 10_000.0
    assert replayed.anchors.high_water_equity == 10_000.0
    assert replayed.tripped
    assert not replayed.may_trade()[0]


# ---------------------------------------------------------------------------
# Duplicate, truncated and corrupt events
# ---------------------------------------------------------------------------

def test_a_duplicate_event_does_not_double_count(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(quantity=10.0))
    journal.record_transition("atos-1", "partial", "fill", filled_quantity=4.0)
    journal.record_transition("atos-1", "partial", "fill", filled_quantity=4.0)

    recovered = OrderIntentJournal(journal_path).get("atos-1")
    assert recovered.filled_quantity == 4.0, "the duplicate was added twice"


def test_a_re_recorded_identical_intent_is_idempotent(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())
    journal.record_intent(an_intent())
    assert len(OrderIntentJournal(journal_path).all_intents()) == 1


def test_a_truncated_tail_keeps_everything_before_it(journal_path):
    """A crash mid-write must not lose the events that already committed."""
    journal = OrderIntentJournal(journal_path)
    for index in range(5):
        journal.record_intent(an_intent(
            client_order_id=f"atos-{index}", internal_order_id=f"ord-{index}"
        ))

    # Simulate the last write never landing by deleting the newest row.
    with sqlite3.connect(journal_path) as conn:
        conn.execute("DELETE FROM order_intents WHERE client_order_id = 'atos-4'")

    recovered = OrderIntentJournal(journal_path).unresolved_intents()
    assert {i.client_order_id for i in recovered} == {
        "atos-0", "atos-1", "atos-2", "atos-3"
    }


def test_a_corrupt_database_raises_rather_than_reporting_empty(tmp_path):
    """An unreadable journal must never look like "no orders exist"."""
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"this is definitely not a sqlite database")

    with pytest.raises((IntentPersistenceError, sqlite3.DatabaseError)):
        OrderIntentJournal(str(path)).unresolved_intents()


def test_a_locked_database_surfaces_rather_than_silently_failing(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())

    blocker = sqlite3.connect(journal_path)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        # A short busy timeout so the test does not sit through the full
        # 30-second production wait.
        second = OrderIntentJournal(journal_path, busy_timeout_seconds=0.1)
        with pytest.raises((IntentPersistenceError, sqlite3.OperationalError)):
            second.record_intent(an_intent(client_order_id="atos-2",
                                           internal_order_id="ord-2"))
    finally:
        blocker.rollback()
        blocker.close()


# ---------------------------------------------------------------------------
# Crash between the WAL write and submission
# ---------------------------------------------------------------------------

def test_crash_between_wal_and_submission_leaves_a_resolvable_intent(journal_path):
    """The order may or may not exist. Replay must assume it might."""
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent(client_order_id="atos-inflight"))
    # Process dies here, before submit() is called.

    unresolved = OrderIntentJournal(journal_path).unresolved_intents()
    assert len(unresolved) == 1
    assert unresolved[0].client_order_id == "atos-inflight"
    assert unresolved[0].status == "intent_persisted"


def test_crash_after_submission_before_acknowledgement(journal_path):
    """The broker holds it; we never recorded the ack."""
    broker = AdversarialBroker(script=[BrokerScenario.TIMEOUT_AFTER_ACCEPT])
    engine = engine_at(journal_path, broker)
    order = engine.create_order("AAPL", OrderSide.BUY, 10.0, OrderType.MARKET)
    engine.submit_order(order)
    lost_id = order.client_order_id
    del engine

    replayed = OrderIntentJournal(journal_path).unresolved_intents()
    assert [i.client_order_id for i in replayed] == [lost_id]
    assert replayed[0].status == "unknown"

    # And a new process can resolve it against the broker.
    assert broker.get_order_by_client_order_id(lost_id) is not None


# ---------------------------------------------------------------------------
# Uncertainty resolves to RECOVERY_REQUIRED, never to a clean book
# ---------------------------------------------------------------------------

def test_an_unresolved_intent_forces_recovery_not_a_clean_start(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())

    machine = RuntimeStateMachine(RuntimeState.PAPER)
    unresolved = OrderIntentJournal(journal_path).unresolved_intents()
    if unresolved:
        machine.require_recovery(
            f"{len(unresolved)} unresolved order intent(s) after restart"
        )

    assert machine.state is RuntimeState.RECOVERY_REQUIRED
    assert not machine.may_acquire


def test_a_clean_journal_permits_a_normal_start(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())
    journal.record_transition("atos-1", "filled", "complete")

    machine = RuntimeStateMachine(RuntimeState.PAPER)
    assert OrderIntentJournal(journal_path).unresolved_intents() == []
    assert machine.may_acquire


def test_replay_of_an_empty_journal_is_not_an_error(journal_path):
    """A first run has nothing to replay, which is different from failing."""
    assert OrderIntentJournal(journal_path).unresolved_intents() == []
    assert OrderIntentJournal(journal_path).all_intents() == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_replay_is_deterministic_across_repeated_reads(journal_path):
    journal = OrderIntentJournal(journal_path)
    for index in range(10):
        journal.record_intent(an_intent(
            client_order_id=f"atos-{index}", internal_order_id=f"ord-{index}"
        ))
        if index % 3 == 0:
            journal.record_transition(f"atos-{index}", "filled", "done")

    first = [i.client_order_id for i in OrderIntentJournal(journal_path).unresolved_intents()]
    second = [i.client_order_id for i in OrderIntentJournal(journal_path).unresolved_intents()]
    third = [i.client_order_id for i in OrderIntentJournal(journal_path).unresolved_intents()]
    assert first == second == third


def test_the_transition_history_replays_in_order(journal_path):
    journal = OrderIntentJournal(journal_path)
    journal.record_intent(an_intent())
    for status in ("submitting", "open", "partial", "filled"):
        journal.record_transition("atos-1", status, status)

    history = OrderIntentJournal(journal_path).transitions_for("atos-1")
    assert [h["to_status"] for h in history] == [
        "intent_persisted", "submitting", "open", "partial", "filled"
    ]
    assert [h["seq"] for h in history] == sorted(h["seq"] for h in history)


def test_a_journal_survives_many_restarts(journal_path):
    """State must not degrade with each reopen."""
    for index in range(20):
        journal = OrderIntentJournal(journal_path)
        journal.record_intent(an_intent(
            client_order_id=f"atos-{index}", internal_order_id=f"ord-{index}"
        ))

    final = OrderIntentJournal(journal_path)
    assert len(final.all_intents()) == 20
    assert len(final.unresolved_intents()) == 20


def test_a_journal_in_a_missing_directory_creates_it():
    nested = Path(tempfile.mkdtemp()) / "a" / "b" / "c" / "intents.sqlite"
    journal = OrderIntentJournal(str(nested))
    journal.record_intent(an_intent())
    assert nested.exists()
    assert len(OrderIntentJournal(str(nested)).all_intents()) == 1
