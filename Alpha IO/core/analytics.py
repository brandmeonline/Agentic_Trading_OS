"""
Performance Analytics Module.

Comprehensive analytics for trading performance including:
- Return analysis
- Risk metrics
- Trade analysis
- Factor attribution
- Benchmark comparison
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
from collections import defaultdict


@dataclass
class ReturnMetrics:
    """Return-based performance metrics."""
    total_return: float = 0.0
    annualized_return: float = 0.0
    cumulative_return: float = 0.0
    daily_returns: List[float] = field(default_factory=list)
    monthly_returns: List[float] = field(default_factory=list)
    best_day: float = 0.0
    worst_day: float = 0.0
    best_month: float = 0.0
    worst_month: float = 0.0
    positive_days: int = 0
    negative_days: int = 0


@dataclass
class RiskMetrics:
    """Risk-based performance metrics."""
    volatility: float = 0.0
    downside_volatility: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 0.0
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    avg_drawdown: float = 0.0
    ulcer_index: float = 0.0


@dataclass
class RatioMetrics:
    """Risk-adjusted return ratios."""
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    omega_ratio: float = 0.0
    information_ratio: float = 0.0
    treynor_ratio: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0


@dataclass
class TradeMetrics:
    """Trade-based performance metrics."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_holding_period: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0


