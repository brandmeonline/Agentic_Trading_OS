"""
Agentic Trading OS - Technical Indicators.

A comprehensive library of 13 technical analysis indicators for charting and
signal generation.

Indicators by Category:
-----------------------

**Trend Indicators:**
- SMA (Simple Moving Average) - Basic trend following
- EMA (Exponential Moving Average) - Weighted recent prices more
- MACD (Moving Average Convergence Divergence) - Trend momentum

**Momentum Indicators:**
- RSI (Relative Strength Index) - Overbought/oversold conditions
- Stochastic Oscillator - Price position within range
- Williams %R - Momentum oscillator
- CCI (Commodity Channel Index) - Deviation from average

**Volatility Indicators:**
- Bollinger Bands - Price volatility channels
- ATR (Average True Range) - Market volatility measure
- Parabolic SAR - Trend reversal points

**Volume Indicators:**
- OBV (On-Balance Volume) - Volume-price confirmation
- VWAP (Volume Weighted Average Price) - Fair value

**Composite:**
- Ichimoku Cloud - Multi-component trend system

Usage:
------
    from core.indicators import get_indicator_calculator

    calc = get_indicator_calculator()
    rsi_values = calc.calculate('rsi', prices, period=14)
    macd_result = calc.calculate('macd', prices, fast=12, slow=26, signal=9)

Each indicator function accepts a list of prices and returns calculated values.
None values are used for periods where insufficient data exists.

API Integration:
----------------
These indicators are exposed via:
- GET /api/indicators - List available indicators
- POST /api/indicators/calculate - Calculate specific indicator
- GET /api/indicators/chart/<symbol> - Get chart with overlays

Author: Agentic Trading OS Team
Version: 2.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from enum import Enum


class IndicatorType(Enum):
    """Indicator categories."""
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"


@dataclass
class IndicatorResult:
    """Result from indicator calculation."""
    name: str
    values: List[float]
    timestamps: List[str] = None
    upper_band: List[float] = None
    lower_band: List[float] = None
    signal_line: List[float] = None
    histogram: List[float] = None


# =============================================================================
# Trend Indicators
# =============================================================================

def sma(prices: List[float], period: int = 20) -> List[float]:
    """Simple Moving Average."""
    if len(prices) < period:
        return [None] * len(prices)

    result = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        result.append(sum(prices[i - period + 1:i + 1]) / period)
    return result


def ema(prices: List[float], period: int = 20) -> List[float]:
    """Exponential Moving Average."""
    if len(prices) < period:
        return [None] * len(prices)

    multiplier = 2 / (period + 1)
    result = [None] * (period - 1)

    # First EMA is SMA
    first_ema = sum(prices[:period]) / period
    result.append(first_ema)

    for i in range(period, len(prices)):
        ema_val = (prices[i] - result[-1]) * multiplier + result[-1]
        result.append(ema_val)

    return result


def macd(prices: List[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> IndicatorResult:
    """Moving Average Convergence Divergence."""
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    macd_line = []
    for i in range(len(prices)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line.append(None)
        else:
            macd_line.append(ema_fast[i] - ema_slow[i])

    # Signal line (EMA of MACD)
    valid_macd = [v for v in macd_line if v is not None]
    signal_ema = ema(valid_macd, signal) if len(valid_macd) >= signal else [None] * len(valid_macd)

    # Pad signal line
    signal_line = [None] * (len(macd_line) - len(signal_ema)) + signal_ema

    # Histogram
    histogram = []
    for i in range(len(macd_line)):
        if macd_line[i] is None or signal_line[i] is None:
            histogram.append(None)
        else:
            histogram.append(macd_line[i] - signal_line[i])

    return IndicatorResult(
        name="MACD",
        values=macd_line,
        signal_line=signal_line,
        histogram=histogram
    )


def adx(high: List[float], low: List[float], close: List[float],
        period: int = 14) -> IndicatorResult:
    """Average Directional Index."""
    if len(high) < period + 1:
        return IndicatorResult(name="ADX", values=[None] * len(high))

    tr_list = []
    plus_dm = []
    minus_dm = []

    for i in range(1, len(high)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
        tr_list.append(tr)

        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    # Smooth TR and DM
    atr = ema(tr_list, period)
    plus_di = []
    minus_di = []

    smoothed_plus = ema(plus_dm, period)
    smoothed_minus = ema(minus_dm, period)

    for i in range(len(atr)):
        if atr[i] is None or atr[i] == 0:
            plus_di.append(None)
            minus_di.append(None)
        else:
            plus_di.append(100 * (smoothed_plus[i] or 0) / atr[i])
            minus_di.append(100 * (smoothed_minus[i] or 0) / atr[i])

    # Calculate DX and ADX
    dx = []
    for i in range(len(plus_di)):
        if plus_di[i] is None or minus_di[i] is None:
            dx.append(None)
        else:
            sum_di = plus_di[i] + minus_di[i]
            if sum_di == 0:
                dx.append(0)
            else:
                dx.append(100 * abs(plus_di[i] - minus_di[i]) / sum_di)

    adx_values = ema([v for v in dx if v is not None], period)
    adx_padded = [None] * (len(dx) - len(adx_values) + 1) + adx_values

    return IndicatorResult(name="ADX", values=adx_padded)


# =============================================================================
# Momentum Indicators
# =============================================================================

def rsi(prices: List[float], period: int = 14) -> List[float]:
    """Relative Strength Index."""
    if len(prices) < period + 1:
        return [None] * len(prices)

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(0, change))
        losses.append(max(0, -change))

    result = [None] * period

    # First average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result.append(100)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - (100 / (1 + rs)))

    # Subsequent values using smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            result.append(100)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - (100 / (1 + rs)))

    return result


def stochastic(high: List[float], low: List[float], close: List[float],
               k_period: int = 14, d_period: int = 3) -> IndicatorResult:
    """Stochastic Oscillator."""
    if len(high) < k_period:
        return IndicatorResult(name="Stochastic", values=[None] * len(high))

    k_values = []

    for i in range(k_period - 1, len(high)):
        highest = max(high[i - k_period + 1:i + 1])
        lowest = min(low[i - k_period + 1:i + 1])

        if highest == lowest:
            k_values.append(50)
        else:
            k_values.append(100 * (close[i] - lowest) / (highest - lowest))

    # Pad with None
    k_padded = [None] * (k_period - 1) + k_values

    # %D is SMA of %K
    d_values = sma(k_values, d_period)
    d_padded = [None] * (k_period - 1) + d_values

    return IndicatorResult(
        name="Stochastic",
        values=k_padded,
        signal_line=d_padded
    )


def cci(high: List[float], low: List[float], close: List[float],
        period: int = 20) -> List[float]:
    """Commodity Channel Index."""
    if len(high) < period:
        return [None] * len(high)

    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]
    tp_sma = sma(tp, period)

    result = [None] * (period - 1)

    for i in range(period - 1, len(tp)):
        if tp_sma[i] is None:
            result.append(None)
            continue

        # Mean deviation
        mean_dev = sum(abs(tp[j] - tp_sma[i]) for j in range(i - period + 1, i + 1)) / period

        if mean_dev == 0:
            result.append(0)
        else:
            result.append((tp[i] - tp_sma[i]) / (0.015 * mean_dev))

    return result


def williams_r(high: List[float], low: List[float], close: List[float],
               period: int = 14) -> List[float]:
    """Williams %R."""
    if len(high) < period:
        return [None] * len(high)

    result = [None] * (period - 1)

    for i in range(period - 1, len(high)):
        highest = max(high[i - period + 1:i + 1])
        lowest = min(low[i - period + 1:i + 1])

        if highest == lowest:
            result.append(-50)
        else:
            result.append(-100 * (highest - close[i]) / (highest - lowest))

    return result


# =============================================================================
# Volatility Indicators
# =============================================================================

def bollinger_bands(prices: List[float], period: int = 20,
                    std_dev: float = 2.0) -> IndicatorResult:
    """Bollinger Bands."""
    middle = sma(prices, period)

    upper = []
    lower = []

    for i in range(len(prices)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            # Calculate standard deviation
            window = prices[max(0, i - period + 1):i + 1]
            mean = sum(window) / len(window)
            variance = sum((x - mean) ** 2 for x in window) / len(window)
            std = math.sqrt(variance)

            upper.append(middle[i] + std_dev * std)
            lower.append(middle[i] - std_dev * std)

    return IndicatorResult(
        name="Bollinger Bands",
        values=middle,
        upper_band=upper,
        lower_band=lower
    )


def atr(high: List[float], low: List[float], close: List[float],
        period: int = 14) -> List[float]:
    """Average True Range."""
    if len(high) < 2:
        return [None] * len(high)

    tr_list = [high[0] - low[0]]

    for i in range(1, len(high)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
        tr_list.append(tr)

    return ema(tr_list, period)


def keltner_channels(high: List[float], low: List[float], close: List[float],
                     ema_period: int = 20, atr_period: int = 10,
                     multiplier: float = 2.0) -> IndicatorResult:
    """Keltner Channels."""
    middle = ema(close, ema_period)
    atr_values = atr(high, low, close, atr_period)

    upper = []
    lower = []

    for i in range(len(close)):
        if middle[i] is None or atr_values[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(middle[i] + multiplier * atr_values[i])
            lower.append(middle[i] - multiplier * atr_values[i])

    return IndicatorResult(
        name="Keltner Channels",
        values=middle,
        upper_band=upper,
        lower_band=lower
    )


# =============================================================================
# Volume Indicators
# =============================================================================

def obv(close: List[float], volume: List[float]) -> List[float]:
    """On-Balance Volume."""
    if not close or not volume:
        return []

    result = [volume[0]]

    for i in range(1, len(close)):
        if close[i] > close[i-1]:
            result.append(result[-1] + volume[i])
        elif close[i] < close[i-1]:
            result.append(result[-1] - volume[i])
        else:
            result.append(result[-1])

    return result


def vwap(high: List[float], low: List[float], close: List[float],
         volume: List[float]) -> List[float]:
    """Volume Weighted Average Price."""
    if not high or not volume:
        return []

    tp = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]

    cumulative_tp_vol = 0
    cumulative_vol = 0
    result = []

    for i in range(len(tp)):
        cumulative_tp_vol += tp[i] * volume[i]
        cumulative_vol += volume[i]

        if cumulative_vol == 0:
            result.append(tp[i])
        else:
            result.append(cumulative_tp_vol / cumulative_vol)

    return result


# =============================================================================
# Indicator Calculator
# =============================================================================

class IndicatorCalculator:
    """Calculate technical indicators from OHLCV data."""

    def __init__(self):
        self.indicators = {
            # Trend
            "sma": {"func": self._calc_sma, "type": IndicatorType.TREND},
            "ema": {"func": self._calc_ema, "type": IndicatorType.TREND},
            "macd": {"func": self._calc_macd, "type": IndicatorType.TREND},
            "adx": {"func": self._calc_adx, "type": IndicatorType.TREND},
            # Momentum
            "rsi": {"func": self._calc_rsi, "type": IndicatorType.MOMENTUM},
            "stochastic": {"func": self._calc_stochastic, "type": IndicatorType.MOMENTUM},
            "cci": {"func": self._calc_cci, "type": IndicatorType.MOMENTUM},
            "williams_r": {"func": self._calc_williams_r, "type": IndicatorType.MOMENTUM},
            # Volatility
            "bollinger": {"func": self._calc_bollinger, "type": IndicatorType.VOLATILITY},
            "atr": {"func": self._calc_atr, "type": IndicatorType.VOLATILITY},
            "keltner": {"func": self._calc_keltner, "type": IndicatorType.VOLATILITY},
            # Volume
            "obv": {"func": self._calc_obv, "type": IndicatorType.VOLUME},
            "vwap": {"func": self._calc_vwap, "type": IndicatorType.VOLUME},
        }

    def calculate(self, indicator_name: str, ohlcv: Dict, **params) -> IndicatorResult:
        """Calculate an indicator."""
        if indicator_name not in self.indicators:
            raise ValueError(f"Unknown indicator: {indicator_name}")

        return self.indicators[indicator_name]["func"](ohlcv, **params)

    def list_indicators(self) -> List[Dict]:
        """List available indicators."""
        return [
            {"name": name, "type": info["type"].value}
            for name, info in self.indicators.items()
        ]

    def _calc_sma(self, ohlcv: Dict, period: int = 20) -> IndicatorResult:
        return IndicatorResult(name=f"SMA({period})", values=sma(ohlcv["close"], period))

    def _calc_ema(self, ohlcv: Dict, period: int = 20) -> IndicatorResult:
        return IndicatorResult(name=f"EMA({period})", values=ema(ohlcv["close"], period))

    def _calc_macd(self, ohlcv: Dict, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> IndicatorResult:
        return macd(ohlcv["close"], fast, slow, signal)

    def _calc_adx(self, ohlcv: Dict, period: int = 14) -> IndicatorResult:
        return adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], period)

    def _calc_rsi(self, ohlcv: Dict, period: int = 14) -> IndicatorResult:
        return IndicatorResult(name=f"RSI({period})", values=rsi(ohlcv["close"], period))

    def _calc_stochastic(self, ohlcv: Dict, k_period: int = 14,
                         d_period: int = 3) -> IndicatorResult:
        return stochastic(ohlcv["high"], ohlcv["low"], ohlcv["close"], k_period, d_period)

    def _calc_cci(self, ohlcv: Dict, period: int = 20) -> IndicatorResult:
        return IndicatorResult(name=f"CCI({period})",
                              values=cci(ohlcv["high"], ohlcv["low"], ohlcv["close"], period))

    def _calc_williams_r(self, ohlcv: Dict, period: int = 14) -> IndicatorResult:
        return IndicatorResult(name=f"Williams %R({period})",
                              values=williams_r(ohlcv["high"], ohlcv["low"], ohlcv["close"], period))

    def _calc_bollinger(self, ohlcv: Dict, period: int = 20,
                        std_dev: float = 2.0) -> IndicatorResult:
        return bollinger_bands(ohlcv["close"], period, std_dev)

    def _calc_atr(self, ohlcv: Dict, period: int = 14) -> IndicatorResult:
        return IndicatorResult(name=f"ATR({period})",
                              values=atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], period))

    def _calc_keltner(self, ohlcv: Dict, ema_period: int = 20, atr_period: int = 10,
                      multiplier: float = 2.0) -> IndicatorResult:
        return keltner_channels(ohlcv["high"], ohlcv["low"], ohlcv["close"],
                               ema_period, atr_period, multiplier)

    def _calc_obv(self, ohlcv: Dict) -> IndicatorResult:
        return IndicatorResult(name="OBV", values=obv(ohlcv["close"], ohlcv["volume"]))

    def _calc_vwap(self, ohlcv: Dict) -> IndicatorResult:
        return IndicatorResult(name="VWAP",
                              values=vwap(ohlcv["high"], ohlcv["low"],
                                         ohlcv["close"], ohlcv["volume"]))


# =============================================================================
# Factory
# =============================================================================

_calculator: Optional[IndicatorCalculator] = None

def get_indicator_calculator() -> IndicatorCalculator:
    """Get or create indicator calculator."""
    global _calculator
    if _calculator is None:
        _calculator = IndicatorCalculator()
    return _calculator
