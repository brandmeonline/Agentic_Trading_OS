"""
Advanced Risk Management System.

Provides comprehensive position sizing, drawdown management, portfolio-level
risk controls, and real-time exposure monitoring.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
from enum import Enum


class RiskLevel(Enum):
    """Risk level classification."""
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class RiskEvent(Enum):
    """Types of risk events."""
    DRAWDOWN_WARNING = "drawdown_warning"
    DRAWDOWN_LIMIT = "drawdown_limit"
    LOSS_STREAK = "loss_streak"
    EXPOSURE_LIMIT = "exposure_limit"
    VOLATILITY_SPIKE = "volatility_spike"
    CORRELATION_RISK = "correlation_risk"


@dataclass
class RiskConfig:
    """Risk management configuration."""
    initial_capital: float = 10000.0
    max_risk_per_trade: float = 0.015  # 1.5%
    max_daily_drawdown: float = 0.06  # 6%
    max_total_drawdown: float = 0.15  # 15%
    max_position_concentration: float = 0.25  # 25% per asset
    max_portfolio_exposure: float = 0.80  # 80% total
    max_loss_streak: int = 3
    volatility_lookback: int = 20
    correlation_threshold: float = 0.7
    kelly_fraction: float = 0.25  # Quarter Kelly for safety
    risk_level: RiskLevel = RiskLevel.MODERATE


@dataclass
class Position:
    """Represents an open position."""
    asset: str
    size: float
    entry_price: float
    current_price: float
    entry_time: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L."""
        return (self.current_price - self.entry_price) * self.size

    @property
    def unrealized_pnl_pct(self) -> float:
        """Calculate unrealized P&L percentage."""
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def market_value(self) -> float:
        """Calculate current market value."""
        return self.size * self.current_price


@dataclass
class RiskMetrics:
    """Comprehensive risk metrics."""
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    var_95: float = 0.0  # Value at Risk 95%
    expected_shortfall: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    risk_reward_ratio: float = 0.0


