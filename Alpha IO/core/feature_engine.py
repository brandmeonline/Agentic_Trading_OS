"""
Advanced Feature Engineering Pipeline.

Implements cutting-edge feature engineering for financial time series:
- Technical indicators with adaptive parameters
- Microstructure features (order flow, market impact)
- Cross-asset features (correlations, lead-lag)
- Regime detection features
- Fractal and complexity features
- Information-theoretic features
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from abc import ABC, abstractmethod
import warnings

warnings.filterwarnings('ignore')


# =============================================================================
# Configuration
# =============================================================================

class FeatureCategory(Enum):
    """Feature categories."""
    TECHNICAL = "technical"
    MICROSTRUCTURE = "microstructure"
    CROSS_ASSET = "cross_asset"
    REGIME = "regime"
    FRACTAL = "fractal"
    INFORMATION = "information"


@dataclass
class FeatureConfig:
    """Feature engineering configuration."""
    lookback_periods: List[int] = field(default_factory=lambda: [5, 10, 20, 50, 100, 200])
    momentum_periods: List[int] = field(default_factory=lambda: [1, 5, 10, 20])
    volatility_periods: List[int] = field(default_factory=lambda: [5, 10, 20, 50])
    correlation_periods: List[int] = field(default_factory=lambda: [20, 50, 100])
    regime_window: int = 50
    fractal_scales: List[int] = field(default_factory=lambda: [8, 16, 32, 64])
    normalize_features: bool = True
    handle_missing: str = "forward_fill"  # forward_fill, zero, drop
    outlier_threshold: float = 5.0  # Standard deviations


@dataclass
class FeatureSet:
    """Container for computed features."""
    features: np.ndarray  # Shape: (n_samples, n_features)
    feature_names: List[str]
    timestamps: Optional[np.ndarray] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_samples(self) -> int:
        return self.features.shape[0]

    @property
    def n_features(self) -> int:
        return self.features.shape[1]

    def get_feature(self, name: str) -> np.ndarray:
        """Get single feature by name."""
        if name not in self.feature_names:
            raise ValueError(f"Unknown feature: {name}")
        idx = self.feature_names.index(name)
        return self.features[:, idx]

    def select_features(self, names: List[str]) -> 'FeatureSet':
        """Select subset of features."""
        indices = [self.feature_names.index(n) for n in names if n in self.feature_names]
        return FeatureSet(
            features=self.features[:, indices],
            feature_names=[self.feature_names[i] for i in indices],
            timestamps=self.timestamps,
            metadata=self.metadata
        )


# =============================================================================
# Mathematical Utilities
# =============================================================================

def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
    return result


def sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    result = np.full_like(data, np.nan)
    cumsum = np.cumsum(np.insert(data, 0, 0))
    result[period-1:] = (cumsum[period:] - cumsum[:-period]) / period
    return result


def rolling_std(data: np.ndarray, period: int) -> np.ndarray:
    """Rolling standard deviation."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        result[i] = np.std(data[i-period+1:i+1], ddof=1)
    return result


def rolling_max(data: np.ndarray, period: int) -> np.ndarray:
    """Rolling maximum."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        result[i] = np.max(data[i-period+1:i+1])
    return result


def rolling_min(data: np.ndarray, period: int) -> np.ndarray:
    """Rolling minimum."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        result[i] = np.min(data[i-period+1:i+1])
    return result


def rolling_correlation(x: np.ndarray, y: np.ndarray, period: int) -> np.ndarray:
    """Rolling correlation coefficient."""
    result = np.full(len(x), np.nan)
    for i in range(period - 1, len(x)):
        x_win = x[i-period+1:i+1]
        y_win = y[i-period+1:i+1]
        if np.std(x_win) > 1e-10 and np.std(y_win) > 1e-10:
            result[i] = np.corrcoef(x_win, y_win)[0, 1]
    return result


def rolling_skewness(data: np.ndarray, period: int) -> np.ndarray:
    """Rolling skewness."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        window = data[i-period+1:i+1]
        mean = np.mean(window)
        std = np.std(window, ddof=1)
        if std > 1e-10:
            result[i] = np.mean(((window - mean) / std) ** 3)
    return result


def rolling_kurtosis(data: np.ndarray, period: int) -> np.ndarray:
    """Rolling kurtosis (excess)."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        window = data[i-period+1:i+1]
        mean = np.mean(window)
        std = np.std(window, ddof=1)
        if std > 1e-10:
            result[i] = np.mean(((window - mean) / std) ** 4) - 3
    return result


# =============================================================================
# Technical Features
# =============================================================================

