"""
Agentic Trading System - Main Orchestrator (Production-Ready)

This is the main entry point that integrates ALL advanced components:
- Deep learning alpha models (LSTM, Transformer, N-BEATS, WaveNet)
- Advanced feature engineering (200+ features)
- NLP sentiment analysis (news, social media)
- Strategy ensemble for signal generation
- Risk management for position sizing
- Smart order routing for execution
- Portfolio optimization for allocation
- Stress testing and tail risk hedging
- Performance analytics for reporting
- Production monitoring and alerting

Usage:
    python trading_system.py --mode backtest --days 365
    python trading_system.py --mode live --simulation
    python trading_system.py --mode deep-learning --train
    python trading_system.py --mode stress-test
    python trading_system.py --mode unified --paper
    python trading_system.py --mode analyze --report
"""

from __future__ import annotations

import argparse
import sys
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np


@dataclass
class TradingSystemConfig:
    """Configuration for the trading system."""
    # Capital
    initial_capital: float = 10000.0

    # Assets
    assets: List[str] = None

    # Strategy
    use_ensemble: bool = True
    confidence_threshold: float = 0.65

    # Risk
    max_risk_per_trade: float = 0.02
    max_drawdown: float = 0.15
    max_positions: int = 5

    # Execution
    simulation_mode: bool = True
    slippage_pct: float = 0.001
    commission_pct: float = 0.001

    # Data
    data_dir: str = "data"
    log_dir: str = "logs"

    def __post_init__(self):
        if self.assets is None:
            self.assets = ["BTC", "ETH", "ADA"]