class RiskManager:
    """
    Advanced risk management system with portfolio-level controls.

    Features:
    - Kelly Criterion position sizing
    - Dynamic drawdown management
    - Correlation-aware exposure limits
    - Volatility-adjusted sizing
    - Real-time risk metrics
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()

        # Capital tracking
        self.initial_capital = self.config.initial_capital
        self.capital = self.config.initial_capital
        self.peak_capital = self.config.initial_capital

        # P&L tracking
        self.pnl_history: List[float] = []
        self.daily_pnl: float = 0.0
        self.total_pnl: float = 0.0

        # Streak tracking
        self.win_streak: int = 0
        self.loss_streak: int = 0
        self.max_win_streak: int = 0
        self.max_loss_streak: int = 0

        # Position tracking
        self.positions: Dict[str, Position] = {}
        self.asset_exposure: Dict[str, float] = {}

        # Volatility tracking
        self.returns_history: deque = deque(maxlen=self.config.volatility_lookback)
        self.volatility: float = 0.0

        # Trade statistics
        self.trade_count: int = 0
        self.win_count: int = 0
        self.loss_count: int = 0
        self.gross_profit: float = 0.0
        self.gross_loss: float = 0.0

        # Risk events
        self.risk_events: List[Tuple[datetime, RiskEvent, str]] = []

        # Daily reset tracking
        self.last_reset_date: Optional[datetime] = None

        # Backward compatibility
        self.max_risk_per_trade = self.config.max_risk_per_trade
        self.max_drawdown = self.config.max_daily_drawdown
        self.daily_loss = 0.0

    def _check_daily_reset(self) -> None:
        """Reset daily metrics if new day."""
        today = datetime.now().date()
        if self.last_reset_date != today:
            self.daily_pnl = 0.0
            self.daily_loss = 0.0
            self.last_reset_date = today

    def _update_volatility(self, return_pct: float) -> None:
        """Update rolling volatility estimate."""
        self.returns_history.append(return_pct)
        if len(self.returns_history) >= 5:
            self.volatility = np.std(list(self.returns_history)) * np.sqrt(252)

    def _calculate_kelly_size(self, win_prob: float, win_loss_ratio: float) -> float:
        """
        Calculate Kelly Criterion position size.

        Kelly = (p * b - q) / b
        where p = win probability, q = loss probability, b = win/loss ratio
        """
        if win_loss_ratio <= 0:
            return 0.0

        q = 1 - win_prob
        kelly = (win_prob * win_loss_ratio - q) / win_loss_ratio

        # Apply fractional Kelly for safety
        return max(0, kelly * self.config.kelly_fraction)

    def _get_volatility_scalar(self) -> float:
        """Get volatility-based position size scalar."""
        if self.volatility == 0:
            return 1.0

        # Target 15% annualized volatility
        target_vol = 0.15
        scalar = target_vol / max(self.volatility, 0.05)

        # Clamp between 0.5x and 2x
        return max(0.5, min(2.0, scalar))

    def _get_drawdown_scalar(self) -> float:
        """Reduce position size during drawdowns."""
        current_dd = self.get_current_drawdown()

        if current_dd < 0.02:
            return 1.0
        elif current_dd < 0.05:
            return 0.75
        elif current_dd < 0.10:
            return 0.5
        else:
            return 0.25

    def _get_streak_scalar(self) -> float:
        """Adjust sizing based on win/loss streaks."""
        if self.win_streak >= 3:
            return 1.1  # Slight increase on hot streak
        elif self.loss_streak >= 2:
            return 0.7  # Reduce on cold streak
        return 1.0

    def get_position_size(
        self,
        asset: str,
        confidence: float,
        entry_price: Optional[float] = None,
        stop_loss_pct: Optional[float] = None
    ) -> float:
        """
        Calculate optimal position size using multiple factors.

        Args:
            asset: Asset symbol
            confidence: Signal confidence (0-1)
            entry_price: Optional entry price for risk calculation
            stop_loss_pct: Optional stop loss percentage

        Returns:
            Recommended position size in capital units
        """
        self._check_daily_reset()

        # Base position size
        base_risk = self.capital * self.config.max_risk_per_trade

        # Confidence scaling (0.7 is baseline)
        confidence_scalar = 1.0 + (confidence - 0.7) * 2.5
        confidence_scalar = max(0.5, min(2.0, confidence_scalar))

        # Apply Kelly if we have statistics
        if self.trade_count >= 20:
            win_prob = self.win_count / self.trade_count
            if self.gross_loss > 0:
                win_loss_ratio = abs(self.gross_profit / self.gross_loss)
                kelly_scalar = self._calculate_kelly_size(win_prob, win_loss_ratio)
                if kelly_scalar > 0:
                    confidence_scalar = min(confidence_scalar, kelly_scalar * 10)

        # Volatility adjustment
        vol_scalar = self._get_volatility_scalar()

        # Drawdown adjustment
        dd_scalar = self._get_drawdown_scalar()

        # Streak adjustment
        streak_scalar = self._get_streak_scalar()

        # Risk level multiplier
        level_multiplier = {
            RiskLevel.CONSERVATIVE: 0.7,
            RiskLevel.MODERATE: 1.0,
            RiskLevel.AGGRESSIVE: 1.3
        }[self.config.risk_level]

        # Calculate final size
        size = base_risk * confidence_scalar * vol_scalar * dd_scalar * streak_scalar * level_multiplier

        # If stop loss provided, size based on risk
        if stop_loss_pct and entry_price:
            risk_amount = self.capital * self.config.max_risk_per_trade
            shares = risk_amount / (entry_price * stop_loss_pct)
            size = min(size, shares * entry_price)

        # Apply exposure limits
        current_exposure = self.asset_exposure.get(asset, 0)
        max_asset_exposure = self.capital * self.config.max_position_concentration
        size = min(size, max_asset_exposure - current_exposure)

        # Apply portfolio exposure limit
        total_exposure = sum(self.asset_exposure.values())
        max_portfolio = self.capital * self.config.max_portfolio_exposure
        size = min(size, max_portfolio - total_exposure)

        # Update exposure tracking
        final_size = max(0, round(size, 2))
        if final_size > 0:
            self.asset_exposure[asset] = self.asset_exposure.get(asset, 0) + final_size

        return final_size

    def update_after_trade(self, asset: str, pnl: float, return_pct: Optional[float] = None) -> None:
        """
        Update risk metrics after a trade.

        Args:
            asset: Asset traded
            pnl: Realized P&L
            return_pct: Optional return percentage for volatility tracking
        """
        self._check_daily_reset()

        # Update P&L tracking
        self.pnl_history.append(pnl)
        self.total_pnl += pnl
        self.capital += pnl
        self.daily_pnl += pnl
        self.trade_count += 1

        # Update daily loss for backward compatibility
        if pnl < 0:
            self.daily_loss += pnl

        # Update peak capital
        if self.capital > self.peak_capital:
            self.peak_capital = self.capital

        # Update win/loss statistics
        if pnl > 0:
            self.win_count += 1
            self.gross_profit += pnl
            self.win_streak += 1
            self.loss_streak = 0
            self.max_win_streak = max(self.max_win_streak, self.win_streak)
        elif pnl < 0:
            self.loss_count += 1
            self.gross_loss += abs(pnl)
            self.loss_streak += 1
            self.win_streak = 0
            self.max_loss_streak = max(self.max_loss_streak, self.loss_streak)

        # Update volatility
        if return_pct is not None:
            self._update_volatility(return_pct)
        elif len(self.pnl_history) > 1:
            implied_return = pnl / max(self.capital - pnl, 1)
            self._update_volatility(implied_return)

        # Update exposure
        if asset in self.asset_exposure:
            self.asset_exposure[asset] = max(0, self.asset_exposure[asset] - abs(pnl))

        # Check for risk events
        self._check_risk_events()

    def check_risk_limits(self, asset: str) -> bool:
        """
        Check if trading should be allowed based on risk limits.

        Args:
            asset: Asset to check

        Returns:
            True if trading is allowed, False otherwise
        """
        self._check_daily_reset()

        # Daily drawdown check
        if abs(self.daily_pnl) >= self.capital * self.config.max_daily_drawdown and self.daily_pnl < 0:
            self._log_risk_event(
                RiskEvent.DRAWDOWN_LIMIT,
                f"Daily drawdown limit reached: {abs(self.daily_pnl):.2f}"
            )
            return False

        # Total drawdown check
        total_dd = self.get_current_drawdown()
        if total_dd >= self.config.max_total_drawdown:
            self._log_risk_event(
                RiskEvent.DRAWDOWN_LIMIT,
                f"Total drawdown limit reached: {total_dd:.1%}"
            )
            return False

        # Loss streak check
        if self.loss_streak >= self.config.max_loss_streak:
            self._log_risk_event(
                RiskEvent.LOSS_STREAK,
                f"Loss streak of {self.loss_streak} triggered pause"
            )
            return False

        # Asset exposure check
        current_exposure = self.asset_exposure.get(asset, 0)
        if current_exposure >= self.capital * self.config.max_position_concentration:
            self._log_risk_event(
                RiskEvent.EXPOSURE_LIMIT,
                f"Exposure limit exceeded for {asset}: {current_exposure:.2f}"
            )
            return False

        # Portfolio exposure check
        total_exposure = sum(self.asset_exposure.values())
        if total_exposure >= self.capital * self.config.max_portfolio_exposure:
            self._log_risk_event(
                RiskEvent.EXPOSURE_LIMIT,
                f"Portfolio exposure limit exceeded: {total_exposure:.2f}"
            )
            return False

        return True

    def _check_risk_events(self) -> None:
        """Check and log any risk events."""
        dd = self.get_current_drawdown()

        # Drawdown warning at 50% of limit
        warning_threshold = self.config.max_total_drawdown * 0.5
        if dd >= warning_threshold and dd < self.config.max_total_drawdown:
            self._log_risk_event(
                RiskEvent.DRAWDOWN_WARNING,
                f"Drawdown warning: {dd:.1%}"
            )

        # Volatility spike
        if self.volatility > 0.4:  # 40% annualized
            self._log_risk_event(
                RiskEvent.VOLATILITY_SPIKE,
                f"Elevated volatility: {self.volatility:.1%}"
            )

    def _log_risk_event(self, event: RiskEvent, message: str) -> None:
        """Log a risk event."""
        self.risk_events.append((datetime.now(), event, message))
        print(f"[RISK] {event.value}: {message}")

    def get_current_drawdown(self) -> float:
        """Calculate current drawdown from peak."""
        if self.peak_capital == 0:
            return 0.0
        return (self.peak_capital - self.capital) / self.peak_capital

    def get_max_drawdown(self) -> float:
        """Calculate maximum historical drawdown."""
        if not self.pnl_history:
            return 0.0

        cumulative = np.cumsum([self.initial_capital] + self.pnl_history)
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak
        return float(np.max(drawdown))

    def calculate_metrics(self) -> RiskMetrics:
        """Calculate comprehensive risk metrics."""
        metrics = RiskMetrics()

        if self.trade_count == 0:
            return metrics

        # Win rate
        metrics.win_rate = self.win_count / self.trade_count if self.trade_count > 0 else 0

        # Average win/loss
        if self.win_count > 0:
            metrics.avg_win = self.gross_profit / self.win_count
        if self.loss_count > 0:
            metrics.avg_loss = self.gross_loss / self.loss_count

        # Profit factor
        if self.gross_loss > 0:
            metrics.profit_factor = self.gross_profit / self.gross_loss

        # Risk/reward ratio
        if metrics.avg_loss > 0:
            metrics.risk_reward_ratio = metrics.avg_win / metrics.avg_loss

        # Drawdown metrics
        metrics.current_drawdown = self.get_current_drawdown()
        metrics.max_drawdown = self.get_max_drawdown()

        # Sharpe and Sortino ratios
        if len(self.returns_history) >= 10:
            returns = list(self.returns_history)
            avg_return = np.mean(returns)
            std_return = np.std(returns)

            risk_free_rate = 0.05 / 252  # Daily risk-free rate

            if std_return > 0:
                metrics.sharpe_ratio = (avg_return - risk_free_rate) / std_return * np.sqrt(252)

            # Sortino (downside deviation only)
            downside_returns = [r for r in returns if r < 0]
            if downside_returns:
                downside_std = np.std(downside_returns)
                if downside_std > 0:
                    metrics.sortino_ratio = (avg_return - risk_free_rate) / downside_std * np.sqrt(252)

            # VaR and Expected Shortfall
            sorted_returns = sorted(returns)
            var_index = int(len(sorted_returns) * 0.05)
            metrics.var_95 = abs(sorted_returns[var_index]) if var_index < len(sorted_returns) else 0
            metrics.expected_shortfall = abs(np.mean(sorted_returns[:var_index + 1])) if var_index > 0 else 0

        return metrics

    def get_summary(self) -> Dict:
        """Get comprehensive risk summary."""
        metrics = self.calculate_metrics()

        return {
            # Backward compatibility keys
            "Daily Loss": round(self.daily_loss, 2),
            "Cumulative P&L": round(self.total_pnl, 2),
            "Win Streak": self.win_streak,
            "Loss Streak": self.loss_streak,
            "Trades": self.trade_count,
            "Exposure": dict(self.asset_exposure),

            # Enhanced metrics
            "capital": round(self.capital, 2),
            "peak_capital": round(self.peak_capital, 2),
            "current_drawdown": f"{metrics.current_drawdown:.2%}",
            "max_drawdown": f"{metrics.max_drawdown:.2%}",
            "win_rate": f"{metrics.win_rate:.2%}",
            "profit_factor": round(metrics.profit_factor, 2),
            "sharpe_ratio": round(metrics.sharpe_ratio, 2),
            "sortino_ratio": round(metrics.sortino_ratio, 2),
            "var_95": round(metrics.var_95, 4),
            "volatility": f"{self.volatility:.2%}",
            "risk_events": len(self.risk_events),
        }

    def reset_daily(self) -> None:
        """Manual daily reset."""
        self.daily_pnl = 0.0
        self.daily_loss = 0.0
        self.last_reset_date = datetime.now().date()

    def set_position(self, asset: str, position: Position) -> None:
        """Track an open position."""
        self.positions[asset] = position

    def close_position(self, asset: str) -> Optional[Position]:
        """Close and return a position."""
        return self.positions.pop(asset, None)


if __name__ == "__main__":
    # Test the enhanced risk manager
    risk = RiskManager(RiskConfig(
        initial_capital=10000,
        risk_level=RiskLevel.MODERATE
    ))

    # Simulate trades
    trades = [
        ("BTC", 0.85, 150), ("ETH", 0.72, -80), ("BTC", 0.91, 210),
        ("ADA", 0.68, -30), ("ETH", 0.78, -120), ("BTC", 0.82, 180),
    ]

    for asset, conf, pnl in trades:
        if risk.check_risk_limits(asset):
            size = risk.get_position_size(asset, conf)
            risk.update_after_trade(asset, pnl, pnl / size if size > 0 else 0)
            print(f"Trade: {asset}, Size: {size:.2f}, P&L: {pnl:+.2f}")
        else:
            print(f"Trade blocked: {asset}")

    print("\nRisk Summary:")
    for key, value in risk.get_summary().items():
        print(f"  {key}: {value}")