class TechnicalFeatures:
    """
    Advanced technical indicator features.

    Implements:
    - Adaptive moving averages (KAMA, FRAMA, MAMA)
    - Volatility indicators (ATR, Parkinson, Garman-Klass)
    - Momentum indicators (RSI, Stochastic, Williams %R)
    - Trend indicators (ADX, Aroon, Supertrend)
    - Volume indicators (OBV, MFI, VWAP)
    - Pattern recognition features
    """

    def __init__(self, config: FeatureConfig):
        self.config = config

    def compute_all(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray
    ) -> Tuple[np.ndarray, List[str]]:
        """Compute all technical features."""
        features = []
        names = []

        # Returns at multiple horizons
        for period in self.config.momentum_periods:
            ret = self._returns(close, period)
            features.append(ret)
            names.append(f"return_{period}")

        # Log returns
        for period in self.config.momentum_periods:
            log_ret = self._log_returns(close, period)
            features.append(log_ret)
            names.append(f"log_return_{period}")

        # Moving averages and crossovers
        for period in self.config.lookback_periods:
            ma = sma(close, period)
            features.append((close - ma) / (ma + 1e-10))  # Distance from MA
            names.append(f"ma_dist_{period}")

            ema_val = ema(close, period)
            features.append((close - ema_val) / (ema_val + 1e-10))
            names.append(f"ema_dist_{period}")

        # KAMA - Kaufman Adaptive Moving Average
        for period in [10, 20]:
            kama = self._kama(close, period)
            features.append((close - kama) / (kama + 1e-10))
            names.append(f"kama_dist_{period}")

        # Volatility measures
        for period in self.config.volatility_periods:
            # Standard deviation
            vol = rolling_std(close / close[0], period)
            features.append(vol)
            names.append(f"volatility_{period}")

            # ATR
            atr = self._atr(high, low, close, period)
            features.append(atr / close)
            names.append(f"atr_norm_{period}")

            # Parkinson volatility
            park_vol = self._parkinson_volatility(high, low, period)
            features.append(park_vol)
            names.append(f"parkinson_vol_{period}")

            # Garman-Klass volatility
            gk_vol = self._garman_klass_volatility(open_, high, low, close, period)
            features.append(gk_vol)
            names.append(f"garman_klass_vol_{period}")

        # RSI
        for period in [7, 14, 21]:
            rsi = self._rsi(close, period)
            features.append(rsi)
            names.append(f"rsi_{period}")

        # Stochastic
        for period in [14, 21]:
            stoch_k, stoch_d = self._stochastic(high, low, close, period)
            features.append(stoch_k)
            features.append(stoch_d)
            names.extend([f"stoch_k_{period}", f"stoch_d_{period}"])

        # Williams %R
        for period in [14, 21]:
            williams = self._williams_r(high, low, close, period)
            features.append(williams)
            names.append(f"williams_r_{period}")

        # MACD
        macd, signal, hist = self._macd(close)
        features.extend([macd, signal, hist])
        names.extend(["macd", "macd_signal", "macd_hist"])

        # ADX
        adx, plus_di, minus_di = self._adx(high, low, close, 14)
        features.extend([adx, plus_di, minus_di])
        names.extend(["adx", "plus_di", "minus_di"])

        # Aroon
        aroon_up, aroon_down, aroon_osc = self._aroon(high, low, 25)
        features.extend([aroon_up, aroon_down, aroon_osc])
        names.extend(["aroon_up", "aroon_down", "aroon_osc"])

        # Bollinger Bands
        for period in [20]:
            upper, middle, lower = self._bollinger_bands(close, period)
            bb_width = (upper - lower) / (middle + 1e-10)
            bb_pos = (close - lower) / (upper - lower + 1e-10)
            features.extend([bb_width, bb_pos])
            names.extend([f"bb_width_{period}", f"bb_pos_{period}"])

        # Volume features
        obv = self._obv(close, volume)
        features.append(obv / (np.max(np.abs(obv)) + 1e-10))
        names.append("obv_norm")

        mfi = self._mfi(high, low, close, volume, 14)
        features.append(mfi)
        names.append("mfi")

        # VWAP distance
        vwap = self._vwap(high, low, close, volume)
        features.append((close - vwap) / (vwap + 1e-10))
        names.append("vwap_dist")

        # Volume momentum
        vol_ma = sma(volume, 20)
        features.append(volume / (vol_ma + 1e-10) - 1)
        names.append("volume_ratio")

        # Price patterns
        doji = self._doji_pattern(open_, high, low, close)
        features.append(doji)
        names.append("doji")

        hammer = self._hammer_pattern(open_, high, low, close)
        features.append(hammer)
        names.append("hammer")

        engulfing = self._engulfing_pattern(open_, close)
        features.append(engulfing)
        names.append("engulfing")

        # Stack features
        feature_matrix = np.column_stack(features)

        return feature_matrix, names

    def _returns(self, close: np.ndarray, period: int) -> np.ndarray:
        """Simple returns."""
        result = np.full_like(close, np.nan)
        result[period:] = close[period:] / close[:-period] - 1
        return result

    def _log_returns(self, close: np.ndarray, period: int) -> np.ndarray:
        """Log returns."""
        result = np.full_like(close, np.nan)
        result[period:] = np.log(close[period:] / close[:-period])
        return result

    def _kama(self, close: np.ndarray, period: int, fast: int = 2, slow: int = 30) -> np.ndarray:
        """Kaufman Adaptive Moving Average."""
        result = np.full_like(close, np.nan)

        # Efficiency ratio
        change = np.abs(close[period:] - close[:-period])
        volatility = np.zeros(len(close) - period)
        for i in range(len(volatility)):
            volatility[i] = np.sum(np.abs(np.diff(close[i:i+period+1])))

        er = np.zeros(len(close))
        er[period:] = change / (volatility + 1e-10)

        # Smoothing constant
        fast_sc = 2.0 / (fast + 1)
        slow_sc = 2.0 / (slow + 1)
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2

        # KAMA
        result[period-1] = close[period-1]
        for i in range(period, len(close)):
            result[i] = result[i-1] + sc[i] * (close[i] - result[i-1])

        return result

    def _atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        """Average True Range."""
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]
        return ema(tr, period)

    def _parkinson_volatility(self, high: np.ndarray, low: np.ndarray, period: int) -> np.ndarray:
        """Parkinson volatility estimator."""
        hl_ratio = np.log(high / low) ** 2
        factor = 1.0 / (4.0 * np.log(2))
        result = np.full_like(high, np.nan)
        for i in range(period - 1, len(high)):
            result[i] = np.sqrt(factor * np.mean(hl_ratio[i-period+1:i+1]))
        return result

    def _garman_klass_volatility(
        self, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> np.ndarray:
        """Garman-Klass volatility estimator."""
        hl = (np.log(high / low)) ** 2
        co = (np.log(close / open_)) ** 2
        gk = 0.5 * hl - (2 * np.log(2) - 1) * co

        result = np.full_like(close, np.nan)
        for i in range(period - 1, len(close)):
            result[i] = np.sqrt(np.mean(gk[i-period+1:i+1]))
        return result

    def _rsi(self, close: np.ndarray, period: int) -> np.ndarray:
        """Relative Strength Index."""
        delta = np.diff(close, prepend=close[0])
        gains = np.where(delta > 0, delta, 0)
        losses = np.where(delta < 0, -delta, 0)

        avg_gain = ema(gains, period)
        avg_loss = ema(losses, period)

        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return rsi / 100  # Normalize to [0, 1]

    def _stochastic(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int, k_smooth: int = 3
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Stochastic oscillator."""
        lowest = rolling_min(low, period)
        highest = rolling_max(high, period)

        stoch_k = (close - lowest) / (highest - lowest + 1e-10)
        stoch_d = sma(stoch_k, k_smooth)

        return stoch_k, stoch_d

    def _williams_r(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        """Williams %R."""
        highest = rolling_max(high, period)
        lowest = rolling_min(low, period)

        wr = (highest - close) / (highest - lowest + 1e-10)
        return -wr  # Invert so higher is bullish

    def _macd(
        self, close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """MACD indicator."""
        fast_ema = ema(close, fast)
        slow_ema = ema(close, slow)

        macd_line = fast_ema - slow_ema
        signal_line = ema(macd_line, signal)
        histogram = macd_line - signal_line

        # Normalize
        norm = np.abs(close).mean()
        return macd_line / norm, signal_line / norm, histogram / norm

    def _adx(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Average Directional Index."""
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]

        plus_dm = np.where(
            (high - np.roll(high, 1)) > (np.roll(low, 1) - low),
            np.maximum(high - np.roll(high, 1), 0),
            0
        )
        minus_dm = np.where(
            (np.roll(low, 1) - low) > (high - np.roll(high, 1)),
            np.maximum(np.roll(low, 1) - low, 0),
            0
        )

        tr_smooth = ema(tr, period)
        plus_dm_smooth = ema(plus_dm, period)
        minus_dm_smooth = ema(minus_dm, period)

        plus_di = 100 * plus_dm_smooth / (tr_smooth + 1e-10)
        minus_di = 100 * minus_dm_smooth / (tr_smooth + 1e-10)

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = ema(dx, period)

        return adx / 100, plus_di / 100, minus_di / 100

    def _aroon(self, high: np.ndarray, low: np.ndarray, period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Aroon indicator."""
        aroon_up = np.full_like(high, np.nan)
        aroon_down = np.full_like(high, np.nan)

        for i in range(period, len(high)):
            window_high = high[i-period:i+1]
            window_low = low[i-period:i+1]

            high_idx = np.argmax(window_high)
            low_idx = np.argmin(window_low)

            aroon_up[i] = (period - (period - high_idx)) / period
            aroon_down[i] = (period - (period - low_idx)) / period

        aroon_osc = aroon_up - aroon_down
        return aroon_up, aroon_down, aroon_osc

    def _bollinger_bands(
        self, close: np.ndarray, period: int, num_std: float = 2.0
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bollinger Bands."""
        middle = sma(close, period)
        std = rolling_std(close, period)

        upper = middle + num_std * std
        lower = middle - num_std * std

        return upper, middle, lower

    def _obv(self, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """On-Balance Volume."""
        direction = np.sign(np.diff(close, prepend=close[0]))
        return np.cumsum(direction * volume)

    def _mfi(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int
    ) -> np.ndarray:
        """Money Flow Index."""
        typical_price = (high + low + close) / 3
        raw_mf = typical_price * volume

        delta = np.diff(typical_price, prepend=typical_price[0])
        pos_mf = np.where(delta > 0, raw_mf, 0)
        neg_mf = np.where(delta < 0, raw_mf, 0)

        result = np.full_like(close, np.nan)
        for i in range(period, len(close)):
            pos_sum = np.sum(pos_mf[i-period+1:i+1])
            neg_sum = np.sum(neg_mf[i-period+1:i+1])
            if neg_sum > 0:
                mf_ratio = pos_sum / neg_sum
                result[i] = 100 - (100 / (1 + mf_ratio))
            else:
                result[i] = 100

        return result / 100

    def _vwap(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
        """Volume Weighted Average Price."""
        typical_price = (high + low + close) / 3
        cumulative_tp_vol = np.cumsum(typical_price * volume)
        cumulative_vol = np.cumsum(volume)
        return cumulative_tp_vol / (cumulative_vol + 1e-10)

    def _doji_pattern(
        self, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> np.ndarray:
        """Detect doji candlestick pattern."""
        body = np.abs(close - open_)
        range_ = high - low
        return (body / (range_ + 1e-10) < 0.1).astype(float)

    def _hammer_pattern(
        self, open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> np.ndarray:
        """Detect hammer candlestick pattern."""
        body = np.abs(close - open_)
        upper_shadow = high - np.maximum(close, open_)
        lower_shadow = np.minimum(close, open_) - low

        is_hammer = (
            (lower_shadow > 2 * body) &
            (upper_shadow < body * 0.5) &
            (body > 0)
        )
        return is_hammer.astype(float)

    def _engulfing_pattern(self, open_: np.ndarray, close: np.ndarray) -> np.ndarray:
        """Detect engulfing candlestick pattern."""
        result = np.zeros_like(close)

        for i in range(1, len(close)):
            prev_body = close[i-1] - open_[i-1]
            curr_body = close[i] - open_[i]

            # Bullish engulfing
            if prev_body < 0 and curr_body > 0:
                if open_[i] < close[i-1] and close[i] > open_[i-1]:
                    result[i] = 1.0
            # Bearish engulfing
            elif prev_body > 0 and curr_body < 0:
                if open_[i] > close[i-1] and close[i] < open_[i-1]:
                    result[i] = -1.0

        return result


# =============================================================================
# Microstructure Features
# =============================================================================

class MicrostructureFeatures:
    """
    Market microstructure features.

    Captures order flow dynamics and market impact:
    - Order flow imbalance
    - Kyle's lambda (price impact)
    - Amihud illiquidity
    - Realized volatility components
    - Volume clock features
    """

    def __init__(self, config: FeatureConfig):
        self.config = config

    def compute_all(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        bid: Optional[np.ndarray] = None,
        ask: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, List[str]]:
        """Compute all microstructure features."""
        features = []
        names = []

        # Amihud illiquidity
        for period in [5, 20]:
            amihud = self._amihud_illiquidity(close, volume, period)
            features.append(amihud)
            names.append(f"amihud_{period}")

        # Kyle's lambda (price impact)
        for period in [20, 50]:
            kyle_lambda = self._kyle_lambda(close, volume, period)
            features.append(kyle_lambda)
            names.append(f"kyle_lambda_{period}")

        # Volume-price correlation
        for period in [10, 20]:
            vp_corr = self._volume_price_correlation(close, volume, period)
            features.append(vp_corr)
            names.append(f"volume_price_corr_{period}")

        # Trade intensity
        trade_intensity = self._trade_intensity(volume, 20)
        features.append(trade_intensity)
        names.append("trade_intensity")

        # Volume acceleration
        vol_accel = self._volume_acceleration(volume, 10)
        features.append(vol_accel)
        names.append("volume_acceleration")

        # Intraday volatility ratio
        vol_ratio = self._volatility_ratio(high, low, close, 20)
        features.append(vol_ratio)
        names.append("volatility_ratio")

        # Close location value
        clv = self._close_location_value(high, low, close)
        features.append(clv)
        names.append("close_location_value")

        # Accumulation/Distribution
        ad_line = self._accumulation_distribution(high, low, close, volume)
        ad_norm = ad_line / (np.max(np.abs(ad_line)) + 1e-10)
        features.append(ad_norm)
        names.append("acc_dist")

        # Chaikin Money Flow
        for period in [10, 20]:
            cmf = self._chaikin_money_flow(high, low, close, volume, period)
            features.append(cmf)
            names.append(f"cmf_{period}")

        # Roll spread estimator
        roll_spread = self._roll_spread(close, 20)
        features.append(roll_spread)
        names.append("roll_spread")

        # If bid/ask available
        if bid is not None and ask is not None:
            spread = (ask - bid) / ((ask + bid) / 2 + 1e-10)
            features.append(spread)
            names.append("bid_ask_spread")

            mid = (bid + ask) / 2
            micro_price = (bid * ask + ask * bid) / (bid + ask + 1e-10)
            features.append((close - micro_price) / (micro_price + 1e-10))
            names.append("micro_price_dist")

        feature_matrix = np.column_stack(features)
        return feature_matrix, names

    def _amihud_illiquidity(self, close: np.ndarray, volume: np.ndarray, period: int) -> np.ndarray:
        """Amihud illiquidity measure."""
        returns = np.abs(np.diff(close, prepend=close[0]) / close)
        dollar_volume = close * volume

        ratio = returns / (dollar_volume + 1e-10)
        result = np.full_like(close, np.nan)

        for i in range(period - 1, len(close)):
            result[i] = np.mean(ratio[i-period+1:i+1])

        # Normalize
        result = np.log1p(result * 1e10)
        return result

    def _kyle_lambda(self, close: np.ndarray, volume: np.ndarray, period: int) -> np.ndarray:
        """Kyle's lambda - price impact coefficient."""
        returns = np.diff(close, prepend=close[0]) / close
        signed_volume = np.sign(returns) * volume

        result = np.full_like(close, np.nan)

        for i in range(period - 1, len(close)):
            ret_window = returns[i-period+1:i+1]
            sv_window = signed_volume[i-period+1:i+1]

            if np.std(sv_window) > 1e-10:
                # Simple regression coefficient
                cov = np.cov(ret_window, sv_window)[0, 1]
                var = np.var(sv_window)
                result[i] = cov / (var + 1e-10)

        return result * 1e6  # Scale

    def _volume_price_correlation(self, close: np.ndarray, volume: np.ndarray, period: int) -> np.ndarray:
        """Rolling correlation between price changes and volume."""
        price_change = np.diff(close, prepend=close[0])
        return rolling_correlation(np.abs(price_change), volume, period)

    def _trade_intensity(self, volume: np.ndarray, period: int) -> np.ndarray:
        """Volume relative to recent average."""
        vol_ma = sma(volume, period)
        return volume / (vol_ma + 1e-10)

    def _volume_acceleration(self, volume: np.ndarray, period: int) -> np.ndarray:
        """Rate of change of volume."""
        vol_ma = sma(volume, period)
        vol_ma_prev = np.roll(vol_ma, period)
        vol_ma_prev[:period] = vol_ma[:period]
        return (vol_ma - vol_ma_prev) / (vol_ma_prev + 1e-10)

    def _volatility_ratio(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> np.ndarray:
        """Ratio of intraday to overnight volatility."""
        intraday_vol = (high - low) / close
        overnight_gap = np.abs(np.diff(close, prepend=close[0])) / close

        intra_ma = sma(intraday_vol, period)
        over_ma = sma(overnight_gap, period)

        return intra_ma / (over_ma + 1e-10)

    def _close_location_value(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
        """Where close is relative to high-low range."""
        return (2 * close - high - low) / (high - low + 1e-10)

    def _accumulation_distribution(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray
    ) -> np.ndarray:
        """Accumulation/Distribution line."""
        clv = (2 * close - high - low) / (high - low + 1e-10)
        return np.cumsum(clv * volume)

    def _chaikin_money_flow(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int
    ) -> np.ndarray:
        """Chaikin Money Flow."""
        clv = (2 * close - high - low) / (high - low + 1e-10)
        mf_volume = clv * volume

        result = np.full_like(close, np.nan)
        for i in range(period - 1, len(close)):
            mfv_sum = np.sum(mf_volume[i-period+1:i+1])
            vol_sum = np.sum(volume[i-period+1:i+1])
            result[i] = mfv_sum / (vol_sum + 1e-10)

        return result

    def _roll_spread(self, close: np.ndarray, period: int) -> np.ndarray:
        """Roll spread estimator."""
        returns = np.diff(close, prepend=close[0]) / close

        result = np.full_like(close, np.nan)
        for i in range(period, len(close)):
            ret_window = returns[i-period+1:i+1]
            cov = np.cov(ret_window[:-1], ret_window[1:])[0, 1]
            if cov < 0:
                result[i] = 2 * np.sqrt(-cov)
            else:
                result[i] = 0

        return result


# =============================================================================
# Cross-Asset Features
# =============================================================================

class CrossAssetFeatures:
    """
    Cross-asset relationship features.

    Captures:
    - Rolling correlations
    - Lead-lag relationships
    - Beta to market/sector
    - Relative strength
    - Cointegration signals
    """

    def __init__(self, config: FeatureConfig):
        self.config = config

    def compute_all(
        self,
        close: np.ndarray,
        other_assets: Dict[str, np.ndarray],
        market_index: Optional[np.ndarray] = None
    ) -> Tuple[np.ndarray, List[str]]:
        """Compute all cross-asset features."""
        features = []
        names = []

        returns = np.diff(close, prepend=close[0]) / close

        for asset_name, asset_prices in other_assets.items():
            if len(asset_prices) != len(close):
                continue

            asset_returns = np.diff(asset_prices, prepend=asset_prices[0]) / asset_prices

            # Rolling correlation
            for period in self.config.correlation_periods:
                corr = rolling_correlation(returns, asset_returns, period)
                features.append(corr)
                names.append(f"corr_{asset_name}_{period}")

            # Lead-lag correlation (does other asset lead us?)
            lead_corr = self._lead_lag_correlation(returns, asset_returns, 20, lag=1)
            features.append(lead_corr)
            names.append(f"lead_corr_{asset_name}")

            # Relative strength
            rel_strength = self._relative_strength(close, asset_prices, 20)
            features.append(rel_strength)
            names.append(f"rel_strength_{asset_name}")

        # Market beta if index provided
        if market_index is not None and len(market_index) == len(close):
            market_returns = np.diff(market_index, prepend=market_index[0]) / market_index

            for period in [20, 60]:
                beta = self._rolling_beta(returns, market_returns, period)
                features.append(beta)
                names.append(f"market_beta_{period}")

            # Alpha (excess return)
            alpha = self._rolling_alpha(returns, market_returns, 20)
            features.append(alpha)
            names.append("market_alpha")

            # Information ratio
            ir = self._information_ratio(returns, market_returns, 60)
            features.append(ir)
            names.append("information_ratio")

        if not features:
            return np.zeros((len(close), 1)), ["placeholder"]

        feature_matrix = np.column_stack(features)
        return feature_matrix, names

    def _lead_lag_correlation(
        self, returns1: np.ndarray, returns2: np.ndarray, period: int, lag: int = 1
    ) -> np.ndarray:
        """Correlation with lag."""
        lagged_returns2 = np.roll(returns2, lag)
        lagged_returns2[:lag] = 0
        return rolling_correlation(returns1, lagged_returns2, period)

    def _relative_strength(self, price1: np.ndarray, price2: np.ndarray, period: int) -> np.ndarray:
        """Relative strength ratio momentum."""
        ratio = price1 / (price2 + 1e-10)
        ratio_ma = sma(ratio, period)
        return (ratio - ratio_ma) / (ratio_ma + 1e-10)

    def _rolling_beta(self, returns: np.ndarray, market_returns: np.ndarray, period: int) -> np.ndarray:
        """Rolling beta to market."""
        result = np.full_like(returns, np.nan)

        for i in range(period - 1, len(returns)):
            ret_window = returns[i-period+1:i+1]
            mkt_window = market_returns[i-period+1:i+1]

            cov = np.cov(ret_window, mkt_window)[0, 1]
            var = np.var(mkt_window)
            result[i] = cov / (var + 1e-10)

        return result

    def _rolling_alpha(self, returns: np.ndarray, market_returns: np.ndarray, period: int) -> np.ndarray:
        """Rolling alpha (Jensen's alpha)."""
        beta = self._rolling_beta(returns, market_returns, period)

        result = np.full_like(returns, np.nan)
        for i in range(period - 1, len(returns)):
            ret_sum = np.sum(returns[i-period+1:i+1])
            mkt_sum = np.sum(market_returns[i-period+1:i+1])
            result[i] = (ret_sum - beta[i] * mkt_sum) / period

        return result * 252  # Annualize

    def _information_ratio(self, returns: np.ndarray, benchmark: np.ndarray, period: int) -> np.ndarray:
        """Information ratio."""
        excess = returns - benchmark

        result = np.full_like(returns, np.nan)
        for i in range(period - 1, len(returns)):
            excess_window = excess[i-period+1:i+1]
            mean_excess = np.mean(excess_window)
            std_excess = np.std(excess_window, ddof=1)
            result[i] = mean_excess / (std_excess + 1e-10) * np.sqrt(252)

        return result


# =============================================================================
# Regime Detection Features
# =============================================================================

class RegimeFeatures:
    """
    Market regime detection features.

    Identifies:
    - Trend vs range-bound regimes
    - Volatility regimes
    - Correlation regimes
    - Hidden Markov Model probabilities
    """

    def __init__(self, config: FeatureConfig):
        self.config = config

    def compute_all(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray
    ) -> Tuple[np.ndarray, List[str]]:
        """Compute all regime features."""
        features = []
        names = []

        window = self.config.regime_window

        # Trend strength (ADX-based)
        trend_strength = self._trend_strength(high, low, close, window)
        features.append(trend_strength)
        names.append("trend_strength")

        # Trend direction
        trend_direction = self._trend_direction(close, window)
        features.append(trend_direction)
        names.append("trend_direction")

        # Volatility regime
        vol_regime = self._volatility_regime(close, window)
        features.append(vol_regime)
        names.append("volatility_regime")

        # Mean reversion indicator
        mean_rev = self._mean_reversion_indicator(close, window)
        features.append(mean_rev)
        names.append("mean_reversion")

        # Efficiency ratio (trending vs noise)
        efficiency = self._efficiency_ratio(close, window)
        features.append(efficiency)
        names.append("efficiency_ratio")

        # Choppiness index
        choppiness = self._choppiness_index(high, low, close, window)
        features.append(choppiness)
        names.append("choppiness")

        # Dispersion (intraday range relative to trend)
        dispersion = self._dispersion(high, low, close, window)
        features.append(dispersion)
        names.append("dispersion")

        # HMM-like regime probabilities
        hmm_probs = self._hmm_regime_probabilities(close, window)
        features.append(hmm_probs[:, 0])  # Bull probability
        features.append(hmm_probs[:, 1])  # Bear probability
        names.extend(["hmm_bull_prob", "hmm_bear_prob"])

        # Volume regime
        vol_regime_indicator = self._volume_regime(volume, window)
        features.append(vol_regime_indicator)
        names.append("volume_regime")

        # Breakout probability
        breakout_prob = self._breakout_probability(high, low, close, window)
        features.append(breakout_prob)
        names.append("breakout_prob")

        feature_matrix = np.column_stack(features)
        return feature_matrix, names

    def _trend_strength(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        """ADX-based trend strength."""
        # Simplified ADX calculation
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]

        plus_dm = np.maximum(high - np.roll(high, 1), 0)
        minus_dm = np.maximum(np.roll(low, 1) - low, 0)

        plus_dm = np.where(plus_dm > minus_dm, plus_dm, 0)
        minus_dm = np.where(minus_dm > plus_dm, minus_dm, 0)

        tr_smooth = ema(tr, period)
        plus_di = ema(plus_dm, period) / (tr_smooth + 1e-10)
        minus_di = ema(minus_dm, period) / (tr_smooth + 1e-10)

        dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = ema(dx, period)

        return adx

    def _trend_direction(self, close: np.ndarray, period: int) -> np.ndarray:
        """Trend direction indicator."""
        ma_fast = ema(close, period // 2)
        ma_slow = ema(close, period)

        return np.sign(ma_fast - ma_slow)

    def _volatility_regime(self, close: np.ndarray, period: int) -> np.ndarray:
        """Volatility regime (high/low) indicator."""
        vol = rolling_std(np.diff(close, prepend=close[0]) / close, period)
        vol_ma = sma(vol, period * 2)

        # Normalize: >1 = high vol, <1 = low vol
        return vol / (vol_ma + 1e-10)

    def _mean_reversion_indicator(self, close: np.ndarray, period: int) -> np.ndarray:
        """Hurst exponent approximation for mean reversion."""
        result = np.full_like(close, np.nan)

        for i in range(period, len(close)):
            window = close[i-period:i+1]

            # R/S analysis simplified
            mean = np.mean(window)
            deviations = window - mean
            cumulative = np.cumsum(deviations)

            r = np.max(cumulative) - np.min(cumulative)
            s = np.std(window, ddof=1)

            if s > 1e-10:
                rs = r / s
                # Hurst approximation
                result[i] = np.log(rs + 1) / np.log(period)

        # H < 0.5 = mean reverting, H > 0.5 = trending
        return result

    def _efficiency_ratio(self, close: np.ndarray, period: int) -> np.ndarray:
        """Kaufman efficiency ratio."""
        change = np.abs(close[period:] - close[:-period])

        volatility = np.zeros(len(close) - period)
        for i in range(len(volatility)):
            volatility[i] = np.sum(np.abs(np.diff(close[i:i+period+1])))

        result = np.full_like(close, np.nan)
        result[period:] = change / (volatility + 1e-10)

        return result

    def _choppiness_index(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> np.ndarray:
        """Choppiness Index - measures trendiness."""
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]

        result = np.full_like(close, np.nan)

        for i in range(period - 1, len(close)):
            atr_sum = np.sum(tr[i-period+1:i+1])
            highest = np.max(high[i-period+1:i+1])
            lowest = np.min(low[i-period+1:i+1])

            if highest > lowest:
                chop = 100 * np.log10(atr_sum / (highest - lowest)) / np.log10(period)
                result[i] = chop / 100  # Normalize to [0, 1]

        return result

    def _dispersion(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        """Price dispersion relative to trend."""
        ma = sma(close, period)
        intraday_range = high - low
        trend_range = np.abs(close - ma)

        return intraday_range / (trend_range + 1e-10)

    def _hmm_regime_probabilities(self, close: np.ndarray, period: int) -> np.ndarray:
        """
        Simple HMM-like regime probability estimation.
        Uses rolling statistics to estimate bull/bear probabilities.
        """
        returns = np.diff(close, prepend=close[0]) / close

        result = np.full((len(close), 2), 0.5)  # [bull_prob, bear_prob]

        for i in range(period, len(close)):
            ret_window = returns[i-period+1:i+1]

            # Mean and std of returns
            mean_ret = np.mean(ret_window)
            std_ret = np.std(ret_window, ddof=1)

            if std_ret > 1e-10:
                # Z-score of current return
                z = mean_ret / std_ret

                # Sigmoid to convert to probability
                bull_prob = 1.0 / (1.0 + np.exp(-z * 2))
                result[i, 0] = bull_prob
                result[i, 1] = 1 - bull_prob

        return result

    def _volume_regime(self, volume: np.ndarray, period: int) -> np.ndarray:
        """Volume regime indicator."""
        vol_ma = sma(volume, period)
        vol_ma_long = sma(volume, period * 2)

        return vol_ma / (vol_ma_long + 1e-10)

    def _breakout_probability(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int
    ) -> np.ndarray:
        """Probability of breakout based on compression."""
        # Bollinger Band squeeze
        ma = sma(close, period)
        std = rolling_std(close, period)

        # Keltner Channel
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        tr[0] = high[0] - low[0]
        atr = ema(tr, period)

        bb_width = 2 * std
        kc_width = 2 * atr

        # Squeeze = BB inside KC
        squeeze_ratio = bb_width / (kc_width + 1e-10)

        # Lower squeeze_ratio = higher breakout probability
        breakout_prob = 1 - np.clip(squeeze_ratio, 0, 1)

        return breakout_prob


# =============================================================================
# Fractal and Complexity Features
# =============================================================================

class FractalFeatures:
    """
    Fractal and complexity-based features.

    Captures market complexity and self-similarity:
    - Hurst exponent
    - Fractal dimension
    - Entropy measures
    - Lyapunov exponent approximation
    """

    def __init__(self, config: FeatureConfig):
        self.config = config

    def compute_all(self, close: np.ndarray) -> Tuple[np.ndarray, List[str]]:
        """Compute all fractal features."""
        features = []
        names = []

        for scale in self.config.fractal_scales:
            if scale > len(close) // 4:
                continue

            # Hurst exponent
            hurst = self._rolling_hurst(close, scale * 2)
            features.append(hurst)
            names.append(f"hurst_{scale}")

            # Fractal dimension
            fd = self._rolling_fractal_dimension(close, scale)
            features.append(fd)
            names.append(f"fractal_dim_{scale}")

        # Sample entropy
        for m in [2, 3]:
            entropy = self._sample_entropy(close, m, self.config.regime_window)
            features.append(entropy)
            names.append(f"sample_entropy_m{m}")

        # Approximate entropy
        approx_ent = self._approximate_entropy(close, 2, self.config.regime_window)
        features.append(approx_ent)
        names.append("approx_entropy")

        # Permutation entropy
        perm_ent = self._permutation_entropy(close, 3, self.config.regime_window)
        features.append(perm_ent)
        names.append("permutation_entropy")

        feature_matrix = np.column_stack(features)
        return feature_matrix, names

    def _rolling_hurst(self, data: np.ndarray, period: int) -> np.ndarray:
        """Rolling Hurst exponent using R/S analysis."""
        result = np.full_like(data, np.nan)

        for i in range(period, len(data)):
            window = data[i-period:i+1]

            # R/S analysis
            mean = np.mean(window)
            deviations = window - mean
            cumsum = np.cumsum(deviations)

            r = np.max(cumsum) - np.min(cumsum)
            s = np.std(window, ddof=1)

            if s > 1e-10 and r > 0:
                rs = r / s
                result[i] = np.log(rs) / np.log(period)

        return result

    def _rolling_fractal_dimension(self, data: np.ndarray, period: int) -> np.ndarray:
        """Rolling fractal dimension using box-counting approximation."""
        result = np.full_like(data, np.nan)

        for i in range(period, len(data)):
            window = data[i-period:i+1]

            # Normalize window
            min_val = np.min(window)
            max_val = np.max(window)
            if max_val > min_val:
                normalized = (window - min_val) / (max_val - min_val)
            else:
                result[i] = 1.0
                continue

            # Box counting at different scales
            scales = [2, 4, 8, 16]
            counts = []

            for scale in scales:
                if scale > period:
                    break

                step = period // scale
                if step < 1:
                    break

                boxes = set()
                for j in range(0, period, step):
                    x_box = j // step
                    y_box = int(normalized[j] * scale)
                    boxes.add((x_box, y_box))

                counts.append((np.log(1/scale), np.log(len(boxes))))

            if len(counts) >= 2:
                x = np.array([c[0] for c in counts])
                y = np.array([c[1] for c in counts])

                # Linear regression for dimension
                if np.std(x) > 1e-10:
                    slope = np.cov(x, y)[0, 1] / np.var(x)
                    result[i] = slope

        return result

    def _sample_entropy(self, data: np.ndarray, m: int, period: int) -> np.ndarray:
        """Rolling sample entropy."""
        result = np.full_like(data, np.nan)

        for i in range(period, len(data)):
            window = data[i-period:i+1]
            result[i] = self._compute_sample_entropy(window, m)

        return result

    def _compute_sample_entropy(self, data: np.ndarray, m: int, r: float = 0.2) -> float:
        """Compute sample entropy for a window."""
        n = len(data)
        if n < m + 2:
            return np.nan

        std = np.std(data)
        if std < 1e-10:
            return 0.0

        tolerance = r * std

        def count_matches(template_len):
            count = 0
            templates = [data[i:i+template_len] for i in range(n - template_len)]
            for i in range(len(templates)):
                for j in range(i + 1, len(templates)):
                    if np.max(np.abs(templates[i] - templates[j])) < tolerance:
                        count += 1
            return count

        a = count_matches(m + 1)
        b = count_matches(m)

        if b == 0:
            return np.nan
        if a == 0:
            return np.nan

        return -np.log(a / b)

    def _approximate_entropy(self, data: np.ndarray, m: int, period: int) -> np.ndarray:
        """Rolling approximate entropy."""
        result = np.full_like(data, np.nan)

        for i in range(period, len(data)):
            window = data[i-period:i+1]
            result[i] = self._compute_approx_entropy(window, m)

        return result

    def _compute_approx_entropy(self, data: np.ndarray, m: int, r: float = 0.2) -> float:
        """Compute approximate entropy."""
        n = len(data)
        if n < m + 2:
            return np.nan

        std = np.std(data)
        if std < 1e-10:
            return 0.0

        tolerance = r * std

        def phi(template_len):
            templates = [data[i:i+template_len] for i in range(n - template_len + 1)]
            counts = []
            for i, t1 in enumerate(templates):
                count = sum(1 for t2 in templates if np.max(np.abs(t1 - t2)) < tolerance)
                counts.append(count / len(templates))
            return np.mean(np.log(counts))

        return phi(m) - phi(m + 1)

    def _permutation_entropy(self, data: np.ndarray, order: int, period: int) -> np.ndarray:
        """Rolling permutation entropy."""
        result = np.full_like(data, np.nan)

        for i in range(period, len(data)):
            window = data[i-period:i+1]
            result[i] = self._compute_permutation_entropy(window, order)

        return result

    def _compute_permutation_entropy(self, data: np.ndarray, order: int) -> float:
        """Compute permutation entropy."""
        n = len(data)
        if n < order + 1:
            return np.nan

        # Count permutation patterns
        from collections import Counter
        patterns = []

        for i in range(n - order):
            window = data[i:i+order+1]
            pattern = tuple(np.argsort(window))
            patterns.append(pattern)

        counter = Counter(patterns)
        probs = np.array(list(counter.values())) / len(patterns)

        # Normalized entropy
        max_entropy = np.log2(np.math.factorial(order + 1))
        entropy = -np.sum(probs * np.log2(probs + 1e-10))

        return entropy / max_entropy if max_entropy > 0 else 0


# =============================================================================
# Main Feature Engine
# =============================================================================

class FeatureEngine:
    """
    Main feature engineering pipeline.

    Orchestrates all feature generators and produces
    a unified, normalized feature set.
    """

    def __init__(self, config: Optional[FeatureConfig] = None):
        self.config = config or FeatureConfig()

        self.technical = TechnicalFeatures(self.config)
        self.microstructure = MicrostructureFeatures(self.config)
        self.cross_asset = CrossAssetFeatures(self.config)
        self.regime = RegimeFeatures(self.config)
        self.fractal = FractalFeatures(self.config)

    def compute_features(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        other_assets: Optional[Dict[str, np.ndarray]] = None,
        market_index: Optional[np.ndarray] = None,
        bid: Optional[np.ndarray] = None,
        ask: Optional[np.ndarray] = None,
        timestamps: Optional[np.ndarray] = None,
        include_fractal: bool = True
    ) -> FeatureSet:
        """
        Compute all features for the given price data.

        Args:
            open_, high, low, close, volume: OHLCV data
            other_assets: Dict of other asset close prices for cross-asset features
            market_index: Market index for beta calculations
            bid, ask: Optional bid/ask for microstructure
            timestamps: Optional timestamps
            include_fractal: Whether to include computationally expensive fractal features

        Returns:
            FeatureSet with all computed features
        """
        all_features = []
        all_names = []

        # Technical features
        tech_features, tech_names = self.technical.compute_all(open_, high, low, close, volume)
        all_features.append(tech_features)
        all_names.extend(tech_names)

        # Microstructure features
        micro_features, micro_names = self.microstructure.compute_all(
            open_, high, low, close, volume, bid, ask
        )
        all_features.append(micro_features)
        all_names.extend(micro_names)

        # Cross-asset features
        if other_assets:
            cross_features, cross_names = self.cross_asset.compute_all(
                close, other_assets, market_index
            )
            all_features.append(cross_features)
            all_names.extend(cross_names)

        # Regime features
        regime_features, regime_names = self.regime.compute_all(open_, high, low, close, volume)
        all_features.append(regime_features)
        all_names.extend(regime_names)

        # Fractal features (optional, computationally expensive)
        if include_fractal:
            fractal_features, fractal_names = self.fractal.compute_all(close)
            all_features.append(fractal_features)
            all_names.extend(fractal_names)

        # Combine all features
        feature_matrix = np.hstack(all_features)

        # Handle missing values
        feature_matrix = self._handle_missing(feature_matrix)

        # Normalize features
        if self.config.normalize_features:
            feature_matrix = self._normalize(feature_matrix)

        # Handle outliers
        feature_matrix = self._handle_outliers(feature_matrix)

        return FeatureSet(
            features=feature_matrix,
            feature_names=all_names,
            timestamps=timestamps,
            metadata={
                "n_technical": len(tech_names),
                "n_microstructure": len(micro_names),
                "n_regime": len(regime_names),
                "config": self.config
            }
        )

    def _handle_missing(self, features: np.ndarray) -> np.ndarray:
        """Handle missing values."""
        if self.config.handle_missing == "forward_fill":
            # Forward fill NaNs
            for col in range(features.shape[1]):
                mask = np.isnan(features[:, col])
                if mask.any():
                    idx = np.where(~mask, np.arange(len(mask)), 0)
                    np.maximum.accumulate(idx, out=idx)
                    features[:, col] = features[idx, col]
                    # Fill any remaining NaNs at the start with 0
                    features[:, col] = np.nan_to_num(features[:, col], nan=0.0)

        elif self.config.handle_missing == "zero":
            features = np.nan_to_num(features, nan=0.0)

        return features

    def _normalize(self, features: np.ndarray) -> np.ndarray:
        """Z-score normalization with rolling statistics."""
        # Use rolling mean/std for online normalization
        window = min(200, len(features) // 2)

        normalized = np.zeros_like(features)

        for i in range(len(features)):
            start = max(0, i - window + 1)
            window_data = features[start:i+1]

            mean = np.nanmean(window_data, axis=0)
            std = np.nanstd(window_data, axis=0)
            std = np.where(std < 1e-10, 1.0, std)

            normalized[i] = (features[i] - mean) / std

        return normalized

    def _handle_outliers(self, features: np.ndarray) -> np.ndarray:
        """Clip outliers based on threshold."""
        threshold = self.config.outlier_threshold
        features = np.clip(features, -threshold, threshold)
        return features

    def get_feature_importance(self, feature_set: FeatureSet, target: np.ndarray) -> Dict[str, float]:
        """
        Calculate feature importance using correlation with target.

        Args:
            feature_set: Computed features
            target: Target variable (e.g., future returns)

        Returns:
            Dict mapping feature names to importance scores
        """
        importance = {}

        for i, name in enumerate(feature_set.feature_names):
            feature = feature_set.features[:, i]

            # Skip if all NaN or constant
            if np.all(np.isnan(feature)) or np.std(feature) < 1e-10:
                importance[name] = 0.0
                continue

            # Correlation with target
            valid = ~(np.isnan(feature) | np.isnan(target))
            if valid.sum() < 10:
                importance[name] = 0.0
                continue

            corr = np.corrcoef(feature[valid], target[valid])[0, 1]
            importance[name] = abs(corr) if not np.isnan(corr) else 0.0

        return importance

    def select_top_features(
        self, feature_set: FeatureSet, target: np.ndarray, n_features: int = 50
    ) -> FeatureSet:
        """Select top N features by importance."""
        importance = self.get_feature_importance(feature_set, target)

        # Sort by importance
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        top_names = [name for name, _ in sorted_features[:n_features]]

        return feature_set.select_features(top_names)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Generate sample data
    np.random.seed(42)
    n = 1000

    # Simulated price data
    returns = np.random.randn(n) * 0.02
    close = 100 * np.exp(np.cumsum(returns))
    high = close * (1 + np.abs(np.random.randn(n) * 0.01))
    low = close * (1 - np.abs(np.random.randn(n) * 0.01))
    open_ = close * (1 + np.random.randn(n) * 0.005)
    volume = np.abs(np.random.randn(n) * 1000000 + 5000000)

    # Create feature engine
    config = FeatureConfig(
        lookback_periods=[5, 10, 20, 50],
        momentum_periods=[1, 5, 10],
        volatility_periods=[5, 10, 20],
        normalize_features=True
    )

    engine = FeatureEngine(config)

    # Compute features
    feature_set = engine.compute_features(
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        include_fractal=False  # Skip for speed in example
    )

    print(f"Generated {feature_set.n_features} features for {feature_set.n_samples} samples")
    print(f"\nFeature categories:")
    print(f"  Technical: {feature_set.metadata.get('n_technical', 0)}")
    print(f"  Microstructure: {feature_set.metadata.get('n_microstructure', 0)}")
    print(f"  Regime: {feature_set.metadata.get('n_regime', 0)}")

    # Feature importance
    future_returns = np.roll(returns, -1)
    future_returns[-1] = 0

    importance = engine.get_feature_importance(feature_set, future_returns)
    top_5 = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5]

    print(f"\nTop 5 features by importance:")
    for name, imp in top_5:
        print(f"  {name}: {imp:.4f}")
