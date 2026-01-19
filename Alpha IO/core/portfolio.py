"""
Portfolio Optimization Module.

Implements various portfolio optimization strategies including:
- Mean-Variance Optimization (Markowitz)
- Risk Parity
- Maximum Sharpe Ratio
- Minimum Variance
- Black-Litterman
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import numpy as np
from datetime import datetime


class OptimizationMethod(Enum):
    """Portfolio optimization methods."""
    EQUAL_WEIGHT = "equal_weight"
    MEAN_VARIANCE = "mean_variance"
    MIN_VARIANCE = "min_variance"
    MAX_SHARPE = "max_sharpe"
    RISK_PARITY = "risk_parity"
    MAX_DIVERSIFICATION = "max_diversification"


@dataclass
class PortfolioWeights:
    """Portfolio allocation weights."""
    weights: Dict[str, float]
    timestamp: datetime = field(default_factory=datetime.now)
    method: OptimizationMethod = OptimizationMethod.EQUAL_WEIGHT
    expected_return: float = 0.0
    expected_volatility: float = 0.0
    sharpe_ratio: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "weights": self.weights,
            "method": self.method.value,
            "expected_return": f"{self.expected_return:.2%}",
            "expected_volatility": f"{self.expected_volatility:.2%}",
            "sharpe_ratio": f"{self.sharpe_ratio:.2f}",
        }


@dataclass
class OptimizationConfig:
    """Configuration for portfolio optimization."""
    risk_free_rate: float = 0.05  # Annual
    min_weight: float = 0.0  # Minimum asset weight
    max_weight: float = 1.0  # Maximum asset weight (no single asset > 100%)
    target_return: Optional[float] = None  # Target annual return
    target_volatility: Optional[float] = None  # Target annual volatility
    lookback_days: int = 252  # Days for return calculation
    rebalance_threshold: float = 0.05  # Rebalance when drift exceeds 5%


class PortfolioOptimizer:
    """
    Portfolio optimization engine.

    Provides multiple optimization methods for constructing
    optimal portfolios based on historical returns.
    """

    def __init__(self, config: Optional[OptimizationConfig] = None):
        self.config = config or OptimizationConfig()
        self.returns_data: Dict[str, List[float]] = {}
        self.assets: List[str] = []

    def add_returns(self, asset: str, returns: List[float]) -> None:
        """Add historical returns for an asset."""
        self.returns_data[asset] = returns
        if asset not in self.assets:
            self.assets.append(asset)

    def calculate_returns_matrix(self) -> np.ndarray:
        """Calculate returns matrix from stored data."""
        if not self.returns_data:
            return np.array([])

        # Ensure all assets have same length
        min_len = min(len(r) for r in self.returns_data.values())
        returns_matrix = np.array([
            self.returns_data[asset][-min_len:]
            for asset in self.assets
        ])
        return returns_matrix

    def calculate_covariance_matrix(self, returns: np.ndarray) -> np.ndarray:
        """Calculate covariance matrix with shrinkage."""
        if returns.size == 0:
            return np.array([])

        # Sample covariance
        sample_cov = np.cov(returns)

        # Ledoit-Wolf shrinkage
        n_assets = returns.shape[0]
        n_obs = returns.shape[1]

        # Shrinkage target: diagonal matrix with average variance
        avg_var = np.trace(sample_cov) / n_assets
        shrinkage_target = np.eye(n_assets) * avg_var

        # Optimal shrinkage intensity (simplified)
        shrinkage_intensity = min(1.0, max(0.0, (n_assets / n_obs)))

        # Shrunk covariance
        cov_matrix = (1 - shrinkage_intensity) * sample_cov + shrinkage_intensity * shrinkage_target

        return cov_matrix

    def _normalize_weights(self, weights: np.ndarray) -> np.ndarray:
        """Normalize weights to sum to 1 and apply constraints."""
        weights = np.clip(weights, self.config.min_weight, self.config.max_weight)
        total = np.sum(weights)
        if total > 0:
            weights = weights / total
        else:
            weights = np.ones(len(weights)) / len(weights)
        return weights

    def optimize_equal_weight(self) -> PortfolioWeights:
        """Equal weight allocation."""
        n = len(self.assets)
        weights = {asset: 1.0 / n for asset in self.assets}

        returns = self.calculate_returns_matrix()
        if returns.size > 0:
            mean_returns = np.mean(returns, axis=1) * 252
            cov_matrix = self.calculate_covariance_matrix(returns) * 252
            w = np.array([1.0 / n] * n)

            exp_ret = np.dot(w, mean_returns)
            exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
            sharpe = (exp_ret - self.config.risk_free_rate) / exp_vol if exp_vol > 0 else 0
        else:
            exp_ret, exp_vol, sharpe = 0, 0, 0

        return PortfolioWeights(
            weights=weights,
            method=OptimizationMethod.EQUAL_WEIGHT,
            expected_return=exp_ret,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe
        )

    def optimize_min_variance(self) -> PortfolioWeights:
        """Minimum variance portfolio optimization."""
        returns = self.calculate_returns_matrix()
        if returns.size == 0:
            return self.optimize_equal_weight()

        n = len(self.assets)
        cov_matrix = self.calculate_covariance_matrix(returns) * 252
        mean_returns = np.mean(returns, axis=1) * 252

        # Analytical solution for minimum variance
        try:
            cov_inv = np.linalg.inv(cov_matrix)
            ones = np.ones(n)
            w = cov_inv @ ones / (ones.T @ cov_inv @ ones)
            w = self._normalize_weights(w)
        except np.linalg.LinAlgError:
            w = np.ones(n) / n

        exp_ret = np.dot(w, mean_returns)
        exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
        sharpe = (exp_ret - self.config.risk_free_rate) / exp_vol if exp_vol > 0 else 0

        weights = {self.assets[i]: w[i] for i in range(n)}

        return PortfolioWeights(
            weights=weights,
            method=OptimizationMethod.MIN_VARIANCE,
            expected_return=exp_ret,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe
        )

    def optimize_max_sharpe(self) -> PortfolioWeights:
        """Maximum Sharpe ratio portfolio (tangency portfolio)."""
        returns = self.calculate_returns_matrix()
        if returns.size == 0:
            return self.optimize_equal_weight()

        n = len(self.assets)
        cov_matrix = self.calculate_covariance_matrix(returns) * 252
        mean_returns = np.mean(returns, axis=1) * 252

        # Excess returns
        excess_returns = mean_returns - self.config.risk_free_rate

        # Analytical solution
        try:
            cov_inv = np.linalg.inv(cov_matrix)
            w = cov_inv @ excess_returns
            w = self._normalize_weights(w)
        except np.linalg.LinAlgError:
            w = np.ones(n) / n

        exp_ret = np.dot(w, mean_returns)
        exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
        sharpe = (exp_ret - self.config.risk_free_rate) / exp_vol if exp_vol > 0 else 0

        weights = {self.assets[i]: w[i] for i in range(n)}

        return PortfolioWeights(
            weights=weights,
            method=OptimizationMethod.MAX_SHARPE,
            expected_return=exp_ret,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe
        )

    def optimize_risk_parity(self) -> PortfolioWeights:
        """
        Risk parity portfolio where each asset contributes equally to risk.

        Uses iterative algorithm to find weights where
        w_i * (Σw)_i = constant for all i.
        """
        returns = self.calculate_returns_matrix()
        if returns.size == 0:
            return self.optimize_equal_weight()

        n = len(self.assets)
        cov_matrix = self.calculate_covariance_matrix(returns) * 252
        mean_returns = np.mean(returns, axis=1) * 252

        # Initial guess: inverse volatility
        vols = np.sqrt(np.diag(cov_matrix))
        w = 1.0 / vols if np.all(vols > 0) else np.ones(n)
        w = self._normalize_weights(w)

        # Iterative optimization
        for _ in range(100):
            port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
            if port_vol == 0:
                break

            # Marginal risk contribution
            mrc = (cov_matrix @ w) / port_vol

            # Risk contribution
            rc = w * mrc

            # Target: equal risk contribution
            target_rc = port_vol / n

            # Update weights
            w_new = w * (target_rc / rc) ** 0.5
            w_new = self._normalize_weights(w_new)

            # Check convergence
            if np.max(np.abs(w_new - w)) < 1e-6:
                break
            w = w_new

        exp_ret = np.dot(w, mean_returns)
        exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
        sharpe = (exp_ret - self.config.risk_free_rate) / exp_vol if exp_vol > 0 else 0

        weights = {self.assets[i]: w[i] for i in range(n)}

        return PortfolioWeights(
            weights=weights,
            method=OptimizationMethod.RISK_PARITY,
            expected_return=exp_ret,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe
        )

    def optimize_max_diversification(self) -> PortfolioWeights:
        """
        Maximum diversification portfolio.

        Maximizes the diversification ratio: weighted average volatility
        divided by portfolio volatility.
        """
        returns = self.calculate_returns_matrix()
        if returns.size == 0:
            return self.optimize_equal_weight()

        n = len(self.assets)
        cov_matrix = self.calculate_covariance_matrix(returns) * 252
        mean_returns = np.mean(returns, axis=1) * 252
        vols = np.sqrt(np.diag(cov_matrix))

        # Iterative optimization
        w = np.ones(n) / n

        for _ in range(100):
            port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
            if port_vol == 0:
                break

            # Gradient of diversification ratio
            weighted_vol = np.dot(w, vols)
            grad = vols / port_vol - weighted_vol * (cov_matrix @ w) / (port_vol ** 3)

            # Update weights (gradient ascent)
            w_new = w + 0.1 * grad
            w_new = self._normalize_weights(w_new)

            if np.max(np.abs(w_new - w)) < 1e-6:
                break
            w = w_new

        exp_ret = np.dot(w, mean_returns)
        exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
        sharpe = (exp_ret - self.config.risk_free_rate) / exp_vol if exp_vol > 0 else 0

        weights = {self.assets[i]: w[i] for i in range(n)}

        return PortfolioWeights(
            weights=weights,
            method=OptimizationMethod.MAX_DIVERSIFICATION,
            expected_return=exp_ret,
            expected_volatility=exp_vol,
            sharpe_ratio=sharpe
        )

    def optimize(self, method: OptimizationMethod = OptimizationMethod.MAX_SHARPE) -> PortfolioWeights:
        """
        Optimize portfolio using specified method.

        Args:
            method: Optimization method to use

        Returns:
            PortfolioWeights with optimized allocation
        """
        method_map = {
            OptimizationMethod.EQUAL_WEIGHT: self.optimize_equal_weight,
            OptimizationMethod.MIN_VARIANCE: self.optimize_min_variance,
            OptimizationMethod.MAX_SHARPE: self.optimize_max_sharpe,
            OptimizationMethod.RISK_PARITY: self.optimize_risk_parity,
            OptimizationMethod.MAX_DIVERSIFICATION: self.optimize_max_diversification,
        }

        optimizer = method_map.get(method, self.optimize_equal_weight)
        return optimizer()

    def calculate_efficient_frontier(self, n_points: int = 50) -> List[Tuple[float, float, Dict[str, float]]]:
        """
        Calculate efficient frontier points.

        Returns list of (volatility, return, weights) tuples.
        """
        returns = self.calculate_returns_matrix()
        if returns.size == 0:
            return []

        n = len(self.assets)
        cov_matrix = self.calculate_covariance_matrix(returns) * 252
        mean_returns = np.mean(returns, axis=1) * 252

        # Find return range
        min_ret = min(mean_returns)
        max_ret = max(mean_returns)
        target_returns = np.linspace(min_ret, max_ret, n_points)

        frontier = []

        for target in target_returns:
            # Solve for minimum variance portfolio with target return
            try:
                cov_inv = np.linalg.inv(cov_matrix)
                ones = np.ones(n)

                A = ones.T @ cov_inv @ ones
                B = ones.T @ cov_inv @ mean_returns
                C = mean_returns.T @ cov_inv @ mean_returns
                D = A * C - B * B

                if D <= 0:
                    continue

                lambda1 = (C - B * target) / D
                lambda2 = (A * target - B) / D

                w = cov_inv @ (lambda1 * ones + lambda2 * mean_returns)
                w = self._normalize_weights(w)

                exp_ret = np.dot(w, mean_returns)
                exp_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))

                weights = {self.assets[i]: w[i] for i in range(n)}
                frontier.append((exp_vol, exp_ret, weights))

            except np.linalg.LinAlgError:
                continue

        return frontier

    def check_rebalance_needed(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float]
    ) -> Tuple[bool, Dict[str, float]]:
        """
        Check if rebalancing is needed based on drift threshold.

        Returns (needs_rebalance, drift_dict)
        """
        drift = {}
        max_drift = 0

        for asset in target_weights:
            current = current_weights.get(asset, 0)
            target = target_weights[asset]
            asset_drift = abs(current - target)
            drift[asset] = asset_drift
            max_drift = max(max_drift, asset_drift)

        needs_rebalance = max_drift > self.config.rebalance_threshold
        return needs_rebalance, drift


class RebalanceEngine:
    """
    Portfolio rebalancing engine.

    Handles rebalancing decisions and trade generation.
    """

    def __init__(self, optimizer: PortfolioOptimizer):
        self.optimizer = optimizer
        self.current_weights: Dict[str, float] = {}
        self.target_weights: Dict[str, float] = {}
        self.rebalance_history: List[Tuple[datetime, Dict[str, float]]] = []

    def set_current_weights(self, weights: Dict[str, float]) -> None:
        """Set current portfolio weights."""
        self.current_weights = weights

    def calculate_target_weights(self, method: OptimizationMethod) -> PortfolioWeights:
        """Calculate new target weights using specified method."""
        result = self.optimizer.optimize(method)
        self.target_weights = result.weights
        return result

    def get_rebalance_trades(
        self,
        portfolio_value: float,
        prices: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Generate trades needed to rebalance portfolio.

        Args:
            portfolio_value: Total portfolio value
            prices: Current prices for each asset

        Returns:
            List of trade dicts with asset, side, quantity, value
        """
        trades = []

        for asset in self.target_weights:
            target_value = portfolio_value * self.target_weights[asset]
            current_value = portfolio_value * self.current_weights.get(asset, 0)
            diff = target_value - current_value

            if abs(diff) < 1:  # Skip tiny trades
                continue

            price = prices.get(asset, 1)
            quantity = abs(diff) / price

            trades.append({
                "asset": asset,
                "side": "buy" if diff > 0 else "sell",
                "quantity": quantity,
                "value": abs(diff),
                "target_weight": self.target_weights[asset],
                "current_weight": self.current_weights.get(asset, 0),
            })

        # Sort by value (largest first)
        trades.sort(key=lambda x: x["value"], reverse=True)

        return trades

    def record_rebalance(self) -> None:
        """Record rebalancing event."""
        self.rebalance_history.append((datetime.now(), dict(self.target_weights)))
        self.current_weights = dict(self.target_weights)


