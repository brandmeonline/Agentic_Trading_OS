"""Fail-closed live startup and recovery — ATOS-P0-REC-001.

Invariant:

    A restart cannot manufacture a clean or flat portfolio from missing state.

The question every test here asks: if the evidence is missing, does the system
refuse to trade, or does it start anyway on an empty book?
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime_state import (  # noqa: E402
    LIVE_STARTUP_STEPS,
    STATES_PERMITTING_ACQUISITION,
    LiveStartupSequence,
    RuntimeState,
    RuntimeStateMachine,
    StartupAborted,
    StartupCheck,
    build_live_startup_checks,
)

pytestmark = pytest.mark.adversarial


def ok(detail="ok"):
    return lambda: (True, detail)


def fails(detail="no evidence"):
    return lambda: (False, detail)


def all_passing(**overrides):
    """A full set of startup checks, every one passing unless overridden."""
    kwargs = {
        "config_loader": ok(),
        "authorization": ok(),
        "storage": ok(),
        "replay": ok(),
        "unresolved_intents": ok(),
        "broker_auth": ok(),
        "account": ok(),
        "positions": ok(),
        "open_orders": ok(),
        "recent_fills": ok(),
        "reconciliation": ok(),
        "risk_anchors": ok(),
        "market_data": ok(),
        "capital_tier": ok(),
    }
    kwargs.update(overrides)
    return build_live_startup_checks(**kwargs)


def run_live(**overrides):
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    report = LiveStartupSequence(machine, all_passing(**overrides), live=True).run()
    return machine, report


# ---------------------------------------------------------------------------
# The happy path exists, and it is narrow
# ---------------------------------------------------------------------------

def test_full_evidence_reaches_live_active():
    machine, report = run_live()
    assert report.succeeded
    assert machine.state is RuntimeState.LIVE_ACTIVE
    assert machine.may_acquire
    assert len(report.checks) == len(LIVE_STARTUP_STEPS)
    assert report.failures == []


def test_live_active_is_only_reachable_through_reconciling():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    for state in RuntimeState:
        if state is RuntimeState.LIVE_RECONCILING:
            continue
        machine._state = state
        assert not machine.can_transition_to(RuntimeState.LIVE_ACTIVE), (
            f"{state.name} must not jump straight to LIVE_ACTIVE"
        )


def test_the_startup_order_is_the_ultraplan_order():
    checks = all_passing()
    assert tuple(c.name for c in checks) == LIVE_STARTUP_STEPS


# ---------------------------------------------------------------------------
# Every single check is load-bearing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("step", LIVE_STARTUP_STEPS)
def test_any_single_failure_blocks_live(step):
    """No check is decorative: failing any one of them must stop startup."""
    keyword = {
        "load_validated_config": "config_loader",
        "verify_live_authorization": "authorization",
        "initialize_durable_storage": "storage",
        "replay_local_state": "replay",
        "enumerate_unresolved_intents": "unresolved_intents",
        "authenticate_broker": "broker_auth",
        "fetch_account": "account",
        "fetch_positions": "positions",
        "fetch_open_orders": "open_orders",
        "fetch_recent_fills": "recent_fills",
        "reconcile_local_against_broker": "reconciliation",
        "restore_risk_anchors": "risk_anchors",
        "validate_market_data_health": "market_data",
        "validate_capital_tier": "capital_tier",
    }[step]

    machine, report = run_live(**{keyword: fails(f"{step} has no evidence")})

    assert not report.succeeded
    assert machine.state is not RuntimeState.LIVE_ACTIVE
    assert machine.is_blocked
    assert not machine.may_acquire, "a blocked system must not add risk"
    assert report.aborted_at == step


def test_startup_stops_at_the_first_failure():
    """Later checks must not run once the evidence chain is broken."""
    ran = []

    def tracking(name, result=True):
        def run():
            ran.append(name)
            return result, name
        return run

    machine, report = run_live(
        storage=tracking("storage", False),
        replay=tracking("replay"),
        reconciliation=tracking("reconciliation"),
    )
    assert "storage" in ran
    assert "replay" not in ran, "startup continued past a failed check"
    assert "reconciliation" not in ran
    assert report.aborted_at == "initialize_durable_storage"


def test_a_check_that_raises_counts_as_failure():
    """An exception during a safety check is not evidence of safety."""
    def explode():
        raise RuntimeError("database on fire")

    machine = RuntimeStateMachine(RuntimeState.PAPER)
    checks = all_passing()
    checks[2] = StartupCheck(
        "initialize_durable_storage", explode, RuntimeState.RECOVERY_REQUIRED
    )
    report = LiveStartupSequence(machine, checks, live=True).run()

    assert not report.succeeded
    assert machine.state is RuntimeState.RECOVERY_REQUIRED
    assert "database on fire" in report.failures[0].detail


# ---------------------------------------------------------------------------
# Lost-state failures need a human, not a retry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("keyword,step", [
    ("storage", "initialize_durable_storage"),
    ("replay", "replay_local_state"),
    ("unresolved_intents", "enumerate_unresolved_intents"),
    ("positions", "fetch_positions"),
    ("open_orders", "fetch_open_orders"),
    ("recent_fills", "fetch_recent_fills"),
    ("reconciliation", "reconcile_local_against_broker"),
    ("risk_anchors", "restore_risk_anchors"),
])
def test_lost_broker_state_escalates_to_recovery(keyword, step):
    machine, report = run_live(**{keyword: fails("state unavailable")})
    assert machine.state is RuntimeState.RECOVERY_REQUIRED, (
        f"{step} implies we may have lost track of real state; that needs an operator"
    )


@pytest.mark.parametrize("keyword", ["config_loader", "authorization",
                                     "broker_auth", "account", "market_data",
                                     "capital_tier"])
def test_recoverable_dependency_failures_freeze(keyword):
    machine, _ = run_live(**{keyword: fails("temporarily unavailable")})
    assert machine.state is RuntimeState.FROZEN
    assert not machine.may_acquire


def test_reconciliation_mismatch_never_reaches_live():
    """The headline case: broker and local disagree."""
    machine, report = run_live(
        reconciliation=fails("broker holds 40 AAPL, local believes 0")
    )
    assert machine.state is RuntimeState.RECOVERY_REQUIRED
    assert not machine.may_acquire
    assert "40 AAPL" in report.failures[0].detail


def test_unresolved_intent_blocks_live_start():
    """An order that may exist must be resolved before adding more."""
    machine, _ = run_live(
        unresolved_intents=fails("1 unresolved intent: atos-abc")
    )
    assert machine.state is RuntimeState.RECOVERY_REQUIRED


# ---------------------------------------------------------------------------
# A sequence that skips a step is itself refused
# ---------------------------------------------------------------------------

def test_a_sequence_missing_a_required_step_is_rejected():
    """Silently omitting a check would pass while never asking the question."""
    checks = [c for c in all_passing() if c.name != "reconcile_local_against_broker"]
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    with pytest.raises(StartupAborted, match="reconcile_local_against_broker"):
        LiveStartupSequence(machine, checks, live=True)


def test_an_empty_sequence_cannot_reach_live():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    with pytest.raises(StartupAborted):
        LiveStartupSequence(machine, [], live=True)


# ---------------------------------------------------------------------------
# Paper mode
# ---------------------------------------------------------------------------

def test_paper_startup_skips_broker_checks_but_keeps_the_rest():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    report = LiveStartupSequence(
        machine, all_passing(broker_auth=fails("no broker in paper")), live=False
    ).run()

    names = [c.name for c in report.checks]
    assert "authenticate_broker" not in names, "paper mode has no broker to authenticate"
    assert "replay_local_state" in names, "local state still has to be replayed"
    assert "load_validated_config" in names
    assert machine.state is RuntimeState.PAPER
    assert machine.may_acquire


def test_capital_protection_checks_are_advisory_in_paper():
    """Reported, but not blocking: paper risks no capital.

    The ULTRAPLAN's fifteen steps are the *live* startup sequence. Applying
    all of them to paper would stop the evidence runs that promotion depends
    on while protecting nothing, so the capital-protection checks still run
    and still report, and only block when real money is involved.
    """
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    report = LiveStartupSequence(
        machine, all_passing(replay=fails("ledger unreadable")), live=False
    ).run()

    assert machine.state is RuntimeState.PAPER
    assert machine.may_acquire
    replay_result = next(c for c in report.checks if c.name == "replay_local_state")
    assert not replay_result.passed, "the failure must still be visible"
    assert report.aborted_at is None


def test_the_same_check_blocks_in_live():
    """The advisory tier must not leak into live mode."""
    machine, report = run_live(replay=fails("ledger unreadable"))
    assert machine.state is RuntimeState.RECOVERY_REQUIRED
    assert not machine.may_acquire
    assert report.aborted_at == "replay_local_state"


def test_paper_still_blocks_on_a_bad_config():
    """A broken config is a bug in any mode, not a capital question."""
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    report = LiveStartupSequence(
        machine, all_passing(config_loader=fails("config invalid")), live=False
    ).run()
    assert not report.succeeded
    assert machine.state is RuntimeState.FROZEN
    assert report.aborted_at == "load_validated_config"


# ---------------------------------------------------------------------------
# The state machine itself
# ---------------------------------------------------------------------------

def test_blocked_states_never_permit_acquisition():
    for state in (RuntimeState.FROZEN, RuntimeState.RECOVERY_REQUIRED,
                  RuntimeState.HALTED):
        machine = RuntimeStateMachine(RuntimeState.PAPER)
        machine._state = state
        assert not machine.may_acquire
        assert machine.is_blocked


def test_leaving_frozen_goes_back_through_reconciliation():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    machine.transition_to(RuntimeState.LIVE_ARMED, "arming")
    machine.transition_to(RuntimeState.LIVE_RECONCILING, "reconciling")
    machine.transition_to(RuntimeState.LIVE_ACTIVE, "evidence complete")
    machine.freeze("data feed went stale")

    assert not machine.can_transition_to(RuntimeState.LIVE_ACTIVE), (
        "a frozen system must re-reconcile, not resume"
    )
    assert machine.can_transition_to(RuntimeState.LIVE_RECONCILING)


def test_recovery_required_cannot_resume_directly():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    machine.require_recovery("unresolved intent")
    assert not machine.can_transition_to(RuntimeState.LIVE_ACTIVE)
    assert not machine.can_transition_to(RuntimeState.LIVE_ARMED)
    assert machine.can_transition_to(RuntimeState.LIVE_RECONCILING)


def test_every_transition_records_a_reason():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    machine.transition_to(RuntimeState.LIVE_ARMED, "operator armed the system")
    assert machine.history[-1]["reason"] == "operator armed the system"
    assert machine.history[-1]["from"] == "paper"
    assert machine.history[-1]["to"] == "live_armed"


def test_a_transition_without_a_reason_is_refused():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    with pytest.raises(ValueError):
        machine.transition_to(RuntimeState.LIVE_ARMED, "")


def test_illegal_transitions_are_refused_and_recorded():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    with pytest.raises(StartupAborted):
        machine.transition_to(RuntimeState.LIVE_ACTIVE, "skip the queue")
    assert machine.state is RuntimeState.PAPER
    assert machine.history[-1]["reason"].startswith("REFUSED")


def test_freeze_is_idempotent():
    machine = RuntimeStateMachine(RuntimeState.PAPER)
    machine.freeze("first")
    machine.freeze("second")
    assert machine.state is RuntimeState.FROZEN


def test_acquisition_permission_matches_the_declared_set():
    for state in RuntimeState:
        machine = RuntimeStateMachine(RuntimeState.PAPER)
        machine._state = state
        assert machine.may_acquire == (state in STATES_PERMITTING_ACQUISITION)


def test_report_serialises_for_the_dashboard():
    _, report = run_live(reconciliation=fails("mismatch"))
    payload = report.to_dict()
    assert payload["succeeded"] is False
    assert payload["final_state"] == "recovery_required"
    assert payload["aborted_at"] == "reconcile_local_against_broker"
    assert any(not c["passed"] for c in payload["checks"])
