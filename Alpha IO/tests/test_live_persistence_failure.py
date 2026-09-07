"""Persistence failure policy — ATOS-P1-PERSIST-001.

Invariant:

    A critical write that fails in live mode stops new risk.

Two questions run through these tests. When a write that safety depends on
fails, does anything notice? And does the caller find out, or does it carry on
believing the write landed?
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.persistence_policy import (  # noqa: E402
    CRITICAL_WRITES,
    NON_CRITICAL_WRITES,
    CriticalWriteFailed,
    PersistencePolicy,
    WriteKind,
    is_critical,
)
from core.runtime_state import RuntimeState, RuntimeStateMachine  # noqa: E402

pytestmark = pytest.mark.adversarial


def live_policy(machine=None):
    machine = machine or RuntimeStateMachine(RuntimeState.LIVE_ACTIVE)
    return PersistencePolicy(live=True, freeze=lambda r: machine.freeze(r)), machine


def paper_policy():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    return PersistencePolicy(live=False, freeze=lambda r: machine.freeze(r)), machine


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_the_critical_list_matches_the_ultraplan():
    assert {k.value for k in CRITICAL_WRITES} == {
        "order_intent", "lifecycle_transition", "fill", "reservation_change",
        "reconciliation_report", "live_session_state", "capital_tier",
        "risk_trip", "daily_loss_anchor",
    }


def test_every_write_kind_is_classified():
    """A kind in neither bucket would be silently treated as non-critical."""
    assert CRITICAL_WRITES | NON_CRITICAL_WRITES == set(WriteKind)
    assert CRITICAL_WRITES & NON_CRITICAL_WRITES == set()


def test_non_critical_kinds_are_the_reconstructible_ones():
    assert {k.value for k in NON_CRITICAL_WRITES} == {
        "analytics", "dashboard_cache", "research_telemetry", "cosmetic_history",
    }


# ---------------------------------------------------------------------------
# Live: a critical failure freezes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(CRITICAL_WRITES, key=lambda k: k.value))
def test_every_critical_write_freezes_live_trading(kind):
    policy, machine = live_policy()
    policy.record_failure(kind, "disk full")

    assert machine.state is RuntimeState.FROZEN, (
        f"{kind.value} failed in live mode without stopping new risk"
    )
    assert not machine.may_acquire
    assert policy.failures[-1].froze_trading


@pytest.mark.parametrize("kind", sorted(NON_CRITICAL_WRITES, key=lambda k: k.value))
def test_non_critical_writes_do_not_freeze_live_trading(kind):
    policy, machine = live_policy()
    policy.record_failure(kind, "cache miss")

    assert machine.state is RuntimeState.LIVE_ACTIVE
    assert machine.may_acquire
    assert not policy.failures[-1].froze_trading
    assert not policy.degraded


def test_the_freeze_reason_says_what_happened():
    policy, machine = live_policy()
    policy.record_failure(WriteKind.ORDER_INTENT, "database locked")
    reason = machine.history[-1]["reason"]
    assert "order_intent" in reason
    assert "database locked" in reason
    assert "No new risk" in reason


# ---------------------------------------------------------------------------
# The guard propagates, so no caller continues on a lost write
# ---------------------------------------------------------------------------

def test_a_failed_critical_write_reaches_the_caller():
    """The pattern this issue exists to kill: except Exception: pass."""
    policy, machine = live_policy()

    with pytest.raises(CriticalWriteFailed) as excinfo:
        with policy.guard(WriteKind.FILL):
            raise OSError("no space left on device")

    assert excinfo.value.kind is WriteKind.FILL
    assert "no space left" in excinfo.value.detail
    assert machine.state is RuntimeState.FROZEN


def test_a_failed_non_critical_write_is_absorbed():
    policy, machine = live_policy()

    with policy.guard(WriteKind.DASHBOARD_CACHE):
        raise OSError("cache unavailable")

    assert machine.state is RuntimeState.LIVE_ACTIVE
    assert len(policy.failures) == 1


def test_a_successful_write_records_nothing():
    policy, machine = live_policy()
    with policy.guard(WriteKind.ORDER_INTENT):
        pass
    assert policy.failures == []
    assert machine.state is RuntimeState.LIVE_ACTIVE


def test_the_original_exception_is_preserved_as_the_cause():
    policy, _ = live_policy()
    original = OSError("disk on fire")
    with pytest.raises(CriticalWriteFailed) as excinfo:
        with policy.guard(WriteKind.RISK_TRIP):
            raise original
    assert excinfo.value.__cause__ is original


# ---------------------------------------------------------------------------
# Paper degrades loudly rather than halting
# ---------------------------------------------------------------------------

def test_paper_does_not_freeze_on_a_critical_failure():
    """Halting paper on a disk hiccup would stop the evidence runs."""
    policy, machine = paper_policy()
    policy.record_failure(WriteKind.ORDER_INTENT, "disk full")

    assert machine.state is RuntimeState.PAPER
    assert machine.may_acquire
    assert not policy.failures[-1].froze_trading


def test_paper_still_records_the_failure_as_degradation():
    """A paper run that lost writes is not clean promotion evidence."""
    policy, _ = paper_policy()
    policy.record_failure(WriteKind.FILL, "disk full")

    assert policy.degraded
    assert len(policy.critical_failures) == 1


def test_paper_guard_still_propagates_critical_failures():
    """Degrading is not the same as pretending the write landed."""
    policy, _ = paper_policy()
    with pytest.raises(CriticalWriteFailed):
        with policy.guard(WriteKind.LIFECYCLE_TRANSITION):
            raise OSError("disk full")


def test_a_clean_paper_run_is_not_degraded():
    policy, _ = paper_policy()
    policy.record_failure(WriteKind.ANALYTICS, "whatever")
    assert not policy.degraded


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_report_distinguishes_critical_from_total():
    policy, _ = live_policy()
    policy.record_failure(WriteKind.ANALYTICS, "a")
    policy.record_failure(WriteKind.DASHBOARD_CACHE, "b")
    policy.record_failure(WriteKind.FILL, "c")

    report = policy.report()
    assert report["failure_count"] == 3
    assert report["critical_failure_count"] == 1
    assert report["degraded"] is True
    assert report["live"] is True


def test_failures_serialise_with_their_classification():
    policy, _ = live_policy()
    policy.record_failure(WriteKind.CAPITAL_TIER, "write rejected")
    entry = policy.report()["failures"][0]
    assert entry["kind"] == "capital_tier"
    assert entry["critical"] is True
    assert entry["froze_trading"] is True
    assert "write rejected" in entry["detail"]


def test_a_policy_with_no_freeze_hook_still_records():
    """Missing wiring must not turn a critical failure into a silent one."""
    policy = PersistencePolicy(live=True, freeze=None)
    policy.record_failure(WriteKind.ORDER_INTENT, "disk full")
    assert policy.degraded
    assert policy.failures[-1].froze_trading is True, (
        "the decision to freeze is recorded even when nothing was wired to act on it"
    )


def test_repeated_failures_accumulate():
    policy, machine = live_policy()
    policy.record_failure(WriteKind.FILL, "first")
    policy.record_failure(WriteKind.FILL, "second")
    assert len(policy.critical_failures) == 2
    assert machine.state is RuntimeState.FROZEN


def test_is_critical_agrees_with_the_sets():
    for kind in WriteKind:
        assert is_critical(kind) == (kind in CRITICAL_WRITES)


# ---------------------------------------------------------------------------
# End to end through the execution engine
# ---------------------------------------------------------------------------

def test_a_failed_intent_write_freezes_live_and_sends_nothing(tmp_path):
    """The whole chain: journal fails, no order is sent, trading freezes."""
    from core.execution import ExecutionConfig, ExecutionEngine, OrderSide, OrderType
    from core.order_intent import IntentPersistenceError, OrderIntentJournal

    class Broker:
        def __init__(self):
            self.submissions = []

        def submit_order(self, order):
            self.submissions.append(order.client_order_id)
            return {"status": "filled", "filled_quantity": 1.0}

    machine = RuntimeStateMachine(RuntimeState.LIVE_ACTIVE)
    policy = PersistencePolicy(live=True, freeze=machine.freeze)
    journal = OrderIntentJournal(str(tmp_path / "intents.sqlite"))

    def explode(_intent):
        raise IntentPersistenceError("disk full")

    journal.record_intent = explode

    broker = Broker()
    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=broker,
        intent_journal=journal,
        persistence_policy=policy,
    )
    order = engine.create_order("AAPL", OrderSide.BUY, 1.0, OrderType.MARKET)
    result = engine.submit_order(order)

    assert not result.success
    assert broker.submissions == [], "an order was sent despite the WAL failing"
    assert machine.state is RuntimeState.FROZEN, (
        "a lost order intent in live mode must stop new risk"
    )
    assert not machine.may_acquire
    assert policy.degraded


def test_a_failed_lifecycle_write_freezes_but_keeps_the_order(tmp_path):
    """The order is already at the broker, so we freeze without losing it."""
    from core.execution import ExecutionConfig, ExecutionEngine, OrderSide, OrderType
    from core.order_intent import IntentPersistenceError, OrderIntentJournal

    class Broker:
        def submit_order(self, order):
            return {"status": "filled", "filled_quantity": 1.0,
                    "avg_fill_price": 100.0}

    machine = RuntimeStateMachine(RuntimeState.LIVE_ACTIVE)
    policy = PersistencePolicy(live=True, freeze=machine.freeze)
    journal = OrderIntentJournal(str(tmp_path / "intents.sqlite"))

    def explode(*args, **kwargs):
        raise IntentPersistenceError("database locked")

    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=False),
        broker_adapter=Broker(),
        intent_journal=journal,
        persistence_policy=policy,
    )
    order = engine.create_order("AAPL", OrderSide.BUY, 1.0, OrderType.MARKET)
    journal.record_transition = explode  # fails only after the intent landed
    engine.submit_order(order)

    assert machine.state is RuntimeState.FROZEN
    assert policy.degraded
    assert order.filled_quantity == 1.0, (
        "the fill really happened; freezing must not discard it"
    )


def test_paper_engine_keeps_running_through_a_journal_failure(tmp_path):
    from core.execution import ExecutionConfig, ExecutionEngine, OrderSide, OrderType
    from core.order_intent import IntentPersistenceError, OrderIntentJournal

    machine = RuntimeStateMachine(RuntimeState.PAPER)
    policy = PersistencePolicy(live=False, freeze=machine.freeze)
    journal = OrderIntentJournal(str(tmp_path / "intents.sqlite"))

    def explode(*args, **kwargs):
        raise IntentPersistenceError("disk full")

    journal.record_transition = explode

    engine = ExecutionEngine(
        config=ExecutionConfig(simulation_mode=True),
        intent_journal=journal,
        persistence_policy=policy,
    )
    engine.set_price("AAPL", 100.0)
    order = engine.create_order("AAPL", OrderSide.BUY, 1.0, OrderType.MARKET)
    engine.submit_order(order)

    assert machine.state is RuntimeState.PAPER
    assert machine.may_acquire