if __name__ == "__main__":
    # Test portfolio optimization
    print("Testing Portfolio Optimization")
    print("=" * 50)

    # Generate sample returns
    np.random.seed(42)
    n_days = 252

    # Simulated asset returns with different characteristics
    assets_returns = {
        "BTC": np.random.normal(0.001, 0.03, n_days),  # High vol, positive drift
        "ETH": np.random.normal(0.0008, 0.035, n_days),  # Higher vol
        "SOL": np.random.normal(0.0012, 0.04, n_days),  # Highest vol
        "USDC": np.random.normal(0.0001, 0.001, n_days),  # Low vol stable
    }

    # Add some correlation
    assets_returns["ETH"] = 0.7 * assets_returns["BTC"] + 0.3 * assets_returns["ETH"]
    assets_returns["SOL"] = 0.5 * assets_returns["BTC"] + 0.5 * assets_returns["SOL"]

    # Create optimizer
    optimizer = PortfolioOptimizer()
    for asset, returns in assets_returns.items():
        optimizer.add_returns(asset, list(returns))

    # Test all methods
    methods = [
        OptimizationMethod.EQUAL_WEIGHT,
        OptimizationMethod.MIN_VARIANCE,
        OptimizationMethod.MAX_SHARPE,
        OptimizationMethod.RISK_PARITY,
        OptimizationMethod.MAX_DIVERSIFICATION,
    ]

    print("\nOptimization Results:")
    print("-" * 50)

    for method in methods:
        result = optimizer.optimize(method)
        print(f"\n{method.value.upper()}:")
        for asset, weight in result.weights.items():
            print(f"  {asset}: {weight:.1%}")
        print(f"  Expected Return: {result.expected_return:.1%}")
        print(f"  Expected Volatility: {result.expected_volatility:.1%}")
        print(f"  Sharpe Ratio: {result.sharpe_ratio:.2f}")

    # Test efficient frontier
    print("\n\nEfficient Frontier (sample points):")
    print("-" * 50)
    frontier = optimizer.calculate_efficient_frontier(10)
    for vol, ret, _ in frontier[:5]:
        print(f"  Vol: {vol:.1%}, Return: {ret:.1%}")

    # Test rebalancing
    print("\n\nRebalancing Test:")
    print("-" * 50)
    engine = RebalanceEngine(optimizer)
    engine.set_current_weights({"BTC": 0.4, "ETH": 0.3, "SOL": 0.2, "USDC": 0.1})
    target = engine.calculate_target_weights(OptimizationMethod.RISK_PARITY)
    print(f"Target Weights: {target.weights}")

    needs_rebalance, drift = optimizer.check_rebalance_needed(
        engine.current_weights, target.weights
    )
    print(f"Needs Rebalance: {needs_rebalance}")
    print(f"Max Drift: {max(drift.values()):.1%}")

    trades = engine.get_rebalance_trades(
        portfolio_value=10000,
        prices={"BTC": 45000, "ETH": 2500, "SOL": 100, "USDC": 1}
    )
    print("\nRebalance Trades:")
    for trade in trades:
        print(f"  {trade['side'].upper()} {trade['asset']}: ${trade['value']:.2f}")