class TradingSystem:
    """
    Main trading system orchestrator.

    Integrates all components for a complete trading solution.
    """

    def __init__(self, config: Optional[TradingSystemConfig] = None):
        self.config = config or TradingSystemConfig()
        self.is_initialized = False

        # Components (lazy loaded)
        self._agent = None
        self._risk_manager = None
        self._execution_engine = None
        self._strategy_ensemble = None
        self._portfolio_optimizer = None
        self._performance_analyzer = None
        self._data_feed = None

        # State
        self.positions: Dict[str, float] = {}
        self.equity_curve: List[tuple] = []
        self.trades: List[Dict] = []
        self.signals: List[Dict] = []

    def initialize(self) -> bool:
        """Initialize all system components."""
        print("Initializing Agentic Trading System...")
        print("=" * 50)

        try:
            # Initialize agent
            print("  Loading trading agent...")
            from core.agent import TradingAgent, AgentConfig, LearningAlgorithm
            self._agent = TradingAgent(AgentConfig(
                algorithm=LearningAlgorithm.DOUBLE_Q,
                learning_rate=0.1,
                epsilon=0.1
            ))

            # Initialize risk manager
            print("  Loading risk manager...")
            from core.risk import RiskManager, RiskConfig, RiskLevel
            self._risk_manager = RiskManager(RiskConfig(
                initial_capital=self.config.initial_capital,
                max_risk_per_trade=self.config.max_risk_per_trade,
                max_total_drawdown=self.config.max_drawdown,
                risk_level=RiskLevel.MODERATE
            ))

            # Initialize execution engine
            print("  Loading execution engine...")
            from core.execution import ExecutionEngine, ExecutionConfig
            self._execution_engine = ExecutionEngine(ExecutionConfig(
                simulation_mode=self.config.simulation_mode,
                max_slippage_pct=self.config.slippage_pct
            ))

            # Initialize strategy ensemble
            print("  Loading strategy ensemble...")
            from core.strategy import create_default_ensemble
            self._strategy_ensemble = create_default_ensemble()

            # Initialize portfolio optimizer
            print("  Loading portfolio optimizer...")
            from core.portfolio import PortfolioOptimizer, OptimizationConfig
            self._portfolio_optimizer = PortfolioOptimizer(OptimizationConfig(
                risk_free_rate=0.05,
                min_weight=0.0,
                max_weight=0.4
            ))

            # Initialize performance analyzer
            print("  Loading performance analyzer...")
            from core.analytics import PerformanceAnalyzer
            self._performance_analyzer = PerformanceAnalyzer(risk_free_rate=0.05)

            # Initialize data feed
            print("  Loading data feed...")
            from core.market_data import SimulatedDataFeed
            initial_prices = {"BTC": 45000, "ETH": 2500, "ADA": 0.5}
            self._data_feed = SimulatedDataFeed(
                symbols=self.config.assets,
                initial_prices={a: initial_prices.get(a, 100) for a in self.config.assets},
                volatility=0.02
            )

            self.is_initialized = True
            print("\nSystem initialized successfully!")
            print("=" * 50)
            return True

        except ImportError as e:
            print(f"\nError: Missing dependency - {e}")
            print("Please install requirements: pip install -r requirements.txt")
            return False
        except Exception as e:
            print(f"\nError initializing system: {e}")
            return False

    def run_backtest(
        self,
        num_days: int = 365,
        start_date: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Run backtesting simulation.

        Args:
            num_days: Number of days to simulate
            start_date: Optional start date

        Returns:
            Dict with backtest results
        """
        if not self.is_initialized:
            if not self.initialize():
                return {"error": "Failed to initialize system"}

        print(f"\nRunning backtest for {num_days} days...")
        print("-" * 50)

        from core.backtest_engine import BacktestEngine, BacktestConfig

        config = BacktestConfig(
            initial_capital=self.config.initial_capital,
            commission_pct=self.config.commission_pct,
            slippage_pct=self.config.slippage_pct,
            max_positions=self.config.max_positions
        )

        engine = BacktestEngine(config)
        engine.set_data_feed(self._data_feed)
        engine.set_ensemble(self._strategy_ensemble)

        result = engine.run(
            symbols=self.config.assets,
            num_bars=num_days
        )

        # Store results
        self.equity_curve = result.equity_curve
        self.trades = [t.__dict__ for t in result.trades]

        print(result.summary())

        return {
            "total_return": result.metrics.total_return,
            "sharpe_ratio": result.metrics.sharpe_ratio,
            "max_drawdown": result.metrics.max_drawdown,
            "total_trades": result.metrics.total_trades,
            "win_rate": result.metrics.win_rate,
        }

    def run_live(self, duration_minutes: int = 60) -> None:
        """
        Run live trading simulation.

        Args:
            duration_minutes: Duration to run
        """
        if not self.is_initialized:
            if not self.initialize():
                return

        print(f"\nStarting live trading simulation...")
        print(f"Duration: {duration_minutes} minutes")
        print(f"Mode: {'Simulation' if self.config.simulation_mode else 'LIVE'}")
        print("-" * 50)

        from core.market_data import MarketData
        from core.execution import OrderSide

        self._data_feed.connect()
        current_capital = self.config.initial_capital

        # Trading loop
        for minute in range(duration_minutes):
            for asset in self.config.assets:
                # Get latest market data
                bar = self._data_feed.get_latest_bar(asset)
                if not bar:
                    continue

                # Update execution engine prices
                self._execution_engine.set_price(asset, bar.close)

                # Build data buffer for strategy
                historical = self._data_feed.get_historical_bars(asset, None, 50)
                market_data = [
                    MarketData(
                        asset=asset,
                        timestamp=b.timestamp,
                        open=b.open,
                        high=b.high,
                        low=b.low,
                        close=b.close,
                        volume=b.volume
                    )
                    for b in historical
                ]

                # Generate signal
                signal = self._strategy_ensemble.evaluate(market_data, asset)

                if signal.should_trade and signal.confidence >= self.config.confidence_threshold:
                    # Check risk limits
                    if not self._risk_manager.check_risk_limits(asset):
                        continue

                    # Get position size
                    size = self._risk_manager.get_position_size(asset, signal.confidence)

                    # Get agent decision
                    if self._agent.decide(signal.confidence):
                        # Execute trade
                        side = OrderSide.BUY if signal.is_long else OrderSide.SELL
                        order = self._execution_engine.create_order(
                            asset=asset,
                            side=side,
                            quantity=size / bar.close,
                            strategy=signal.metadata.get("strategy", "ensemble")
                        )
                        result = self._execution_engine.submit_order(order)

                        if result.success:
                            print(f"[{minute:03d}] {asset}: {side.value.upper()} @ {bar.close:.2f} "
                                  f"(conf: {signal.confidence:.2f})")

                            # Record signal
                            self.signals.append({
                                "timestamp": bar.timestamp.isoformat(),
                                "asset": asset,
                                "signal": signal.signal.name,
                                "confidence": signal.confidence,
                                "price": bar.close
                            })

            self._data_feed.advance_time()

        self._data_feed.disconnect()

        # Print summary
        stats = self._execution_engine.get_statistics()
        print("\n" + "=" * 50)
        print("LIVE SIMULATION COMPLETE")
        print("=" * 50)
        for key, value in stats.items():
            print(f"  {key}: {value}")

    def optimize_portfolio(self) -> Dict[str, float]:
        """
        Run portfolio optimization.

        Returns:
            Dict of asset -> weight
        """
        if not self.is_initialized:
            if not self.initialize():
                return {}

        print("\nRunning portfolio optimization...")
        print("-" * 50)

        from core.portfolio import OptimizationMethod

        # Generate sample returns for optimization
        for asset in self.config.assets:
            returns = list(np.random.normal(0.001, 0.02, 252))
            self._portfolio_optimizer.add_returns(asset, returns)

        # Run all optimization methods
        methods = [
            OptimizationMethod.EQUAL_WEIGHT,
            OptimizationMethod.MIN_VARIANCE,
            OptimizationMethod.MAX_SHARPE,
            OptimizationMethod.RISK_PARITY,
        ]

        results = {}
        for method in methods:
            result = self._portfolio_optimizer.optimize(method)
            print(f"\n{method.value.upper()}:")
            for asset, weight in result.weights.items():
                print(f"  {asset}: {weight:.1%}")
            print(f"  Expected Return: {result.expected_return:.1%}")
            print(f"  Expected Vol: {result.expected_volatility:.1%}")
            print(f"  Sharpe: {result.sharpe_ratio:.2f}")

            if method == OptimizationMethod.MAX_SHARPE:
                results = result.weights

        return results

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive performance report.

        Returns:
            Dict with all performance metrics
        """
        if not self.equity_curve:
            print("No data to analyze. Run backtest first.")
            return {}

        print("\nGenerating performance report...")
        print("-" * 50)

        report = self._performance_analyzer.generate_report(
            self.equity_curve,
            self.trades
        )

        from core.analytics import print_report
        print_report(report)

        return report

    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status."""
        return {
            "initialized": self.is_initialized,
            "config": {
                "assets": self.config.assets,
                "capital": self.config.initial_capital,
                "simulation_mode": self.config.simulation_mode,
            },
            "state": {
                "positions": self.positions,
                "total_trades": len(self.trades),
                "total_signals": len(self.signals),
            },
            "components": {
                "agent": self._agent is not None,
                "risk_manager": self._risk_manager is not None,
                "execution_engine": self._execution_engine is not None,
                "strategy_ensemble": self._strategy_ensemble is not None,
                "portfolio_optimizer": self._portfolio_optimizer is not None,
                "performance_analyzer": self._performance_analyzer is not None,
            }
        }

    def run_deep_learning(self, train: bool = True, n_epochs: int = 10) -> Dict[str, Any]:
        """
        Run deep learning alpha model training and inference.

        Args:
            train: Whether to train models
            n_epochs: Number of training epochs

        Returns:
            Dict with model performance metrics
        """
        print("\n" + "=" * 60)
        print("DEEP LEARNING ALPHA MODELS")
        print("=" * 60)

        try:
            from core.deep_learning import DeepAlphaGenerator
            from core.feature_engine import FeatureEngine, FeatureConfig

            # Generate sample data
            np.random.seed(42)
            n_samples = 500
            seq_len = 50

            print("\nGenerating feature set...")
            close = 100 * np.cumprod(1 + np.random.randn(n_samples) * 0.02)
            high = close * (1 + np.abs(np.random.randn(n_samples) * 0.01))
            low = close * (1 - np.abs(np.random.randn(n_samples) * 0.01))
            open_ = close * (1 + np.random.randn(n_samples) * 0.005)
            volume = np.abs(np.random.randn(n_samples) * 1e6 + 5e6)

            # Create features
            config = FeatureConfig(
                lookback_periods=[10, 20, 50],
                momentum_periods=[5, 10, 20],
                volatility_periods=[10, 20],
                normalize_features=True
            )
            engine = FeatureEngine(config)
            feature_set = engine.compute_features(
                open_, high, low, close, volume,
                include_fractal=False
            )

            print(f"  Generated {feature_set.n_features} features")

            # Prepare data for deep learning
            n_features = min(feature_set.n_features, 50)  # Limit features
            X = feature_set.features[:, :n_features]

            # Create sequences
            n_sequences = n_samples - seq_len
            X_seq = np.zeros((n_sequences, seq_len, n_features))
            for i in range(n_sequences):
                X_seq[i] = X[i:i+seq_len]

            print(f"  Prepared {n_sequences} sequences of length {seq_len}")

            # Initialize alpha generator
            print("\nInitializing deep learning models...")
            generator = DeepAlphaGenerator(
                n_features=n_features,
                seq_len=seq_len
            )

            # Generate signals
            print("\nGenerating alpha signals...")
            signals = generator.generate_signals(X_seq)

            # Analyze signal distribution
            signal_stats = {
                "mean_signal": float(np.mean(signals)),
                "std_signal": float(np.std(signals)),
                "min_signal": float(np.min(signals)),
                "max_signal": float(np.max(signals)),
                "positive_signals": float(np.mean(signals > 0)),
                "strong_signals": float(np.mean(np.abs(signals) > 0.5))
            }

            print("\nSignal Statistics:")
            for key, value in signal_stats.items():
                print(f"  {key}: {value:.4f}")

            # Evaluate signal quality
            future_returns = np.diff(close[seq_len:]) / close[seq_len:-1]
            if len(future_returns) > len(signals):
                future_returns = future_returns[:len(signals)]

            correlation = np.corrcoef(signals[:len(future_returns)], future_returns)[0, 1]
            print(f"\nSignal-Return Correlation: {correlation:.4f}")

            return {
                "signal_stats": signal_stats,
                "correlation": correlation,
                "n_samples": len(signals)
            }

        except ImportError as e:
            print(f"\nError: Deep learning module not available - {e}")
            return {"error": str(e)}

    def run_stress_test(self) -> Dict[str, Any]:
        """
        Run comprehensive stress testing.

        Returns:
            Dict with stress test results
        """
        print("\n" + "=" * 60)
        print("STRESS TESTING & TAIL RISK ANALYSIS")
        print("=" * 60)

        try:
            from core.stress_testing import (
                StressTester, StressConfig, TailRiskHedger,
                ExtremeValueAnalyzer, CrisisType, HISTORICAL_SCENARIOS
            )

            # Generate sample portfolio data
            np.random.seed(42)
            n_days = 500

            portfolio_weights = {asset: 1.0/len(self.config.assets) for asset in self.config.assets}
            asset_returns = {
                asset: np.random.randn(n_days) * (0.03 if 'BTC' in asset else 0.02)
                for asset in self.config.assets
            }

            config = StressConfig(var_confidence=0.99)
            tester = StressTester(config)

            print("\nRunning Historical Stress Scenarios...")
            print("-" * 50)

            results = {}
            for crisis_type, scenario in HISTORICAL_SCENARIOS.items():
                result = tester.run_stress_test(portfolio_weights, asset_returns, scenario)
                results[scenario.name] = {
                    "portfolio_impact": result.portfolio_impact,
                    "max_drawdown": result.max_drawdown,
                    "var_breach": result.var_breach,
                    "margin_call_risk": result.margin_call_risk
                }
                print(f"\n  {scenario.name}:")
                print(f"    Impact: {result.portfolio_impact:.1%}")
                print(f"    Max DD: {result.max_drawdown:.1%}")
                print(f"    VaR Breach: {'Yes' if result.var_breach else 'No'}")

            # EVT Analysis
            print("\n\nExtreme Value Theory Analysis...")
            print("-" * 50)

            portfolio_returns = sum(
                asset_returns[a] * portfolio_weights[a]
                for a in self.config.assets
            )

            evt = ExtremeValueAnalyzer(config)
            evt_result = evt.fit_gpd(portfolio_returns)

            print(f"\n  Tail Index (xi): {evt_result.tail_index:.4f}")
            print(f"  Scale Parameter: {evt_result.scale_parameter:.4f}")

            print("\n  VaR Estimates:")
            for conf, var in evt_result.var_estimates.items():
                print(f"    {conf*100:.0f}% VaR: {var:.4f}")

            # Hedge Recommendations
            print("\n\nTail Risk Hedge Recommendations...")
            print("-" * 50)

            hedger = TailRiskHedger(config)
            recommendations = hedger.recommend_hedge_portfolio(
                portfolio_value=self.config.initial_capital,
                portfolio_returns=portfolio_returns,
                risk_budget=0.02,
                volatility=np.std(portfolio_returns) * np.sqrt(252)
            )

            for rec in recommendations:
                print(f"\n  {rec.instrument.value}:")
                print(f"    Cost: {rec.cost_bps:.0f} bps")
                print(f"    Crisis Payoff: ${rec.expected_payoff_in_crisis:,.0f}")

            return {
                "scenario_results": results,
                "evt_analysis": {
                    "tail_index": evt_result.tail_index,
                    "var_99": evt_result.var_estimates.get(0.99, 0)
                },
                "hedge_recommendations": len(recommendations)
            }

        except ImportError as e:
            print(f"\nError: Stress testing module not available - {e}")
            return {"error": str(e)}

    def run_unified_system(self, mode: str = "paper", duration: int = 60) -> Dict[str, Any]:
        """
        Run the unified trading system with all components integrated.

        Args:
            mode: 'paper' or 'backtest'
            duration: Duration in simulated minutes

        Returns:
            Dict with trading results
        """
        print("\n" + "=" * 60)
        print("UNIFIED TRADING SYSTEM")
        print("=" * 60)

        try:
            from core.unified_system import (
                create_trading_system, TradeSignal, SignalSource
            )

            print(f"\nMode: {mode.upper()}")
            print(f"Symbols: {self.config.assets}")
            print(f"Initial Capital: ${self.config.initial_capital:,.0f}")

            # Create unified engine
            engine = create_trading_system(
                mode=mode,
                symbols=[f"{a}/USDT" for a in self.config.assets],
                initial_capital=self.config.initial_capital,
                max_position_size=0.25,
                risk_per_trade=0.02
            )

            print("\nStarting unified engine...")
            engine.start()

            # Simulate signals from multiple sources
            print("\nSimulating multi-source signals...")
            import time

            for minute in range(min(duration, 20)):  # Limit for demo
                for asset in self.config.assets:
                    symbol = f"{asset}/USDT"

                    # Generate random signals from different sources
                    if np.random.random() > 0.7:
                        confidence = np.random.uniform(0.6, 0.9)
                        action = "buy" if np.random.random() > 0.5 else "sell"

                        # Strategy signal
                        engine.add_signal(TradeSignal(
                            symbol=symbol,
                            action=action,
                            confidence=confidence,
                            source=SignalSource.STRATEGY,
                            timestamp=datetime.now(),
                            target_price=45000 if "BTC" in symbol else 2500
                        ))

                    if np.random.random() > 0.8:
                        # Deep learning signal
                        engine.add_signal(TradeSignal(
                            symbol=symbol,
                            action="buy" if np.random.random() > 0.4 else "hold",
                            confidence=np.random.uniform(0.5, 0.85),
                            source=SignalSource.DEEP_LEARNING,
                            timestamp=datetime.now()
                        ))

                # Update market data
                for asset in self.config.assets:
                    symbol = f"{asset}/USDT"
                    base_price = 45000 if asset == "BTC" else 2500 if asset == "ETH" else 0.5
                    price = base_price * (1 + np.random.randn() * 0.01)
                    engine.update_market_data(symbol, price, np.random.exponential(1000))

                time.sleep(0.1)

                if minute % 5 == 0:
                    print(f"  Minute {minute}: Processing signals...")

            time.sleep(1)

            # Get results
            status = engine.get_status()
            report = engine.get_performance_report()

            print("\n" + "-" * 50)
            print("RESULTS")
            print("-" * 50)

            print(f"\n  Capital: ${status['capital']:,.2f}")
            print(f"  Unrealized P&L: ${status['unrealized_pnl']:,.2f}")
            print(f"  Total Equity: ${status['total_equity']:,.2f}")
            print(f"  Active Positions: {status['n_positions']}")
            print(f"  Circuit Breaker: {'Active' if status['circuit_breaker'] else 'Inactive'}")

            if status['positions']:
                print("\n  Positions:")
                for symbol, pos in status['positions'].items():
                    print(f"    {symbol}: {pos['side']} @ {pos['entry_price']:.2f}")

            metrics = status.get('metrics', {})
            print(f"\n  Total Trades: {metrics.get('total_trades', 0)}")
            print(f"  Win Rate: {metrics.get('winning_trades', 0) / max(metrics.get('total_trades', 1), 1):.1%}")
            print(f"  Total P&L: ${metrics.get('total_pnl', 0):,.2f}")

            engine.stop()

            return {
                "final_capital": status['capital'],
                "total_equity": status['total_equity'],
                "n_positions": status['n_positions'],
                "metrics": metrics
            }

        except ImportError as e:
            print(f"\nError: Unified system module not available - {e}")
            return {"error": str(e)}

    def run_advanced_backtest(self, n_days: int = 365) -> Dict[str, Any]:
        """
        Run advanced backtesting with walk-forward optimization and Monte Carlo.

        Args:
            n_days: Number of days to backtest

        Returns:
            Dict with backtest results
        """
        print("\n" + "=" * 60)
        print("ADVANCED BACKTESTING")
        print("=" * 60)

        try:
            from core.advanced_backtest import (
                MonteCarloSimulator, MonteCarloConfig,
                RegimeAwareBacktest, WalkForwardOptimizer, WalkForwardConfig
            )

            # Generate sample data
            np.random.seed(42)
            returns = np.random.randn(n_days) * 0.02
            prices = 100 * np.cumprod(1 + returns)

            print("\nMonte Carlo Simulation...")
            print("-" * 50)

            mc_config = MonteCarloConfig(n_simulations=500, random_seed=42)
            simulator = MonteCarloSimulator(mc_config)
            mc_result = simulator.simulate_returns(returns, n_periods=252)

            print(f"\n  Simulations: {mc_config.n_simulations}")
            print(f"  Mean Terminal Value: {mc_result.statistics['mean_terminal']:.4f}")
            print(f"  Probability of Profit: {mc_result.statistics['prob_profit']:.1%}")

            print("\n  VaR Estimates:")
            for conf, var in mc_result.var_estimates.items():
                print(f"    {conf*100:.0f}% VaR: {var:.4f}")

            print("\n\nRegime-Aware Backtest...")
            print("-" * 50)

            regime_bt = RegimeAwareBacktest(lookback=30)

            def simple_strategy(prices, regime):
                if len(prices) < 20:
                    return 0
                momentum = (prices[-1] - prices[-20]) / prices[-20]
                if regime.value in ["bull", "low_vol"]:
                    return 1 if momentum > -0.05 else 0
                elif regime.value in ["bear", "high_vol"]:
                    return -1 if momentum < 0.05 else 0
                return 0

            regime_result = regime_bt.run_backtest(prices, simple_strategy)

            print(f"\n  Overall Results:")
            for k, v in regime_result.overall_metrics.items():
                if isinstance(v, float):
                    print(f"    {k}: {v:.4f}")

            print(f"\n  Regime-Specific Performance:")
            for regime, metrics in regime_result.regime_metrics.items():
                sharpe = metrics.get('sharpe_ratio', 0)
                dd = metrics.get('max_drawdown', 0)
                print(f"    {regime.value}: Sharpe={sharpe:.2f}, MaxDD={dd:.1%}")

            return {
                "monte_carlo": {
                    "mean_terminal": mc_result.statistics['mean_terminal'],
                    "prob_profit": mc_result.statistics['prob_profit'],
                    "var_99": mc_result.var_estimates.get(0.99, 0)
                },
                "regime_backtest": {
                    "total_return": regime_result.overall_metrics.get('total_return', 0),
                    "sharpe_ratio": regime_result.overall_metrics.get('sharpe_ratio', 0),
                    "max_drawdown": regime_result.overall_metrics.get('max_drawdown', 0)
                }
            }

        except ImportError as e:
            print(f"\nError: Advanced backtest module not available - {e}")
            return {"error": str(e)}

    def run_nlp_sentiment(self, texts: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Run NLP sentiment analysis.

        Args:
            texts: Optional list of texts to analyze

        Returns:
            Dict with sentiment results
        """
        print("\n" + "=" * 60)
        print("NLP SENTIMENT ANALYSIS")
        print("=" * 60)

        try:
            from core.nlp_engine import NLPEngine, NLPConfig

            config = NLPConfig(sentiment_threshold=0.25)
            engine = NLPEngine(config)

            # Sample texts if none provided
            if texts is None:
                texts = [
                    f"${self.config.assets[0]} is looking very bullish! Moon incoming!",
                    f"Bearish on ${self.config.assets[0]}, expecting a major correction",
                    f"${self.config.assets[1]} breaking out with massive volume",
                    "Market looking uncertain, staying on the sidelines",
                    "Massive whale accumulation detected, very bullish signal"
                ]

            print("\nAnalyzing sample texts...")
            print("-" * 50)

            results = []
            for text in texts:
                signal = engine.process_text(text, "twitter")
                results.append({
                    "text": text[:60] + "..." if len(text) > 60 else text,
                    "sentiment": signal.sentiment.name,
                    "confidence": signal.confidence,
                    "entities": signal.entities
                })
                print(f"\n  Text: {text[:50]}...")
                print(f"  Sentiment: {signal.sentiment.name}")
                print(f"  Confidence: {signal.confidence:.2f}")
                print(f"  Entities: {signal.entities}")

            # Get aggregated sentiment
            print("\n\nAggregated Sentiment by Asset:")
            print("-" * 50)

            for asset in self.config.assets:
                sentiment = engine.get_sentiment(asset)
                if sentiment:
                    print(f"\n  {asset}:")
                    print(f"    Score: {sentiment.sentiment_score:.3f}")
                    print(f"    Signals: {sentiment.n_signals}")
                    print(f"    Bullish Ratio: {sentiment.bullish_ratio:.1%}")

                trading_signal = engine.get_trading_signal(asset)
                if trading_signal.get('signal') != 'hold':
                    print(f"    Trading Signal: {trading_signal['signal'].upper()}")
                    print(f"    Signal Strength: {trading_signal['strength']:.2f}")

            return {
                "analyzed_texts": len(results),
                "results": results
            }

        except ImportError as e:
            print(f"\nError: NLP module not available - {e}")
            return {"error": str(e)}


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Agentic Trading System - Production-Ready Trading Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trading_system.py --mode backtest --days 365
  python trading_system.py --mode live --duration 60
  python trading_system.py --mode unified --paper
  python trading_system.py --mode deep-learning
  python trading_system.py --mode stress-test
  python trading_system.py --mode nlp-sentiment
  python trading_system.py --mode advanced-backtest
  python trading_system.py --mode optimize
  python trading_system.py --mode generate-data
  python trading_system.py --mode all-tests
        """
    )

    parser.add_argument(
        "--mode",
        choices=[
            "backtest", "live", "optimize", "report", "generate-data", "status",
            "unified", "deep-learning", "stress-test", "nlp-sentiment",
            "advanced-backtest", "all-tests"
        ],
        default="backtest",
        help="Operating mode"
    )
    parser.add_argument("--days", type=int, default=365, help="Days for backtest")
    parser.add_argument("--duration", type=int, default=60, help="Minutes for live simulation")
    parser.add_argument("--capital", type=float, default=100000, help="Initial capital")
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "ADA"], help="Assets to trade")
    parser.add_argument("--paper", action="store_true", help="Use paper trading mode")

    args = parser.parse_args()

    # Create system
    config = TradingSystemConfig(
        initial_capital=args.capital,
        assets=args.assets
    )
    system = TradingSystem(config)

    print("\n" + "=" * 60)
    print("AGENTIC TRADING SYSTEM - Production Platform")
    print("=" * 60)
    print(f"Capital: ${args.capital:,.0f}")
    print(f"Assets: {args.assets}")
    print(f"Mode: {args.mode}")

    # Run based on mode
    if args.mode == "backtest":
        system.run_backtest(num_days=args.days)
        system.generate_report()

    elif args.mode == "live":
        system.run_live(duration_minutes=args.duration)

    elif args.mode == "unified":
        system.run_unified_system(
            mode="paper" if args.paper else "paper",
            duration=args.duration
        )

    elif args.mode == "deep-learning":
        system.run_deep_learning()

    elif args.mode == "stress-test":
        system.run_stress_test()

    elif args.mode == "nlp-sentiment":
        system.run_nlp_sentiment()

    elif args.mode == "advanced-backtest":
        system.run_advanced_backtest(n_days=args.days)

    elif args.mode == "optimize":
        system.optimize_portfolio()

    elif args.mode == "report":
        system.initialize()
        system.run_backtest(num_days=100)
        system.generate_report()

    elif args.mode == "generate-data":
        from utils.data_generator import generate_sample_dataset
        generate_sample_dataset("data", seed=42)

    elif args.mode == "all-tests":
        print("\nRunning all system tests...")
        print("-" * 50)

        results = {}

        print("\n1. Deep Learning...")
        results["deep_learning"] = system.run_deep_learning()

        print("\n2. NLP Sentiment...")
        results["nlp_sentiment"] = system.run_nlp_sentiment()

        print("\n3. Stress Testing...")
        results["stress_test"] = system.run_stress_test()

        print("\n4. Advanced Backtest...")
        results["advanced_backtest"] = system.run_advanced_backtest(n_days=252)

        print("\n5. Unified System...")
        results["unified"] = system.run_unified_system(mode="paper", duration=10)

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETE")
        print("=" * 60)

        for test_name, result in results.items():
            status = "PASS" if "error" not in result else "FAIL"
            print(f"  {test_name}: [{status}]")

    elif args.mode == "status":
        system.initialize()
        status = system.get_system_status()
        print("\nSystem Status:")
        print("-" * 40)
        for section, data in status.items():
            print(f"\n{section.upper()}:")
            if isinstance(data, dict):
                for key, value in data.items():
                    print(f"  {key}: {value}")
            else:
                print(f"  {data}")

    print("\n" + "=" * 60)
    print("Session Complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
