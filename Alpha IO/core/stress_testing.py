"""
Stress Testing & Tail Risk Hedging.

Advanced risk analysis and hedging:
- Historical stress scenarios
- Hypothetical scenario generation
- Extreme value analysis (EVT)
- Tail risk hedging strategies
- Crisis simulation
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime
import copy


# =============================================================================
# Configuration
# =============================================================================

class CrisisType(Enum):
    """Historical crisis types."""
    DOT_COM_CRASH = "dot_com_crash"
    GFC_2008 = "gfc_2008"
    FLASH_CRASH_2010 = "flash_crash_2010"
    COVID_CRASH = "covid_crash"
    CRYPTO_WINTER_2018 = "crypto_winter_2018"
    LUNA_COLLAPSE = "luna_collapse"
    CUSTOM = "custom"


class HedgeInstrument(Enum):
    """Hedging instrument types."""
    PUT_OPTIONS = "put_options"
    VIX_CALLS = "vix_calls"
    INVERSE_ETF = "inverse_etf"
    SHORT_FUTURES = "short_futures"
    TAIL_HEDGE_FUND = "tail_hedge_fund"
    DYNAMIC_HEDGE = "dynamic_hedge"


@dataclass
class StressConfig:
    """Stress testing configuration."""
    var_confidence: float = 0.99
    cvar_confidence: float = 0.99
    max_drawdown_threshold: float = 0.20
    tail_threshold: float = 0.05  # Define tail as worst 5%
    evt_threshold: float = 0.10  # EVT threshold for GPD
    n_bootstrap_samples: int = 1000


@dataclass
class ScenarioDefinition:
    """Definition of a stress scenario."""
    name: str
    description: str
    crisis_type: CrisisType
    equity_shock: float  # e.g., -0.30 for 30% drop
    volatility_multiplier: float  # e.g., 3.0 for 3x vol
    correlation_shift: float  # e.g., 0.3 for correlation increase
    duration_days: int
    recovery_days: int
    liquidity_factor: float  # e.g., 0.5 for 50% liquidity
    custom_params: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Historical Scenarios
# =============================================================================

# Pre-defined historical crisis scenarios
HISTORICAL_SCENARIOS = {
    CrisisType.DOT_COM_CRASH: ScenarioDefinition(
        name="Dot-Com Crash",
        description="2000-2002 technology bubble burst",
        crisis_type=CrisisType.DOT_COM_CRASH,
        equity_shock=-0.78,
        volatility_multiplier=2.5,
        correlation_shift=0.3,
        duration_days=750,
        recovery_days=1500,
        liquidity_factor=0.7
    ),
    CrisisType.GFC_2008: ScenarioDefinition(
        name="Global Financial Crisis",
        description="2008-2009 financial crisis",
        crisis_type=CrisisType.GFC_2008,
        equity_shock=-0.57,
        volatility_multiplier=4.0,
        correlation_shift=0.5,
        duration_days=350,
        recovery_days=700,
        liquidity_factor=0.3
    ),
    CrisisType.FLASH_CRASH_2010: ScenarioDefinition(
        name="Flash Crash",
        description="May 2010 flash crash",
        crisis_type=CrisisType.FLASH_CRASH_2010,
        equity_shock=-0.09,
        volatility_multiplier=10.0,
        correlation_shift=0.8,
        duration_days=1,
        recovery_days=1,
        liquidity_factor=0.1
    ),
    CrisisType.COVID_CRASH: ScenarioDefinition(
        name="COVID-19 Crash",
        description="March 2020 pandemic crash",
        crisis_type=CrisisType.COVID_CRASH,
        equity_shock=-0.34,
        volatility_multiplier=5.0,
        correlation_shift=0.6,
        duration_days=23,
        recovery_days=120,
        liquidity_factor=0.4
    ),
    CrisisType.CRYPTO_WINTER_2018: ScenarioDefinition(
        name="Crypto Winter 2018",
        description="2018 cryptocurrency crash",
        crisis_type=CrisisType.CRYPTO_WINTER_2018,
        equity_shock=-0.84,
        volatility_multiplier=3.0,
        correlation_shift=0.4,
        duration_days=365,
        recovery_days=700,
        liquidity_factor=0.5
    ),
    CrisisType.LUNA_COLLAPSE: ScenarioDefinition(
        name="LUNA/UST Collapse",
        description="May 2022 Terra collapse",
        crisis_type=CrisisType.LUNA_COLLAPSE,
        equity_shock=-0.99,
        volatility_multiplier=8.0,
        correlation_shift=0.7,
        duration_days=7,
        recovery_days=float('inf'),  # No recovery
        liquidity_factor=0.05
    )
}


# =============================================================================
# Stress Test Results
# =============================================================================

@dataclass
class StressTestResult:
    """Result of a stress test."""
    scenario: ScenarioDefinition
    portfolio_impact: float  # Percentage loss
    max_drawdown: float
    time_to_recovery: Optional[int]  # Days, or None if no recovery
    var_breach: bool
    cvar_breach: bool
    liquidity_cost: float
    margin_call_risk: float
    detailed_pnl: np.ndarray
    risk_metrics: Dict[str, float]


@dataclass
class EVTAnalysis:
    """Extreme Value Theory analysis results."""
    tail_index: float  # Shape parameter (xi)
    scale_parameter: float  # Scale parameter (sigma)
    threshold: float
    var_estimates: Dict[float, float]  # Confidence -> VaR
    cvar_estimates: Dict[float, float]  # Confidence -> CVaR
    exceedance_probability: Callable[[float], float]
    return_level: Callable[[int], float]  # Return period -> level


@dataclass
class HedgeRecommendation:
    """Tail risk hedge recommendation."""
    instrument: HedgeInstrument
    notional: float
    cost_bps: float
    expected_payoff_in_crisis: float
    hedge_ratio: float
    strike_or_level: Optional[float] = None
    rationale: str = ""


# =============================================================================
# Scenario Generator
# =============================================================================

class ScenarioGenerator:
    """
    Generates stress scenarios for testing.

    Methods:
    - Historical scenarios
    - Hypothetical scenarios
    - Reverse stress testing
    - Monte Carlo extremes
    """

    def __init__(self, config: StressConfig):
        self.config = config

    def get_historical_scenario(self, crisis_type: CrisisType) -> ScenarioDefinition:
        """Get a historical stress scenario."""
        if crisis_type not in HISTORICAL_SCENARIOS:
            raise ValueError(f"Unknown crisis type: {crisis_type}")
        return HISTORICAL_SCENARIOS[crisis_type]

    def create_custom_scenario(
        self,
        name: str,
        equity_shock: float,
        volatility_multiplier: float = 2.0,
        correlation_shift: float = 0.3,
        duration_days: int = 30,
        liquidity_factor: float = 0.5
    ) -> ScenarioDefinition:
        """Create a custom stress scenario."""
        return ScenarioDefinition(
            name=name,
            description=f"Custom scenario: {name}",
            crisis_type=CrisisType.CUSTOM,
            equity_shock=equity_shock,
            volatility_multiplier=volatility_multiplier,
            correlation_shift=correlation_shift,
            duration_days=duration_days,
            recovery_days=duration_days * 3,
            liquidity_factor=liquidity_factor
        )

    def create_combined_scenario(
        self,
        scenarios: List[ScenarioDefinition],
        weights: Optional[List[float]] = None
    ) -> ScenarioDefinition:
        """Combine multiple scenarios into one."""
        if weights is None:
            weights = [1.0 / len(scenarios)] * len(scenarios)

        combined = ScenarioDefinition(
            name="Combined Scenario",
            description="Weighted combination of scenarios",
            crisis_type=CrisisType.CUSTOM,
            equity_shock=sum(s.equity_shock * w for s, w in zip(scenarios, weights)),
            volatility_multiplier=sum(s.volatility_multiplier * w for s, w in zip(scenarios, weights)),
            correlation_shift=sum(s.correlation_shift * w for s, w in zip(scenarios, weights)),
            duration_days=int(sum(s.duration_days * w for s, w in zip(scenarios, weights))),
            recovery_days=int(sum(s.recovery_days * w for s, w in zip(scenarios, weights))),
            liquidity_factor=sum(s.liquidity_factor * w for s, w in zip(scenarios, weights))
        )

        return combined

    def generate_monte_carlo_extremes(
        self,
        historical_returns: np.ndarray,
        n_scenarios: int = 100
    ) -> List[ScenarioDefinition]:
        """Generate extreme scenarios from Monte Carlo tail."""
        # Find tail events
        sorted_returns = np.sort(historical_returns)
        tail_cutoff = int(len(sorted_returns) * self.config.tail_threshold)
        tail_returns = sorted_returns[:tail_cutoff]

        scenarios = []
        for i, ret in enumerate(np.linspace(tail_returns[0], tail_returns[-1], n_scenarios)):
            scenario = ScenarioDefinition(
                name=f"MC_Extreme_{i}",
                description=f"Monte Carlo tail scenario {i}",
                crisis_type=CrisisType.CUSTOM,
                equity_shock=ret,
                volatility_multiplier=2.0 + abs(ret) * 5,
                correlation_shift=0.3 + abs(ret) * 0.5,
                duration_days=max(1, int(abs(ret) * 100)),
                recovery_days=max(1, int(abs(ret) * 300)),
                liquidity_factor=max(0.1, 1.0 + ret * 2)
            )
            scenarios.append(scenario)

        return scenarios

    def reverse_stress_test(
        self,
        target_loss: float,
        portfolio_value: float,
        base_volatility: float
    ) -> ScenarioDefinition:
        """
        Find scenario that causes specific loss.

        Reverse engineers the scenario needed to breach a threshold.
        """
        # Calculate required shock
        required_shock = -target_loss

        # Estimate other parameters
        vol_multiplier = max(2.0, abs(required_shock) / base_volatility / 3)
        duration = max(1, int(abs(required_shock) * 200))

        return ScenarioDefinition(
            name=f"Reverse_Stress_{target_loss:.0%}",
            description=f"Scenario causing {target_loss:.0%} loss",
            crisis_type=CrisisType.CUSTOM,
            equity_shock=required_shock,
            volatility_multiplier=vol_multiplier,
            correlation_shift=0.5,
            duration_days=duration,
            recovery_days=duration * 3,
            liquidity_factor=0.3
        )


# =============================================================================
# Extreme Value Analyzer
# =============================================================================

class ExtremeValueAnalyzer:
    """
    Extreme Value Theory (EVT) analysis.

    Implements:
    - Generalized Pareto Distribution (GPD)
    - Peak-over-threshold method
    - Return level estimation
    """

    def __init__(self, config: StressConfig):
        self.config = config

    def fit_gpd(self, returns: np.ndarray) -> EVTAnalysis:
        """
        Fit Generalized Pareto Distribution to tail losses.

        Uses Peak-Over-Threshold (POT) method.
        """
        # Get negative returns (losses)
        losses = -returns

        # Set threshold (e.g., 90th percentile of losses)
        threshold = np.percentile(losses, (1 - self.config.evt_threshold) * 100)

        # Get exceedances
        exceedances = losses[losses > threshold] - threshold

        if len(exceedances) < 10:
            # Not enough data for EVT
            return self._fallback_analysis(returns)

        # Estimate GPD parameters using method of moments
        mean_excess = np.mean(exceedances)
        var_excess = np.var(exceedances)

        # Shape parameter (xi)
        xi = 0.5 * ((mean_excess ** 2 / var_excess) - 1)
        xi = np.clip(xi, -0.5, 1.0)  # Bound for stability

        # Scale parameter (sigma)
        sigma = mean_excess * (1 - xi)
        sigma = max(sigma, 1e-6)

        # VaR and CVaR estimates
        n = len(returns)
        n_u = len(exceedances)

        def var_gpd(confidence: float) -> float:
            """VaR using GPD."""
            p = 1 - confidence
            if xi == 0:
                return threshold - sigma * np.log(p * n / n_u)
            else:
                return threshold + (sigma / xi) * ((p * n / n_u) ** (-xi) - 1)

        def cvar_gpd(confidence: float) -> float:
            """CVaR (Expected Shortfall) using GPD."""
            var = var_gpd(confidence)
            if xi == 0:
                return var + sigma
            elif xi < 1:
                return (var + sigma - xi * threshold) / (1 - xi)
            else:
                return float('inf')

        var_estimates = {conf: var_gpd(conf) for conf in [0.90, 0.95, 0.99, 0.999]}
        cvar_estimates = {conf: cvar_gpd(conf) for conf in [0.90, 0.95, 0.99, 0.999]}

        def exceedance_prob(x: float) -> float:
            """Probability of exceeding level x."""
            if x <= threshold:
                return n_u / n
            if xi == 0:
                return (n_u / n) * np.exp(-(x - threshold) / sigma)
            else:
                return (n_u / n) * (1 + xi * (x - threshold) / sigma) ** (-1 / xi)

        def return_level(period: int) -> float:
            """Expected maximum loss over period years."""
            p = 1 - 1 / (period * 252)  # Annual periods, daily data
            return var_gpd(p)

        return EVTAnalysis(
            tail_index=xi,
            scale_parameter=sigma,
            threshold=threshold,
            var_estimates=var_estimates,
            cvar_estimates=cvar_estimates,
            exceedance_probability=exceedance_prob,
            return_level=return_level
        )

    def _fallback_analysis(self, returns: np.ndarray) -> EVTAnalysis:
        """Fallback when not enough data for EVT."""
        losses = -returns

        def simple_var(conf):
            return np.percentile(losses, conf * 100)

        def simple_cvar(conf):
            var = simple_var(conf)
            return np.mean(losses[losses >= var])

        return EVTAnalysis(
            tail_index=0.0,
            scale_parameter=np.std(losses),
            threshold=np.percentile(losses, 90),
            var_estimates={conf: simple_var(conf) for conf in [0.90, 0.95, 0.99]},
            cvar_estimates={conf: simple_cvar(conf) for conf in [0.90, 0.95, 0.99]},
            exceedance_probability=lambda x: np.mean(losses > x),
            return_level=lambda p: simple_var(1 - 1/(p*252))
        )

    def estimate_tail_dependence(
        self,
        returns1: np.ndarray,
        returns2: np.ndarray
    ) -> float:
        """
        Estimate tail dependence coefficient between two assets.

        Higher values indicate stronger co-movement in tails.
        """
        n = len(returns1)
        threshold_pct = self.config.tail_threshold

        # Get threshold indices
        threshold1 = np.percentile(returns1, threshold_pct * 100)
        threshold2 = np.percentile(returns2, threshold_pct * 100)

        # Count joint exceedances
        joint_tail = np.sum((returns1 <= threshold1) & (returns2 <= threshold2))
        marginal_tail = np.sum(returns1 <= threshold1)

        if marginal_tail == 0:
            return 0.0

        return joint_tail / marginal_tail


# =============================================================================
# Stress Tester
# =============================================================================

class StressTester:
    """
    Main stress testing engine.

    Conducts:
    - Scenario-based stress tests
    - Sensitivity analysis
    - Concentration analysis
    - Liquidity stress tests
    """

    def __init__(self, config: StressConfig):
        self.config = config
        self.scenario_generator = ScenarioGenerator(config)
        self.evt_analyzer = ExtremeValueAnalyzer(config)

    def run_stress_test(
        self,
        portfolio_weights: Dict[str, float],
        asset_returns: Dict[str, np.ndarray],
        scenario: ScenarioDefinition
    ) -> StressTestResult:
        """
        Run stress test on portfolio.

        Args:
            portfolio_weights: Asset -> weight mapping
            asset_returns: Asset -> historical returns
            scenario: Stress scenario to apply
        """
        # Calculate portfolio returns
        assets = list(portfolio_weights.keys())
        weights = np.array([portfolio_weights[a] for a in assets])
        returns_matrix = np.column_stack([asset_returns[a] for a in assets])
        portfolio_returns = returns_matrix @ weights

        # Apply scenario
        stressed_returns = self._apply_scenario(portfolio_returns, returns_matrix, scenario)

        # Calculate impact
        portfolio_impact = np.sum(stressed_returns)
        cumulative = np.cumprod(1 + stressed_returns)
        max_drawdown = self._calculate_max_drawdown(cumulative)

        # Check VaR/CVaR breaches
        historical_var = np.percentile(-portfolio_returns, self.config.var_confidence * 100)
        historical_cvar = np.mean(-portfolio_returns[-portfolio_returns <= -historical_var])

        var_breach = -scenario.equity_shock > historical_var
        cvar_breach = -scenario.equity_shock > historical_cvar

        # Liquidity cost
        liquidity_cost = self._estimate_liquidity_cost(
            portfolio_weights, scenario.liquidity_factor
        )

        # Margin call risk
        margin_call_risk = self._estimate_margin_call_risk(
            portfolio_impact, scenario.volatility_multiplier
        )

        # Time to recovery
        if scenario.recovery_days < float('inf'):
            time_to_recovery = scenario.recovery_days
        else:
            time_to_recovery = None

        risk_metrics = {
            "stressed_volatility": np.std(stressed_returns) * np.sqrt(252),
            "stressed_sharpe": np.mean(stressed_returns) / np.std(stressed_returns) * np.sqrt(252) if np.std(stressed_returns) > 0 else 0,
            "correlation_increase": scenario.correlation_shift,
            "liquidity_score": scenario.liquidity_factor
        }

        return StressTestResult(
            scenario=scenario,
            portfolio_impact=portfolio_impact,
            max_drawdown=max_drawdown,
            time_to_recovery=time_to_recovery,
            var_breach=var_breach,
            cvar_breach=cvar_breach,
            liquidity_cost=liquidity_cost,
            margin_call_risk=margin_call_risk,
            detailed_pnl=stressed_returns,
            risk_metrics=risk_metrics
        )

    def _apply_scenario(
        self,
        portfolio_returns: np.ndarray,
        asset_returns: np.ndarray,
        scenario: ScenarioDefinition
    ) -> np.ndarray:
        """Apply stress scenario to returns."""
        n_days = min(scenario.duration_days, len(portfolio_returns))

        # Generate stressed returns
        stressed_returns = np.zeros(n_days)

        # Daily shock distributed over duration
        daily_shock = scenario.equity_shock / n_days

        # Increase volatility
        volatility = np.std(portfolio_returns) * scenario.volatility_multiplier

        for i in range(n_days):
            # Apply systematic shock + increased volatility
            shock = daily_shock + np.random.randn() * volatility / np.sqrt(252)
            stressed_returns[i] = shock

        return stressed_returns

    def _calculate_max_drawdown(self, cumulative: np.ndarray) -> float:
        """Calculate maximum drawdown."""
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak
        return np.max(drawdown)

    def _estimate_liquidity_cost(
        self,
        weights: Dict[str, float],
        liquidity_factor: float
    ) -> float:
        """Estimate cost of liquidating in stressed conditions."""
        # Base liquidation cost
        base_cost = 0.001  # 10 bps

        # Increased cost due to illiquidity
        stress_multiplier = 1.0 / max(liquidity_factor, 0.1)

        total_cost = 0.0
        for asset, weight in weights.items():
            asset_cost = base_cost * stress_multiplier * abs(weight)
            total_cost += asset_cost

        return total_cost

    def _estimate_margin_call_risk(
        self,
        portfolio_impact: float,
        volatility_multiplier: float
    ) -> float:
        """Estimate probability of margin call."""
        # Assume 25% maintenance margin
        maintenance_margin = 0.25

        # Probability of margin breach
        if portfolio_impact < -maintenance_margin:
            return 1.0
        elif portfolio_impact < -maintenance_margin * 0.5:
            return 0.5 + abs(portfolio_impact) / maintenance_margin
        else:
            return max(0, abs(portfolio_impact) * 2)

    def run_all_historical_scenarios(
        self,
        portfolio_weights: Dict[str, float],
        asset_returns: Dict[str, np.ndarray]
    ) -> List[StressTestResult]:
        """Run all historical stress scenarios."""
        results = []

        for crisis_type, scenario in HISTORICAL_SCENARIOS.items():
            result = self.run_stress_test(portfolio_weights, asset_returns, scenario)
            results.append(result)

        return results

    def sensitivity_analysis(
        self,
        portfolio_weights: Dict[str, float],
        asset_returns: Dict[str, np.ndarray],
        shock_range: List[float] = None
    ) -> Dict[float, StressTestResult]:
        """Analyze portfolio sensitivity to market shocks."""
        if shock_range is None:
            shock_range = [-0.05, -0.10, -0.15, -0.20, -0.30, -0.40, -0.50]

        results = {}

        for shock in shock_range:
            scenario = self.scenario_generator.create_custom_scenario(
                name=f"Shock_{shock:.0%}",
                equity_shock=shock,
                volatility_multiplier=1 + abs(shock) * 5,
                duration_days=max(1, int(abs(shock) * 100))
            )
            result = self.run_stress_test(portfolio_weights, asset_returns, scenario)
            results[shock] = result

        return results


# =============================================================================
# Tail Risk Hedger
# =============================================================================

class TailRiskHedger:
    """
    Tail risk hedging strategies.

    Implements:
    - Put option hedging
    - VIX-based hedging
    - Dynamic hedging
    - Optimal hedge ratios
    """

    def __init__(self, config: StressConfig):
        self.config = config

    def calculate_optimal_hedge_ratio(
        self,
        portfolio_returns: np.ndarray,
        hedge_returns: np.ndarray
    ) -> float:
        """
        Calculate optimal hedge ratio using minimum variance.

        Returns ratio of hedge instrument to portfolio.
        """
        # Covariance matrix
        cov = np.cov(portfolio_returns, hedge_returns)
        var_hedge = cov[1, 1]
        cov_port_hedge = cov[0, 1]

        if var_hedge == 0:
            return 0.0

        # Optimal hedge ratio
        h_star = -cov_port_hedge / var_hedge

        return h_star

    def calculate_put_hedge(
        self,
        portfolio_value: float,
        target_protection: float,  # e.g., 0.90 for 90% floor
        volatility: float,
        time_to_expiry: float,  # in years
        risk_free_rate: float = 0.05
    ) -> HedgeRecommendation:
        """
        Calculate put option hedge for downside protection.

        Args:
            portfolio_value: Current portfolio value
            target_protection: Minimum value as fraction
            volatility: Annualized volatility
            time_to_expiry: Time to option expiry
            risk_free_rate: Risk-free rate
        """
        # Strike for target protection
        strike = portfolio_value * target_protection

        # Black-Scholes put price approximation
        d1 = (np.log(portfolio_value / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry) / (volatility * np.sqrt(time_to_expiry))
        d2 = d1 - volatility * np.sqrt(time_to_expiry)

        # Approximate N(x)
        def norm_cdf(x):
            return 0.5 * (1 + np.tanh(np.sqrt(2/np.pi) * (x + 0.044715 * x**3)))

        put_price = strike * np.exp(-risk_free_rate * time_to_expiry) * norm_cdf(-d2) - portfolio_value * norm_cdf(-d1)

        # Cost as basis points
        cost_bps = (put_price / portfolio_value) * 10000

        # Expected payoff in crisis (assuming 30% drop)
        crisis_drop = 0.30
        crisis_value = portfolio_value * (1 - crisis_drop)
        expected_payoff = max(0, strike - crisis_value)

        return HedgeRecommendation(
            instrument=HedgeInstrument.PUT_OPTIONS,
            notional=portfolio_value,
            cost_bps=cost_bps,
            expected_payoff_in_crisis=expected_payoff,
            hedge_ratio=1.0,
            strike_or_level=strike,
            rationale=f"Put option at {target_protection:.0%} strike provides floor at {strike:,.0f}"
        )

    def calculate_vix_hedge(
        self,
        portfolio_value: float,
        portfolio_beta: float,
        current_vix: float,
        target_vix_exposure: float = 0.10  # 10% of portfolio
    ) -> HedgeRecommendation:
        """
        Calculate VIX-based tail hedge.

        VIX typically spikes during market crashes, providing convex payoff.
        """
        # VIX exposure notional
        vix_notional = portfolio_value * target_vix_exposure

        # Typical VIX beta to SPX
        vix_spx_beta = -4.0  # VIX moves ~4x opposite to SPX

        # Hedge ratio based on portfolio beta
        hedge_ratio = portfolio_beta / abs(vix_spx_beta)

        # Cost (VIX futures typically in contango)
        # Assume 3% monthly roll cost
        cost_bps = 300  # annualized

        # Expected payoff in crisis
        # VIX typically goes from ~15 to ~50+ in crisis
        vix_spike = 50 / current_vix - 1
        expected_payoff = vix_notional * vix_spike

        return HedgeRecommendation(
            instrument=HedgeInstrument.VIX_CALLS,
            notional=vix_notional,
            cost_bps=cost_bps,
            expected_payoff_in_crisis=expected_payoff,
            hedge_ratio=hedge_ratio,
            strike_or_level=current_vix * 1.5,  # 50% OTM
            rationale=f"VIX calls provide convex payoff in crisis (expected spike to {current_vix * 3:.0f})"
        )

    def calculate_dynamic_hedge(
        self,
        portfolio_returns: np.ndarray,
        threshold_drawdown: float = 0.10
    ) -> HedgeRecommendation:
        """
        Calculate dynamic hedging parameters.

        Increases hedge as drawdown increases.
        """
        # Current drawdown
        cumulative = np.cumprod(1 + portfolio_returns)
        peak = np.maximum.accumulate(cumulative)
        current_dd = 1 - cumulative[-1] / peak[-1]

        # Dynamic hedge ratio
        if current_dd < threshold_drawdown:
            hedge_ratio = 0.0
        else:
            # Linear increase above threshold
            hedge_ratio = min(1.0, (current_dd - threshold_drawdown) / (0.20 - threshold_drawdown))

        # Estimated cost (rebalancing)
        cost_bps = hedge_ratio * 50  # 50 bps per 100% hedge

        return HedgeRecommendation(
            instrument=HedgeInstrument.DYNAMIC_HEDGE,
            notional=0,  # Dynamic
            cost_bps=cost_bps,
            expected_payoff_in_crisis=hedge_ratio,  # Protection level
            hedge_ratio=hedge_ratio,
            rationale=f"Dynamic hedge at {hedge_ratio:.0%} based on {current_dd:.1%} drawdown"
        )

    def recommend_hedge_portfolio(
        self,
        portfolio_value: float,
        portfolio_returns: np.ndarray,
        risk_budget: float = 0.02,  # 2% of portfolio for hedging
        volatility: float = 0.20
    ) -> List[HedgeRecommendation]:
        """
        Recommend optimal hedge portfolio.

        Args:
            portfolio_value: Portfolio value
            portfolio_returns: Historical returns
            risk_budget: Maximum budget for hedging
            volatility: Portfolio volatility
        """
        recommendations = []
        remaining_budget = portfolio_value * risk_budget

        # 1. Core put protection (50% of budget)
        put_budget = remaining_budget * 0.5
        put_hedge = self.calculate_put_hedge(
            portfolio_value=portfolio_value,
            target_protection=0.85,  # 85% floor
            volatility=volatility,
            time_to_expiry=0.25  # Quarterly
        )
        recommendations.append(put_hedge)

        # 2. VIX hedge (30% of budget)
        vix_hedge = self.calculate_vix_hedge(
            portfolio_value=portfolio_value,
            portfolio_beta=1.0,
            current_vix=18,  # Assumed
            target_vix_exposure=remaining_budget * 0.3 / portfolio_value
        )
        recommendations.append(vix_hedge)

        # 3. Dynamic hedge
        dynamic_hedge = self.calculate_dynamic_hedge(
            portfolio_returns=portfolio_returns,
            threshold_drawdown=0.08
        )
        recommendations.append(dynamic_hedge)

        return recommendations


# =============================================================================
# Crisis Simulator
# =============================================================================

class CrisisSimulator:
    """
    Simulates crisis dynamics for testing.

    Models:
    - Contagion effects
    - Liquidity spirals
    - Correlation breakdown
    """

    def __init__(self, config: StressConfig):
        self.config = config

    def simulate_crisis_path(
        self,
        initial_value: float,
        scenario: ScenarioDefinition,
        n_paths: int = 100
    ) -> np.ndarray:
        """
        Simulate multiple crisis paths.

        Returns array of shape (n_paths, duration_days)
        """
        duration = scenario.duration_days
        paths = np.zeros((n_paths, duration))

        for path_idx in range(n_paths):
            value = initial_value

            for day in range(duration):
                # Calculate daily parameters
                progress = day / duration

                # Shock intensity varies over crisis
                if progress < 0.3:
                    # Initial panic
                    daily_intensity = 2.0
                elif progress < 0.7:
                    # Peak crisis
                    daily_intensity = 1.5
                else:
                    # Recovery phase
                    daily_intensity = 0.5

                # Daily return
                expected_daily = scenario.equity_shock / duration
                daily_vol = abs(expected_daily) * scenario.volatility_multiplier * daily_intensity

                # Add mean reversion in later phase
                if progress > 0.7:
                    mean_reversion = (initial_value - value) / initial_value * 0.1
                else:
                    mean_reversion = 0

                daily_return = expected_daily + mean_reversion + np.random.randn() * daily_vol
                value *= (1 + daily_return)

                paths[path_idx, day] = value

        return paths

    def simulate_contagion(
        self,
        asset_returns: Dict[str, np.ndarray],
        shock_asset: str,
        shock_magnitude: float,
        contagion_speed: float = 0.3
    ) -> Dict[str, np.ndarray]:
        """
        Simulate contagion from one asset to others.

        Args:
            asset_returns: Historical returns by asset
            shock_asset: Asset receiving initial shock
            shock_magnitude: Size of initial shock
            contagion_speed: How fast contagion spreads
        """
        assets = list(asset_returns.keys())
        n_assets = len(assets)
        n_days = 30  # Contagion simulation period

        # Build correlation matrix
        returns_matrix = np.column_stack([asset_returns[a][-252:] for a in assets])
        corr_matrix = np.corrcoef(returns_matrix.T)

        # Initialize shocked returns
        shocked_returns = {a: np.zeros(n_days) for a in assets}

        # Day 0: Initial shock
        shock_idx = assets.index(shock_asset)
        shocked_returns[shock_asset][0] = shock_magnitude

        # Propagate contagion
        shock_levels = np.zeros(n_assets)
        shock_levels[shock_idx] = abs(shock_magnitude)

        for day in range(1, n_days):
            new_shock_levels = shock_levels.copy()

            for i, asset in enumerate(assets):
                if i == shock_idx:
                    # Original shock decays
                    decay = 0.9 ** day
                    shocked_returns[asset][day] = shock_magnitude * decay * 0.1
                else:
                    # Contagion from correlated assets
                    contagion = 0
                    for j in range(n_assets):
                        if j != i:
                            contagion += corr_matrix[i, j] * shock_levels[j] * contagion_speed

                    new_shock_levels[i] = max(new_shock_levels[i], abs(contagion))
                    shocked_returns[asset][day] = -contagion * np.sign(shock_magnitude)

            shock_levels = new_shock_levels

        return shocked_returns

    def simulate_liquidity_spiral(
        self,
        initial_value: float,
        margin_requirement: float = 0.25,
        n_days: int = 10
    ) -> np.ndarray:
        """
        Simulate liquidity spiral with forced selling.
        """
        values = [initial_value]
        margin_level = 1.0

        for day in range(n_days):
            # Price drop causes margin calls
            if margin_level < margin_requirement:
                # Forced selling
                forced_sale_fraction = (margin_requirement - margin_level) / margin_level
                price_impact = -forced_sale_fraction * 0.5  # 50% price impact
            else:
                price_impact = np.random.randn() * 0.02 - 0.01  # Slight downward bias

            new_value = values[-1] * (1 + price_impact)
            values.append(new_value)

            # Update margin level
            margin_level = new_value / initial_value

        return np.array(values)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    np.random.seed(42)

    # Generate sample data
    n_days = 1000
    assets = ["BTC", "ETH", "SPY"]

    asset_returns = {}
    for asset in assets:
        vol = 0.03 if "BTC" in asset else 0.02 if "ETH" in asset else 0.01
        returns = np.random.randn(n_days) * vol - 0.0001
        asset_returns[asset] = returns

    portfolio_weights = {"BTC": 0.4, "ETH": 0.3, "SPY": 0.3}
    portfolio_value = 1000000

    # =========================================================================
    # Stress Testing
    # =========================================================================
    print("=" * 60)
    print("Stress Testing")
    print("=" * 60)

    config = StressConfig(var_confidence=0.99)
    tester = StressTester(config)

    # Run historical scenarios
    print("\nHistorical Scenario Results:")
    for crisis_type, scenario in HISTORICAL_SCENARIOS.items():
        result = tester.run_stress_test(portfolio_weights, asset_returns, scenario)
        print(f"\n  {scenario.name}:")
        print(f"    Portfolio Impact: {result.portfolio_impact:.1%}")
        print(f"    Max Drawdown: {result.max_drawdown:.1%}")
        print(f"    VaR Breach: {result.var_breach}")
        print(f"    Margin Call Risk: {result.margin_call_risk:.1%}")

    # =========================================================================
    # EVT Analysis
    # =========================================================================
    print("\n" + "=" * 60)
    print("Extreme Value Theory Analysis")
    print("=" * 60)

    portfolio_returns = np.column_stack([asset_returns[a] for a in assets]) @ np.array([portfolio_weights[a] for a in assets])

    evt = ExtremeValueAnalyzer(config)
    evt_result = evt.fit_gpd(portfolio_returns)

    print(f"\n  Tail Index (xi): {evt_result.tail_index:.4f}")
    print(f"  Scale Parameter: {evt_result.scale_parameter:.4f}")
    print(f"  Threshold: {evt_result.threshold:.4f}")

    print("\n  VaR Estimates:")
    for conf, var in evt_result.var_estimates.items():
        print(f"    {conf*100:.0f}% VaR: {var:.4f}")

    print("\n  Return Levels:")
    for period in [1, 5, 10]:
        level = evt_result.return_level(period)
        print(f"    {period}-year return level: {level:.4f}")

    # =========================================================================
    # Tail Risk Hedging
    # =========================================================================
    print("\n" + "=" * 60)
    print("Tail Risk Hedging Recommendations")
    print("=" * 60)

    hedger = TailRiskHedger(config)
    recommendations = hedger.recommend_hedge_portfolio(
        portfolio_value=portfolio_value,
        portfolio_returns=portfolio_returns,
        risk_budget=0.02,
        volatility=np.std(portfolio_returns) * np.sqrt(252)
    )

    for rec in recommendations:
        print(f"\n  {rec.instrument.value}:")
        print(f"    Notional: ${rec.notional:,.0f}")
        print(f"    Cost: {rec.cost_bps:.0f} bps")
        print(f"    Expected Crisis Payoff: ${rec.expected_payoff_in_crisis:,.0f}")
        print(f"    Rationale: {rec.rationale}")

    # =========================================================================
    # Crisis Simulation
    # =========================================================================
    print("\n" + "=" * 60)
    print("Crisis Simulation")
    print("=" * 60)

    simulator = CrisisSimulator(config)
    gfc_scenario = HISTORICAL_SCENARIOS[CrisisType.GFC_2008]

    paths = simulator.simulate_crisis_path(
        initial_value=portfolio_value,
        scenario=gfc_scenario,
        n_paths=100
    )

    terminal_values = paths[:, -1]
    print(f"\n  Simulated {gfc_scenario.name}:")
    print(f"    Mean Terminal Value: ${np.mean(terminal_values):,.0f}")
    print(f"    Min Terminal Value: ${np.min(terminal_values):,.0f}")
    print(f"    Max Terminal Value: ${np.max(terminal_values):,.0f}")
    print(f"    Probability of >50% loss: {np.mean(terminal_values < portfolio_value * 0.5):.1%}")
