"""ATOS-P3-ML-001 — a learned policy is an artifact, not a running process.

An RL agent is the one component here that changes its own behaviour, which
breaks three habits that work everywhere else: reviewing the code does not
review the model, "it is doing well" is not evidence, and a guard expressed
inside the objective is subject to the objective.

So: the artifact is hashed over everything that determines what it will do,
promotion requires out-of-sample evidence across regimes, and the guard sits
outside the network where it cannot be trained around.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.advanced_rl import (  # noqa: E402
    DQNAgent,
    PPOAgent,
    RLConfig,
    TradingEnvironment,
)
from core.model_governance import (  # noqa: E402
    MINIMUM_REGIMES,
    ActionLimits,
    CatastrophicAction,
    CatastrophicActionGuard,
    DataSplit,
    DistributionShiftDetector,
    ModelArtifact,
    ModelRegistry,
    ModelStage,
    NotPromoted,
    ObservationSchema,
    Phase,
    PromotionEvidence,
    RegimeResult,
    RewardCosts,
    SchemaMismatch,
    SplitGuard,
    SplitViolation,
    config_digest,
    make_split,
    risk_adjusted_reward,
    weights_digest,
)

pytestmark = pytest.mark.adversarial


def _schema(names=("ret_1", "ret_5", "vol_20"), version="v1"):
    return ObservationSchema(
        feature_names=tuple(names), version=version,
        appended_state=("position", "balance", "unrealized_pnl"),
    )


def _artifact(**overrides):
    defaults = dict(
        model_id="ppo-2026-03", algorithm="ppo", schema=_schema(), seed=7,
        weights="abc123", config="def456", split=make_split(1000),
    )
    defaults.update(overrides)
    return ModelArtifact(**defaults)


def _regimes(n=MINIMUM_REGIMES, sharpe=1.2, trades=50):
    names = ["bull", "bear", "sideways", "high_vol", "low_vol"]
    return [RegimeResult(regime=names[i], sharpe=sharpe, max_drawdown=0.1,
                         trades=trades) for i in range(n)]


def _evidence(**overrides):
    defaults = dict(
        artifact=_artifact(), regime_results=_regimes(), shadow_sessions=3,
        approved_by="risk-officer", costs_modelled=True, guard_active=True,
    )
    defaults.update(overrides)
    return PromotionEvidence(**defaults)


# ---------------------------------------------------------------------------
# Observation schema
# ---------------------------------------------------------------------------


def test_the_same_features_in_a_different_order_are_different_inputs():
    """state_dim: int = 50 accepts any fifty numbers. This does not."""
    trained = _schema(("a", "b", "c"))
    served = _schema(("c", "b", "a"))

    assert trained.width == served.width
    problems = trained.compatibility_problems(served)
    assert problems and "different order" in problems[0]
    with pytest.raises(SchemaMismatch):
        trained.assert_compatible(served)


def test_missing_and_extra_features_are_named():
    problems = _schema(("a", "b")).compatibility_problems(_schema(("a", "z")))
    assert any("missing feature(s): b" in p for p in problems)
    assert any("unexpected feature(s): z" in p for p in problems)


def test_an_identical_schema_is_compatible():
    """Otherwise every mismatch test above passes for the wrong reason."""
    assert _schema().compatibility_problems(_schema()) == []
    _schema().assert_compatible(_schema())


def test_the_fingerprint_changes_with_order_membership_and_version():
    base = _schema(("a", "b"))
    assert base.fingerprint() != _schema(("b", "a")).fingerprint()
    assert base.fingerprint() != _schema(("a", "b", "c")).fingerprint()
    assert base.fingerprint() != _schema(("a", "b"), version="v2").fingerprint()
    assert base.fingerprint() == _schema(("a", "b")).fingerprint()


def test_differing_appended_state_is_a_mismatch():
    left = ObservationSchema(("a",), "v1", appended_state=("position",))
    right = ObservationSchema(("a",), "v1", appended_state=("position", "cash"))
    assert left.compatibility_problems(right)


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------


def test_a_split_leaves_a_purge_between_every_phase():
    split = make_split(1000, purge=50)
    assert split.problems() == []
    assert split.gaps() == (50, 50)
    assert split.train[1] < split.validation[0] < split.validation[1] < split.test[0]


def test_a_split_that_cannot_fit_the_purge_is_refused():
    with pytest.raises(ValueError):
        make_split(60, purge=50)


def test_nonsense_fractions_are_refused():
    for kwargs in ({"train_frac": 0.0}, {"validation_frac": 1.5},
                   {"train_frac": 0.8, "validation_frac": 0.3}):
        with pytest.raises(ValueError):
            make_split(1000, **kwargs)


def test_a_hand_built_split_with_no_gap_reports_it():
    split = DataSplit(train=(0, 100), validation=(100, 150),
                      test=(150, 200), purge=20)
    problems = split.problems()
    assert len(problems) == 2
    assert all("are required" in p for p in problems)


def test_the_guard_notices_a_phase_reading_outside_its_range():
    split = make_split(1000)
    guard = SplitGuard(split)

    guard.observe(Phase.TRAIN, range(*split.train))
    guard.observe(Phase.TEST, range(*split.test))
    assert guard.violations() == []

    # One bar past the boundary - the off-by-one nobody sees in a diff.
    guard.observe(Phase.TRAIN, [split.train[1] + 1])
    problems = guard.violations()
    assert problems and "outside its range" in problems[0]

    with pytest.raises(SplitViolation):
        guard.assert_clean()


def test_the_guard_notices_two_phases_reading_the_same_row():
    split = make_split(1000)
    guard = SplitGuard(split)
    guard.observe(Phase.TRAIN, [10, 11, 12])
    guard.observe(Phase.TEST, [10])

    problems = guard.violations()
    assert any("both read" in p for p in problems)


def test_a_clean_run_produces_no_violations():
    split = make_split(1000)
    guard = SplitGuard(split)
    for phase in Phase:
        guard.observe(phase, split.indices(phase))
    guard.assert_clean()


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_the_weights_digest_distinguishes_two_models():
    left = [(np.zeros((2, 2)), np.zeros(2))]
    right = [(np.ones((2, 2)), np.zeros(2))]

    assert weights_digest(left) == weights_digest([(np.zeros((2, 2)), np.zeros(2))])
    assert weights_digest(left) != weights_digest(right)


def test_the_weights_digest_notices_a_reshape():
    assert weights_digest(np.zeros((4, 1))) != weights_digest(np.zeros((1, 4)))


def test_the_config_digest_is_stable_and_discriminating():
    assert config_digest({"lr": 0.1}) == config_digest({"lr": 0.1})
    assert config_digest({"lr": 0.1}) != config_digest({"lr": 0.2})
    assert config_digest(RLConfig(seed=1)) != config_digest(RLConfig(seed=2))


def test_the_fingerprint_covers_everything_that_changes_behaviour():
    base = _artifact()
    for field, value in (
        ("algorithm", "dqn"), ("seed", 8), ("weights", "zzz"),
        ("config", "yyy"), ("schema", _schema(("x",))),
    ):
        assert _artifact(**{field: value}).fingerprint() != base.fingerprint(), field
    # The name is not part of the identity: renaming a model does not make it
    # a different model.
    assert _artifact(model_id="renamed").fingerprint() == base.fingerprint()


def test_an_unseeded_model_is_not_reproducible():
    assert _artifact(seed=None).reproducible is False
    assert _artifact(seed=0).reproducible is True   # zero is a seed


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_complete_evidence_is_sufficient():
    """The baseline. Every refusal test below breaks exactly one thing."""
    assert _evidence().problems() == []
    assert _evidence().sufficient is True


@pytest.mark.parametrize("override,expected", [
    ({"artifact": _artifact(seed=None)}, "cannot be reproduced"),
    ({"artifact": _artifact(split=None)}, "no train/validation/test split"),
    ({"regime_results": _regimes(n=1)}, "regime(s)"),
    ({"regime_results": _regimes(trades=3)}, "regime(s)"),
    ({"regime_results": _regimes(sharpe=-0.5)}, "negative or zero"),
    ({"costs_modelled": False}, "transaction costs"),
    ({"guard_active": False}, "guard was not active"),
    ({"shadow_sessions": 0}, "shadow session"),
    ({"approved_by": ""}, "no human has approved"),
])
def test_each_missing_piece_of_evidence_blocks_promotion(override, expected):
    problems = _evidence(**override).problems()
    assert any(expected in p for p in problems), problems


def test_a_registry_refuses_to_serve_an_unknown_model():
    registry = ModelRegistry()
    allowed, reason = registry.may_serve_live("never-heard-of-it")
    assert allowed is False
    assert "not in the registry" in reason


def test_registering_a_model_does_not_promote_it():
    registry = ModelRegistry()
    registry.register(_artifact())
    assert registry.may_serve_live("ppo-2026-03")[0] is False
    assert registry.stage("ppo-2026-03") is ModelStage.TRAINING


def test_a_model_cannot_be_registered_straight_into_production():
    with pytest.raises(NotPromoted):
        ModelRegistry().register(_artifact(), stage=ModelStage.PROMOTED)


def test_promotion_with_insufficient_evidence_is_refused_and_recorded():
    registry = ModelRegistry()
    registry.register(_artifact())

    with pytest.raises(NotPromoted):
        registry.promote(_evidence(approved_by=""))

    assert registry.may_serve_live("ppo-2026-03")[0] is False
    refusals = [e for e in registry.audit if e["outcome"] == "refused"]
    assert refusals and "no human has approved" in refusals[0]["detail"]


def test_a_promoted_model_may_serve_and_a_retired_one_may_not():
    registry = ModelRegistry()
    registry.register(_artifact())
    registry.promote(_evidence())

    assert registry.may_serve_live("ppo-2026-03")[0] is True
    assert registry.require_live("ppo-2026-03").model_id == "ppo-2026-03"

    registry.retire("ppo-2026-03", "superseded")
    assert registry.may_serve_live("ppo-2026-03")[0] is False
    with pytest.raises(NotPromoted):
        registry.require_live("ppo-2026-03")


def test_the_audit_records_who_promoted_what():
    registry = ModelRegistry()
    registry.register(_artifact())
    registry.promote(_evidence(approved_by="risk-officer"))
    promotions = [e for e in registry.audit if e["outcome"] == "promoted"]
    assert promotions and promotions[0]["detail"] == "risk-officer"


# ---------------------------------------------------------------------------
# The guard outside the network
# ---------------------------------------------------------------------------


def _permit(guard, **overrides):
    kwargs = dict(action=0, intended_position=0.5, notional=1_000.0,
                  equity=10_000.0)
    kwargs.update(overrides)
    guard.permit(**kwargs)


def test_an_action_within_the_limits_is_permitted():
    """The baseline for the refusals below."""
    _permit(CatastrophicActionGuard())


def test_an_action_outside_the_action_space_is_refused():
    guard = CatastrophicActionGuard(ActionLimits(permitted_actions=(0, 1, 2)))
    with pytest.raises(CatastrophicAction):
        _permit(guard, action=7)


def test_a_position_beyond_the_limit_is_refused():
    guard = CatastrophicActionGuard(ActionLimits(max_position_units=1.0))
    with pytest.raises(CatastrophicAction):
        _permit(guard, intended_position=2.5)


def test_a_notional_beyond_the_limit_is_refused():
    guard = CatastrophicActionGuard(ActionLimits(max_notional=1_000.0))
    with pytest.raises(CatastrophicAction):
        _permit(guard, notional=5_000.0)


def test_adding_risk_while_a_limit_is_tripped_is_refused():
    guard = CatastrophicActionGuard()
    with pytest.raises(CatastrophicAction):
        _permit(guard, risk_tripped=True)
    # Reducing is still allowed - a freeze must not trap a position.
    _permit(guard, risk_tripped=True, reducing=True)


def test_turnover_accumulates_across_a_session_and_then_blocks():
    guard = CatastrophicActionGuard(ActionLimits(max_turnover=2.0,
                                                 max_notional=10_000.0))
    _permit(guard, notional=9_000.0, equity=10_000.0)
    _permit(guard, notional=9_000.0, equity=10_000.0)
    with pytest.raises(CatastrophicAction):
        _permit(guard, notional=9_000.0, equity=10_000.0)

    guard.reset_session()
    _permit(guard, notional=9_000.0, equity=10_000.0)


def test_every_refusal_is_recorded_with_its_reasons():
    guard = CatastrophicActionGuard(ActionLimits(max_notional=100.0))
    with pytest.raises(CatastrophicAction):
        _permit(guard, notional=1_000.0)
    assert guard.refusals and guard.refusals[0]["reasons"]


def test_the_guard_is_not_part_of_the_reward():
    """Stated as a test because it is the design point.

    A guard implemented as a penalty term is inside the objective the network
    is optimising, and a network that finds the penalty worth paying will pay
    it. This guard raises; there is no number to trade off against.
    """
    import inspect

    # It takes no reward, returns no reward, and has no reward to weigh: the
    # only outcome of a refusal is an exception.
    for method in (CatastrophicActionGuard.check, CatastrophicActionGuard.permit):
        parameters = inspect.signature(method).parameters
        assert not any("reward" in name for name in parameters), method

    assert not any(
        "reward" in name for name in vars(CatastrophicActionGuard(ActionLimits()))
    )
    assert inspect.signature(
        CatastrophicActionGuard.permit
    ).return_annotation in (None, "None")

    guard = CatastrophicActionGuard(ActionLimits(max_notional=1.0))
    with pytest.raises(CatastrophicAction):
        _permit(guard, notional=1_000.0)


# ---------------------------------------------------------------------------
# Distribution shift
# ---------------------------------------------------------------------------


def _detector(threshold=4.0):
    return DistributionShiftDetector(
        feature_names=["a", "b", "c"], means=[0.0, 10.0, -5.0],
        stds=[1.0, 2.0, 0.5], threshold=threshold,
    )


def test_an_in_distribution_observation_is_eligible():
    allowed, reason = _detector().eligibility([0.5, 10.5, -5.1])
    assert allowed is True and reason == ""


def test_an_out_of_distribution_observation_falls_back_to_shadow():
    allowed, reason = _detector().eligibility([0.0, 10.0, 50.0])
    assert allowed is False
    assert "outside the training distribution" in reason
    assert "shadow" in reason
    assert "c" in reason


def test_the_report_names_every_drifted_feature():
    report = _detector().inspect([100.0, 10.0, 50.0])
    assert set(report.drifted_features) == {"a", "c"}
    assert report.shifted is True
    assert report.max_z > 4.0


def test_a_zero_standard_deviation_does_not_divide_by_zero():
    detector = DistributionShiftDetector(["a"], [1.0], [0.0])
    assert detector.inspect([1.0]).max_z == 0.0


def test_an_observation_of_the_wrong_width_is_a_schema_error():
    with pytest.raises(SchemaMismatch):
        _detector().inspect([1.0, 2.0])


def test_mismatched_detector_inputs_are_refused_at_construction():
    with pytest.raises(ValueError):
        DistributionShiftDetector(["a", "b"], [0.0], [1.0])


# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------


def test_the_reward_charges_for_trading():
    free = risk_adjusted_reward(100.0, traded_notional=0.0, drawdown=0.0,
                                recent_volatility=0.0)
    traded = risk_adjusted_reward(100.0, traded_notional=50_000.0,
                                  drawdown=0.0, recent_volatility=0.0)
    assert free == 100.0
    assert traded < free


def test_the_reward_penalises_drawdown_and_volatility():
    base = risk_adjusted_reward(100.0, 0.0, 0.0, 0.0)
    assert risk_adjusted_reward(100.0, 0.0, 5_000.0, 0.0) < base
    assert risk_adjusted_reward(100.0, 0.0, 0.0, 500.0) < base


def test_the_default_cost_model_is_not_free():
    assert RewardCosts().free() is False
    assert RewardCosts().round_trip_pct() > 0
    assert RewardCosts(commission_pct=0, half_spread_pct=0,
                       slippage_pct=0).free() is True


# ---------------------------------------------------------------------------
# The agents themselves
# ---------------------------------------------------------------------------


def test_the_same_seed_produces_the_same_agent():
    config = dict(state_dim=8, action_dim=3)
    left = DQNAgent(RLConfig(seed=7, **config))
    right = DQNAgent(RLConfig(seed=7, **config))
    other = DQNAgent(RLConfig(seed=8, **config))

    def weights(agent):
        return agent.q_network.layers[0].weights

    assert np.array_equal(weights(left), weights(right))
    assert not np.array_equal(weights(left), weights(other))


def test_a_seeded_agent_takes_the_same_actions():
    state = np.zeros(8)
    left = DQNAgent(RLConfig(seed=11, state_dim=8, action_dim=3))
    right = DQNAgent(RLConfig(seed=11, state_dim=8, action_dim=3))
    assert [left.select_action(state) for _ in range(20)] == \
           [right.select_action(state) for _ in range(20)]


def test_an_agent_does_not_draw_from_the_global_rng():
    """Otherwise anything else in the process perturbs the training run."""
    agent = DQNAgent(RLConfig(seed=3, state_dim=8, action_dim=3))
    state = np.zeros(8)

    np.random.seed(1)
    first = [agent.select_action(state) for _ in range(10)]

    agent = DQNAgent(RLConfig(seed=3, state_dim=8, action_dim=3))
    np.random.seed(999)
    for _ in range(50):
        np.random.random()
    second = [agent.select_action(state) for _ in range(10)]

    assert first == second


def test_ppo_is_seeded_too():
    left = PPOAgent(RLConfig(seed=5, state_dim=6, action_dim=3))
    right = PPOAgent(RLConfig(seed=5, state_dim=6, action_dim=3))
    assert np.array_equal(left.actor.layers[0].weights,
                          right.actor.layers[0].weights)


def test_an_unseeded_agent_is_still_usable_but_unpromotable():
    """Exploration is allowed; promoting the result of it is not."""
    agent = DQNAgent(RLConfig(state_dim=4, action_dim=3))
    assert agent.select_action(np.zeros(4)) in (0, 1, 2)
    assert _artifact(seed=None).reproducible is False


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


def _env(**kwargs):
    rng = np.random.default_rng(0)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))
    features = rng.normal(0, 1, (200, 5))
    return TradingEnvironment(prices, features, **kwargs)


def _drive(env, steps=120):
    env.reset()
    total = 0.0
    for i in range(steps):
        _, reward, done, _ = env.step(i % 3)
        total += reward
        if done:
            break
    return total


def test_trading_costs_the_reward_something():
    free = RewardCosts(commission_pct=0, half_spread_pct=0, slippage_pct=0,
                       drawdown_penalty=0, volatility_penalty=0)
    assert _drive(_env()) < _drive(_env(costs=free))


def test_the_guard_can_force_a_hold():
    guard = CatastrophicActionGuard(ActionLimits(max_notional=1.0))
    env = _env(guard=guard)
    _drive(env)

    assert env.refused_actions > 0
    assert env.trades == [] or all(t is not None for t in env.trades)


def test_equity_is_balance_plus_unrealised_not_balance_plus_notional():
    """The environment never debits the notional on entry, so adding
    position * price would count the same money twice and make every buy
    look like it doubled the account."""
    env = _env()
    env.reset()
    _, _, _, info = env.step(0)   # buy
    assert info["equity"] < env.initial_balance * 1.5


def test_the_unrealised_reward_term_is_dimensionally_the_position_s_value():
    """It was position * price_change * initial_balance * 0.01, which scaled
    the term by an arbitrary initial_balance/price factor."""
    prices = np.array([100.0, 110.0, 110.0, 110.0])
    features = np.zeros((4, 2))
    free = RewardCosts(commission_pct=0, half_spread_pct=0, slippage_pct=0,
                       drawdown_penalty=0, volatility_penalty=0)
    env = TradingEnvironment(prices, features, initial_balance=1_000.0,
                             commission=0.0, costs=free)
    env.reset()
    _, reward, _, _ = env.step(0)   # buy at 100, price moves to 110

    # 10 units bought with 1,000 at 100; a 10% move is worth 100.
    assert reward == pytest.approx(100.0, rel=1e-6)


def test_a_recorded_schema_travels_with_the_environment():
    schema = _schema()
    env = _env(schema=schema)
    assert env.schema is schema
    assert env.schema.fingerprint() == schema.fingerprint()
