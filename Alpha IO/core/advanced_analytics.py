"""
Agentic Trading OS - Advanced Analytics.

Next-generation analytics with:
- Monte Carlo simulations
- Modern Portfolio Theory optimization
- Factor analysis
- Risk decomposition (VaR, CVaR, Sortino)
- Correlation analysis
- Scenario analysis
- Machine learning predictions
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum
import threading


class RiskMetricType(Enum):
    """Types of risk metrics."""
    VAR = "var"  # Value at Risk
    CVAR = "cvar"  # Conditional VaR (Expected Shortfall)
    VOLATILITY = "volatility"
    SHARPE = "sharpe"
    SORTINO = "sortino"
    MAX_DRAWDOWN = "max_drawdown"
    BETA = "beta"
    CALMAR = "calmar"


@dataclass
class MonteCarloResult:
    """Monte Carlo simulation result."""
    simulations: int
    time_horizon_days: int
    initial_value: float
    mean_final_value: float
    median_final_value: float
    std_dev: float
    percentile_5: float
    percentile_25: float
    percentile_75: float
    percentile_95: float
    prob_profit: float
    prob_loss_10pct: float
    prob_loss_20pct: float
    max_gain: float
    max_loss: float
    paths: List[List[float]] = field(default_factory=list)  # Sample paths for visualization
    histogram_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioOptimization:
    """Portfolio optimization result."""
    weights: Dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float
    efficient_frontier: List[Dict[str, float]] = field(default_factory=list)
    max_sharpe_weights: Dict[str, float] = field(default_factory=dict)
    min_vol_weights: Dict[str, float] = field(default_factory=dict)
    constraints_applied: List[str] = field(default_factory=list)


@dataclass
class RiskDecomposition:
    """Risk decomposition result."""
    total_var: float
    component_var: Dict[str, float]
    marginal_var: Dict[str, float]
    incremental_var: Dict[str, float]
    diversification_benefit: float
    concentration_index: float


@dataclass
class FactorExposure:
    """Factor exposure analysis."""
    factor_loadings: Dict[str, float]
    factor_returns: Dict[str, float]
    specific_return: float
    r_squared: float
    tracking_error: float


@dataclass
class ScenarioResult:
    """Scenario analysis result."""
    scenario_name: str
    portfolio_impact: float
    portfolio_impact_pct: float
    asset_impacts: Dict[str, float]
    probability: float
    description: str


class MonteCarloSimulator:
    """Monte Carlo simulation engine."""

    def __init__(self, seed: int = None):
        if seed:
            random.seed(seed)

    def simulate_portfolio(
        self,
        initial_value: float,
        expected_return: float,
        volatility: float,
        time_horizon_days: int = 252,
        num_simulations: int = 10000,
        confidence_levels: List[float] = None
    ) -> MonteCarloResult:
        """Run Monte Carlo simulation on portfolio value."""
        if confidence_levels is None:
            confidence_levels = [0.05, 0.25, 0.50, 0.75, 0.95]

        # Daily parameters
        daily_return = expected_return / 252
        daily_vol = volatility / math.sqrt(252)

        final_values = []
        sample_paths = []

        for i in range(num_simulations):
            path = [initial_value]
            value = initial_value

            for _ in range(time_horizon_days):
                # Geometric Brownian Motion
                z = random.gauss(0, 1)
                daily_change = daily_return + daily_vol * z
                value = value * (1 + daily_change)
                path.append(value)

            final_values.append(value)

            # Keep some sample paths for visualization
            if i < 100:
                sample_paths.append(path)

        # Sort for percentile calculations
        final_values.sort()

        # Calculate statistics
        mean_val = sum(final_values) / len(final_values)
        median_val = final_values[len(final_values) // 2]
        variance = sum((v - mean_val) ** 2 for v in final_values) / len(final_values)
        std_dev = math.sqrt(variance)

        # Percentiles
        def percentile(data, p):
            idx = int(len(data) * p)
            return data[min(idx, len(data) - 1)]

        p5 = percentile(final_values, 0.05)
        p25 = percentile(final_values, 0.25)
        p75 = percentile(final_values, 0.75)
        p95 = percentile(final_values, 0.95)

        # Probabilities
        prob_profit = sum(1 for v in final_values if v > initial_value) / len(final_values)
        prob_loss_10 = sum(1 for v in final_values if v < initial_value * 0.9) / len(final_values)
        prob_loss_20 = sum(1 for v in final_values if v < initial_value * 0.8) / len(final_values)

        # Histogram data
        bins = 50
        min_val, max_val = min(final_values), max(final_values)
        bin_width = (max_val - min_val) / bins
        histogram = {}
        for i in range(bins):
            bin_start = min_val + i * bin_width
            bin_end = bin_start + bin_width
            count = sum(1 for v in final_values if bin_start <= v < bin_end)
            histogram[f"{bin_start:.0f}-{bin_end:.0f}"] = count

        return MonteCarloResult(
            simulations=num_simulations,
            time_horizon_days=time_horizon_days,
            initial_value=initial_value,
            mean_final_value=round(mean_val, 2),
            median_final_value=round(median_val, 2),
            std_dev=round(std_dev, 2),
            percentile_5=round(p5, 2),
            percentile_25=round(p25, 2),
            percentile_75=round(p75, 2),
            percentile_95=round(p95, 2),
            prob_profit=round(prob_profit * 100, 1),
            prob_loss_10pct=round(prob_loss_10 * 100, 1),
            prob_loss_20pct=round(prob_loss_20 * 100, 1),
            max_gain=round(max(final_values) - initial_value, 2),
            max_loss=round(min(final_values) - initial_value, 2),
            paths=sample_paths[:10],  # Only keep 10 paths for visualization
            histogram_data=histogram
        )

    def simulate_drawdown(
        self,
        initial_value: float,
        expected_return: float,
        volatility: float,
        time_horizon_days: int = 252,
        num_simulations: int = 5000
    ) -> Dict:
        """Simulate max drawdown distribution."""
        max_drawdowns = []

        daily_return = expected_return / 252
        daily_vol = volatility / math.sqrt(252)

        for _ in range(num_simulations):
            value = initial_value
            peak = initial_value
            max_dd = 0

            for _ in range(time_horizon_days):
                z = random.gauss(0, 1)
                value = value * (1 + daily_return + daily_vol * z)

                if value > peak:
                    peak = value

                dd = (peak - value) / peak
                if dd > max_dd:
                    max_dd = dd

            max_drawdowns.append(max_dd)

        max_drawdowns.sort()

        return {
            "mean_max_drawdown": round(sum(max_drawdowns) / len(max_drawdowns) * 100, 2),
            "median_max_drawdown": round(max_drawdowns[len(max_drawdowns) // 2] * 100, 2),
            "percentile_95_drawdown": round(max_drawdowns[int(len(max_drawdowns) * 0.95)] * 100, 2),
            "worst_drawdown": round(max(max_drawdowns) * 100, 2),
            "prob_drawdown_10pct": round(sum(1 for d in max_drawdowns if d > 0.10) / len(max_drawdowns) * 100, 1),
            "prob_drawdown_20pct": round(sum(1 for d in max_drawdowns if d > 0.20) / len(max_drawdowns) * 100, 1)
        }


class PortfolioOptimizer:
    """Modern Portfolio Theory optimization."""

    def __init__(self):
        self._cache = {}

    def calculate_efficient_frontier(
        self,
        assets: List[str],
        expected_returns: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]],
        risk_free_rate: float = 0.05,
        num_points: int = 50
    ) -> List[Dict]:
        """Calculate the efficient frontier."""
        frontier = []

        # Find min and max expected returns
        min_return = min(expected_returns.values())
        max_return = max(expected_returns.values())

        for i in range(num_points):
            target_return = min_return + (max_return - min_return) * i / (num_points - 1)

            # Simplified optimization (in production, use scipy.optimize)
            weights = self._optimize_for_return(
                assets, expected_returns, covariance_matrix, target_return
            )

            if weights:
                port_return = sum(weights[a] * expected_returns[a] for a in assets)
                port_vol = self._calculate_portfolio_volatility(weights, covariance_matrix)
                sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 0 else 0

                frontier.append({
                    "expected_return": round(port_return * 100, 2),
                    "volatility": round(port_vol * 100, 2),
                    "sharpe_ratio": round(sharpe, 2),
                    "weights": {k: round(v * 100, 1) for k, v in weights.items()}
                })

        return frontier

    def optimize_portfolio(
        self,
        assets: List[str],
        expected_returns: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]],
        risk_free_rate: float = 0.05,
        target_return: float = None,
        max_weight: float = 0.4,
        min_weight: float = 0.0
    ) -> PortfolioOptimization:
        """Optimize portfolio weights."""
        # Find max Sharpe ratio portfolio
        best_sharpe = -float('inf')
        best_weights = {}

        # Grid search (simplified - use scipy in production)
        for _ in range(1000):
            weights = self._generate_random_weights(assets, min_weight, max_weight)
            port_return = sum(weights[a] * expected_returns[a] for a in assets)
            port_vol = self._calculate_portfolio_volatility(weights, covariance_matrix)
            sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 0 else 0

            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_weights = weights.copy()

        # Calculate final metrics
        final_return = sum(best_weights[a] * expected_returns[a] for a in assets)
        final_vol = self._calculate_portfolio_volatility(best_weights, covariance_matrix)

        # Efficient frontier
        frontier = self.calculate_efficient_frontier(
            assets, expected_returns, covariance_matrix, risk_free_rate, 20
        )

        return PortfolioOptimization(
            weights={k: round(v, 4) for k, v in best_weights.items()},
            expected_return=round(final_return * 100, 2),
            volatility=round(final_vol * 100, 2),
            sharpe_ratio=round(best_sharpe, 2),
            efficient_frontier=frontier,
            max_sharpe_weights=best_weights,
            constraints_applied=[f"Max weight: {max_weight*100}%", f"Min weight: {min_weight*100}%"]
        )

    def _generate_random_weights(
        self,
        assets: List[str],
        min_weight: float,
        max_weight: float
    ) -> Dict[str, float]:
        """Generate random portfolio weights."""
        n = len(assets)
        weights = {}

        remaining = 1.0
        for i, asset in enumerate(assets[:-1]):
            max_w = min(max_weight, remaining - min_weight * (n - i - 1))
            min_w = max(min_weight, remaining - max_weight * (n - i - 1))
            w = random.uniform(min_w, max_w)
            weights[asset] = w
            remaining -= w

        weights[assets[-1]] = remaining
        return weights

    def _optimize_for_return(
        self,
        assets: List[str],
        expected_returns: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]],
        target_return: float
    ) -> Optional[Dict[str, float]]:
        """Find minimum volatility portfolio for target return."""
        best_vol = float('inf')
        best_weights = None

        for _ in range(500):
            weights = self._generate_random_weights(assets, 0.0, 0.5)
            port_return = sum(weights[a] * expected_returns[a] for a in assets)

            # Check if close to target return
            if abs(port_return - target_return) < 0.01:
                port_vol = self._calculate_portfolio_volatility(weights, covariance_matrix)
                if port_vol < best_vol:
                    best_vol = port_vol
                    best_weights = weights.copy()

        return best_weights

    def _calculate_portfolio_volatility(
        self,
        weights: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]]
    ) -> float:
        """Calculate portfolio volatility."""
        variance = 0
        assets = list(weights.keys())

        for i, a1 in enumerate(assets):
            for j, a2 in enumerate(assets):
                cov = covariance_matrix.get(a1, {}).get(a2, 0)
                variance += weights[a1] * weights[a2] * cov

        return math.sqrt(variance) if variance > 0 else 0


class RiskAnalyzer:
    """Advanced risk analysis."""

    def __init__(self):
        pass

    def calculate_var(
        self,
        returns: List[float],
        confidence_level: float = 0.95,
        method: str = "historical"
    ) -> float:
        """Calculate Value at Risk."""
        if method == "historical":
            sorted_returns = sorted(returns)
            idx = int(len(sorted_returns) * (1 - confidence_level))
            return -sorted_returns[idx]

        elif method == "parametric":
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / len(returns)
            std = math.sqrt(variance)

            # Z-score for confidence level
            z_scores = {0.90: 1.28, 0.95: 1.65, 0.99: 2.33}
            z = z_scores.get(confidence_level, 1.65)

            return -(mean - z * std)

        return 0

    def calculate_cvar(
        self,
        returns: List[float],
        confidence_level: float = 0.95
    ) -> float:
        """Calculate Conditional VaR (Expected Shortfall)."""
        sorted_returns = sorted(returns)
        cutoff_idx = int(len(sorted_returns) * (1 - confidence_level))
        tail_returns = sorted_returns[:cutoff_idx]

        if not tail_returns:
            return 0

        return -sum(tail_returns) / len(tail_returns)

    def calculate_sortino(
        self,
        returns: List[float],
        risk_free_rate: float = 0.0,
        target_return: float = 0.0
    ) -> float:
        """Calculate Sortino ratio."""
        mean_return = sum(returns) / len(returns)
        excess_return = mean_return - risk_free_rate

        # Downside deviation
        downside_returns = [r for r in returns if r < target_return]
        if not downside_returns:
            return float('inf')

        downside_variance = sum((r - target_return) ** 2 for r in downside_returns) / len(downside_returns)
        downside_std = math.sqrt(downside_variance)

        if downside_std == 0:
            return float('inf')

        return excess_return / downside_std * math.sqrt(252)

    def calculate_max_drawdown(self, prices: List[float]) -> Tuple[float, int, int]:
        """Calculate maximum drawdown and its duration."""
        if not prices:
            return 0, 0, 0

        peak = prices[0]
        max_dd = 0
        peak_idx = 0
        trough_idx = 0

        for i, price in enumerate(prices):
            if price > peak:
                peak = price
                peak_idx = i

            dd = (peak - price) / peak
            if dd > max_dd:
                max_dd = dd
                trough_idx = i

        return max_dd, peak_idx, trough_idx

    def calculate_calmar(
        self,
        returns: List[float],
        prices: List[float]
    ) -> float:
        """Calculate Calmar ratio (return / max drawdown)."""
        annual_return = sum(returns) / len(returns) * 252
        max_dd, _, _ = self.calculate_max_drawdown(prices)

        if max_dd == 0:
            return float('inf')

        return annual_return / max_dd

    def decompose_risk(
        self,
        weights: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]],
        returns: Dict[str, List[float]]
    ) -> RiskDecomposition:
        """Decompose portfolio risk by component."""
        assets = list(weights.keys())

        # Total portfolio variance
        total_var = 0
        for a1 in assets:
            for a2 in assets:
                cov = covariance_matrix.get(a1, {}).get(a2, 0)
                total_var += weights[a1] * weights[a2] * cov

        total_vol = math.sqrt(total_var)

        # Component VaR (marginal contribution)
        component_var = {}
        marginal_var = {}

        for asset in assets:
            # Marginal VaR = partial derivative of portfolio vol w.r.t. weight
            marginal = 0
            for a2 in assets:
                cov = covariance_matrix.get(asset, {}).get(a2, 0)
                marginal += weights[a2] * cov

            marginal = marginal / total_vol if total_vol > 0 else 0
            marginal_var[asset] = marginal

            # Component VaR = weight * marginal VaR
            component_var[asset] = weights[asset] * marginal

        # Diversification benefit
        sum_individual_vol = sum(
            weights[a] * math.sqrt(covariance_matrix.get(a, {}).get(a, 0))
            for a in assets
        )
        diversification_benefit = (sum_individual_vol - total_vol) / sum_individual_vol if sum_individual_vol > 0 else 0

        # Concentration index (Herfindahl)
        concentration = sum(w ** 2 for w in weights.values())

        return RiskDecomposition(
            total_var=round(total_vol * 100, 2),
            component_var={k: round(v * 100, 2) for k, v in component_var.items()},
            marginal_var={k: round(v * 100, 2) for k, v in marginal_var.items()},
            incremental_var={},  # Would require recalculation for each asset
            diversification_benefit=round(diversification_benefit * 100, 1),
            concentration_index=round(concentration, 3)
        )


class ScenarioAnalyzer:
    """Scenario and stress testing."""

    PREDEFINED_SCENARIOS = {
        "market_crash": {
            "name": "Market Crash (2008-style)",
            "description": "50% equity decline, flight to safety",
            "impacts": {"stocks": -0.50, "bonds": 0.10, "gold": 0.15, "crypto": -0.70},
            "probability": 0.02
        },
        "recession": {
            "name": "Mild Recession",
            "description": "20% equity decline, bond rally",
            "impacts": {"stocks": -0.20, "bonds": 0.05, "gold": 0.08, "crypto": -0.35},
            "probability": 0.10
        },
        "inflation_spike": {
            "name": "Inflation Spike",
            "description": "High inflation, rising rates",
            "impacts": {"stocks": -0.15, "bonds": -0.10, "gold": 0.20, "crypto": -0.25},
            "probability": 0.08
        },
        "tech_bubble": {
            "name": "Tech Bubble Pop",
            "description": "Tech sector correction",
            "impacts": {"stocks": -0.25, "tech": -0.45, "bonds": 0.05, "crypto": -0.40},
            "probability": 0.05
        },
        "crypto_winter": {
            "name": "Crypto Winter",
            "description": "Severe crypto bear market",
            "impacts": {"stocks": -0.05, "bonds": 0.02, "crypto": -0.80, "defi": -0.85},
            "probability": 0.15
        },
        "bull_market": {
            "name": "Bull Market Rally",
            "description": "Strong risk-on environment",
            "impacts": {"stocks": 0.30, "bonds": -0.05, "crypto": 0.80, "gold": -0.10},
            "probability": 0.20
        },
        "geopolitical_shock": {
            "name": "Geopolitical Shock",
            "description": "Major geopolitical event",
            "impacts": {"stocks": -0.15, "bonds": 0.08, "gold": 0.25, "crypto": -0.20},
            "probability": 0.05
        }
    }

    def run_scenario(
        self,
        scenario_id: str,
        portfolio: Dict[str, float],
        asset_classes: Dict[str, str]
    ) -> ScenarioResult:
        """Run a predefined scenario on portfolio."""
        scenario = self.PREDEFINED_SCENARIOS.get(scenario_id)
        if not scenario:
            raise ValueError(f"Unknown scenario: {scenario_id}")

        total_value = sum(portfolio.values())
        asset_impacts = {}
        portfolio_impact = 0

        for asset, value in portfolio.items():
            asset_class = asset_classes.get(asset, "stocks")
            impact_pct = scenario["impacts"].get(asset_class, 0)
            impact = value * impact_pct
            asset_impacts[asset] = round(impact, 2)
            portfolio_impact += impact

        return ScenarioResult(
            scenario_name=scenario["name"],
            portfolio_impact=round(portfolio_impact, 2),
            portfolio_impact_pct=round(portfolio_impact / total_value * 100, 2) if total_value > 0 else 0,
            asset_impacts=asset_impacts,
            probability=scenario["probability"],
            description=scenario["description"]
        )

    def run_all_scenarios(
        self,
        portfolio: Dict[str, float],
        asset_classes: Dict[str, str]
    ) -> List[ScenarioResult]:
        """Run all predefined scenarios."""
        results = []
        for scenario_id in self.PREDEFINED_SCENARIOS:
            result = self.run_scenario(scenario_id, portfolio, asset_classes)
            results.append(result)

        # Sort by impact (worst first)
        results.sort(key=lambda x: x.portfolio_impact)
        return results

    def custom_scenario(
        self,
        name: str,
        impacts: Dict[str, float],
        portfolio: Dict[str, float],
        asset_classes: Dict[str, str]
    ) -> ScenarioResult:
        """Run a custom scenario."""
        total_value = sum(portfolio.values())
        asset_impacts = {}
        portfolio_impact = 0

        for asset, value in portfolio.items():
            asset_class = asset_classes.get(asset, "other")
            impact_pct = impacts.get(asset_class, impacts.get(asset, 0))
            impact = value * impact_pct
            asset_impacts[asset] = round(impact, 2)
            portfolio_impact += impact

        return ScenarioResult(
            scenario_name=name,
            portfolio_impact=round(portfolio_impact, 2),
            portfolio_impact_pct=round(portfolio_impact / total_value * 100, 2) if total_value > 0 else 0,
            asset_impacts=asset_impacts,
            probability=0,
            description="Custom scenario"
        )


class AdvancedAnalytics:
    """Main analytics class combining all analysis tools."""

    def __init__(self):
        self.monte_carlo = MonteCarloSimulator()
        self.optimizer = PortfolioOptimizer()
        self.risk_analyzer = RiskAnalyzer()
        self.scenario_analyzer = ScenarioAnalyzer()

    def full_portfolio_analysis(
        self,
        portfolio: Dict[str, float],
        returns: Dict[str, List[float]],
        expected_returns: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]],
        asset_classes: Dict[str, str] = None
    ) -> Dict:
        """Run comprehensive portfolio analysis."""
        total_value = sum(portfolio.values())
        weights = {k: v / total_value for k, v in portfolio.items()}

        # Portfolio metrics
        port_return = sum(weights[a] * expected_returns.get(a, 0) for a in portfolio)
        port_vol = self._calculate_portfolio_volatility(weights, covariance_matrix)

        # Risk metrics
        all_returns = []
        for asset, asset_returns in returns.items():
            weight = weights.get(asset, 0)
            for i, r in enumerate(asset_returns):
                if len(all_returns) <= i:
                    all_returns.append(0)
                all_returns[i] += weight * r

        var_95 = self.risk_analyzer.calculate_var(all_returns, 0.95) if all_returns else 0
        cvar_95 = self.risk_analyzer.calculate_cvar(all_returns, 0.95) if all_returns else 0
        sortino = self.risk_analyzer.calculate_sortino(all_returns) if all_returns else 0

        # Monte Carlo
        mc_result = self.monte_carlo.simulate_portfolio(
            initial_value=total_value,
            expected_return=port_return,
            volatility=port_vol,
            time_horizon_days=252,
            num_simulations=5000
        )

        # Optimization
        optimization = self.optimizer.optimize_portfolio(
            assets=list(portfolio.keys()),
            expected_returns=expected_returns,
            covariance_matrix=covariance_matrix
        )

        # Risk decomposition
        risk_decomp = self.risk_analyzer.decompose_risk(weights, covariance_matrix, returns)

        # Scenario analysis
        if asset_classes:
            scenarios = self.scenario_analyzer.run_all_scenarios(portfolio, asset_classes)
        else:
            scenarios = []

        return {
            "portfolio_value": total_value,
            "expected_annual_return": round(port_return * 100, 2),
            "annual_volatility": round(port_vol * 100, 2),
            "sharpe_ratio": round((port_return - 0.05) / port_vol, 2) if port_vol > 0 else 0,
            "var_95": round(var_95 * 100, 2),
            "cvar_95": round(cvar_95 * 100, 2),
            "sortino_ratio": round(sortino, 2),
            "weights": {k: round(v * 100, 1) for k, v in weights.items()},
            "monte_carlo": {
                "mean_1yr_value": mc_result.mean_final_value,
                "median_1yr_value": mc_result.median_final_value,
                "percentile_5": mc_result.percentile_5,
                "percentile_95": mc_result.percentile_95,
                "prob_profit": mc_result.prob_profit,
                "prob_loss_20pct": mc_result.prob_loss_20pct
            },
            "optimization": {
                "optimal_weights": {k: round(v * 100, 1) for k, v in optimization.weights.items()},
                "optimal_sharpe": optimization.sharpe_ratio,
                "current_sharpe_gap": round(optimization.sharpe_ratio - ((port_return - 0.05) / port_vol if port_vol > 0 else 0), 2)
            },
            "risk_decomposition": {
                "total_risk": risk_decomp.total_var,
                "diversification_benefit": risk_decomp.diversification_benefit,
                "concentration_index": risk_decomp.concentration_index,
                "component_risks": risk_decomp.component_var
            },
            "scenarios": [
                {
                    "name": s.scenario_name,
                    "impact": s.portfolio_impact,
                    "impact_pct": s.portfolio_impact_pct,
                    "probability": s.probability
                }
                for s in scenarios[:5]  # Top 5 worst scenarios
            ]
        }

    def _calculate_portfolio_volatility(
        self,
        weights: Dict[str, float],
        covariance_matrix: Dict[str, Dict[str, float]]
    ) -> float:
        """Calculate portfolio volatility."""
        variance = 0
        assets = list(weights.keys())

        for a1 in assets:
            for a2 in assets:
                cov = covariance_matrix.get(a1, {}).get(a2, 0)
                variance += weights[a1] * weights[a2] * cov

        return math.sqrt(variance) if variance > 0 else 0


# =============================================================================
# Singleton Factory
# =============================================================================

_analytics: Optional[AdvancedAnalytics] = None


def get_advanced_analytics() -> AdvancedAnalytics:
    """Get or create the analytics singleton."""
    global _analytics
    if _analytics is None:
        _analytics = AdvancedAnalytics()
    return _analytics
