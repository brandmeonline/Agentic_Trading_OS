"""
Agentic Trading System - Main Orchestrator

This is the main entry point that integrates all components:
- Strategy ensemble for signal generation
- Risk management for position sizing
- Execution engine for order management
- Portfolio optimization for allocation
- Performance analytics for reporting

Usage:
    python trading_system.py --mode backtest --days 365
    python trading_system.py --mode live --simulation
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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Agentic Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trading_system.py --mode backtest --days 365
  python trading_system.py --mode live --duration 60
  python trading_system.py --mode optimize
  python trading_system.py --mode generate-data
        """
    )

    parser.add_argument(
        "--mode",
        choices=["backtest", "live", "optimize", "report", "generate-data", "status"],
        default="backtest",
        help="Operating mode"
    )
    parser.add_argument("--days", type=int, default=365, help="Days for backtest")
    parser.add_argument("--duration", type=int, default=60, help="Minutes for live simulation")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital")
    parser.add_argument("--assets", nargs="+", default=["BTC", "ETH", "ADA"], help="Assets to trade")

    args = parser.parse_args()

    # Create system
    config = TradingSystemConfig(
        initial_capital=args.capital,
        assets=args.assets
    )
    system = TradingSystem(config)

    # Run based on mode
    if args.mode == "backtest":
        system.run_backtest(num_days=args.days)
        system.generate_report()

    elif args.mode == "live":
        system.run_live(duration_minutes=args.duration)

    elif args.mode == "optimize":
        system.optimize_portfolio()

    elif args.mode == "report":
        system.initialize()
        system.run_backtest(num_days=100)
        system.generate_report()

    elif args.mode == "generate-data":
        from utils.data_generator import generate_sample_dataset
        generate_sample_dataset("data", seed=42)

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


if __name__ == "__main__":
    main()
