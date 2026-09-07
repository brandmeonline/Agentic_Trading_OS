"""ATOS-P3-BT-001 — leak traps, and fills nobody could have got.

Lookahead is the bug that flatters. It does not crash, it does not warn, and
it produces exactly the number the author hoped for. Reading the code is the
defence that has already failed by the time the bug exists, so these tests do
not read code: they check a property.

The property is **prefix invariance**. Given only the first k bars, a causal
transform must produce, for those k bars, exactly what it produces given the
whole series. If truncating the future changes the past, the past was reading
the future. That single check catches full-series normalisation, a centred
window, a back-filled gap, a scaler fitted before the split, and a wrap-around
shift that puts the last bar of history before the first.

It found three real leaks in this repository, all pinned below.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.advanced_backtest import WalkForwardConfig, WalkForwardOptimizer  # noqa: E402
from core.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402
from core.feature_engine import (  # noqa: E402
    FeatureConfig,
    FeatureEngine,
    ema,
    shift1,
)
from core.fill_model import (  # noqa: E402
    Bar,
    FillCosts,
    FillModel,
    FillTiming,
    ImpossibleFill,
)
from core.leakage import (  # noqa: E402
    LeakageDetected,
    TrainOnlyScaler,
    Window,
    assert_causal,
    causality_violations,
    full_series_normalisation_leak,
    judge_canary,
    overlap_problems,
    purged_windows,
    required_gap,
    unpredictable_series,
)

pytestmark = pytest.mark.adversarial

PRICES = unpredictable_series(400, seed=20260101)


# ---------------------------------------------------------------------------
# The trap itself must be able to catch a leak
# ---------------------------------------------------------------------------


def _causal_sma(series, period=10):
    out = []
    for i in range(len(series)):
        window = series[max(0, i - period + 1):i + 1]
        out.append(sum(window) / len(window))
    return out


def _centred_sma(series, period=11):
    """A centred window. Every value averages five bars of the future."""
    half = period // 2
    out = []
    for i in range(len(series)):
        window = series[max(0, i - half):i + half + 1]
        out.append(sum(window) / len(window))
    return out


def _full_series_zscore(series):
    mean = sum(series) / len(series)
    std = (sum((v - mean) ** 2 for v in series) / len(series)) ** 0.5 or 1.0
    return [(v - mean) / std for v in series]


def _backfilled(series):
    """Gaps filled from the next observation - the future, by definition."""
    out = list(series)
    for i in range(len(out) - 2, -1, -1):
        if i % 7 == 0:
            out[i] = out[i + 1]
    return out


def test_the_trap_passes_a_causal_transform():
    """Otherwise every test below passes because the trap never fires."""
    assert causality_violations(_causal_sma, PRICES) == []
    assert_causal(_causal_sma, PRICES, name="causal sma")


@pytest.mark.parametrize("leaky,label", [
    (_centred_sma, "a centred window"),
    (_full_series_zscore, "full-series normalisation"),
    (_backfilled, "back-filling from the next bar"),
])
def test_the_trap_catches_each_classic_leak(leaky, label):
    violations = causality_violations(leaky, PRICES)
    assert violations, f"{label} was not detected"

    with pytest.raises(LeakageDetected) as caught:
        assert_causal(leaky, PRICES, name=label)
    assert "reads the future" in str(caught.value)


def test_a_warmup_allowance_does_not_excuse_a_later_leak():
    """A transform that settles down has still leaked for the earlier bars."""
    violations = causality_violations(_centred_sma, PRICES, warmup=100)
    assert violations


# ---------------------------------------------------------------------------
# Leaks this found in the repository
# ---------------------------------------------------------------------------


def test_the_feature_normalisation_window_does_not_depend_on_the_dataset_length():
    """It was min(200, len(features) // 2).

    That looks harmless and is not: the normalised value of bar 37 depended on
    how many bars came after it, so the same bar normalised differently in a
    600-bar backtest than in a 300-bar one, and differently again live.
    """
    engine = FeatureEngine(FeatureConfig())

    def normalise(series):
        column = np.array(series, dtype=float).reshape(-1, 1)
        return list(engine._normalize(column)[:, 0])

    assert_causal(normalise, unpredictable_series(600, seed=11), warmup=5,
                  name="FeatureEngine._normalize")


def test_shift1_does_not_wrap_the_end_of_history_onto_the_start():
    """np.roll(x, 1) puts x[-1] at position 0.

    On its own that is one wrong value. Fed into an EMA, which is recursive
    from index 0, the last bar of the dataset contaminates every bar in it -
    which is exactly what adx, plus_di and trend_strength were doing.
    """
    data = np.array([1.0, 2.0, 3.0, 99.0])

    rolled = np.roll(data, 1)
    assert rolled[0] == 99.0  # the leak, demonstrated

    shifted = shift1(data)
    assert shifted[0] == 1.0
    assert list(shifted[1:]) == [1.0, 2.0, 3.0]
    assert 99.0 not in list(shifted[:-1])


def test_the_wrap_would_have_contaminated_every_bar_through_the_ema():
    """Why the single wrong value at index 0 mattered so much."""
    data = np.array([1.0] * 50 + [1000.0])

    from_wrapped = ema(np.roll(data, 1), 14)
    from_shifted = ema(shift1(data), 14)

    # The wrapped version starts at the final value and decays from it, so a
    # bar in the middle of the series differs by orders of magnitude.
    assert from_wrapped[0] == 1000.0
    assert from_shifted[0] == 1.0
    assert abs(from_wrapped[20] - from_shifted[20]) > 1.0


def test_every_feature_column_is_causal():
    """The sweep that found the wrap. 102 columns, none may read forward."""
    engine = FeatureEngine(FeatureConfig())
    prices = np.array(unpredictable_series(400, seed=3))

    def compute(close):
        return engine.compute_features(
            open_=close, high=close * 1.005, low=close * 0.995,
            close=close, volume=np.full(len(close), 1e6),
        )

    full = compute(prices)
    cut = 320
    truncated = compute(prices[:cut])

    leaking = []
    for column in range(full.features.shape[1]):
        delta = np.nanmax(np.abs(
            truncated.features[200:cut, column] - full.features[200:cut, column]
        ))
        if delta > 1e-9:
            leaking.append((full.feature_names[column], float(delta)))

    assert full.features.shape[1] > 50, "the sweep must actually cover features"
    assert leaking == []


def test_the_feature_pipeline_runs_at_all():
    """np.math.factorial was removed in NumPy 2; the default path raised."""
    engine = FeatureEngine(FeatureConfig())
    close = np.array(unpredictable_series(300, seed=5))
    result = engine.compute_features(
        open_=close, high=close * 1.01, low=close * 0.99, close=close,
        volume=np.full(len(close), 1e6), include_fractal=True,
    )
    assert result.features.shape[0] == 300


# ---------------------------------------------------------------------------
# Purging and embargo
# ---------------------------------------------------------------------------


def test_the_required_gap_accounts_for_all_three_reasons():
    assert required_gap(feature_lookback=20, label_horizon=5, embargo=3) == 28
    assert required_gap(0, 0, 0) == 0
    assert required_gap(-5, 0, 0) == 0


def test_purged_windows_leave_a_real_gap():
    windows = purged_windows(
        n_bars=1000, train_size=200, test_size=50,
        feature_lookback=20, label_horizon=5, embargo=3,
    )
    assert windows
    for window in windows:
        assert window.gap == 28
        assert window.train_end <= window.test_start
        assert window.train_length == 200
        assert window.test_length == 50
    assert overlap_problems(windows, minimum_gap=28) == []


def test_a_zero_gap_is_reported_as_a_problem():
    """The trap for the naive split: test starts where train ended."""
    windows = [Window(0, 200, 200, 250)]
    problems = overlap_problems(windows, minimum_gap=25)
    assert problems and "25 are needed" in problems[0]


def test_overlapping_windows_are_reported():
    problems = overlap_problems([Window(0, 300, 250, 400)], minimum_gap=0)
    assert problems and "they overlap" in problems[0]


def test_windows_are_dropped_rather_than_shrinking_the_gap():
    """Not enough history to test a period honestly means not testing it."""
    windows = purged_windows(
        n_bars=210, train_size=200, test_size=50, feature_lookback=100,
    )
    assert windows == []


def test_purged_windows_rejects_nonsense_sizes():
    for kwargs in ({"train_size": 0}, {"test_size": 0}, {"step": 0}):
        base = {"n_bars": 500, "train_size": 100, "test_size": 20}
        base.update(kwargs)
        with pytest.raises(ValueError):
            purged_windows(**base)


# ---------------------------------------------------------------------------
# Normalisation discipline
# ---------------------------------------------------------------------------


def test_a_scaler_cannot_be_fitted_twice():
    scaler = TrainOnlyScaler().fit([1.0, 2.0, 3.0])
    with pytest.raises(LeakageDetected):
        scaler.fit([4.0, 5.0, 6.0])


def test_a_scaler_cannot_transform_before_it_is_fitted():
    with pytest.raises(LeakageDetected):
        TrainOnlyScaler().transform([1.0, 2.0])


def test_train_only_statistics_differ_from_full_series_statistics():
    """If they matched, the pipeline normalised over the whole series."""
    series = list(range(100))
    assert full_series_normalisation_leak(series, train_end=50)


def test_a_scaler_fitted_on_train_applies_the_same_numbers_to_test():
    scaler = TrainOnlyScaler().fit([0.0, 10.0])
    assert scaler.mean == 5.0
    assert scaler.transform([5.0]) == [0.0]
    # The test set does not move the mean, which is the whole point.
    scaler.transform([1000.0])
    assert scaler.mean == 5.0


def test_a_constant_training_set_does_not_divide_by_zero():
    scaler = TrainOnlyScaler().fit([3.0, 3.0, 3.0])
    assert scaler.transform([3.0, 4.0]) == [0.0, 1.0]


# ---------------------------------------------------------------------------
# Fill realism
# ---------------------------------------------------------------------------


def test_same_bar_close_fills_are_refused_by_construction():
    """Not configurable. The timing exists so it can be named and rejected."""
    with pytest.raises(ImpossibleFill):
        FillModel(timing=FillTiming.SAME_BAR_CLOSE)


def test_filling_on_the_deciding_bar_is_refused():
    model = FillModel()
    bar = Bar(open=100, high=101, low=99, close=100.5, volume=10_000)
    with pytest.raises(ImpossibleFill):
        model.fill("buy", 10, fill_bar=bar, decision_bar=bar)


def test_costs_always_run_against_the_trader():
    model = FillModel()
    bar = Bar(open=100, high=101, low=99, close=100.5, volume=1_000_000)

    buy = model.fill("buy", 100, bar)
    sell = model.fill("sell", 100, bar)

    assert buy.price > bar.open
    assert sell.price < bar.open
    assert buy.commission > 0 and sell.commission > 0


def test_a_round_trip_is_not_free():
    model = FillModel()
    assert model.round_trip_cost_pct() > 0

    bar = Bar(open=100, high=100, low=100, close=100, volume=1_000_000)
    buy = model.fill("buy", 10, bar)
    sell = model.fill("sell", 10, bar)
    proceeds = sell.notional - sell.commission
    outlay = buy.notional + buy.commission
    assert proceeds < outlay, "buying and selling at the same price made money"


def test_size_is_capped_by_what_the_bar_actually_traded():
    model = FillModel(FillCosts(max_participation=0.10))
    bar = Bar(open=100, high=101, low=99, close=100, volume=1_000)

    fill = model.fill("buy", 5_000, bar)

    assert fill.quantity == 100  # 10% of 1,000
    assert fill.capped_by_liquidity is True
    assert fill.unfilled_quantity == 4_900


def test_a_bar_with_no_volume_has_no_liquidity():
    """Missing data is not permission to trade infinite size."""
    model = FillModel()
    fill = model.fill("buy", 10, Bar(open=100, high=100, low=100, close=100,
                                     volume=0))
    assert fill.filled is False
    assert fill.capped_by_liquidity is True


def test_taking_more_of_the_bar_costs_more_per_share():
    model = FillModel(FillCosts(impact_pct_at_full_participation=0.01))
    bar = Bar(open=100, high=101, low=99, close=100, volume=10_000)

    small = model.fill("buy", 10, bar)
    large = model.fill("buy", 1_000, bar)

    assert large.price > small.price


def test_next_bar_open_and_next_bar_close_price_differently():
    bar = Bar(open=100, high=110, low=90, close=105, volume=1_000_000)
    at_open = FillModel(timing=FillTiming.NEXT_BAR_OPEN).fill("buy", 10, bar)
    at_close = FillModel(timing=FillTiming.NEXT_BAR_CLOSE).fill("buy", 10, bar)
    assert at_open.price < at_close.price


def test_a_free_cost_model_is_recognisable_as_such():
    assert FillCosts().free() is False
    assert FillCosts(half_spread_pct=0, commission_pct=0, slippage_pct=0,
                     impact_pct_at_full_participation=0).free() is True


def test_an_unknown_side_is_refused():
    with pytest.raises(ValueError):
        FillModel().fill("hodl", 1, Bar(100, 100, 100, 100, 1_000))


# ---------------------------------------------------------------------------
# The backtest engine
# ---------------------------------------------------------------------------


class _AlwaysBuy:
    """Buys on every bar, so the fill timing is the only variable."""

    name = "always-buy"

    def evaluate(self, data, symbol):
        from core.strategy import StrategyOutput, StrategySignal
        return StrategyOutput(
            signal=StrategySignal.BUY, confidence=1.0, asset=symbol,
            reasoning="test", metadata={"strategy": self.name},
        )


class _ScriptedFeed:
    """A deterministic feed whose bars are all different, so a fill price
    identifies the bar it came from."""

    def __init__(self, closes, volume=1_000_000.0):
        from core.market_data import OHLCV
        start = datetime(2026, 1, 2, 14, 30)
        self.bars = [
            OHLCV(
                timestamp=start + timedelta(minutes=i),
                # open and close deliberately far apart so a fill at the open
                # cannot be mistaken for a fill at the close.
                open=close * 0.90, high=close * 1.10, low=close * 0.80,
                close=close, volume=volume,
            )
            for i, close in enumerate(closes)
        ]
        self.index = 0

    def connect(self):
        return True

    def disconnect(self):
        return True

    def get_latest_bar(self, symbol):
        if self.index >= len(self.bars):
            return None
        return self.bars[self.index]

    def advance_time(self):
        self.index += 1


def _run(closes, **config):
    feed = _ScriptedFeed(closes)
    engine = BacktestEngine(BacktestConfig(
        warmup_bars=2, position_sizing_pct=0.5, max_positions=1, **config
    ))
    engine.set_data_feed(feed)
    engine.add_strategy(_AlwaysBuy())

    # Drive the loop by hand so advance_time() runs for a scripted feed.
    engine.data_feed = feed
    feed.connect()
    engine._reset()
    for _ in range(len(closes)):
        bar = feed.get_latest_bar("TEST")
        if bar:
            engine._process_bar("TEST", bar)
        feed.advance_time()
    engine._close_all_positions()
    return engine, feed


def test_a_signal_from_a_bar_never_fills_at_that_bar_s_price():
    """The headline defect. The engine generated a signal from a buffer that
    already contained the bar - including its close - and then filled at that
    same close.

    The scripted feed puts each bar's open 10% below its close, and costs are
    a fraction of a percent, so "filled near the open" and "filled near the
    close" are ten percent apart and cannot be confused. Comparing against
    the raw close would not discriminate: the old engine filled at
    close * (1 + slippage), which is not equal to any close either.
    """
    closes = [100.0 + i for i in range(20)]
    engine, feed = _run(closes)

    holdings = engine.trades + list(engine.positions.values())
    assert holdings, "the strategy never traded"

    by_timestamp = {bar.timestamp: bar for bar in feed.bars}
    for trade in holdings:
        bar = by_timestamp[trade.entry_time]
        assert abs(trade.entry_price - bar.open) / bar.open < 0.02, (
            f"entry {trade.entry_price} is not the fill bar's open {bar.open}"
        )
        assert abs(trade.entry_price - bar.close) / bar.close > 0.05, (
            f"entry {trade.entry_price} looks like the close {bar.close}"
        )


def test_the_entry_price_comes_from_the_bar_after_the_decision():
    closes = [100.0 + i * 10 for i in range(10)]
    engine, feed = _run(closes)

    trade = (engine.trades + list(engine.positions.values()))[0]
    decision_index = next(
        i for i, bar in enumerate(feed.bars)
        if bar.timestamp == trade.entry_time
    )
    fill_open = feed.bars[decision_index].open

    # Entry is the fill bar's open plus costs, and the fill bar is the one
    # whose timestamp the trade carries - which is the bar *after* the one
    # the signal was computed from.
    assert trade.entry_price > fill_open
    assert trade.entry_price < fill_open * 1.02
    assert decision_index >= engine.config.warmup_bars


def test_a_bar_with_no_volume_is_not_traded():
    closes = [100.0 + i for i in range(15)]
    feed = _ScriptedFeed(closes, volume=0.0)
    engine = BacktestEngine(BacktestConfig(warmup_bars=2, position_sizing_pct=0.5))
    engine.set_data_feed(feed)
    engine.add_strategy(_AlwaysBuy())
    engine._reset()
    for _ in range(len(closes)):
        bar = feed.get_latest_bar("TEST")
        if bar:
            engine._process_bar("TEST", bar)
        feed.advance_time()

    assert engine.trades == []
    assert engine.positions == {}
    assert engine.rejected_for_liquidity > 0


def test_position_size_is_capped_by_the_bar_s_volume():
    closes = [100.0] * 15
    feed = _ScriptedFeed(closes, volume=1.0)   # one unit traded per bar
    engine = BacktestEngine(BacktestConfig(
        warmup_bars=2, position_sizing_pct=1.0, initial_capital=1_000_000,
        max_participation=0.10,
    ))
    engine.set_data_feed(feed)
    engine.add_strategy(_AlwaysBuy())
    engine._reset()
    for _ in range(len(closes)):
        bar = feed.get_latest_bar("TEST")
        if bar:
            engine._process_bar("TEST", bar)
        feed.advance_time()

    holdings = engine.trades + list(engine.positions.values())
    assert holdings
    for trade in holdings:
        assert trade.quantity <= 0.1 + 1e-9


def test_holding_period_counts_bars_held_not_bars_elapsed():
    """It recorded self.bar_count, so every trade looked longer than the last."""
    closes = [100.0 + (i % 3) for i in range(30)]
    engine, _ = _run(closes)
    for trade in engine.trades:
        assert 0 <= trade.holding_period <= 30


def test_a_decision_on_the_final_bar_is_discarded_rather_than_filled():
    closes = [100.0 + i for i in range(10)]
    engine, _ = _run(closes)
    assert engine.pending == {}


def test_the_engine_charges_a_spread_by_default():
    assert BacktestConfig().half_spread_pct > 0
    assert BacktestConfig().commission_pct > 0
    assert BacktestConfig().max_participation > 0


# ---------------------------------------------------------------------------
# Walk-forward optimisation
# ---------------------------------------------------------------------------


def _sharpe(returns):
    returns = np.asarray(returns)
    if returns.size == 0:
        return 0.0
    return float(np.mean(returns) / (np.std(returns) + 1e-12) * np.sqrt(252))


def _optimizer(**overrides):
    config = dict(num_windows=3, min_in_sample_bars=150, step_size=60)
    config.update(overrides)
    return WalkForwardOptimizer(WalkForwardConfig(**config), object, _sharpe)


def test_walk_forward_runs_for_a_realistic_lookback():
    """It could not. signals[:-1] and returns[lookback:] have different
    lengths for every lookback but 1, so run() raised ValueError."""
    closes = np.array(unpredictable_series(900, seed=17))
    result = _optimizer().run({"close": closes},
                              {"lookback": [20, 40], "threshold": [0.0]})
    assert result.window_results
    assert result.combined_returns.size > 0


def test_the_out_of_sample_window_does_not_start_where_the_in_sample_ended():
    """It did. Every feature has a lookback, so the first out-of-sample
    decisions were computed from bars the parameters were fitted on."""
    closes = np.array(unpredictable_series(900, seed=17))
    result = _optimizer().run({"close": closes},
                              {"lookback": [20], "threshold": [0.0]})

    expected = WalkForwardConfig().purge_bars + WalkForwardConfig().embargo_bars
    assert expected > 0
    for window in result.window_results:
        assert window["oos_start"] - window["is_end"] == expected
        assert window["purge_gap"] == expected


def test_the_purge_gap_is_configurable_and_honoured():
    closes = np.array(unpredictable_series(1200, seed=19))
    result = _optimizer(purge_bars=100, embargo_bars=20).run(
        {"close": closes}, {"lookback": [20], "threshold": [0.0]}
    )
    for window in result.window_results:
        assert window["oos_start"] - window["is_end"] == 120


def test_the_strategy_signal_and_its_return_are_aligned_one_bar_apart():
    """A position taken at the close of bar i earns bar i's forward return -
    no more, and crucially no less than one bar of delay."""
    optimizer = _optimizer()
    closes = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    returns = optimizer._evaluate({"close": closes},
                                  {"lookback": 1, "threshold": 0.0})
    assert len(returns) == len(closes) - 1


def test_the_walk_forward_signal_function_is_causal():
    optimizer = _optimizer()

    def signals(series):
        closes = np.array(series, dtype=float)
        return list(optimizer._evaluate({"close": closes},
                                        {"lookback": 20, "threshold": 0.0}))

    assert_causal(signals, unpredictable_series(400, seed=23), warmup=25,
                  name="WalkForwardOptimizer._evaluate")


# ---------------------------------------------------------------------------
# The canary
# ---------------------------------------------------------------------------


def test_the_canary_series_is_deterministic():
    assert unpredictable_series(50, seed=1) == unpredictable_series(50, seed=1)
    assert unpredictable_series(50, seed=1) != unpredictable_series(50, seed=2)


def test_a_large_return_on_noise_is_judged_a_leak():
    verdict = judge_canary(total_return=0.85, trades=40)
    assert verdict.leaked
    assert "seen the future" in verdict.explain()


def test_a_flat_result_on_noise_is_not():
    verdict = judge_canary(total_return=-0.02, trades=40)
    assert not verdict.leaked
    assert "no edge and no leak" in verdict.explain()


def test_a_strategy_that_never_traded_proves_nothing():
    verdict = judge_canary(total_return=5.0, trades=0)
    assert not verdict.leaked
    assert "proves nothing" in verdict.explain()


def test_the_engine_does_not_profit_on_an_unpredictable_series():
    """The end-to-end canary. There is no edge in this series; a positive
    return after costs would mean the engine found information that is not
    there."""
    closes = unpredictable_series(300, seed=31)
    engine, _ = _run(closes)

    total = sum(trade.pnl for trade in engine.trades)
    verdict = judge_canary(
        total_return=total / engine.config.initial_capital,
        trades=len(engine.trades),
    )
    assert not verdict.leaked, verdict.explain()
