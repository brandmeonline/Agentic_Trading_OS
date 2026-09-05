"""ATOS-P3-TUNE-001 — a tuner proposes, and can only ever tighten.

The rule this replaces was

    if win_rate > 0.65:
        confidence -= 0.05
        risk += 0.005

which is a martingale with a spreadsheet: most aggressive right after a run
of luck, computed from the same trade log it had just been judged on, and
returning neutral values when that log was missing so it tuned on nothing.

The asymmetry below is the point. Bad news tightens; good news changes
nothing. The failure mode of being too careful is opportunity cost, and the
failure mode of the other direction is the account.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.auto_tuner import MINIMUM_TRADES, AutoTuner  # noqa: E402
from core.safety_config import SafetyConfig  # noqa: E402
from core.tuning_governance import (  # noqa: E402
    MATERIAL_PARAMETERS,
    MINIMUM_OOS_BARS,
    MINIMUM_SHADOW_SESSIONS,
    IllegalTransition,
    ParameterSet,
    TuningEvidence,
    TuningRefused,
    TuningRegistry,
    TuningStage,
    Window,
    invalidates_strategy_promotion,
    loosening_problems,
    transition_problems,
)

pytestmark = pytest.mark.adversarial


CHAMPION = ParameterSet(
    {"min_confidence": 0.70, "risk_per_trade": 0.010, "leverage": 1.0},
    label="champion",
)
TIGHTER = ParameterSet(
    {"min_confidence": 0.75, "risk_per_trade": 0.008, "leverage": 1.0},
    label="tighter",
)
LOOSER = ParameterSet(
    {"min_confidence": 0.65, "risk_per_trade": 0.020, "leverage": 2.0},
    label="looser",
)

CEILINGS = SafetyConfig(max_risk_per_trade=0.015, max_leverage=1.0)


def _evidence(proposed=TIGHTER, current=CHAMPION, **overrides):
    defaults = dict(
        proposed=proposed, current=current,
        fit_window=Window(0, 1000), oos_window=Window(1000, 1400),
        oos_sharpe=1.4, incumbent_oos_sharpe=1.0,
        shadow_sessions=MINIMUM_SHADOW_SESSIONS, approved_by="risk-officer",
        ceilings=CEILINGS,
    )
    defaults.update(overrides)
    return TuningEvidence(**defaults)


def _promoted_registry():
    registry = TuningRegistry(CHAMPION, ceilings=CEILINGS)
    registry.register_candidate(TIGHTER)
    registry.advance(TIGHTER, TuningStage.SHADOW)
    registry.advance(TIGHTER, TuningStage.OOS_VALIDATED)
    return registry


# ---------------------------------------------------------------------------
# The lifecycle
# ---------------------------------------------------------------------------


def test_the_six_named_stages_exist():
    assert {s.value for s in TuningStage} == {
        "candidate", "shadow", "oos_validated", "promoted", "rolled_back",
        "retired",
    }


def test_the_ordinary_path_is_legal():
    """The baseline; every refusal below breaks one step of it."""
    for start, target in (
        (TuningStage.CANDIDATE, TuningStage.SHADOW),
        (TuningStage.SHADOW, TuningStage.OOS_VALIDATED),
        (TuningStage.OOS_VALIDATED, TuningStage.PROMOTED),
        (TuningStage.PROMOTED, TuningStage.ROLLED_BACK),
        (TuningStage.ROLLED_BACK, TuningStage.CANDIDATE),
    ):
        assert transition_problems(start, target) == []


def test_shadow_cannot_go_straight_to_promoted():
    """Shadow shows a parameter set behaves; out-of-sample evidence shows it
    works. They are different claims."""
    assert transition_problems(TuningStage.SHADOW, TuningStage.PROMOTED)


def test_a_candidate_cannot_skip_to_production():
    assert transition_problems(TuningStage.CANDIDATE, TuningStage.PROMOTED)
    assert transition_problems(TuningStage.CANDIDATE, TuningStage.OOS_VALIDATED)


def test_a_retired_set_goes_nowhere():
    for target in TuningStage:
        if target is TuningStage.RETIRED:
            continue
        assert transition_problems(TuningStage.RETIRED, target)


def test_every_stage_can_be_retired():
    for stage in TuningStage:
        if stage is TuningStage.RETIRED:
            continue
        assert transition_problems(stage, TuningStage.RETIRED) == []


def test_a_rolled_back_set_starts_again_rather_than_resuming():
    """Whatever went wrong in production is not evidence that it is still
    shadow-clean."""
    assert transition_problems(TuningStage.ROLLED_BACK, TuningStage.CANDIDATE) == []
    assert transition_problems(TuningStage.ROLLED_BACK, TuningStage.OOS_VALIDATED)
    assert transition_problems(TuningStage.ROLLED_BACK, TuningStage.PROMOTED)


def test_the_registry_enforces_the_transitions():
    registry = TuningRegistry(CHAMPION)
    registry.register_candidate(TIGHTER)
    with pytest.raises(IllegalTransition):
        registry.advance(TIGHTER, TuningStage.OOS_VALIDATED)
    registry.advance(TIGHTER, TuningStage.SHADOW)
    assert registry.stage(TIGHTER) is TuningStage.SHADOW


def test_advance_cannot_be_used_to_promote():
    registry = _promoted_registry()
    with pytest.raises(TuningRefused, match="requires evidence"):
        registry.advance(TIGHTER, TuningStage.PROMOTED)


# ---------------------------------------------------------------------------
# Tighten yes, loosen never
# ---------------------------------------------------------------------------


def test_tightening_is_permitted():
    """Baseline: this must be clean or every refusal below is meaningless."""
    assert loosening_problems(TIGHTER, CHAMPION, CEILINGS) == []


def test_raising_risk_per_trade_is_refused():
    problems = loosening_problems(LOOSER, CHAMPION, CEILINGS)
    assert any("risk_per_trade raised" in p for p in problems)


def test_lowering_the_confidence_threshold_is_refused():
    """A lower bar trades more, so lowering it loosens."""
    problems = loosening_problems(LOOSER, CHAMPION, CEILINGS)
    assert any("min_confidence lowered" in p for p in problems)


def test_raising_leverage_is_refused():
    problems = loosening_problems(LOOSER, CHAMPION, CEILINGS)
    assert any("leverage raised" in p for p in problems)


def test_exceeding_a_promoted_ceiling_is_refused_even_when_tightening():
    """Tightening from an already-illegal value still leaves an illegal one."""
    illegal_champion = ParameterSet({"risk_per_trade": 0.05})
    slightly_less_illegal = ParameterSet({"risk_per_trade": 0.04})

    problems = loosening_problems(slightly_less_illegal, illegal_champion,
                                  CEILINGS)
    assert any("exceeds the promoted max_risk_per_trade" in p for p in problems)


def test_loosening_inside_a_generous_ceiling_is_still_refused():
    """The relative check and the absolute check catch different things."""
    generous = SafetyConfig(max_risk_per_trade=0.50, max_leverage=10.0)
    problems = loosening_problems(LOOSER, CHAMPION, generous)
    assert problems
    assert all("exceeds the promoted" not in p for p in problems)


def test_every_upward_looseneing_parameter_is_checked():
    from core.tuning_governance import LOOSENING_UPWARD

    for name in LOOSENING_UPWARD:
        current = ParameterSet({name: 1.0})
        proposed = ParameterSet({name: 2.0})
        assert loosening_problems(proposed, current), name


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_complete_evidence_is_sufficient():
    assert _evidence().problems() == []
    assert _evidence().sufficient is True


def test_evidence_from_the_fitting_window_is_no_evidence():
    problems = _evidence(oos_window=Window(500, 1400)).problems()
    assert any("overlaps the fitting window" in p for p in problems)


def test_evidence_from_before_the_fit_is_refused():
    problems = _evidence(fit_window=Window(1000, 2000),
                         oos_window=Window(0, 900)).problems()
    assert any("starts before the fitting window ends" in p for p in problems)


def test_a_short_evaluation_window_is_refused():
    problems = _evidence(
        oos_window=Window(1000, 1000 + MINIMUM_OOS_BARS - 1)
    ).problems()
    assert any("out-of-sample bar(s)" in p for p in problems)


def test_missing_windows_are_refused():
    assert any("no way to tell whether the evidence is out of sample" in p
               for p in _evidence(fit_window=None).problems())


def test_not_beating_the_incumbent_is_refused():
    """The default is to keep the champion."""
    problems = _evidence(oos_sharpe=0.9, incumbent_oos_sharpe=1.0).problems()
    assert any("does not beat the incumbent" in p for p in problems)

    tied = _evidence(oos_sharpe=1.0, incumbent_oos_sharpe=1.0).problems()
    assert any("does not beat" in p for p in tied)


def test_a_result_with_nothing_to_compare_against_is_refused():
    problems = _evidence(incumbent_oos_sharpe=None).problems()
    assert any("needs both halves" in p for p in problems)


def test_too_few_shadow_sessions_are_refused():
    problems = _evidence(shadow_sessions=MINIMUM_SHADOW_SESSIONS - 1).problems()
    assert any("shadow session(s)" in p for p in problems)


def test_no_human_approval_is_refused():
    assert any("no human has approved" in p
               for p in _evidence(approved_by="").problems())


def test_a_loosening_proposal_cannot_buy_its_way_in_with_good_evidence():
    """Good out-of-sample numbers do not license more risk."""
    problems = _evidence(proposed=LOOSER, oos_sharpe=5.0).problems()
    assert any("never loosen" in p for p in problems)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def test_promotion_replaces_the_champion_and_rolls_the_old_one_back():
    registry = _promoted_registry()
    registry.promote(_evidence())

    assert registry.champion is TIGHTER
    assert registry.stage(TIGHTER) is TuningStage.PROMOTED
    assert registry.stage(CHAMPION) is TuningStage.ROLLED_BACK


def test_promotion_without_the_lifecycle_is_refused():
    registry = TuningRegistry(CHAMPION, ceilings=CEILINGS)
    registry.register_candidate(TIGHTER)
    with pytest.raises(TuningRefused, match="not a legal tuning transition"):
        registry.promote(_evidence())


def test_evidence_against_a_stale_champion_is_refused():
    """'Better' has to be better than what is actually running."""
    registry = _promoted_registry()
    other = ParameterSet({"min_confidence": 0.9, "risk_per_trade": 0.001})
    with pytest.raises(TuningRefused, match="not the current champion"):
        registry.promote(_evidence(current=other))


def test_a_refused_promotion_is_recorded():
    registry = _promoted_registry()
    with pytest.raises(TuningRefused):
        registry.promote(_evidence(approved_by=""))
    refusals = [e for e in registry.audit if e["outcome"] == "refused"]
    assert refusals and "no human has approved" in refusals[0]["detail"]
    assert registry.champion is CHAMPION


def test_the_registry_supplies_its_own_ceilings_when_the_evidence_omits_them():
    registry = TuningRegistry(CHAMPION, ceilings=CEILINGS)
    registry.register_candidate(LOOSER)
    registry.advance(LOOSER, TuningStage.SHADOW)
    registry.advance(LOOSER, TuningStage.OOS_VALIDATED)

    with pytest.raises(TuningRefused, match="never loosen|exceeds the promoted"):
        registry.promote(_evidence(proposed=LOOSER, ceilings=None))


def test_a_material_change_invalidates_the_strategy_promotion():
    registry = _promoted_registry()
    assert registry.strategy_promotion_valid is True

    registry.promote(_evidence())

    assert registry.strategy_promotion_valid is False
    assert any("material" in e["detail"] for e in registry.audit)


def test_re_approval_restores_it_and_needs_a_name():
    registry = _promoted_registry()
    registry.promote(_evidence())

    with pytest.raises(TuningRefused):
        registry.revalidate_strategy_promotion("")

    registry.revalidate_strategy_promotion("risk-officer")
    assert registry.strategy_promotion_valid is True


def test_a_tighter_change_also_invalidates_promotion():
    """A strategy approved at 1% risk per trade was not approved at 0.2%."""
    much_tighter = ParameterSet({"min_confidence": 0.70,
                                 "risk_per_trade": 0.002, "leverage": 1.0})
    assert invalidates_strategy_promotion(much_tighter, CHAMPION) == \
        ["risk_per_trade"]


def test_a_cosmetic_change_does_not_invalidate_promotion():
    same = ParameterSet(dict(CHAMPION.values), label="renamed")
    assert invalidates_strategy_promotion(same, CHAMPION) == []


def test_material_parameters_cover_the_risk_bearing_ones():
    for name in ("risk_per_trade", "leverage", "capital_tier",
                 "daily_drawdown", "min_confidence"):
        assert name in MATERIAL_PARAMETERS


def test_rollback_returns_to_a_known_set_and_invalidates_promotion():
    registry = _promoted_registry()
    registry.promote(_evidence())

    registry.roll_back(CHAMPION, "live drawdown")

    assert registry.champion is CHAMPION
    assert registry.stage(CHAMPION) is TuningStage.PROMOTED
    assert registry.stage(TIGHTER) is TuningStage.ROLLED_BACK
    assert registry.strategy_promotion_valid is False


def test_rolling_back_to_an_unknown_set_is_refused():
    """A rollback target must be something that was promoted before; an
    untested change made in a hurry is the worst kind."""
    registry = _promoted_registry()
    with pytest.raises(TuningRefused, match="unknown to the registry"):
        registry.roll_back(ParameterSet({"risk_per_trade": 0.99}), "panic")


def test_the_artifact_hash_identifies_the_values_not_the_label():
    assert ParameterSet({"a": 1.0}, "x").artifact_hash() == \
        ParameterSet({"a": 1.0}, "y").artifact_hash()
    assert ParameterSet({"a": 1.0}).artifact_hash() != \
        ParameterSet({"a": 1.1}).artifact_hash()


def test_a_non_numeric_parameter_is_refused():
    with pytest.raises(ValueError):
        ParameterSet({"risk_per_trade": "aggressive"})
    with pytest.raises(ValueError):
        ParameterSet({"risk_per_trade": True})


def test_the_report_is_serialisable_and_names_the_champion():
    registry = _promoted_registry()
    report = registry.report()
    assert report["champion"]["artifact_hash"] == CHAMPION.artifact_hash()
    assert report["strategy_promotion_valid"] is True


# ---------------------------------------------------------------------------
# The tuner itself
# ---------------------------------------------------------------------------


def _performance(win_rate=0.5, avg_pnl=0.0, loss_streak=0, trades=100):
    return {"win_rate": win_rate, "avg_pnl": avg_pnl,
            "loss_streak": loss_streak, "trades": trades}


def test_a_good_run_changes_nothing():
    """The removed rule. A high win rate used to lower the bar and raise the
    size, which is a martingale."""
    tuner = AutoTuner()
    suggestion = tuner.suggest(_performance(win_rate=0.90, avg_pnl=50.0))

    assert suggestion.unchanged is True
    assert suggestion.parameters.get("risk_per_trade") == tuner.base_risk
    assert suggestion.parameters.get("min_confidence") == tuner.base_confidence
    assert "not a reason to loosen" in suggestion.reasons[0]


def test_a_bad_run_tightens():
    tuner = AutoTuner()
    suggestion = tuner.suggest(
        _performance(win_rate=0.30, avg_pnl=-20.0, loss_streak=5)
    )

    assert suggestion.unchanged is False
    assert suggestion.parameters.get("min_confidence") > tuner.base_confidence
    assert suggestion.parameters.get("risk_per_trade") < tuner.base_risk
    assert len(suggestion.reasons) == 3


def test_the_tuner_can_never_produce_a_looser_set():
    """Swept, rather than argued: no combination of inputs loosens."""
    tuner = AutoTuner()
    current = ParameterSet({"min_confidence": tuner.base_confidence,
                            "risk_per_trade": tuner.base_risk})

    for win_rate in (0.0, 0.25, 0.44, 0.45, 0.5, 0.65, 0.9, 1.0):
        for avg_pnl in (-100.0, -0.01, 0.0, 0.01, 100.0):
            for streak in (0, 2, 3, 10):
                suggestion = tuner.suggest(
                    _performance(win_rate, avg_pnl, streak)
                )
                problems = loosening_problems(suggestion.parameters, current)
                assert problems == [], (win_rate, avg_pnl, streak, problems)


def test_too_few_trades_produces_no_change():
    tuner = AutoTuner()
    suggestion = tuner.suggest(
        _performance(win_rate=0.0, avg_pnl=-100.0, loss_streak=9,
                     trades=MINIMUM_TRADES - 1)
    )
    assert suggestion.unchanged is True
    assert "closed trade(s)" in suggestion.reasons[0]


def test_a_missing_trade_log_produces_no_change_rather_than_neutral_numbers():
    """It used to return (0.5, 0, 0), which a caller could not distinguish
    from a genuinely break-even record."""
    tuner = AutoTuner()
    assert tuner.evaluate_performance("/nonexistent/trade_log.csv") is None

    suggestion = tuner.suggest(None)
    assert suggestion.unchanged is True
    assert "nothing to conclude" in suggestion.reasons[0]


def test_the_legacy_signature_still_works_and_cannot_loosen():
    tuner = AutoTuner()

    confidence, risk = tuner.adjust_parameters(0.9, 100.0, 0)
    assert confidence == tuner.base_confidence
    assert risk == tuner.base_risk

    confidence, risk = tuner.adjust_parameters(0.2, -50.0, 6)
    assert confidence > tuner.base_confidence
    assert risk < tuner.base_risk


def test_the_streak_helper_counts_from_the_most_recent_trade():
    tuner = AutoTuner()
    assert tuner._calc_streak([1.0, -1.0, -1.0, -1.0]) == 3
    assert tuner._calc_streak([-1.0, -1.0, 1.0]) == 0
    assert tuner._calc_streak([]) == 0


def test_a_trade_log_is_read_when_it_exists(tmp_path):
    log = tmp_path / "trade_log.csv"
    log.write_text("pnl\n1.0\n-1.0\n-2.0\n", encoding="utf-8")

    performance = AutoTuner().evaluate_performance(str(log))

    assert performance is not None
    assert performance["trades"] == 3
    assert performance["win_rate"] == pytest.approx(1 / 3)
    assert performance["loss_streak"] == 2


def test_a_trade_log_with_no_pnl_column_is_refused(tmp_path):
    log = tmp_path / "trade_log.csv"
    log.write_text("symbol\nAAPL\n", encoding="utf-8")
    assert AutoTuner().evaluate_performance(str(log)) is None
