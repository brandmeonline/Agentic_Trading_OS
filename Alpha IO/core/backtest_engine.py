"""
Professional Backtesting Engine.

Provides event-driven backtesting with realistic execution simulation,
comprehensive performance analytics, and detailed reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from collections import defaultdict

from core.market_data import MarketDataFeed, SimulatedDataFeed, OHLCV
from core.strategy import Strategy, StrategyOutput, StrategySignal, MarketData, StrategyEnsemble


from core.fill_model import Bar, FillCosts, FillModel, FillTiming

@dataclass
class BacktestConfig:
    """Configuration for backtesting."""
    initial_capital: float = 10000.0
    commission_pct: float = 0.001  # 0.1%
    slippage_pct: float = 0.0005  # 0.05%
    position_sizing_pct: float = 0.02  # 2% per trade
    max_positions: int = 5
    warmup_bars: int = 50  # Bars before trading starts
    risk_free_rate: float = 0.05  # Annual

    # ATOS-P3-BT-001. A signal derived from bar t fills on bar t+1, not at
    # the close of bar t. The old behaviour filled at the same close the
    # signal was computed from, which is a price nobody could have traded.
    fill_timing: FillTiming = FillTiming.NEXT_BAR_OPEN
    #: Half the bid/ask, paid on every fill. A strategy whose edge is smaller
    #: than the round trip is a losing strategy that used to backtest flat.
    half_spread_pct: float = 0.0005
    #: The most of a bar's volume this backtest will assume it could take.
    max_participation: float = 0.10
    #: Linear impact at full participation, on top of the spread.
    impact_pct: float = 0.002
    #: Refuse to trade a bar that reports no volume, rather than assuming the
    #: bar had infinite liquidity. Missing data is not permission.
    require_volume: bool = True


@dataclass
class BacktestTrade:
    """Record of a single trade."""
    id: str
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    quantity: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    holding_period: int = 0  # In bars
    signal_confidence: float = 0.0
    strategy_name: str = ""

    @property
    def is_closed(self) -> bool:
        return self.exit_time is not None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


@dataclass
class BacktestMetrics:
    """Comprehensive backtest performance metrics."""
    # Returns
    total_return: float = 0.0
    annualized_return: float = 0.0
    benchmark_return: float = 0.0
    alpha: float = 0.0
    beta: float = 0.0

    # Risk metrics
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    var_95: float = 0.0
    expected_shortfall: float = 0.0

    # Trade metrics
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

    # Execution
    total_commission: float = 0.0
    total_slippage: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Total Return": f"{self.total_return:.2%}",
            "Annualized Return": f"{self.annualized_return:.2%}",
            "Volatility": f"{self.volatility:.2%}",
            "Sharpe Ratio": f"{self.sharpe_ratio:.2f}",
            "Sortino Ratio": f"{self.sortino_ratio:.2f}",
            "Max Drawdown": f"{self.max_drawdown:.2%}",
            "Calmar Ratio": f"{self.calmar_ratio:.2f}",
            "Total Trades": self.total_trades,
            "Win Rate": f"{self.win_rate:.2%}",
            "Profit Factor": f"{self.profit_factor:.2f}",
            "Avg Win": f"${self.avg_win:.2f}",
            "Avg Loss": f"${self.avg_loss:.2f}",
            "Largest Win": f"${self.largest_win:.2f}",
            "Largest Loss": f"${self.largest_loss:.2f}",
            "Avg Holding Period": f"{self.avg_holding_period:.1f} bars",
            "Total Commission": f"${self.total_commission:.2f}",
        }


@dataclass
class BacktestResult:
    """Complete backtest results."""
    config: BacktestConfig
    metrics: BacktestMetrics
    trades: List[BacktestTrade]
    equity_curve: List[Tuple[datetime, float]]
    drawdown_curve: List[Tuple[datetime, float]]
    daily_returns: List[float]
    benchmark_curve: Optional[List[Tuple[datetime, float]]] = None

    def summary(self) -> str:
        """Generate text summary of results."""
        lines = [
            "=" * 50,
            "BACKTEST RESULTS",
            "=" * 50,
            "",
            "Performance Metrics:",
            "-" * 30,
        ]

        for key, value in self.metrics.to_dict().items():
            lines.append(f"  {key}: {value}")

        lines.extend([
            "",
            "Configuration:",
            "-" * 30,
            f"  Initial Capital: ${self.config.initial_capital:,.2f}",
            f"  Commission: {self.config.commission_pct:.2%}",
            f"  Position Size: {self.config.position_sizing_pct:.2%}",
            f"  Max Positions: {self.config.max_positions}",
            "",
            "=" * 50,
        ])

        return "\n".join(lines)


class BacktestEngine:
    """
    Event-driven backtesting engine.

    Features:
    - Realistic execution simulation with slippage and commission
    - Multiple strategy support
    - Comprehensive performance analytics
    - Equity curve and drawdown tracking
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.data_feed: Optional[MarketDataFeed] = None
        self.strategies: List[Strategy] = []
        self.ensemble: Optional[StrategyEnsemble] = None

        # State
        self.capital = self.config.initial_capital
        self.positions: Dict[str, BacktestTrade] = {}
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.drawdown_curve: List[Tuple[datetime, float]] = []
        self.daily_returns: List[float] = []

        # Tracking
        self.peak_equity = self.config.initial_capital
        self.current_drawdown = 0.0
        self.bar_count = 0
        self.trade_counter = 0

        # Data buffers
        self.data_buffer: Dict[str, List[MarketData]] = defaultdict(list)

        # ATOS-P3-BT-001: a decision taken on bar t waits here until bar t+1.
        # Holding it in a queue rather than executing inline is what makes the
        # one-bar delay structural instead of a convention someone can forget.
        self.pending: Dict[str, StrategyOutput] = {}
        self.entry_bar: Dict[str, int] = {}
        self.rejected_for_liquidity = 0

        self.fill_model = FillModel(
            costs=FillCosts(
                half_spread_pct=self.config.half_spread_pct,
                commission_pct=self.config.commission_pct,
                slippage_pct=self.config.slippage_pct,
                max_participation=self.config.max_participation,
                impact_pct_at_full_participation=self.config.impact_pct,
            ),
            timing=self.config.fill_timing,
        )

    def set_data_feed(self, feed: MarketDataFeed) -> None:
        """Set the data feed for backtesting."""
        self.data_feed = feed

    def add_strategy(self, strategy: Strategy) -> None:
        """Add a strategy to backtest."""
        self.strategies.append(strategy)

    def set_ensemble(self, ensemble: StrategyEnsemble) -> None:
        """Set strategy ensemble for combined signals."""
        self.ensemble = ensemble

    def run(
        self,
        symbols: List[str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        num_bars: Optional[int] = 1000
    ) -> BacktestResult:
        """
        Run backtest.

        Args:
            symbols: List of symbols to trade
            start_date: Start date (for historical data)
            end_date: End date (for historical data)
            num_bars: Number of bars for simulation

        Returns:
            BacktestResult with all metrics and trades
        """
        if self.data_feed is None:
            # Create simulated feed if none provided
            self.data_feed = SimulatedDataFeed(
                symbols=symbols,
                initial_prices={s: 100.0 for s in symbols}
            )

        self.data_feed.connect()
        self._reset()

        # Run simulation
        for _ in range(num_bars or 1000):
            for symbol in symbols:
                bar = self.data_feed.get_latest_bar(symbol)
                if bar:
                    self._process_bar(symbol, bar)

            if isinstance(self.data_feed, SimulatedDataFeed):
                self.data_feed.advance_time()

        # Close any open positions
        self._close_all_positions()

        self.data_feed.disconnect()

        # Calculate final metrics
        metrics = self._calculate_metrics()

        return BacktestResult(
            config=self.config,
            metrics=metrics,
            trades=self.trades,
            equity_curve=self.equity_curve,
            drawdown_curve=self.drawdown_curve,
            daily_returns=self.daily_returns,
        )

    def _reset(self) -> None:
        """Reset engine state."""
        self.capital = self.config.initial_capital
        self.positions.clear()
        self.trades.clear()
        self.equity_curve.clear()
        self.drawdown_curve.clear()
        self.daily_returns.clear()
        self.peak_equity = self.config.initial_capital
        self.current_drawdown = 0.0
        self.bar_count = 0
        self.trade_counter = 0
        self.data_buffer.clear()
        self.pending.clear()
        self.entry_bar.clear()
        self.rejected_for_liquidity = 0

    def _process_bar(self, symbol: str, bar: OHLCV) -> None:
        """Process a single bar."""
        # Convert OHLCV to MarketData for strategy
        market_data = MarketData(
            asset=symbol,
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume
        )

        self.data_buffer[symbol].append(market_data)
        self.bar_count += 1

        # Update open positions
        self._update_positions(symbol, bar)

        # ATOS-P3-BT-001. Order matters here, and it is the whole fix.
        #
        # First, fill anything decided on the *previous* bar, against this
        # bar. Then look at this bar and decide, which cannot fill until the
        # next one arrives. Previously the engine generated a signal from a
        # buffer that already contained this bar - including its close - and
        # then filled at that same close, which is a price the decision helped
        # produce and nobody could have traded.
        self._execute_pending(symbol, bar)

        # Wait for warmup
        if self.bar_count < self.config.warmup_bars:
            return

        # Generate signals
        signal = self._generate_signal(symbol)

        # Queue for the next bar. Nothing executes against `bar` below here.
        if signal and signal.should_trade:
            self.pending[symbol] = signal

        # Record equity
        equity = self._calculate_equity(symbol, bar.close)
        self.equity_curve.append((bar.timestamp, equity))

        # Update drawdown
        if equity > self.peak_equity:
            self.peak_equity = equity
        self.current_drawdown = (self.peak_equity - equity) / self.peak_equity
        self.drawdown_curve.append((bar.timestamp, self.current_drawdown))

    def _generate_signal(self, symbol: str) -> Optional[StrategyOutput]:
        """Generate trading signal for symbol."""
        data = self.data_buffer[symbol]

        if self.ensemble:
            return self.ensemble.evaluate(data, symbol)

        if self.strategies:
            # Use first strategy if no ensemble
            return self.strategies[0].evaluate(data, symbol)

        return None

    def _execute_pending(self, symbol: str, bar: OHLCV) -> None:
        """Fill the decision taken on the previous bar, against this one."""
        signal = self.pending.pop(symbol, None)
        if signal is not None:
            self._execute_signal(symbol, signal, bar)

    def _execute_signal(self, symbol: str, signal: StrategyOutput, bar: OHLCV) -> None:
        """Execute trade based on signal, against the bar it may fill on."""
        # Check position limits
        if len(self.positions) >= self.config.max_positions and symbol not in self.positions:
            return

        # Determine action
        has_position = symbol in self.positions

        if signal.is_long and not has_position:
            self._open_position(symbol, "long", signal, bar)
        elif not signal.is_long and signal.signal != StrategySignal.HOLD and has_position:
            self._close_position(symbol, bar)
        elif not signal.is_long and signal.signal in [StrategySignal.SELL, StrategySignal.STRONG_SELL] and not has_position:
            # Could implement short selling here
            pass

    def _open_position(self, symbol: str, side: str, signal: StrategyOutput, bar: OHLCV) -> None:
        """Open a new position, filled on `bar` at a price that charges."""
        fill_bar = self._as_fill_bar(bar)
        if self.config.require_volume and fill_bar.volume <= 0:
            # A bar with no recorded volume did not trade. Assuming it would
            # have absorbed an order is how a backtest fills in illiquidity.
            self.rejected_for_liquidity += 1
            return

        reference = self.fill_model.reference_price(fill_bar)
        position_value = self.capital * self.config.position_sizing_pct * signal.confidence
        requested = position_value / reference if reference > 0 else 0.0

        fill = self.fill_model.fill("buy", requested, fill_bar)
        if not fill.filled:
            self.rejected_for_liquidity += 1
            return
        if fill.capped_by_liquidity:
            self.rejected_for_liquidity += 1

        if fill.notional + fill.commission > self.capital:
            return  # Insufficient capital

        self.trade_counter += 1
        trade = BacktestTrade(
            id=f"T{self.trade_counter:05d}",
            symbol=symbol,
            side=side,
            entry_time=bar.timestamp,
            entry_price=fill.price,
            quantity=fill.quantity,
            commission=fill.commission,
            slippage=fill.slippage_per_unit * fill.quantity,
            signal_confidence=signal.confidence,
            strategy_name=signal.metadata.get("strategy", "unknown")
        )

        self.positions[symbol] = trade
        self.entry_bar[symbol] = self.bar_count
        self.capital -= (fill.notional + fill.commission)

    def _close_position(self, symbol: str, bar: OHLCV) -> None:
        """Close an existing position."""
        if symbol not in self.positions:
            return

        trade = self.positions[symbol]
        fill_bar = self._as_fill_bar(bar)
        fill = self.fill_model.fill("sell", trade.quantity, fill_bar)
        if not fill.filled:
            # Cannot get out on this bar. The position stays open and stays
            # exposed, which is what would actually happen.
            self.rejected_for_liquidity += 1
            return

        price = fill.price
        position_value = fill.notional

        trade.exit_time = bar.timestamp
        trade.exit_price = price
        trade.commission += fill.commission
        trade.slippage += fill.slippage_per_unit * fill.quantity

        # Calculate P&L
        gross_pnl = (price - trade.entry_price) * fill.quantity
        trade.pnl = gross_pnl - trade.commission
        trade.pnl_pct = trade.pnl / (trade.entry_price * trade.quantity)
        # Bars held, not the global bar count - which is what this used to
        # record, making every trade look longer than the last.
        trade.holding_period = self.bar_count - self.entry_bar.get(symbol, self.bar_count)

        self.capital += position_value - fill.commission
        self.trades.append(trade)
        del self.positions[symbol]
        self.entry_bar.pop(symbol, None)

    def _as_fill_bar(self, bar: OHLCV) -> Bar:
        return Bar(
            open=bar.open, high=bar.high, low=bar.low, close=bar.close,
            volume=getattr(bar, "volume", 0.0) or 0.0,
        )

    def _update_positions(self, symbol: str, bar: OHLCV) -> None:
        """Update open positions with current prices."""
        if symbol in self.positions:
            trade = self.positions[symbol]
            trade.holding_period = self.bar_count

    def _close_all_positions(self) -> None:
        """Close all open positions at end of backtest.

        Anything still queued is discarded rather than filled: a decision
        taken on the final bar has no next bar to fill against, and inventing
        one is the same lookahead this engine was just fixed for.
        """
        self.pending.clear()

        for symbol in list(self.positions.keys()):
            data = self.data_buffer.get(symbol, [])
            if data:
                last_bar = OHLCV(
                    timestamp=data[-1].timestamp,
                    open=data[-1].open,
                    high=data[-1].high,
                    low=data[-1].low,
                    close=data[-1].close,
                    volume=data[-1].volume
                )
                self._close_position(symbol, last_bar)

    def _calculate_equity(self, symbol: str, current_price: float) -> float:
        """Calculate total equity including open positions."""
        equity = self.capital

        for sym, trade in self.positions.items():
            if sym == symbol:
                equity += trade.quantity * current_price
            else:
                # Use last known price
                data = self.data_buffer.get(sym, [])
                if data:
                    equity += trade.quantity * data[-1].close

        return equity

    def _calculate_metrics(self) -> BacktestMetrics:
        """Calculate comprehensive performance metrics."""
        metrics = BacktestMetrics()

        if not self.trades:
            return metrics

        # Trade statistics
        metrics.total_trades = len(self.trades)
        winners = [t for t in self.trades if t.is_winner]
        losers = [t for t in self.trades if not t.is_winner]

        metrics.winning_trades = len(winners)
        metrics.losing_trades = len(losers)
        metrics.win_rate = len(winners) / len(self.trades) if self.trades else 0

        # P&L statistics
        pnls = [t.pnl for t in self.trades]
        win_pnls = [t.pnl for t in winners]
        loss_pnls = [abs(t.pnl) for t in losers]

        metrics.avg_trade = np.mean(pnls) if pnls else 0
        metrics.avg_win = np.mean(win_pnls) if win_pnls else 0
        metrics.avg_loss = np.mean(loss_pnls) if loss_pnls else 0
        metrics.largest_win = max(pnls) if pnls else 0
        metrics.largest_loss = min(pnls) if pnls else 0

        # Profit factor
        gross_profit = sum(win_pnls) if win_pnls else 0
        gross_loss = sum(loss_pnls) if loss_pnls else 0
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Holding period
        holding_periods = [t.holding_period for t in self.trades]
        metrics.avg_holding_period = np.mean(holding_periods) if holding_periods else 0

        # Costs
        metrics.total_commission = sum(t.commission for t in self.trades)
        metrics.total_slippage = sum(t.slippage for t in self.trades)

        # Returns
        if self.equity_curve:
            final_equity = self.equity_curve[-1][1]
            metrics.total_return = (final_equity - self.config.initial_capital) / self.config.initial_capital

            # Annualized return (assuming daily bars)
            num_years = len(self.equity_curve) / 252
            if num_years > 0:
                metrics.annualized_return = (1 + metrics.total_return) ** (1 / num_years) - 1

        # Calculate returns series
        equity_values = [e[1] for e in self.equity_curve]
        if len(equity_values) > 1:
            returns = [(equity_values[i] - equity_values[i-1]) / equity_values[i-1]
                       for i in range(1, len(equity_values))]
            self.daily_returns = returns

            # Volatility
            metrics.volatility = np.std(returns) * np.sqrt(252) if returns else 0

            # Sharpe ratio
            if metrics.volatility > 0:
                excess_return = metrics.annualized_return - self.config.risk_free_rate
                metrics.sharpe_ratio = excess_return / metrics.volatility

            # Sortino ratio
            downside_returns = [r for r in returns if r < 0]
            if downside_returns:
                downside_std = np.std(downside_returns) * np.sqrt(252)
                if downside_std > 0:
                    metrics.sortino_ratio = (metrics.annualized_return - self.config.risk_free_rate) / downside_std

            # VaR and Expected Shortfall
            sorted_returns = sorted(returns)
            var_idx = int(len(sorted_returns) * 0.05)
            if var_idx > 0:
                metrics.var_95 = abs(sorted_returns[var_idx])
                metrics.expected_shortfall = abs(np.mean(sorted_returns[:var_idx]))

        # Drawdown
        if self.drawdown_curve:
            drawdowns = [d[1] for d in self.drawdown_curve]
            metrics.max_drawdown = max(drawdowns) if drawdowns else 0

            # Calmar ratio
            if metrics.max_drawdown > 0:
                metrics.calmar_ratio = metrics.annualized_return / metrics.max_drawdown

            # Max drawdown duration
            in_drawdown = False
            current_duration = 0
            max_duration = 0
            for dd in drawdowns:
                if dd > 0:
                    in_drawdown = True
                    current_duration += 1
                else:
                    if in_drawdown:
                        max_duration = max(max_duration, current_duration)
                        current_duration = 0
                        in_drawdown = False
            metrics.max_drawdown_duration = max(max_duration, current_duration)

        return metrics


def run_quick_backtest(
    strategy: Strategy,
    symbols: List[str],
    num_bars: int = 500,
    initial_capital: float = 10000
) -> BacktestResult:
    """
    Convenience function for quick backtesting.

    Args:
        strategy: Strategy to backtest
        symbols: List of symbols
        num_bars: Number of bars to simulate
        initial_capital: Starting capital

    Returns:
        BacktestResult
    """
    config = BacktestConfig(initial_capital=initial_capital)
    engine = BacktestEngine(config)
    engine.add_strategy(strategy)

    return engine.run(symbols, num_bars=num_bars)


if __name__ == "__main__":
    from core.strategy import MomentumStrategy, create_default_ensemble

    print("Running Backtest...")
    print("=" * 50)

    # Test with single strategy
    strategy = MomentumStrategy(lookback=14, threshold=0.02)
    result = run_quick_backtest(
        strategy=strategy,
        symbols=["BTC", "ETH"],
        num_bars=500,
        initial_capital=10000
    )

    print(result.summary())

    # Test with ensemble
    print("\n\nRunning Ensemble Backtest...")
    print("=" * 50)

    config = BacktestConfig(initial_capital=10000)
    engine = BacktestEngine(config)
    engine.set_ensemble(create_default_ensemble())

    result = engine.run(["BTC", "ETH", "ADA"], num_bars=500)
    print(result.summary())

    # Print trade samples
    print("\nSample Trades:")
    for trade in result.trades[:5]:
        print(f"  {trade.symbol}: {trade.side} @ {trade.entry_price:.2f} -> "
              f"{trade.exit_price:.2f if trade.exit_price else 'Open'}, "
              f"P&L: ${trade.pnl:.2f}")
