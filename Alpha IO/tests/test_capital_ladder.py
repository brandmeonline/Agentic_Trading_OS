"""ATOS-P3-CAP-001 — spend authority is a grant, not a config field.

``initial_capital: float = 100000.0`` is the value somebody picked while
writing a dataclass. Nobody authorised it, and the orchestrator treated it as
how much there was to trade. The ladder replaces that with a persisted tier
that a named owner granted against evidence about the rung below.

Four properties carry the weight and each is tested against the way it fails:
rungs cannot be skipped, promotion needs a person, a breach freezes and
demotes rather than raising the ceiling to fit, and a tier is bound to the
configuration it was granted against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.capital_ladder import (  # noqa: E402
    CapitalLadder,
    CapitalTier,
    LadderState,
    LadderStore,
    TierEvidence,
    TierRefused,
    next_tier,
    spend_authority,
)

pytestmark = pytest.mark.adversarial


def _evidence(target: CapitalTier, **overrides):
    """Evidence sufficient for `target`, so each test can break one thing."""
    defaults = dict(
        target=target,
        approved_by="owner",
        safety_config_hash="cfg-1",
        strategy_hash="strat-1",
        p0_p1_gates_passed=True,
        reconciliation_clean=True,
        supervised_lifecycle_samples=5,
        timeout_drill_passed=True,
        cancel_drill_passed=True,
        crash_drill_passed=True,
        supervised_sessions=20,
        unexplained_mismatches=0,
        oos_net_of_cost_sharpe=1.1,
        execution_shortfall_bps=3.0,
        external_alerting_verified=True,
        independent_review_by="reviewer",
    )
    defaults.update(overrides)
    return TierEvidence(**defaults)


def _at(tier: CapitalTier, **state) -> CapitalLadder:
    """A ladder already sitting at `tier`, without walking it up."""
    defaults = dict(tier=tier, approved_by="owner",
                    safety_config_hash="cfg-1", strategy_hash="strat-1")
    defaults.update(state)
    return CapitalLadder(state=LadderState(**defaults))


# ---------------------------------------------------------------------------
# The ladder itself
# ---------------------------------------------------------------------------


def test_the_ladder_matches_section_27():
    assert [t.max_capital for t in CapitalTier] == [
        0.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0
    ]
    assert CapitalTier.L0.is_live is False
    assert all(t.is_live for t in CapitalTier if t is not CapitalTier.L0)


def test_every_tier_states_its_evidence():
    for tier in CapitalTier:
        assert tier.evidence_required


def test_the_top_of_the_ladder_has_no_next_rung():
    assert next_tier(CapitalTier.L0) is CapitalTier.L1
    assert next_tier(CapitalTier.L7) is None


def test_a_new_ladder_authorises_nothing():
    """The default has to be zero, because a default is what an unconfigured
    system gets."""
    ladder = CapitalLadder()
    assert ladder.tier is CapitalTier.L0
    assert ladder.max_capital_at_risk == 0.0
    assert ladder.may_risk(0.01)[0] is False


# ---------------------------------------------------------------------------
# initial_capital grants nothing
# ---------------------------------------------------------------------------


def test_initial_capital_alone_grants_no_authority():
    allowed, reason = spend_authority(None, 100_000.0)
    assert allowed == 0.0
    assert "grants no spend authority" in reason


def test_the_tier_ceiling_beats_a_larger_initial_capital():
    allowed, reason = spend_authority(_at(CapitalTier.L1), 100_000.0)
    assert allowed == 10.0
    assert "the ceiling wins" in reason


def test_a_smaller_initial_capital_is_respected():
    """The config number may lower the amount risked; it may never raise it."""
    allowed, reason = spend_authority(_at(CapitalTier.L4), 30.0)
    assert allowed == 30.0
    assert reason == ""


def test_a_frozen_ladder_authorises_nothing_whatever_the_tier():
    ladder = _at(CapitalTier.L7, frozen=True, freeze_reason="breach")
    assert ladder.max_capital_at_risk == 0.0
    assert spend_authority(ladder, 1_000.0)[0] == 0.0


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_a_well_evidenced_single_step_is_granted():
    """The baseline; every refusal below breaks exactly one thing."""
    ladder = CapitalLadder()
    assert ladder.promote(_evidence(CapitalTier.L1)) is CapitalTier.L1
    assert ladder.max_capital_at_risk == 10.0
    assert ladder.state.approved_by == "owner"
    assert ladder.state.approved_at is not None


def test_a_rung_cannot_be_skipped():
    ladder = CapitalLadder()
    with pytest.raises(TierRefused, match="rungs are not skipped"):
        ladder.promote(_evidence(CapitalTier.L3))
    assert ladder.tier is CapitalTier.L0


def test_the_whole_ladder_can_be_walked_one_step_at_a_time():
    ladder = CapitalLadder()
    for tier in list(CapitalTier)[1:]:
        assert ladder.promote(_evidence(tier)) is tier
    assert ladder.tier is CapitalTier.L7
    assert ladder.max_capital_at_risk == 1000.0


def test_there_is_nothing_above_the_top_rung():
    ladder = _at(CapitalTier.L7)
    with pytest.raises(TierRefused, match="top of the ladder"):
        ladder.promote(_evidence(CapitalTier.L7))


def test_promotion_needs_a_named_owner():
    with pytest.raises(TierRefused, match="granted by a person"):
        CapitalLadder().promote(_evidence(CapitalTier.L1, approved_by=""))


def test_promotion_needs_the_configuration_it_is_granted_against():
    for field in ("safety_config_hash", "strategy_hash"):
        with pytest.raises(TierRefused):
            CapitalLadder().promote(_evidence(CapitalTier.L1, **{field: ""}))


@pytest.mark.parametrize("target,override,expected", [
    (CapitalTier.L1, {"p0_p1_gates_passed": False}, "safety gates"),
    (CapitalTier.L1, {"reconciliation_clean": False}, "reconciliation"),
    (CapitalTier.L2, {"supervised_lifecycle_samples": 0}, "lifecycle"),
    (CapitalTier.L3, {"cancel_drill_passed": False}, "drills not passed"),
    (CapitalTier.L4, {"supervised_sessions": 3}, "supervised session(s)"),
    (CapitalTier.L4, {"unexplained_mismatches": 1}, "unexplained"),
    (CapitalTier.L5, {"oos_net_of_cost_sharpe": None}, "out-of-sample"),
    (CapitalTier.L5, {"oos_net_of_cost_sharpe": -0.5}, "not positive"),
    (CapitalTier.L6, {"execution_shortfall_bps": None}, "shortfall"),
    (CapitalTier.L6, {"external_alerting_verified": False}, "alerting"),
    (CapitalTier.L7, {"independent_review_by": ""}, "independent review"),
])
def test_each_tier_asks_for_its_own_evidence(target, override, expected):
    problems = _evidence(target, **override).problems()
    assert any(expected in p for p in problems), problems


def test_a_lower_tier_does_not_ask_for_a_higher_tier_s_evidence():
    """Otherwise L1 would need an independent review, and nothing would ever
    reach the first rung."""
    lean = TierEvidence(
        target=CapitalTier.L1, approved_by="owner",
        safety_config_hash="cfg-1", strategy_hash="strat-1",
        p0_p1_gates_passed=True, reconciliation_clean=True,
    )
    assert lean.problems() == []


def test_a_self_review_is_not_an_independent_review():
    problems = _evidence(CapitalTier.L7, approved_by="alex",
                         independent_review_by="alex").problems()
    assert any("not independent" in p for p in problems)


def test_a_refused_promotion_is_recorded():
    ladder = CapitalLadder()
    with pytest.raises(TierRefused):
        ladder.promote(_evidence(CapitalTier.L1, approved_by=""))
    refusals = [e for e in ladder.audit if e["outcome"] == "refused"]
    assert refusals and "granted by a person" in refusals[0]["detail"]


def test_a_frozen_ladder_cannot_be_promoted_out_of_its_freeze():
    ladder = _at(CapitalTier.L1, frozen=True, freeze_reason="breach")
    with pytest.raises(TierRefused, match="frozen"):
        ladder.promote(_evidence(CapitalTier.L2))


# ---------------------------------------------------------------------------
# Binding to the configuration
# ---------------------------------------------------------------------------


def test_a_changed_safety_config_invalidates_the_tier():
    ladder = _at(CapitalTier.L4)
    assert ladder.may_risk(50.0, safety_config_hash="cfg-1")[0] is True

    allowed, reason = ladder.may_risk(50.0, safety_config_hash="cfg-2")
    assert allowed is False
    assert "no longer running" in reason


def test_a_changed_strategy_invalidates_the_tier():
    ladder = _at(CapitalTier.L4)
    allowed, reason = ladder.may_risk(50.0, strategy_hash="strat-2")
    assert allowed is False
    assert "strategy has changed" in reason


def test_rebinding_restores_it_and_needs_an_approver():
    ladder = _at(CapitalTier.L4)
    with pytest.raises(TierRefused):
        ladder.rebind("cfg-2", "strat-2", approved_by="")

    ladder.rebind("cfg-2", "strat-2", approved_by="owner")
    assert ladder.may_risk(50.0, safety_config_hash="cfg-2",
                           strategy_hash="strat-2")[0] is True


def test_a_tier_with_no_approver_authorises_nothing():
    ladder = _at(CapitalTier.L3, approved_by="")
    allowed, reason = ladder.may_risk(1.0)
    assert allowed is False
    assert "no approver" in reason


# ---------------------------------------------------------------------------
# Breach
# ---------------------------------------------------------------------------


def test_the_ceiling_is_enforced():
    ladder = _at(CapitalTier.L2)
    assert ladder.may_risk(25.0)[0] is True
    allowed, reason = ladder.may_risk(25.01)
    assert allowed is False
    assert "exceeds the L2 ceiling" in reason


def test_a_breach_freezes_and_demotes():
    """Raising the ceiling to fit is the direction a system under pressure
    wants to move, so the ladder moves the other way and stops."""
    ladder = _at(CapitalTier.L4)
    ladder.record_breach(150.0, reason="unreconciled position")

    assert ladder.state.frozen is True
    assert ladder.tier is CapitalTier.L3
    assert ladder.max_capital_at_risk == 0.0
    assert "exceeded the L4 ceiling" in ladder.state.freeze_reason


def test_a_breach_at_the_bottom_freezes_without_demoting_further():
    ladder = CapitalLadder()
    ladder.record_breach(1.0)
    assert ladder.state.frozen is True
    assert ladder.tier is CapitalTier.L0


def test_clearing_a_freeze_needs_a_name():
    ladder = _at(CapitalTier.L2, frozen=True, freeze_reason="breach")
    with pytest.raises(TierRefused):
        ladder.clear_freeze("")

    ladder.clear_freeze("owner", note="root cause found")
    assert ladder.state.frozen is False
    assert ladder.max_capital_at_risk == 25.0


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------


def test_demotion_may_skip_rungs_where_promotion_may_not():
    """The failure mode of demoting too far is opportunity cost."""
    ladder = _at(CapitalTier.L6)
    assert ladder.demote("live drawdown", to=CapitalTier.L1) is CapitalTier.L1


def test_demotion_defaults_to_one_rung():
    assert _at(CapitalTier.L5).demote("caution") is CapitalTier.L4


def test_demotion_must_state_a_reason():
    with pytest.raises(TierRefused):
        _at(CapitalTier.L3).demote("")


def test_a_demotion_upwards_is_a_promotion_and_is_refused():
    with pytest.raises(TierRefused, match="needs evidence"):
        _at(CapitalTier.L2).demote("more please", to=CapitalTier.L5)


def test_demoting_from_the_bottom_stays_at_the_bottom():
    assert CapitalLadder().demote("belt and braces") is CapitalTier.L0


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_a_grant_survives_a_restart(tmp_path):
    path = tmp_path / "ladder.json"
    first = CapitalLadder(LadderStore(str(path)))
    first.promote(_evidence(CapitalTier.L1))

    restarted = CapitalLadder(LadderStore(str(path)))
    assert restarted.tier is CapitalTier.L1
    assert restarted.state.approved_by == "owner"
    assert restarted.state.safety_config_hash == "cfg-1"


def test_a_freeze_survives_a_restart(tmp_path):
    """Otherwise a breach is cleared by turning it off and on again."""
    path = tmp_path / "ladder.json"
    ladder = CapitalLadder(LadderStore(str(path)))
    ladder.promote(_evidence(CapitalTier.L1))
    ladder.record_breach(50.0)

    restarted = CapitalLadder(LadderStore(str(path)))
    assert restarted.state.frozen is True
    assert restarted.max_capital_at_risk == 0.0


def test_a_missing_file_reads_as_no_authority(tmp_path):
    ladder = CapitalLadder(LadderStore(str(tmp_path / "absent.json")))
    assert ladder.tier is CapitalTier.L0


def test_an_unreadable_grant_reads_as_no_authority(tmp_path, caplog):
    """A truncated file that parses as L0 is safe; one that parses as L7 is
    not, so anything unparseable is L0 and says so loudly."""
    path = tmp_path / "ladder.json"
    path.write_text("{ this is not json", encoding="utf-8")

    ladder = CapitalLadder(LadderStore(str(path)))

    assert ladder.tier is CapitalTier.L0
    assert "unreadable" in caplog.text


def test_the_write_is_atomic(tmp_path):
    path = tmp_path / "ladder.json"
    store = LadderStore(str(path))
    store.save(LadderState(tier=CapitalTier.L2, approved_by="owner"))

    assert json.loads(path.read_text(encoding="utf-8"))["tier"] == "L2"
    # No temp files left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["ladder.json"]


def test_the_report_is_serialisable():
    ladder = _at(CapitalTier.L3)
    report = ladder.report()
    assert report["tier"] == "L3"
    assert report["max_capital_at_risk"] == 50.0
    assert report["authority_problems"] == []
    json.dumps(report)


# ---------------------------------------------------------------------------
# The orchestrator's startup check
# ---------------------------------------------------------------------------


def test_the_startup_check_refuses_without_a_ladder():
    from core.orchestrator import TradingOrchestrator

    passed, detail = TradingOrchestrator()._check_capital_tier()
    assert passed is False
    assert "grants no spend authority" in detail


def test_the_startup_check_passes_with_a_granted_tier(tmp_path):
    from core.orchestrator import OrchestratorConfig, TradingOrchestrator

    path = tmp_path / "ladder.json"
    CapitalLadder(LadderStore(str(path))).promote(_evidence(CapitalTier.L1))

    orchestrator = TradingOrchestrator(
        OrchestratorConfig(capital_ladder_path=str(path), initial_capital=5.0)
    )
    passed, detail = orchestrator._check_capital_tier()

    assert passed is True
    assert "L1 authorises 5.00" in detail


def test_the_startup_check_caps_a_large_initial_capital(tmp_path):
    from core.orchestrator import OrchestratorConfig, TradingOrchestrator

    path = tmp_path / "ladder.json"
    CapitalLadder(LadderStore(str(path))).promote(_evidence(CapitalTier.L1))

    orchestrator = TradingOrchestrator(
        OrchestratorConfig(capital_ladder_path=str(path),
                           initial_capital=100_000.0)
    )
    passed, detail = orchestrator._check_capital_tier()

    assert passed is True
    assert "L1 authorises 10.00" in detail
    assert "the ceiling wins" in detail
