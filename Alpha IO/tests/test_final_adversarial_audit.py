"""The final adversarial review — ULTRAPLAN section 37.

Twenty questions, each of the form "can the system create, retain, duplicate,
mis-size or fail to observe real exposure while believing state is safer than
broker reality". The ULTRAPLAN asks for a read-only pass answering them.

A read-only pass would be an opinion. These are the answers as assertions, so
that "no" means something a machine checked rather than something I recalled -
and so that an answer that stops being "no" fails a build rather than ageing
quietly in a document.

One question was answered "yes" when this file was written. Question 11: a
feed that keeps arriving with the same number kept the arrival timestamp
current, so a disconnected socket replaying its last tick read as LIVE. That
is fixed and pinned below rather than recorded as a caveat.

The answers assume the components' own suites pass; this file checks the
composed property each question actually asks about, not the units again.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.adversarial

NOW = datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Can a network timeout duplicate an order?
# ---------------------------------------------------------------------------


def test_q01_a_network_timeout_cannot_duplicate_an_order():
    """The client order id is generated before the network is touched and
    survives a retry, so the broker sees the same id twice, not two orders."""
    from core.execution import Order, OrderSide, OrderStatus, OrderType

    order = Order(id="o-1", asset="AAPL", side=OrderSide.BUY,
                  order_type=OrderType.MARKET, quantity=10)
    first = order.client_order_id

    assert first
    assert len(first) >= 32, "a short id is not a UUID and may collide"
    # Reading it again, and after a transition, must not mint a new one.
    order.mark_ambiguous("submit raised TimeoutError; acceptance unproven")
    assert order.client_order_id == first
    assert order.status is OrderStatus.UNKNOWN


def test_q01_a_transport_failure_is_unknown_not_rejected():
    """"The request failed" is not "the broker refused". Treating it as a
    rejection is how a filled order becomes an untracked position."""
    from core.execution import OrderStatus

    assert OrderStatus.UNKNOWN.value == "unknown"
    assert OrderStatus.UNKNOWN is not OrderStatus.REJECTED


# ---------------------------------------------------------------------------
# 2. Can a partial fill release too much reserved exposure?
# ---------------------------------------------------------------------------


def test_q02_a_partial_fill_leaves_the_unfilled_remainder_reserved():
    """Effective exposure is the position *plus* what is still outstanding,
    so filling 40 of 100 does not hand back the other 60."""
    from core.exposure import BrokerAuthoritativeExposure, ExposureView

    exposure = BrokerAuthoritativeExposure(
        max_asset_concentration=0.5, max_portfolio_exposure=1.0,
        configured_capital=10_000.0,
    )
    exposure.update(ExposureView(
        positions={"AAPL": 40.0},
        outstanding_acquisitions={"AAPL": 60.0},
        equity=10_000.0, complete=True, reconciled=True,
    ))

    # 100 units' worth of exposure, not 40.
    view = exposure.report()["view"]
    assert view["by_instrument"]["AAPL"] == {
        "position": 40.0, "outstanding": 60.0, "effective": 100.0,
    }
    assert view["gross_exposure"] == 100.0


# ---------------------------------------------------------------------------
# 3. Can a fill occur after cancel and be missed?
# ---------------------------------------------------------------------------


def test_q03_a_cancel_request_is_not_a_cancelled_order():
    """The request and the outcome are different states, so a fill that
    arrives after the request still has somewhere to land."""
    from core.execution import OrderStatus

    assert OrderStatus.CANCEL_REQUESTED is not OrderStatus.CANCELLED
    assert OrderStatus.CANCEL_REQUESTED.value != OrderStatus.CANCELLED.value


def test_q03_a_cancel_requested_order_can_still_reach_a_filled_state():
    """The request does not close the door: a fill arriving afterwards has a
    legal transition to land on."""
    from core.execution import Order, OrderSide, OrderStatus, OrderType

    order = Order(id="o-2", asset="AAPL", side=OrderSide.BUY,
                  order_type=OrderType.MARKET, quantity=10)
    order.transition_to(OrderStatus.SUBMITTED, "sent")
    order.transition_to(OrderStatus.CANCEL_REQUESTED, "operator cancelled")

    order.transition_to(OrderStatus.PARTIAL, "fill arrived after the request")
    assert order.status is OrderStatus.PARTIAL

    order.transition_to(OrderStatus.FILLED, "and completed")
    assert order.status is OrderStatus.FILLED


# ---------------------------------------------------------------------------
# 4. Can restart forget a broker position or open order?
# ---------------------------------------------------------------------------


def test_q04_an_empty_local_book_is_not_evidence_of_an_empty_broker_book():
    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine

    report = ReconciliationEngine().reconcile(
        broker=BrokerSnapshot(account_fingerprint="A1",
                              positions={"AAPL": 100.0}, cash=1000.0),
        local=LocalSnapshot(account_fingerprint="A1", positions={},
                            cash=1000.0),
    )
    assert report.may_acquire is False
    assert report.mismatches


def test_q04_an_incomplete_broker_snapshot_blocks_acquisition():
    """Not knowing is not the same as nothing being there."""
    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine

    report = ReconciliationEngine().reconcile(
        broker=BrokerSnapshot(account_fingerprint="A1", cash=0.0,
                              positions_complete=False),
        local=LocalSnapshot(account_fingerprint="A1", cash=0.0),
    )
    assert report.may_acquire is False


# ---------------------------------------------------------------------------
# 5. Can database failure permit new live risk?
# ---------------------------------------------------------------------------


def test_q05_a_failed_critical_write_stops_the_system():
    from core.persistence_policy import (
        CRITICAL_WRITES,
        CriticalWriteFailed,
        PersistencePolicy,
        WriteKind,
    )

    policy = PersistencePolicy()
    with pytest.raises(CriticalWriteFailed):
        with policy.guard(WriteKind.ORDER_INTENT):
            raise OSError("disk full")

    assert WriteKind.ORDER_INTENT in CRITICAL_WRITES


def test_q05_readiness_refuses_without_healthy_persistence():
    from core.readiness import REQUIREMENT_KEYS, evaluate_readiness

    evidence = {key: True for key in REQUIREMENT_KEYS}
    evidence["persistence_healthy"] = (False, "store unreachable")
    assert evaluate_readiness(evidence).ready is False


# ---------------------------------------------------------------------------
# 6. Can manual broker activity evade local risk limits?
# ---------------------------------------------------------------------------


def test_q06_exposure_is_taken_from_the_broker_not_from_local_intent():
    """A position opened by hand in the broker's own UI is still exposure, and
    it consumes the limit that a locally-originated order would have used."""
    from core.exposure import BrokerAuthoritativeExposure, ExposureView

    exposure = BrokerAuthoritativeExposure(
        max_asset_concentration=0.10, max_portfolio_exposure=0.50,
        configured_capital=10_000.0,
    )
    # Nothing local knows about this; the broker reports it.
    exposure.update(ExposureView(
        positions={"AAPL": 1_000.0}, equity=10_000.0,
        complete=True, reconciled=True,
    ))

    allowed, reason = exposure.may_acquire("AAPL", 1.0)
    assert allowed is False
    assert reason


def test_q06_a_stale_reconciliation_stops_acquisition():
    from datetime import timedelta as _td

    from core.recurring_reconciliation import RecurringReconciler

    clock = {"now": NOW}
    reconciler = RecurringReconciler(
        reconcile=lambda: _matched_report(),
        freshness_window=_td(minutes=15),
        clock=lambda: clock["now"],
    )
    reconciler.run(_trigger())
    assert reconciler.may_acquire()[0] is True

    clock["now"] = NOW + _td(minutes=30)
    allowed, reason = reconciler.may_acquire()
    assert allowed is False
    assert "freshness window" in reason


def _matched_report():
    from core.reconciliation import BrokerSnapshot, LocalSnapshot, ReconciliationEngine
    return ReconciliationEngine().reconcile(
        broker=BrokerSnapshot(account_fingerprint="A1", cash=0.0, equity=0.0),
        local=LocalSnapshot(account_fingerprint="A1", cash=0.0, equity=0.0),
    )


def _trigger():
    from core.recurring_reconciliation import ReconciliationTrigger
    return ReconciliationTrigger.STARTUP


# ---------------------------------------------------------------------------
# 7. Can credentials or mode="live" alone activate real trading?
# ---------------------------------------------------------------------------


def test_q07_credentials_and_a_mode_flag_are_not_authorisation():
    from core.live_authorization import LiveAuthorizationGate, LiveAuthorizationRequest

    request = LiveAuthorizationRequest(
        credential_present=True, explicit_live_flag=True,
    )
    decision = LiveAuthorizationGate().authorize(request)
    assert decision.authorized is False
    assert len(decision.failures) > 5


def test_q07_no_capital_ladder_means_no_spend_authority():
    from core.capital_ladder import spend_authority

    allowed, reason = spend_authority(None, 100_000.0)
    assert allowed == 0.0
    assert "grants no spend authority" in reason


# ---------------------------------------------------------------------------
# 8. Can a model, RL policy, LLM or agent bypass hard risk?
# ---------------------------------------------------------------------------


def test_q08_the_catastrophic_guard_is_outside_the_policy():
    from core.model_governance import (
        ActionLimits,
        CatastrophicAction,
        CatastrophicActionGuard,
    )

    guard = CatastrophicActionGuard(ActionLimits(max_notional=100.0))
    with pytest.raises(CatastrophicAction):
        guard.permit(action=0, intended_position=0.5, notional=1_000_000.0,
                     equity=1_000.0)


def test_q08_an_unpromoted_model_may_not_serve():
    from core.model_governance import ModelRegistry, NotPromoted

    with pytest.raises(NotPromoted):
        ModelRegistry().require_live("some-model")


def test_q08_an_agent_proposal_cannot_enlarge_itself_past_the_risk_engine():
    from core.trade_proposal import (
        DeterministicTradeBoundary,
        Direction,
        Eligibility,
        TradeProposal,
    )

    boundary = DeterministicTradeBoundary(
        max_proposal_notional=100.0,
        risk_check=lambda instrument, notional: (False, "daily loss limit"),
    )
    decision = boundary.evaluate(TradeProposal(
        instrument="AAPL", direction=Direction.BUY, confidence=1.0,
        desired_notional=1_000_000.0, eligibility=Eligibility.LIVE,
    ))
    assert decision.approved is False
    assert any("risk engine refused" in r for r in decision.reasons)


# ---------------------------------------------------------------------------
# 9. Can two agents create duplicate or conflicting orders?
# ---------------------------------------------------------------------------


def test_q09_agreement_produces_one_proposal_and_disagreement_produces_none():
    from core.swarm_arbitration import AgentIdentity, SwarmArbiter, Vote
    from core.trade_proposal import Direction

    a = AgentIdentity("a", "v1")
    b = AgentIdentity("b", "v1")

    def vote(agent, direction):
        return Vote(agent=agent, instrument="AAPL", direction=direction,
                    confidence=0.9, horizon=timedelta(hours=4),
                    desired_notional=1_000.0, cast_at=NOW)

    agreeing = SwarmArbiter().arbitrate(
        [vote(a, Direction.BUY), vote(b, Direction.BUY)], now=NOW
    )
    assert len([o for o in agreeing if o.proposed]) == 1

    conflicting = SwarmArbiter().arbitrate(
        [vote(a, Direction.BUY), vote(b, Direction.SELL)], now=NOW
    )
    assert [o for o in conflicting if o.proposed] == []


# ---------------------------------------------------------------------------
# 10. Can auto-tuning raise risk without promotion?
# ---------------------------------------------------------------------------


def test_q10_the_tuner_cannot_loosen_anything():
    from core.auto_tuner import AutoTuner
    from core.tuning_governance import ParameterSet, loosening_problems

    tuner = AutoTuner()
    current = ParameterSet({"min_confidence": tuner.base_confidence,
                            "risk_per_trade": tuner.base_risk})

    for win_rate in (0.0, 0.5, 0.9, 1.0):
        for avg_pnl in (-100.0, 0.0, 100.0):
            suggestion = tuner.suggest({
                "trades": 200, "win_rate": win_rate, "avg_pnl": avg_pnl,
                "loss_streak": 0,
            })
            assert loosening_problems(suggestion.parameters, current) == []


def test_q10_a_looser_parameter_set_cannot_be_promoted_on_good_results():
    from core.safety_config import SafetyConfig
    from core.tuning_governance import (
        ParameterSet,
        TuningEvidence,
        Window,
    )

    current = ParameterSet({"risk_per_trade": 0.01})
    looser = ParameterSet({"risk_per_trade": 0.05})
    evidence = TuningEvidence(
        proposed=looser, current=current,
        fit_window=Window(0, 1000), oos_window=Window(1000, 1400),
        oos_sharpe=9.9, incumbent_oos_sharpe=0.1, shadow_sessions=10,
        approved_by="someone", ceilings=SafetyConfig(max_risk_per_trade=0.02),
    )
    assert evidence.sufficient is False


# ---------------------------------------------------------------------------
# 11. Can stale but frequently repeated data appear fresh?
# ---------------------------------------------------------------------------


def test_q11_a_feed_that_repeats_the_same_value_is_not_fresh():
    """This was YES until the section 37 pass.

    Freshness was measured as "when did we last receive something", which a
    disconnected socket replaying its last tick satisfies perfectly.
    """
    from core.operator_status import DataTrust, FeedHealth

    feed = FeedHealth("AAPL")
    moment = NOW
    for _ in range(1_200):        # one tick a second for twenty minutes
        feed.observe(190.00, moment)
        moment += timedelta(seconds=1)

    # Arriving constantly...
    assert (moment - feed.last_seen_at) < timedelta(seconds=5)
    # ...and not fresh.
    assert feed.trust(moment) is DataTrust.STALE
    assert feed.frozen(moment) is True
    assert feed.repeats > 500


def test_q11_a_moving_feed_is_fresh():
    """Otherwise the check above passes because everything is stale."""
    from core.operator_status import DataTrust, FeedHealth

    feed = FeedHealth("AAPL")
    moment = NOW
    for i in range(1_200):
        feed.observe(190.00 + i * 0.01, moment)
        moment += timedelta(seconds=1)

    assert feed.trust(moment) is DataTrust.LIVE
    assert feed.frozen(moment) is False


def test_q11_the_dashboard_reports_a_frozen_feed():
    from core.operator_status import DataTrust, FeedMonitor

    monitor = FeedMonitor()
    moment = NOW
    for _ in range(1200):
        monitor.observe("AAPL", 190.0, moment)
        monitor.observe("MSFT", 400.0, moment)
        moment += timedelta(seconds=1)

    assert monitor.trust(moment) is DataTrust.STALE
    assert monitor.frozen_symbols(moment) == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# 12. Can a SELL or close increase absolute exposure?
# ---------------------------------------------------------------------------


def test_q12_a_sell_that_would_flip_the_position_is_not_a_reduction():
    """Selling 250 against a 100 long closes 100 and opens a 150 short. The
    absolute exposure went up, and calling it a "close" does not change that."""
    from core.exposure import ExposureView, ReduceOnlyGate

    gate = ReduceOnlyGate(
        view=ExposureView(positions={"AAPL": 100.0}, equity=10_000.0,
                          complete=True, reconciled=True),
        allow_shorting=False,
    )

    overshoot = gate.evaluate("AAPL", "sell", quantity=250.0,
                              held_quantity=100.0)
    assert overshoot.allowed is False
    assert overshoot.max_reducible_quantity == 100.0
    assert abs(overshoot.post_trade_exposure) > abs(overshoot.pre_trade_exposure)

    exact = gate.evaluate("AAPL", "sell", quantity=100.0, held_quantity=100.0)
    assert exact.allowed is True
    assert abs(exact.post_trade_exposure) < abs(exact.pre_trade_exposure)


# ---------------------------------------------------------------------------
# 13. Can daily drawdown reset on restart?
# ---------------------------------------------------------------------------


def test_q13_a_drawdown_trip_survives_a_restart(tmp_path):
    from core.risk_anchors import DurableRiskAnchors, RiskAnchorStore

    path = str(tmp_path / "anchors.db")
    first = DurableRiskAnchors(RiskAnchorStore(path), max_daily_drawdown=0.05)
    first.observe_equity(10_000.0)
    first.record_realized_pnl(-2_000.0)
    first.observe_equity(8_000.0)

    tripped_before = list(first.active_trips)
    assert tripped_before, "the setup must actually trip something"
    assert first.may_trade()[0] is False

    restarted = DurableRiskAnchors(RiskAnchorStore(path),
                                   max_daily_drawdown=0.05)
    assert list(restarted.active_trips) == tripped_before
    assert restarted.may_trade()[0] is False


# ---------------------------------------------------------------------------
# 14. Can a dashboard or API mutation bypass strong auth and audit?
# ---------------------------------------------------------------------------


def test_q14_a_mutation_without_an_actor_is_denied_and_logged():
    from core.control_plane import ControlPlaneGuard

    guard = ControlPlaneGuard()
    entry = guard.authorize("place_order", actor=None, mutating=True)

    assert entry.allowed is False
    assert guard.denials() == [entry]


def test_q14_a_dangerous_action_needs_more_than_trade_permission():
    from core.control_plane import DANGEROUS_ACTIONS, ControlPlaneGuard

    guard = ControlPlaneGuard()
    for action in sorted(DANGEROUS_ACTIONS):
        entry = guard.authorize(action, actor="bot", permissions=["trade"])
        assert entry.allowed is False, action


def test_q14_auth_is_not_tied_to_the_mode():
    source = (REPO / "Alpha IO" / "core" / "orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "enable_auth=self.config.mode == TradingMode.LIVE" not in source
    assert "enable_auth=True" in source


# ---------------------------------------------------------------------------
# 15. Can demo data look live?
# ---------------------------------------------------------------------------


def test_q15_a_demo_panel_outranks_everything_else_on_the_bar():
    from core.operator_status import DataTrust, Surface, worst_trust

    assert worst_trust([DataTrust.LIVE, DataTrust.DEMO]) is DataTrust.DEMO
    assert worst_trust([DataTrust.UNAVAILABLE, DataTrust.DEMO]) is DataTrust.DEMO
    assert Surface("x").trust is DataTrust.UNAVAILABLE   # pessimistic default


def test_q15_a_demo_panel_while_live_is_reported_as_such():
    from core.operator_status import (
        DataTrust,
        OperatingMode,
        OperatorStatus,
        Surface,
    )

    status = OperatorStatus(
        mode=OperatingMode.LIVE,
        surfaces=[Surface("defi", DataTrust.DEMO, "generated")],
    )
    assert any("real capital is committed" in p for p in status.problems())
    assert status.fit_to_trade is False


# ---------------------------------------------------------------------------
# 16. Can real secrets be committed or logged?
# ---------------------------------------------------------------------------


def test_q16_an_alert_payload_is_redacted_on_the_way_out():
    from core.alerting import AlertKind, AlertManager, CallbackAlertChannel

    seen = []
    manager = AlertManager(
        channels=[CallbackAlertChannel("test", seen.append)]
    )
    # Deliberately not a vendor-shaped literal. The exhaustive per-shape
    # coverage lives in test_alerting_escalation.py, which is exempt from the
    # secret scan precisely so it can carry realistic tokens; this file is not
    # exempt and should stay that way, because it is a forty-test file that
    # will be edited. What is checked here is the composed property: the two
    # redaction layers - a secret-looking key name, and a secret-looking value
    # inside otherwise innocent text - both fire on the way to a channel.
    manager.raise_alert(
        AlertKind.BROKER_AUTH_FAILURE, "auth failed",
        api_key="the-key-itself-whatever-shape-it-is",
        detail="rejected with bearer abcdefghijklmnopqrstuvwx",
    )

    rendered = repr(seen)
    assert "the-key-itself-whatever-shape-it-is" not in rendered
    assert "abcdefghijklmnopqrstuvwx" not in rendered
    # The surrounding message survives, so this is redaction rather than
    # deletion - an alert nobody can read is a different failure.
    assert "auth failed" in rendered


def test_q16_the_credential_paths_are_ignored_by_git_and_docker():
    for name in (".gitignore", ".dockerignore"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert ".credentials" in text, name


# ---------------------------------------------------------------------------
# 17. Can backtest leakage make a strategy appear promotable?
# ---------------------------------------------------------------------------


def test_q17_a_leaking_transform_is_detected():
    from core.leakage import LeakageDetected, assert_causal, unpredictable_series

    series = unpredictable_series(200, seed=1)

    def full_series_zscore(values):
        mean = sum(values) / len(values)
        return [v - mean for v in values]

    with pytest.raises(LeakageDetected):
        assert_causal(full_series_zscore, series, name="full-series")


def test_q17_a_backtest_cannot_fill_at_the_price_it_decided_from():
    from core.fill_model import Bar, FillModel, FillTiming, ImpossibleFill

    with pytest.raises(ImpossibleFill):
        FillModel(timing=FillTiming.SAME_BAR_CLOSE)

    bar = Bar(open=100, high=101, low=99, close=100.5, volume=1_000)
    with pytest.raises(ImpossibleFill):
        FillModel().fill("buy", 1, fill_bar=bar, decision_bar=bar)


# ---------------------------------------------------------------------------
# 18. Can unsupported futures or options semantics reach real execution?
# ---------------------------------------------------------------------------


def test_q18_derivatives_are_refused_for_live_at_three_layers():
    from core.precision_trade_planner import map_signal_to_trade, plan_for_execution
    from core.safety_config import SafetyConfig
    from core.venue_rules import (
        AssetClass,
        ExecutionContext,
        InstrumentRules,
        UnsupportedAssetClass,
        require_live_support,
    )

    # The config.
    assert any("not supported for live execution" in p
               for p in SafetyConfig(mode="live", allow_futures=True).problems())
    # The venue rules.
    with pytest.raises(UnsupportedAssetClass):
        require_live_support(AssetClass.OPTION)
    live = ExecutionContext(rules=InstrumentRules("ES", AssetClass.FUTURE),
                            live=True)
    assert live.may_place(1, 5000.0, NOW)[0] is False
    # The planner.
    assert map_signal_to_trade("x", 0.9)["executable"] is False
    with pytest.raises(UnsupportedAssetClass):
        plan_for_execution()


# ---------------------------------------------------------------------------
# 19. Can a safety-relevant change reuse old approval evidence?
# ---------------------------------------------------------------------------


def test_q19_a_changed_safety_config_loses_its_promotion():
    from core.safety_config import SafetyConfig

    approved = SafetyConfig(mode="paper", max_risk_per_trade=0.01)
    changed = SafetyConfig(mode="paper", max_risk_per_trade=0.02)

    assert approved.safety_hash() != changed.safety_hash()
    assert changed.invalidates_promotion_of(approved) is True


def test_q19_a_capital_tier_is_bound_to_the_config_it_was_granted_against():
    from core.capital_ladder import CapitalLadder, CapitalTier, LadderState

    ladder = CapitalLadder(state=LadderState(
        tier=CapitalTier.L3, approved_by="owner",
        safety_config_hash="cfg-1", strategy_hash="strat-1",
    ))
    assert ladder.may_risk(10.0, safety_config_hash="cfg-1")[0] is True
    assert ladder.may_risk(10.0, safety_config_hash="cfg-2")[0] is False


def test_q19_a_material_tuning_change_invalidates_the_strategy_promotion():
    from core.tuning_governance import ParameterSet, invalidates_strategy_promotion

    assert invalidates_strategy_promotion(
        ParameterSet({"risk_per_trade": 0.002}),
        ParameterSet({"risk_per_trade": 0.010}),
    ) == ["risk_per_trade"]


# ---------------------------------------------------------------------------
# 20. Can any UNKNOWN condition become RUNNING or SAFE without reconciliation?
# ---------------------------------------------------------------------------


def test_q20_live_active_is_reachable_only_through_reconciling():
    from core.runtime_state import RuntimeState, RuntimeStateMachine

    for state in RuntimeState:
        machine = RuntimeStateMachine(state)
        if machine.can_transition_to(RuntimeState.LIVE_ACTIVE):
            assert state is RuntimeState.LIVE_RECONCILING, state


def test_q20_leaving_a_blocked_state_means_reconciling_not_resuming():
    from core.runtime_state import RuntimeState, RuntimeStateMachine

    for blocked in (RuntimeState.FROZEN, RuntimeState.RECOVERY_REQUIRED):
        machine = RuntimeStateMachine(blocked)
        assert machine.can_transition_to(RuntimeState.LIVE_ACTIVE) is False
        assert machine.can_transition_to(RuntimeState.LIVE_RECONCILING) is True


def test_q20_a_restarted_process_never_comes_back_into_a_trading_state():
    from core.orchestrator import OrchestratorConfig, TradingMode, TradingOrchestrator
    from core.readiness import check_restart_state

    for mode in (TradingMode.PAPER, TradingMode.LIVE):
        orchestrator = TradingOrchestrator(OrchestratorConfig(mode=mode))
        check_restart_state(orchestrator.runtime.state.value)   # must not raise


def test_q20_readiness_defaults_to_not_ready():
    """The composed version of the whole audit: with nothing established, the
    system reports that it may not add risk."""
    from core.readiness import evaluate_readiness

    report = evaluate_readiness({})
    assert report.ready is False
    assert report.http_status == 503


# ---------------------------------------------------------------------------
# The audit record itself
# ---------------------------------------------------------------------------


def test_all_twenty_questions_have_at_least_one_test():
    """A question with no test is an opinion, and this file exists to have
    none of those."""
    source = Path(__file__).read_text(encoding="utf-8")
    for number in range(1, 21):
        assert f"def test_q{number:02d}_" in source, number


def test_the_audit_report_exists_and_answers_every_question():
    report = (REPO / "docs" / "FINAL_ADVERSARIAL_AUDIT.md").read_text(
        encoding="utf-8"
    )
    for number in range(1, 21):
        assert f"### {number}." in report, number