class PerformanceAnalyzer:
    """
    Comprehensive performance analysis engine.

    Calculates all standard performance metrics for trading strategies.
    """

    def __init__(self, risk_free_rate: float = 0.05):
        self.risk_free_rate = risk_free_rate
        self.daily_rf = risk_free_rate / 252

    def analyze_returns(self, equity_curve: List[Tuple[datetime, float]]) -> ReturnMetrics:
        """
        Analyze returns from equity curve.

        Args:
            equity_curve: List of (timestamp, equity) tuples

        Returns:
            ReturnMetrics with all return metrics
        """
        metrics = ReturnMetrics()

        if len(equity_curve) < 2:
            return metrics

        # Extract equity values
        equities = [e[1] for e in equity_curve]

        # Calculate daily returns
        metrics.daily_returns = [
            (equities[i] - equities[i-1]) / equities[i-1]
            for i in range(1, len(equities))
        ]

        if not metrics.daily_returns:
            return metrics

        # Total and annualized returns
        metrics.total_return = (equities[-1] - equities[0]) / equities[0]

        num_years = len(equity_curve) / 252
        if num_years > 0:
            metrics.annualized_return = (1 + metrics.total_return) ** (1 / num_years) - 1

        # Cumulative return
        metrics.cumulative_return = equities[-1] / equities[0] - 1

        # Best/worst days
        metrics.best_day = max(metrics.daily_returns)
        metrics.worst_day = min(metrics.daily_returns)

        # Count positive/negative days
        metrics.positive_days = sum(1 for r in metrics.daily_returns if r > 0)
        metrics.negative_days = sum(1 for r in metrics.daily_returns if r < 0)

        # Monthly returns (approximate - every 21 trading days)
        for i in range(0, len(equities) - 21, 21):
            monthly_ret = (equities[i + 21] - equities[i]) / equities[i]
            metrics.monthly_returns.append(monthly_ret)

        if metrics.monthly_returns:
            metrics.best_month = max(metrics.monthly_returns)
            metrics.worst_month = min(metrics.monthly_returns)

        return metrics

    def analyze_risk(self, returns: List[float]) -> RiskMetrics:
        """
        Analyze risk metrics from returns series.

        Args:
            returns: List of period returns

        Returns:
            RiskMetrics with all risk metrics
        """
        metrics = RiskMetrics()

        if len(returns) < 10:
            return metrics

        returns_arr = np.array(returns)

        # Volatility (annualized)
        metrics.volatility = np.std(returns_arr) * np.sqrt(252)

        # Downside volatility
        downside = returns_arr[returns_arr < 0]
        if len(downside) > 0:
            metrics.downside_volatility = np.std(downside) * np.sqrt(252)

        # Higher moments
        metrics.skewness = float(self._calculate_skewness(returns_arr))
        metrics.kurtosis = float(self._calculate_kurtosis(returns_arr))

        # VaR
        sorted_returns = sorted(returns)
        var_idx_95 = int(len(sorted_returns) * 0.05)
        var_idx_99 = int(len(sorted_returns) * 0.01)

        metrics.var_95 = abs(sorted_returns[var_idx_95]) if var_idx_95 < len(sorted_returns) else 0
        metrics.var_99 = abs(sorted_returns[var_idx_99]) if var_idx_99 < len(sorted_returns) else 0

        # CVaR (Expected Shortfall)
        if var_idx_95 > 0:
            metrics.cvar_95 = abs(np.mean(sorted_returns[:var_idx_95]))

        # Drawdown analysis
        dd_info = self._calculate_drawdowns(returns)
        metrics.max_drawdown = dd_info["max_drawdown"]
        metrics.max_drawdown_duration = dd_info["max_duration"]
        metrics.avg_drawdown = dd_info["avg_drawdown"]

        # Ulcer Index
        if dd_info["drawdowns"]:
            squared_dd = [d ** 2 for d in dd_info["drawdowns"]]
            metrics.ulcer_index = np.sqrt(np.mean(squared_dd))

        return metrics

    def calculate_ratios(
        self,
        returns: List[float],
        benchmark_returns: Optional[List[float]] = None
    ) -> RatioMetrics:
        """
        Calculate risk-adjusted performance ratios.

        Args:
            returns: Strategy returns
            benchmark_returns: Optional benchmark returns for comparison

        Returns:
            RatioMetrics with all ratios
        """
        metrics = RatioMetrics()

        if len(returns) < 20:
            return metrics

        returns_arr = np.array(returns)

        # Annualized stats
        mean_return = np.mean(returns_arr) * 252
        volatility = np.std(returns_arr) * np.sqrt(252)

        # Sharpe ratio
        if volatility > 0:
            metrics.sharpe_ratio = (mean_return - self.risk_free_rate) / volatility

        # Sortino ratio
        downside = returns_arr[returns_arr < 0]
        if len(downside) > 0:
            downside_vol = np.std(downside) * np.sqrt(252)
            if downside_vol > 0:
                metrics.sortino_ratio = (mean_return - self.risk_free_rate) / downside_vol

        # Calmar ratio
        dd_info = self._calculate_drawdowns(returns)
        if dd_info["max_drawdown"] > 0:
            metrics.calmar_ratio = mean_return / dd_info["max_drawdown"]

        # Omega ratio
        threshold = self.daily_rf
        gains = sum(r - threshold for r in returns_arr if r > threshold)
        losses = sum(threshold - r for r in returns_arr if r <= threshold)
        if losses > 0:
            metrics.omega_ratio = gains / losses

        # Benchmark-relative metrics
        if benchmark_returns and len(benchmark_returns) == len(returns):
            bench_arr = np.array(benchmark_returns)

            # Beta and Alpha
            covariance = np.cov(returns_arr, bench_arr)[0, 1]
            bench_variance = np.var(bench_arr)
            if bench_variance > 0:
                metrics.beta = covariance / bench_variance
                metrics.alpha = mean_return - (self.risk_free_rate + metrics.beta * (np.mean(bench_arr) * 252 - self.risk_free_rate))

            # Information ratio
            tracking_error = np.std(returns_arr - bench_arr) * np.sqrt(252)
            if tracking_error > 0:
                excess_return = mean_return - np.mean(bench_arr) * 252
                metrics.information_ratio = excess_return / tracking_error

            # Treynor ratio
            if metrics.beta != 0:
                metrics.treynor_ratio = (mean_return - self.risk_free_rate) / metrics.beta

        return metrics

    def analyze_trades(self, trades: List[Dict[str, Any]]) -> TradeMetrics:
        """
        Analyze trade-level performance.

        Args:
            trades: List of trade dicts with 'pnl', 'holding_period', etc.

        Returns:
            TradeMetrics with all trade metrics
        """
        metrics = TradeMetrics()

        if not trades:
            return metrics

        metrics.total_trades = len(trades)

        # Separate winners and losers
        pnls = [t.get("pnl", 0) for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]

        metrics.winning_trades = len(winners)
        metrics.losing_trades = len(losers)
        metrics.win_rate = len(winners) / len(trades)

        # P&L stats
        metrics.avg_trade = np.mean(pnls)
        if winners:
            metrics.avg_win = np.mean(winners)
            metrics.largest_win = max(winners)
        if losers:
            metrics.avg_loss = abs(np.mean(losers))
            metrics.largest_loss = min(losers)

        # Profit factor
        gross_profit = sum(winners) if winners else 0
        gross_loss = abs(sum(losers)) if losers else 0
        if gross_loss > 0:
            metrics.profit_factor = gross_profit / gross_loss

        # Holding period
        holding_periods = [t.get("holding_period", 0) for t in trades]
        if holding_periods:
            metrics.avg_holding_period = np.mean(holding_periods)

        # Consecutive wins/losses
        metrics.max_consecutive_wins = self._max_consecutive(pnls, positive=True)
        metrics.max_consecutive_losses = self._max_consecutive(pnls, positive=False)

        # Expectancy
        if metrics.win_rate > 0 and metrics.avg_loss > 0:
            metrics.expectancy = (metrics.win_rate * metrics.avg_win) - ((1 - metrics.win_rate) * metrics.avg_loss)

        return metrics

    def _calculate_skewness(self, returns: np.ndarray) -> float:
        """Calculate skewness of returns."""
        n = len(returns)
        if n < 3:
            return 0.0
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 0.0
        return float(np.sum(((returns - mean) / std) ** 3) * n / ((n - 1) * (n - 2)))

    def _calculate_kurtosis(self, returns: np.ndarray) -> float:
        """Calculate excess kurtosis of returns."""
        n = len(returns)
        if n < 4:
            return 0.0
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 0.0
        return float(np.sum(((returns - mean) / std) ** 4) * n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) - 3)

    def _calculate_drawdowns(self, returns: List[float]) -> Dict[str, Any]:
        """Calculate drawdown statistics."""
        if not returns:
            return {"max_drawdown": 0, "max_duration": 0, "avg_drawdown": 0, "drawdowns": []}

        # Calculate cumulative returns
        cumulative = [1.0]
        for r in returns:
            cumulative.append(cumulative[-1] * (1 + r))

        # Calculate peak and drawdown
        peak = cumulative[0]
        drawdowns = []
        current_duration = 0
        max_duration = 0
        in_drawdown = False

        for value in cumulative[1:]:
            if value > peak:
                peak = value
                if in_drawdown:
                    max_duration = max(max_duration, current_duration)
                    current_duration = 0
                    in_drawdown = False
            else:
                dd = (peak - value) / peak
                drawdowns.append(dd)
                in_drawdown = True
                current_duration += 1

        max_duration = max(max_duration, current_duration)

        return {
            "max_drawdown": max(drawdowns) if drawdowns else 0,
            "max_duration": max_duration,
            "avg_drawdown": np.mean(drawdowns) if drawdowns else 0,
            "drawdowns": drawdowns,
        }

    def _max_consecutive(self, pnls: List[float], positive: bool = True) -> int:
        """Calculate maximum consecutive wins or losses."""
        max_streak = 0
        current_streak = 0

        for pnl in pnls:
            if (positive and pnl > 0) or (not positive and pnl < 0):
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        return max_streak

    def generate_report(
        self,
        equity_curve: List[Tuple[datetime, float]],
        trades: List[Dict[str, Any]],
        benchmark_returns: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive performance report.

        Args:
            equity_curve: Equity curve data
            trades: Trade history
            benchmark_returns: Optional benchmark for comparison

        Returns:
            Dict containing all metrics
        """
        # Analyze returns
        return_metrics = self.analyze_returns(equity_curve)

        # Analyze risk
        risk_metrics = self.analyze_risk(return_metrics.daily_returns)

        # Calculate ratios
        ratio_metrics = self.calculate_ratios(
            return_metrics.daily_returns,
            benchmark_returns
        )

        # Analyze trades
        trade_metrics = self.analyze_trades(trades)

        return {
            "returns": {
                "total_return": f"{return_metrics.total_return:.2%}",
                "annualized_return": f"{return_metrics.annualized_return:.2%}",
                "best_day": f"{return_metrics.best_day:.2%}",
                "worst_day": f"{return_metrics.worst_day:.2%}",
                "positive_days": return_metrics.positive_days,
                "negative_days": return_metrics.negative_days,
                "best_month": f"{return_metrics.best_month:.2%}",
                "worst_month": f"{return_metrics.worst_month:.2%}",
            },
            "risk": {
                "volatility": f"{risk_metrics.volatility:.2%}",
                "downside_volatility": f"{risk_metrics.downside_volatility:.2%}",
                "max_drawdown": f"{risk_metrics.max_drawdown:.2%}",
                "max_drawdown_duration": f"{risk_metrics.max_drawdown_duration} periods",
                "var_95": f"{risk_metrics.var_95:.2%}",
                "cvar_95": f"{risk_metrics.cvar_95:.2%}",
                "skewness": f"{risk_metrics.skewness:.2f}",
                "kurtosis": f"{risk_metrics.kurtosis:.2f}",
            },
            "ratios": {
                "sharpe_ratio": f"{ratio_metrics.sharpe_ratio:.2f}",
                "sortino_ratio": f"{ratio_metrics.sortino_ratio:.2f}",
                "calmar_ratio": f"{ratio_metrics.calmar_ratio:.2f}",
                "omega_ratio": f"{ratio_metrics.omega_ratio:.2f}",
                "alpha": f"{ratio_metrics.alpha:.2%}",
                "beta": f"{ratio_metrics.beta:.2f}",
            },
            "trades": {
                "total_trades": trade_metrics.total_trades,
                "win_rate": f"{trade_metrics.win_rate:.2%}",
                "profit_factor": f"{trade_metrics.profit_factor:.2f}",
                "avg_win": f"${trade_metrics.avg_win:.2f}",
                "avg_loss": f"${trade_metrics.avg_loss:.2f}",
                "expectancy": f"${trade_metrics.expectancy:.2f}",
                "max_consecutive_wins": trade_metrics.max_consecutive_wins,
                "max_consecutive_losses": trade_metrics.max_consecutive_losses,
            },
        }


def print_report(report: Dict[str, Any]) -> None:
    """Pretty print performance report."""
    print("\n" + "=" * 60)
    print("PERFORMANCE REPORT")
    print("=" * 60)

    for section, metrics in report.items():
        print(f"\n{section.upper()}")
        print("-" * 40)
        for key, value in metrics.items():
            print(f"  {key.replace('_', ' ').title()}: {value}")


if __name__ == "__main__":
    # Test analytics
    print("Testing Performance Analytics")

    # Generate sample equity curve
    np.random.seed(42)
    n_days = 500
    initial_equity = 10000
    returns = np.random.normal(0.0005, 0.015, n_days)

    equity_curve = []
    equity = initial_equity
    current_date = datetime.now() - timedelta(days=n_days)

    for ret in returns:
        equity *= (1 + ret)
        equity_curve.append((current_date, equity))
        current_date += timedelta(days=1)

    # Generate sample trades
    trades = []
    for i in range(100):
        pnl = np.random.normal(20, 100)
        trades.append({
            "pnl": pnl,
            "holding_period": np.random.randint(1, 20),
        })

    # Generate benchmark
    benchmark_returns = list(np.random.normal(0.0003, 0.012, n_days))

    # Run analysis
    analyzer = PerformanceAnalyzer(risk_free_rate=0.05)
    report = analyzer.generate_report(equity_curve, trades, benchmark_returns)

    print_report(report)
