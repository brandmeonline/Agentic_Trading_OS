"""
Strategy Framework for Pluggable Trading Strategies.

Provides an abstract base class and implementations for various trading strategies
including momentum, mean reversion, trend following, and ML-based approaches.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import numpy as np
from collections import deque


class StrategySignal(Enum):
    """Strategy output signals."""
    STRONG_BUY = 2
    BUY = 1
    HOLD = 0
    SELL = -1
    STRONG_SELL = -2


@dataclass
class StrategyOutput:
    """Output from a strategy evaluation."""
    signal: StrategySignal
    confidence: float
    asset: str
    timestamp: datetime = field(default_factory=datetime.now)
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def should_trade(self) -> bool:
        """Check if signal warrants trading."""
        return self.signal != StrategySignal.HOLD and self.confidence >= 0.6

    @property
    def is_long(self) -> bool:
        """Check if signal is bullish."""
        return self.signal in [StrategySignal.BUY, StrategySignal.STRONG_BUY]

    @property
    def position_multiplier(self) -> float:
        """Get position size multiplier based on signal strength."""
        multipliers = {
            StrategySignal.STRONG_BUY: 1.5,
            StrategySignal.BUY: 1.0,
            StrategySignal.HOLD: 0.0,
            StrategySignal.SELL: 1.0,
            StrategySignal.STRONG_SELL: 1.5,
        }
        return multipliers.get(self.signal, 1.0) * self.confidence


@dataclass
class MarketData:
    """Market data for strategy evaluation."""
    asset: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    # Optional additional data
    bid: Optional[float] = None
    ask: Optional[float] = None
    vwap: Optional[float] = None

    @property
    def typical_price(self) -> float:
        """Calculate typical price."""
        return (self.high + self.low + self.close) / 3

    @property
    def range(self) -> float:
        """Calculate price range."""
        return self.high - self.low

    @property
    def body(self) -> float:
        """Calculate candle body size."""
        return abs(self.close - self.open)


class Strategy(ABC):
    """
    Abstract base class for trading strategies.

    All strategies must implement the evaluate() method which takes
    market data and returns a StrategyOutput with trading signals.
    """

    def __init__(self, name: str, params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.params = params or {}
        self.is_active = True
        self.last_signal: Optional[StrategyOutput] = None
        self.signal_history: List[StrategyOutput] = []
        self.performance_metrics: Dict[str, float] = {}

    @abstractmethod
    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        """
        Evaluate market data and generate trading signal.

        Args:
            data: List of MarketData objects (most recent last)
            asset: Asset symbol being evaluated

        Returns:
            StrategyOutput with signal and confidence
        """
        pass

    def update_performance(self, realized_pnl: float) -> None:
        """Update performance metrics after trade completion."""
        if "total_pnl" not in self.performance_metrics:
            self.performance_metrics["total_pnl"] = 0.0
            self.performance_metrics["trade_count"] = 0
            self.performance_metrics["win_count"] = 0

        self.performance_metrics["total_pnl"] += realized_pnl
        self.performance_metrics["trade_count"] += 1
        if realized_pnl > 0:
            self.performance_metrics["win_count"] += 1

    @property
    def win_rate(self) -> float:
        """Calculate win rate."""
        trades = self.performance_metrics.get("trade_count", 0)
        wins = self.performance_metrics.get("win_count", 0)
        return wins / trades if trades > 0 else 0.0

    def reset(self) -> None:
        """Reset strategy state."""
        self.last_signal = None
        self.signal_history.clear()


class MomentumStrategy(Strategy):
    """
    Momentum-based trading strategy.

    Generates signals based on price momentum over a lookback period.
    Uses rate of change and momentum oscillators.
    """

    def __init__(self, lookback: int = 14, threshold: float = 0.02):
        super().__init__("momentum", {"lookback": lookback, "threshold": threshold})
        self.lookback = lookback
        self.threshold = threshold

    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        if len(data) < self.lookback + 1:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="Insufficient data"
            )

        # Calculate momentum (Rate of Change)
        current_price = data[-1].close
        past_price = data[-self.lookback - 1].close
        roc = (current_price - past_price) / past_price

        # Calculate momentum strength
        prices = [d.close for d in data[-self.lookback:]]
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        momentum_std = np.std(returns) if returns else 0.01
        momentum_zscore = roc / momentum_std if momentum_std > 0 else 0

        # Generate signal based on momentum
        if roc > self.threshold * 2:
            signal = StrategySignal.STRONG_BUY
            confidence = min(0.95, 0.7 + abs(momentum_zscore) * 0.1)
        elif roc > self.threshold:
            signal = StrategySignal.BUY
            confidence = min(0.85, 0.6 + abs(momentum_zscore) * 0.1)
        elif roc < -self.threshold * 2:
            signal = StrategySignal.STRONG_SELL
            confidence = min(0.95, 0.7 + abs(momentum_zscore) * 0.1)
        elif roc < -self.threshold:
            signal = StrategySignal.SELL
            confidence = min(0.85, 0.6 + abs(momentum_zscore) * 0.1)
        else:
            signal = StrategySignal.HOLD
            confidence = 0.5

        output = StrategyOutput(
            signal=signal,
            confidence=confidence,
            asset=asset,
            reasoning=f"ROC: {roc:.2%}, Z-Score: {momentum_zscore:.2f}",
            metadata={"roc": roc, "zscore": momentum_zscore}
        )

        self.last_signal = output
        self.signal_history.append(output)
        return output


class MeanReversionStrategy(Strategy):
    """
    Mean reversion trading strategy.

    Generates signals when price deviates significantly from moving average,
    expecting reversion to the mean.
    """

    def __init__(self, ma_period: int = 20, std_multiplier: float = 2.0):
        super().__init__("mean_reversion", {"ma_period": ma_period, "std_multiplier": std_multiplier})
        self.ma_period = ma_period
        self.std_multiplier = std_multiplier

    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        if len(data) < self.ma_period:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="Insufficient data"
            )

        prices = [d.close for d in data[-self.ma_period:]]
        ma = np.mean(prices)
        std = np.std(prices)

        current_price = data[-1].close
        zscore = (current_price - ma) / std if std > 0 else 0

        # Generate signal based on deviation from mean
        if zscore < -self.std_multiplier * 1.5:
            signal = StrategySignal.STRONG_BUY  # Oversold
            confidence = min(0.95, 0.7 + abs(zscore - self.std_multiplier) * 0.1)
        elif zscore < -self.std_multiplier:
            signal = StrategySignal.BUY
            confidence = min(0.85, 0.6 + abs(zscore) * 0.1)
        elif zscore > self.std_multiplier * 1.5:
            signal = StrategySignal.STRONG_SELL  # Overbought
            confidence = min(0.95, 0.7 + abs(zscore - self.std_multiplier) * 0.1)
        elif zscore > self.std_multiplier:
            signal = StrategySignal.SELL
            confidence = min(0.85, 0.6 + abs(zscore) * 0.1)
        else:
            signal = StrategySignal.HOLD
            confidence = 0.5

        output = StrategyOutput(
            signal=signal,
            confidence=confidence,
            asset=asset,
            reasoning=f"Price: {current_price:.2f}, MA: {ma:.2f}, Z-Score: {zscore:.2f}",
            metadata={"ma": ma, "std": std, "zscore": zscore}
        )

        self.last_signal = output
        self.signal_history.append(output)
        return output


class TrendFollowingStrategy(Strategy):
    """
    Trend following strategy using multiple timeframe analysis.

    Combines short, medium, and long-term trend indicators
    to generate directional signals.
    """

    def __init__(self, short_period: int = 10, medium_period: int = 20, long_period: int = 50):
        super().__init__("trend_following", {
            "short_period": short_period,
            "medium_period": medium_period,
            "long_period": long_period
        })
        self.short_period = short_period
        self.medium_period = medium_period
        self.long_period = long_period

    def _ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return prices[-1]
        multiplier = 2 / (period + 1)
        ema = prices[0]
        for price in prices[1:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        return ema

    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        if len(data) < self.long_period:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="Insufficient data"
            )

        prices = [d.close for d in data]

        # Calculate EMAs
        ema_short = self._ema(prices, self.short_period)
        ema_medium = self._ema(prices, self.medium_period)
        ema_long = self._ema(prices, self.long_period)

        current_price = prices[-1]

        # Trend alignment scoring
        trend_score = 0
        if current_price > ema_short:
            trend_score += 1
        if current_price > ema_medium:
            trend_score += 1
        if current_price > ema_long:
            trend_score += 1
        if ema_short > ema_medium:
            trend_score += 1
        if ema_medium > ema_long:
            trend_score += 1

        # Generate signal based on trend alignment
        if trend_score >= 5:
            signal = StrategySignal.STRONG_BUY
            confidence = 0.9
        elif trend_score >= 4:
            signal = StrategySignal.BUY
            confidence = 0.75
        elif trend_score <= 0:
            signal = StrategySignal.STRONG_SELL
            confidence = 0.9
        elif trend_score <= 1:
            signal = StrategySignal.SELL
            confidence = 0.75
        else:
            signal = StrategySignal.HOLD
            confidence = 0.5

        output = StrategyOutput(
            signal=signal,
            confidence=confidence,
            asset=asset,
            reasoning=f"Trend Score: {trend_score}/5, EMA Short: {ema_short:.2f}, Medium: {ema_medium:.2f}, Long: {ema_long:.2f}",
            metadata={
                "trend_score": trend_score,
                "ema_short": ema_short,
                "ema_medium": ema_medium,
                "ema_long": ema_long
            }
        )

        self.last_signal = output
        self.signal_history.append(output)
        return output


class VolatilityBreakoutStrategy(Strategy):
    """
    Volatility breakout strategy.

    Trades breakouts from volatility compression (Bollinger Band squeeze)
    and captures momentum after the breakout.
    """

    def __init__(self, period: int = 20, squeeze_threshold: float = 0.5):
        super().__init__("volatility_breakout", {"period": period, "squeeze_threshold": squeeze_threshold})
        self.period = period
        self.squeeze_threshold = squeeze_threshold
        self.squeeze_history: deque = deque(maxlen=10)

    def _calculate_bollinger_width(self, prices: List[float], period: int) -> Tuple[float, float, float]:
        """Calculate Bollinger Band width."""
        if len(prices) < period:
            return 0, prices[-1], prices[-1]

        ma = np.mean(prices[-period:])
        std = np.std(prices[-period:])
        upper = ma + 2 * std
        lower = ma - 2 * std
        width = (upper - lower) / ma if ma > 0 else 0
        return width, upper, lower

    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        if len(data) < self.period + 5:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="Insufficient data"
            )

        prices = [d.close for d in data]
        current_price = prices[-1]

        # Calculate current and historical Bollinger width
        width, upper, lower = self._calculate_bollinger_width(prices, self.period)

        # Calculate average width for squeeze detection
        widths = []
        for i in range(5):
            w, _, _ = self._calculate_bollinger_width(prices[:-i-1] if i > 0 else prices, self.period)
            widths.append(w)
        avg_width = np.mean(widths)

        # Detect squeeze (low volatility)
        in_squeeze = width < avg_width * self.squeeze_threshold
        self.squeeze_history.append(in_squeeze)

        # Check for breakout after squeeze
        was_in_squeeze = sum(self.squeeze_history) >= 3

        if was_in_squeeze and not in_squeeze:
            # Breakout from squeeze
            if current_price > upper:
                signal = StrategySignal.STRONG_BUY
                confidence = 0.85
                reasoning = "Bullish breakout from volatility squeeze"
            elif current_price < lower:
                signal = StrategySignal.STRONG_SELL
                confidence = 0.85
                reasoning = "Bearish breakout from volatility squeeze"
            else:
                signal = StrategySignal.HOLD
                confidence = 0.5
                reasoning = "Squeeze release, no clear direction"
        elif current_price > upper:
            signal = StrategySignal.BUY
            confidence = 0.7
            reasoning = "Price above upper band"
        elif current_price < lower:
            signal = StrategySignal.SELL
            confidence = 0.7
            reasoning = "Price below lower band"
        else:
            signal = StrategySignal.HOLD
            confidence = 0.5
            reasoning = f"Within bands, squeeze: {in_squeeze}"

        output = StrategyOutput(
            signal=signal,
            confidence=confidence,
            asset=asset,
            reasoning=reasoning,
            metadata={
                "bb_width": width,
                "upper": upper,
                "lower": lower,
                "in_squeeze": in_squeeze,
                "was_in_squeeze": was_in_squeeze
            }
        )

        self.last_signal = output
        self.signal_history.append(output)
        return output


class StrategyEnsemble:
    """
    Ensemble of multiple strategies with weighted voting.

    Combines signals from multiple strategies to produce
    a consensus signal with higher confidence.
    """

    def __init__(self, strategies: Optional[List[Tuple[Strategy, float]]] = None):
        """
        Initialize ensemble with strategies and weights.

        Args:
            strategies: List of (strategy, weight) tuples
        """
        self.strategies: List[Tuple[Strategy, float]] = strategies or []
        self.last_consensus: Optional[StrategyOutput] = None

    def add_strategy(self, strategy: Strategy, weight: float = 1.0) -> None:
        """Add a strategy to the ensemble."""
        self.strategies.append((strategy, weight))

    def remove_strategy(self, name: str) -> bool:
        """Remove a strategy by name."""
        for i, (s, _) in enumerate(self.strategies):
            if s.name == name:
                self.strategies.pop(i)
                return True
        return False

    def evaluate(self, data: List[MarketData], asset: str) -> StrategyOutput:
        """
        Evaluate all strategies and produce consensus signal.

        Uses weighted voting to combine signals.
        """
        if not self.strategies:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="No strategies in ensemble"
            )

        # Collect signals from all strategies
        signals = []
        total_weight = 0
        weighted_score = 0
        reasoning_parts = []

        for strategy, weight in self.strategies:
            if not strategy.is_active:
                continue

            output = strategy.evaluate(data, asset)
            signals.append((output, weight))

            # Convert signal to numeric score
            score_map = {
                StrategySignal.STRONG_BUY: 2,
                StrategySignal.BUY: 1,
                StrategySignal.HOLD: 0,
                StrategySignal.SELL: -1,
                StrategySignal.STRONG_SELL: -2,
            }
            score = score_map[output.signal] * output.confidence * weight
            weighted_score += score
            total_weight += weight * output.confidence

            reasoning_parts.append(f"{strategy.name}: {output.signal.name} ({output.confidence:.2f})")

        if total_weight == 0:
            return StrategyOutput(
                signal=StrategySignal.HOLD,
                confidence=0.0,
                asset=asset,
                reasoning="All strategies returned HOLD"
            )

        # Calculate consensus
        normalized_score = weighted_score / total_weight

        # Map score to signal
        if normalized_score > 1.5:
            consensus_signal = StrategySignal.STRONG_BUY
        elif normalized_score > 0.5:
            consensus_signal = StrategySignal.BUY
        elif normalized_score < -1.5:
            consensus_signal = StrategySignal.STRONG_SELL
        elif normalized_score < -0.5:
            consensus_signal = StrategySignal.SELL
        else:
            consensus_signal = StrategySignal.HOLD

        # Calculate consensus confidence
        consensus_confidence = min(0.95, abs(normalized_score) * 0.4 + 0.3)

        # Agreement bonus: higher confidence if strategies agree
        signal_agreement = len(set(s.signal for s, _ in signals))
        if signal_agreement == 1 and len(signals) > 1:
            consensus_confidence = min(0.95, consensus_confidence + 0.15)

        output = StrategyOutput(
            signal=consensus_signal,
            confidence=consensus_confidence,
            asset=asset,
            reasoning=f"Ensemble ({len(signals)} strategies): " + "; ".join(reasoning_parts),
            metadata={
                "weighted_score": normalized_score,
                "num_strategies": len(signals),
                "agreement": signal_agreement == 1
            }
        )

        self.last_consensus = output
        return output

    def get_strategy_performance(self) -> Dict[str, Dict]:
        """Get performance metrics for all strategies."""
        return {
            s.name: {
                "win_rate": s.win_rate,
                "total_pnl": s.performance_metrics.get("total_pnl", 0),
                "trade_count": s.performance_metrics.get("trade_count", 0),
            }
            for s, _ in self.strategies
        }


# Factory function for creating common strategy configurations
def create_default_ensemble() -> StrategyEnsemble:
    """Create a default ensemble with balanced strategies."""
    ensemble = StrategyEnsemble()
    ensemble.add_strategy(MomentumStrategy(lookback=14), weight=1.0)
    ensemble.add_strategy(MeanReversionStrategy(ma_period=20), weight=0.8)
    ensemble.add_strategy(TrendFollowingStrategy(), weight=1.2)
    ensemble.add_strategy(VolatilityBreakoutStrategy(), weight=0.7)
    return ensemble


if __name__ == "__main__":
    # Test strategies with sample data
    sample_data = []
    base_price = 100.0

    for i in range(100):
        # Simulate trending then mean-reverting price
        trend = 0.1 * np.sin(i / 20) + 0.002 * i
        noise = np.random.normal(0, 0.5)
        price = base_price * (1 + trend + noise / 100)

        sample_data.append(MarketData(
            asset="TEST",
            timestamp=datetime.now(),
            open=price * 0.998,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1000000 + np.random.randint(-100000, 100000)
        ))

    # Test individual strategies
    print("Testing Individual Strategies:")
    strategies = [
        MomentumStrategy(),
        MeanReversionStrategy(),
        TrendFollowingStrategy(),
        VolatilityBreakoutStrategy(),
    ]

    for strategy in strategies:
        output = strategy.evaluate(sample_data, "TEST")
        print(f"\n{strategy.name}:")
        print(f"  Signal: {output.signal.name}")
        print(f"  Confidence: {output.confidence:.2f}")
        print(f"  Reasoning: {output.reasoning}")

    # Test ensemble
    print("\n\nTesting Strategy Ensemble:")
    ensemble = create_default_ensemble()
    output = ensemble.evaluate(sample_data, "TEST")
    print(f"Consensus Signal: {output.signal.name}")
    print(f"Confidence: {output.confidence:.2f}")
    print(f"Reasoning: {output.reasoning}")
