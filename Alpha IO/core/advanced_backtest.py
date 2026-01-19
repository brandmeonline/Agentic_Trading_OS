"""
Advanced Backtesting Framework.

Sophisticated backtesting methodologies:
- Walk-forward optimization
- Monte Carlo simulation
- Regime-aware backtesting
- Out-of-sample validation
- Bootstrap confidence intervals
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import copy


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class WalkForwardConfig:
    """Walk-forward optimization configuration."""
    in_sample_ratio: float = 0.7  # Fraction for optimization
    min_in_sample_bars: int = 252  # Minimum bars for optimization
    step_size: int = 63  # Bars to step forward (quarterly)
    num_windows: int = 8  # Number of walk-forward windows
    reoptimize_interval: int = 1  # Windows between reoptimization
    parameter_stability_weight: float = 0.1  # Weight for parameter stability


@dataclass
class MonteCarloConfig:
    """Monte Carlo simulation configuration."""
    n_simulations: int = 1000
    confidence_levels: List[float] = field(default_factory=lambda: [0.90, 0.95, 0.99])
    bootstrap_block_size: int = 20  # Block bootstrap size
    enable_path_dependency: bool = True
    random_seed: Optional[int] = None


@dataclass
class ValidationConfig:
    """Validation configuration."""
    n_splits: int = 5  # Number of time series splits
    embargo_period: int = 5  # Bars between train/test
    purge_period: int = 10  # Bars to purge around test
    min_test_size: int = 50


# =============================================================================
# Walk-Forward Optimizer
# =============================================================================

@dataclass
class OptimizationResult:
    """Result of parameter optimization."""
    parameters: Dict[str, Any]
    objective_value: float
    in_sample_metrics: Dict[str, float]
    timestamp: datetime


@dataclass
class WalkForwardResult:
    """Result of walk-forward analysis."""
    window_results: List[Dict]
    combined_returns: np.ndarray
    combined_metrics: Dict[str, float]
    parameter_history: List[Dict]
    stability_score: float


class WalkForwardOptimizer:
    """
    Walk-forward optimization framework.

    Implements:
    - Rolling window optimization
    - Out-of-sample testing
    - Parameter stability analysis
    - Anchored vs rolling variants
    """

    def __init__(
        self,
        config: WalkForwardConfig,
        strategy_class: Any,
        objective_func: Callable[[np.ndarray], float]
    ):
        self.config = config
        self.strategy_class = strategy_class
        self.objective_func = objective_func

    def run(
        self,
        data: Dict[str, np.ndarray],
        parameter_space: Dict[str, List[Any]]
    ) -> WalkForwardResult:
        """
        Run walk-forward optimization.

        Args:
            data: Dict with 'close', 'high', 'low', 'volume', etc.
            parameter_space: Dict of parameter name -> possible values

        Returns:
            WalkForwardResult with combined analysis
        """
        n_bars = len(data['close'])
        window_size = self.config.min_in_sample_bars + int(
            self.config.min_in_sample_bars * (1 - self.config.in_sample_ratio) / self.config.in_sample_ratio
        )

        window_results = []
        all_oos_returns = []
        parameter_history = []

        current_params = None

        for window_idx in range(self.config.num_windows):
            # Calculate window boundaries
            end_idx = n_bars - (self.config.num_windows - window_idx - 1) * self.config.step_size
            start_idx = max(0, end_idx - window_size)

            if end_idx <= start_idx + self.config.min_in_sample_bars:
                continue

            # Split into in-sample and out-of-sample
            is_end = start_idx + int((end_idx - start_idx) * self.config.in_sample_ratio)
            oos_start = is_end
            oos_end = end_idx

            # Slice data
            is_data = {k: v[start_idx:is_end] for k, v in data.items()}
            oos_data = {k: v[oos_start:oos_end] for k, v in data.items()}

            # Optimize on in-sample (if needed)
            if window_idx % self.config.reoptimize_interval == 0:
                current_params = self._optimize(is_data, parameter_space)
                parameter_history.append({
                    "window": window_idx,
                    "parameters": current_params.parameters.copy(),
                    "is_objective": current_params.objective_value
                })

            # Test on out-of-sample
            oos_returns = self._evaluate(oos_data, current_params.parameters)
            all_oos_returns.extend(oos_returns.tolist())

            window_results.append({
                "window": window_idx,
                "is_start": start_idx,
                "is_end": is_end,
                "oos_start": oos_start,
                "oos_end": oos_end,
                "parameters": current_params.parameters.copy(),
                "is_metrics": current_params.in_sample_metrics,
                "oos_returns": oos_returns,
                "oos_sharpe": self._calculate_sharpe(oos_returns)
            })

        # Combine results
        combined_returns = np.array(all_oos_returns)
        combined_metrics = self._calculate_metrics(combined_returns)

        # Calculate parameter stability
        stability_score = self._calculate_stability(parameter_history)

        return WalkForwardResult(
            window_results=window_results,
            combined_returns=combined_returns,
            combined_metrics=combined_metrics,
            parameter_history=parameter_history,
            stability_score=stability_score
        )

    def _optimize(
        self,
        data: Dict[str, np.ndarray],
        parameter_space: Dict[str, List[Any]]
    ) -> OptimizationResult:
        """Optimize parameters on in-sample data."""
        best_params = None
        best_objective = float('-inf')
        best_returns = None

        # Grid search (could be replaced with more sophisticated optimization)
        param_combinations = self._generate_combinations(parameter_space)

        for params in param_combinations:
            returns = self._evaluate(data, params)
            objective = self.objective_func(returns)

            # Add stability penalty if we have previous params
            if best_params is not None:
                stability_penalty = self._param_distance(params, best_params)
                objective -= self.config.parameter_stability_weight * stability_penalty

            if objective > best_objective:
                best_objective = objective
                best_params = params
                best_returns = returns

        metrics = self._calculate_metrics(best_returns) if best_returns is not None else {}

        return OptimizationResult(
            parameters=best_params or {},
            objective_value=best_objective,
            in_sample_metrics=metrics,
            timestamp=datetime.now()
        )

    def _evaluate(self, data: Dict[str, np.ndarray], params: Dict[str, Any]) -> np.ndarray:
        """Evaluate strategy with given parameters."""
        # Create strategy instance with parameters
        # This is a simplified implementation
        close = data['close']
        returns = np.diff(close) / close[:-1]

        # Apply simple momentum strategy based on params
        lookback = params.get('lookback', 20)
        threshold = params.get('threshold', 0.0)

        signals = np.zeros(len(returns))
        for i in range(lookback, len(returns)):
            momentum = (close[i] - close[i - lookback]) / close[i - lookback]
            if momentum > threshold:
                signals[i] = 1
            elif momentum < -threshold:
                signals[i] = -1

        # Strategy returns
        strategy_returns = signals[:-1] * returns[lookback:]

        return strategy_returns

    def _generate_combinations(self, parameter_space: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        """Generate all parameter combinations."""
        from itertools import product

        keys = list(parameter_space.keys())
        values = [parameter_space[k] for k in keys]

        combinations = []
        for combo in product(*values):
            combinations.append(dict(zip(keys, combo)))

        return combinations

    def _param_distance(self, params1: Dict, params2: Dict) -> float:
        """Calculate distance between parameter sets."""
        distance = 0.0
        for key in params1:
            if key in params2:
                v1 = params1[key]
                v2 = params2[key]
                if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                    distance += abs(v1 - v2) / (abs(v2) + 1e-10)
        return distance

    def _calculate_sharpe(self, returns: np.ndarray) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        return np.mean(returns) / np.std(returns) * np.sqrt(252)

    def _calculate_metrics(self, returns: np.ndarray) -> Dict[str, float]:
        """Calculate performance metrics."""
        if len(returns) == 0:
            return {}

        cumulative = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak

        return {
            "total_return": cumulative[-1] - 1 if len(cumulative) > 0 else 0,
            "annualized_return": (cumulative[-1] ** (252 / len(returns)) - 1) if len(returns) > 0 else 0,
            "sharpe_ratio": self._calculate_sharpe(returns),
            "max_drawdown": np.max(drawdown),
            "volatility": np.std(returns) * np.sqrt(252),
            "win_rate": np.mean(returns > 0),
            "n_trades": len(returns)
        }

    def _calculate_stability(self, parameter_history: List[Dict]) -> float:
        """Calculate parameter stability score."""
        if len(parameter_history) < 2:
            return 1.0

        distances = []
        for i in range(1, len(parameter_history)):
            d = self._param_distance(
                parameter_history[i]["parameters"],
                parameter_history[i-1]["parameters"]
            )
            distances.append(d)

        # Lower distance = higher stability
        avg_distance = np.mean(distances)
        stability = 1.0 / (1.0 + avg_distance)

        return stability


# =============================================================================
# Monte Carlo Simulator
# =============================================================================

@dataclass
class MonteCarloResult:
    """Result of Monte Carlo simulation."""
    simulated_paths: np.ndarray  # Shape: (n_simulations, n_periods)
    terminal_values: np.ndarray
    statistics: Dict[str, float]
    var_estimates: Dict[float, float]  # confidence_level -> VaR
    cvar_estimates: Dict[float, float]  # confidence_level -> CVaR
    confidence_intervals: Dict[str, Tuple[float, float]]


class MonteCarloSimulator:
    """
    Monte Carlo simulation for trading strategies.

    Implements:
    - Bootstrap simulation
    - Geometric Brownian Motion
    - Path-dependent scenarios
    - Risk estimation
    """

    def __init__(self, config: MonteCarloConfig):
        self.config = config
        if config.random_seed is not None:
            np.random.seed(config.random_seed)

    def simulate_returns(
        self,
        historical_returns: np.ndarray,
        n_periods: int
    ) -> MonteCarloResult:
        """
        Simulate future return paths using bootstrap.

        Args:
            historical_returns: Array of historical returns
            n_periods: Number of periods to simulate

        Returns:
            MonteCarloResult with simulated paths and statistics
        """
        n_sims = self.config.n_simulations
        block_size = self.config.bootstrap_block_size

        # Block bootstrap
        simulated_paths = np.zeros((n_sims, n_periods))

        for sim in range(n_sims):
            path = []
            while len(path) < n_periods:
                # Random start index
                start_idx = np.random.randint(0, len(historical_returns) - block_size)
                block = historical_returns[start_idx:start_idx + block_size]
                path.extend(block.tolist())

            simulated_paths[sim, :] = path[:n_periods]

        # Calculate terminal values (cumulative returns)
        cumulative_paths = np.cumprod(1 + simulated_paths, axis=1)
        terminal_values = cumulative_paths[:, -1]

        # Statistics
        statistics = {
            "mean_terminal": np.mean(terminal_values),
            "std_terminal": np.std(terminal_values),
            "median_terminal": np.median(terminal_values),
            "min_terminal": np.min(terminal_values),
            "max_terminal": np.max(terminal_values),
            "prob_profit": np.mean(terminal_values > 1.0),
            "prob_loss_10pct": np.mean(terminal_values < 0.9),
            "prob_loss_20pct": np.mean(terminal_values < 0.8),
            "expected_return": np.mean(terminal_values) - 1,
            "median_return": np.median(terminal_values) - 1
        }

        # VaR and CVaR estimates
        var_estimates = {}
        cvar_estimates = {}
        returns = terminal_values - 1

        for conf in self.config.confidence_levels:
            var = np.percentile(-returns, conf * 100)
            var_estimates[conf] = var

            # CVaR (Expected Shortfall)
            tail_returns = returns[returns <= -var]
            cvar = -np.mean(tail_returns) if len(tail_returns) > 0 else var
            cvar_estimates[conf] = cvar

        # Confidence intervals
        confidence_intervals = {
            "return": (np.percentile(returns, 2.5), np.percentile(returns, 97.5)),
            "terminal_value": (np.percentile(terminal_values, 2.5), np.percentile(terminal_values, 97.5)),
            "max_drawdown": self._calculate_drawdown_distribution(cumulative_paths)
        }

        return MonteCarloResult(
            simulated_paths=simulated_paths,
            terminal_values=terminal_values,
            statistics=statistics,
            var_estimates=var_estimates,
            cvar_estimates=cvar_estimates,
            confidence_intervals=confidence_intervals
        )

    def simulate_gbm(
        self,
        initial_value: float,
        mu: float,
        sigma: float,
        n_periods: int,
        dt: float = 1/252
    ) -> MonteCarloResult:
        """
        Simulate using Geometric Brownian Motion.

        Args:
            initial_value: Starting value
            mu: Drift (annualized)
            sigma: Volatility (annualized)
            n_periods: Number of periods
            dt: Time step (default: 1 day)
        """
        n_sims = self.config.n_simulations

        # Generate random increments
        z = np.random.standard_normal((n_sims, n_periods))

        # GBM formula
        drift = (mu - 0.5 * sigma**2) * dt
        diffusion = sigma * np.sqrt(dt)

        log_returns = drift + diffusion * z
        simulated_paths = initial_value * np.cumprod(np.exp(log_returns), axis=1)

        terminal_values = simulated_paths[:, -1]
        returns = (terminal_values - initial_value) / initial_value

        statistics = {
            "mean_terminal": np.mean(terminal_values),
            "std_terminal": np.std(terminal_values),
            "median_terminal": np.median(terminal_values),
            "prob_profit": np.mean(terminal_values > initial_value),
            "expected_return": np.mean(returns),
            "theoretical_mean": initial_value * np.exp(mu * n_periods * dt),
            "theoretical_std": initial_value * np.exp(mu * n_periods * dt) * np.sqrt(
                np.exp(sigma**2 * n_periods * dt) - 1
            )
        }

        var_estimates = {}
        cvar_estimates = {}
        for conf in self.config.confidence_levels:
            var = np.percentile(-returns, conf * 100)
            var_estimates[conf] = var

            tail = returns[returns <= -var]
            cvar = -np.mean(tail) if len(tail) > 0 else var
            cvar_estimates[conf] = cvar

        confidence_intervals = {
            "return": (np.percentile(returns, 2.5), np.percentile(returns, 97.5)),
            "terminal_value": (np.percentile(terminal_values, 2.5), np.percentile(terminal_values, 97.5))
        }

        return MonteCarloResult(
            simulated_paths=log_returns,
            terminal_values=terminal_values,
            statistics=statistics,
            var_estimates=var_estimates,
            cvar_estimates=cvar_estimates,
            confidence_intervals=confidence_intervals
        )

    def simulate_strategy(
        self,
        strategy_func: Callable[[np.ndarray], np.ndarray],
        price_paths: np.ndarray
    ) -> MonteCarloResult:
        """
        Simulate strategy over multiple price paths.

        Args:
            strategy_func: Function that takes prices and returns positions
            price_paths: Shape (n_simulations, n_periods)
        """
        n_sims, n_periods = price_paths.shape
        strategy_returns = np.zeros((n_sims, n_periods - 1))

        for sim in range(n_sims):
            prices = price_paths[sim, :]
            positions = strategy_func(prices)

            # Calculate returns
            price_returns = np.diff(prices) / prices[:-1]
            strategy_returns[sim, :] = positions[:-1] * price_returns

        # Calculate terminal values
        terminal_values = np.prod(1 + strategy_returns, axis=1)

        statistics = {
            "mean_terminal": np.mean(terminal_values),
            "std_terminal": np.std(terminal_values),
            "sharpe_ratio": np.mean(strategy_returns) / np.std(strategy_returns) * np.sqrt(252),
            "prob_profit": np.mean(terminal_values > 1.0)
        }

        var_estimates = {}
        cvar_estimates = {}
        returns = terminal_values - 1

        for conf in self.config.confidence_levels:
            var = np.percentile(-returns, conf * 100)
            var_estimates[conf] = var
            tail = returns[returns <= -var]
            cvar = -np.mean(tail) if len(tail) > 0 else var
            cvar_estimates[conf] = cvar

        return MonteCarloResult(
            simulated_paths=strategy_returns,
            terminal_values=terminal_values,
            statistics=statistics,
            var_estimates=var_estimates,
            cvar_estimates=cvar_estimates,
            confidence_intervals={}
        )

    def _calculate_drawdown_distribution(
        self,
        cumulative_paths: np.ndarray
    ) -> Tuple[float, float]:
        """Calculate drawdown distribution across paths."""
        max_drawdowns = []

        for path in cumulative_paths:
            peak = np.maximum.accumulate(path)
            drawdown = (peak - path) / peak
            max_drawdowns.append(np.max(drawdown))

        return (np.percentile(max_drawdowns, 2.5), np.percentile(max_drawdowns, 97.5))


# =============================================================================
# Regime-Aware Backtest
# =============================================================================

class MarketRegime(Enum):
    """Market regime types."""
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"


@dataclass
class RegimeBacktestResult:
    """Result of regime-aware backtest."""
    overall_metrics: Dict[str, float]
    regime_metrics: Dict[MarketRegime, Dict[str, float]]
    regime_labels: np.ndarray
    regime_transitions: List[Tuple[int, MarketRegime, MarketRegime]]


class RegimeAwareBacktest:
    """
    Backtest with market regime awareness.

    Analyzes strategy performance across:
    - Different market conditions
    - Regime transitions
    - Adaptive behavior
    """

    def __init__(self, lookback: int = 50, vol_threshold: float = 0.02):
        self.lookback = lookback
        self.vol_threshold = vol_threshold

    def identify_regimes(self, prices: np.ndarray) -> np.ndarray:
        """Identify market regimes from price data."""
        n = len(prices)
        regimes = np.full(n, MarketRegime.SIDEWAYS)

        returns = np.diff(prices) / prices[:-1]

        for i in range(self.lookback, n):
            window_returns = returns[i-self.lookback:i]

            mean_return = np.mean(window_returns)
            vol = np.std(window_returns)

            # Classify regime
            if vol > self.vol_threshold:
                regimes[i] = MarketRegime.HIGH_VOL
            elif vol < self.vol_threshold / 2:
                regimes[i] = MarketRegime.LOW_VOL
            elif mean_return > 0.0005:  # ~12% annual
                regimes[i] = MarketRegime.BULL
            elif mean_return < -0.0005:
                regimes[i] = MarketRegime.BEAR
            else:
                regimes[i] = MarketRegime.SIDEWAYS

        return regimes

    def run_backtest(
        self,
        prices: np.ndarray,
        strategy_func: Callable[[np.ndarray, MarketRegime], np.ndarray]
    ) -> RegimeBacktestResult:
        """
        Run regime-aware backtest.

        Args:
            prices: Price array
            strategy_func: Strategy that takes prices and current regime
        """
        # Identify regimes
        regimes = self.identify_regimes(prices)

        # Get strategy positions (regime-aware)
        positions = np.zeros(len(prices))
        for i in range(self.lookback, len(prices)):
            positions[i] = strategy_func(prices[:i+1], regimes[i])

        # Calculate returns
        price_returns = np.diff(prices) / prices[:-1]
        strategy_returns = positions[:-1] * price_returns

        # Overall metrics
        overall_metrics = self._calculate_metrics(strategy_returns)

        # Regime-specific metrics
        regime_metrics = {}
        for regime in MarketRegime:
            mask = regimes[:-1] == regime
            if np.sum(mask) > 10:
                regime_returns = strategy_returns[mask]
                regime_metrics[regime] = self._calculate_metrics(regime_returns)

        # Find regime transitions
        transitions = []
        for i in range(1, len(regimes)):
            if regimes[i] != regimes[i-1]:
                transitions.append((i, regimes[i-1], regimes[i]))

        return RegimeBacktestResult(
            overall_metrics=overall_metrics,
            regime_metrics=regime_metrics,
            regime_labels=regimes,
            regime_transitions=transitions
        )

    def _calculate_metrics(self, returns: np.ndarray) -> Dict[str, float]:
        """Calculate performance metrics."""
        if len(returns) == 0:
            return {}

        cumulative = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak

        return {
            "total_return": cumulative[-1] - 1,
            "sharpe_ratio": np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0,
            "max_drawdown": np.max(drawdown),
            "volatility": np.std(returns) * np.sqrt(252),
            "win_rate": np.mean(returns > 0),
            "n_periods": len(returns)
        }


# =============================================================================
# Out-of-Sample Validator
# =============================================================================

@dataclass
class ValidationResult:
    """Cross-validation result."""
    fold_results: List[Dict]
    aggregated_metrics: Dict[str, float]
    overfitting_score: float  # In-sample vs out-of-sample performance gap


class OutOfSampleValidator:
    """
    Out-of-sample validation using time series cross-validation.

    Implements:
    - Purged time series CV
    - Embargo periods
    - Multiple validation schemes
    """

    def __init__(self, config: ValidationConfig):
        self.config = config

    def time_series_split(
        self,
        n_samples: int
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """Generate time series train/test splits with embargo."""
        splits = []
        test_size = n_samples // (self.config.n_splits + 1)

        for i in range(self.config.n_splits):
            test_start = (i + 1) * test_size
            test_end = min(test_start + test_size, n_samples)

            # Training: everything before test (minus embargo)
            train_end = test_start - self.config.embargo_period - self.config.purge_period
            train_indices = np.arange(0, train_end)

            # Test indices
            test_indices = np.arange(test_start, test_end)

            if len(train_indices) > 0 and len(test_indices) >= self.config.min_test_size:
                splits.append((train_indices, test_indices))

        return splits

    def combinatorial_purged_cv(
        self,
        n_samples: int,
        n_test_splits: int = 2
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Combinatorial purged cross-validation.

        Creates multiple non-overlapping test sets.
        """
        from itertools import combinations

        # Divide into groups
        n_groups = self.config.n_splits + n_test_splits
        group_size = n_samples // n_groups

        groups = []
        for i in range(n_groups):
            start = i * group_size
            end = start + group_size if i < n_groups - 1 else n_samples
            groups.append(np.arange(start, end))

        # Generate all combinations of test groups
        splits = []
        for test_groups in combinations(range(n_groups), n_test_splits):
            test_indices = np.concatenate([groups[g] for g in test_groups])

            # Train on remaining groups (with purging)
            train_indices = []
            for i, group in enumerate(groups):
                if i not in test_groups:
                    # Check if adjacent to test
                    is_adjacent = any(abs(i - t) == 1 for t in test_groups)
                    if is_adjacent:
                        # Purge edges
                        purge_size = self.config.purge_period
                        group = group[purge_size:-purge_size] if len(group) > 2 * purge_size else np.array([])

                    if len(group) > 0:
                        train_indices.append(group)

            if train_indices:
                train_indices = np.concatenate(train_indices)
                if len(train_indices) > 0 and len(test_indices) >= self.config.min_test_size:
                    splits.append((train_indices, test_indices))

        return splits

    def validate(
        self,
        data: Dict[str, np.ndarray],
        fit_func: Callable[[Dict], Any],
        predict_func: Callable[[Any, Dict], np.ndarray],
        metric_func: Callable[[np.ndarray, np.ndarray], float]
    ) -> ValidationResult:
        """
        Run cross-validation.

        Args:
            data: Input data
            fit_func: Function to fit model on training data
            predict_func: Function to generate predictions
            metric_func: Function to calculate metric

        Returns:
            ValidationResult with fold results and aggregated metrics
        """
        n_samples = len(data['close'])
        splits = self.time_series_split(n_samples)

        fold_results = []
        is_scores = []
        oos_scores = []

        for fold_idx, (train_idx, test_idx) in enumerate(splits):
            # Split data
            train_data = {k: v[train_idx] for k, v in data.items()}
            test_data = {k: v[test_idx] for k, v in data.items()}

            # Fit model
            model = fit_func(train_data)

            # In-sample prediction
            is_pred = predict_func(model, train_data)
            is_actual = train_data['close']
            is_score = metric_func(is_actual, is_pred) if len(is_pred) > 0 else 0

            # Out-of-sample prediction
            oos_pred = predict_func(model, test_data)
            oos_actual = test_data['close']
            oos_score = metric_func(oos_actual, oos_pred) if len(oos_pred) > 0 else 0

            fold_results.append({
                "fold": fold_idx,
                "train_size": len(train_idx),
                "test_size": len(test_idx),
                "in_sample_score": is_score,
                "out_of_sample_score": oos_score
            })

            is_scores.append(is_score)
            oos_scores.append(oos_score)

        # Aggregated metrics
        aggregated_metrics = {
            "mean_is_score": np.mean(is_scores),
            "std_is_score": np.std(is_scores),
            "mean_oos_score": np.mean(oos_scores),
            "std_oos_score": np.std(oos_scores),
            "n_folds": len(fold_results)
        }

        # Overfitting score (gap between IS and OOS)
        is_mean = np.mean(is_scores) if is_scores else 0
        oos_mean = np.mean(oos_scores) if oos_scores else 0
        overfitting_score = (is_mean - oos_mean) / (abs(is_mean) + 1e-10) if is_mean != 0 else 0

        return ValidationResult(
            fold_results=fold_results,
            aggregated_metrics=aggregated_metrics,
            overfitting_score=overfitting_score
        )


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    np.random.seed(42)

    # Generate sample price data
    n_bars = 1000
    returns = np.random.randn(n_bars) * 0.02
    prices = 100 * np.cumprod(1 + returns)

    data = {
        'close': prices,
        'high': prices * (1 + np.abs(np.random.randn(n_bars) * 0.01)),
        'low': prices * (1 - np.abs(np.random.randn(n_bars) * 0.01)),
        'volume': np.abs(np.random.randn(n_bars) * 1e6)
    }

    # =========================================================================
    # Walk-Forward Optimization
    # =========================================================================
    print("=" * 60)
    print("Walk-Forward Optimization")
    print("=" * 60)

    def sharpe_objective(returns):
        if len(returns) == 0 or np.std(returns) == 0:
            return -999
        return np.mean(returns) / np.std(returns) * np.sqrt(252)

    wf_config = WalkForwardConfig(
        in_sample_ratio=0.7,
        min_in_sample_bars=200,
        step_size=50,
        num_windows=5
    )

    wf_optimizer = WalkForwardOptimizer(
        config=wf_config,
        strategy_class=None,
        objective_func=sharpe_objective
    )

    parameter_space = {
        'lookback': [10, 20, 50],
        'threshold': [0.0, 0.01, 0.02]
    }

    wf_result = wf_optimizer.run(data, parameter_space)

    print(f"\nCombined Metrics:")
    for k, v in wf_result.combined_metrics.items():
        print(f"  {k}: {v:.4f}")

    print(f"\nParameter Stability Score: {wf_result.stability_score:.4f}")

    # =========================================================================
    # Monte Carlo Simulation
    # =========================================================================
    print("\n" + "=" * 60)
    print("Monte Carlo Simulation")
    print("=" * 60)

    mc_config = MonteCarloConfig(
        n_simulations=1000,
        confidence_levels=[0.90, 0.95, 0.99],
        random_seed=42
    )

    mc_simulator = MonteCarloSimulator(mc_config)

    # Bootstrap simulation
    mc_result = mc_simulator.simulate_returns(returns, n_periods=252)

    print(f"\nSimulation Statistics:")
    for k, v in mc_result.statistics.items():
        print(f"  {k}: {v:.4f}")

    print(f"\nVaR Estimates:")
    for conf, var in mc_result.var_estimates.items():
        print(f"  {conf*100:.0f}% VaR: {var:.4f}")

    print(f"\nCVaR Estimates:")
    for conf, cvar in mc_result.cvar_estimates.items():
        print(f"  {conf*100:.0f}% CVaR: {cvar:.4f}")

    # =========================================================================
    # Regime-Aware Backtest
    # =========================================================================
    print("\n" + "=" * 60)
    print("Regime-Aware Backtest")
    print("=" * 60)

    regime_backtest = RegimeAwareBacktest(lookback=50)

    def regime_strategy(prices, regime):
        """Simple regime-aware strategy."""
        if len(prices) < 20:
            return 0

        momentum = (prices[-1] - prices[-20]) / prices[-20]

        if regime == MarketRegime.BULL:
            return 1 if momentum > -0.05 else 0
        elif regime == MarketRegime.BEAR:
            return -1 if momentum < 0.05 else 0
        elif regime == MarketRegime.HIGH_VOL:
            return 0  # Stay flat in high vol
        else:
            return 1 if momentum > 0 else -1

    regime_result = regime_backtest.run_backtest(prices, regime_strategy)

    print(f"\nOverall Metrics:")
    for k, v in regime_result.overall_metrics.items():
        print(f"  {k}: {v:.4f}")

    print(f"\nRegime-Specific Performance:")
    for regime, metrics in regime_result.regime_metrics.items():
        print(f"\n  {regime.value}:")
        for k, v in metrics.items():
            print(f"    {k}: {v:.4f}")

    print(f"\nRegime Transitions: {len(regime_result.regime_transitions)}")
