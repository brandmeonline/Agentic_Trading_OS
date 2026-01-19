"""
Unified Trading System Integration Layer.

Connects all components into a cohesive trading platform:
- Data flow orchestration
- Signal generation pipeline
- Trade execution workflow
- Risk monitoring integration
- Performance tracking
"""

from __future__ import annotations

import numpy as np
import threading
import queue
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from datetime import datetime
import json


# =============================================================================
# System State and Configuration
# =============================================================================

class SystemMode(Enum):
    """Trading system operation modes."""
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"
    RESEARCH = "research"


class SignalSource(Enum):
    """Sources of trading signals."""
    STRATEGY = "strategy"
    DEEP_LEARNING = "deep_learning"
    NLP_SENTIMENT = "nlp_sentiment"
    ENSEMBLE = "ensemble"
    MANUAL = "manual"


@dataclass
class SystemConfig:
    """Unified system configuration."""
    mode: SystemMode = SystemMode.PAPER
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    base_currency: str = "USDT"
    initial_capital: float = 100000.0
    max_position_size: float = 0.20  # 20% of capital per position
    max_total_exposure: float = 1.0  # 100% max exposure
    risk_per_trade: float = 0.02  # 2% risk per trade
    enable_deep_learning: bool = True
    enable_nlp: bool = True
    enable_smart_routing: bool = True
    enable_monitoring: bool = True
    rebalance_frequency: str = "daily"  # daily, hourly, real-time
    signal_threshold: float = 0.6  # Minimum confidence for signals
    stop_loss_pct: float = 0.05  # 5% stop loss
    take_profit_pct: float = 0.15  # 15% take profit


@dataclass
class Position:
    """Active trading position."""
    symbol: str
    side: str  # 'long' or 'short'
    entry_price: float
    quantity: float
    entry_time: datetime
    stop_loss: float
    take_profit: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    signal_source: SignalSource = SignalSource.STRATEGY


@dataclass
class TradeSignal:
    """Trading signal from any source."""
    symbol: str
    action: str  # 'buy', 'sell', 'hold'
    confidence: float
    source: SignalSource
    timestamp: datetime
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemState:
    """Current system state."""
    mode: SystemMode
    capital: float
    positions: Dict[str, Position]
    pending_signals: List[TradeSignal]
    recent_trades: List[Dict]
    pnl_history: List[float]
    last_update: datetime
    is_trading_enabled: bool = True
    circuit_breaker_active: bool = False
    error_count: int = 0


# =============================================================================
# Signal Aggregator
# =============================================================================

class SignalAggregator:
    """
    Aggregates signals from multiple sources into unified trading decisions.

    Combines:
    - Traditional strategy signals
    - Deep learning predictions
    - NLP sentiment signals
    - Technical indicators
    """

    def __init__(self, config: SystemConfig):
        self.config = config
        self.weights = {
            SignalSource.STRATEGY: 0.25,
            SignalSource.DEEP_LEARNING: 0.35,
            SignalSource.NLP_SENTIMENT: 0.20,
            SignalSource.ENSEMBLE: 0.20
        }

    def aggregate_signals(
        self,
        signals: List[TradeSignal]
    ) -> Optional[TradeSignal]:
        """
        Aggregate multiple signals into a single trading decision.

        Args:
            signals: List of signals from different sources

        Returns:
            Aggregated signal or None if no clear consensus
        """
        if not signals:
            return None

        # Group by symbol
        by_symbol: Dict[str, List[TradeSignal]] = {}
        for signal in signals:
            if signal.symbol not in by_symbol:
                by_symbol[signal.symbol] = []
            by_symbol[signal.symbol].append(signal)

        aggregated_signals = []

        for symbol, symbol_signals in by_symbol.items():
            # Calculate weighted consensus
            buy_score = 0.0
            sell_score = 0.0
            hold_score = 0.0
            total_weight = 0.0

            for signal in symbol_signals:
                weight = self.weights.get(signal.source, 0.1) * signal.confidence

                if signal.action == "buy":
                    buy_score += weight
                elif signal.action == "sell":
                    sell_score += weight
                else:
                    hold_score += weight

                total_weight += weight

            if total_weight == 0:
                continue

            # Normalize scores
            buy_score /= total_weight
            sell_score /= total_weight
            hold_score /= total_weight

            # Determine consensus action
            max_score = max(buy_score, sell_score, hold_score)

            if max_score < self.config.signal_threshold:
                action = "hold"
                confidence = hold_score
            elif buy_score == max_score:
                action = "buy"
                confidence = buy_score
            elif sell_score == max_score:
                action = "sell"
                confidence = sell_score
            else:
                action = "hold"
                confidence = hold_score

            # Create aggregated signal
            aggregated = TradeSignal(
                symbol=symbol,
                action=action,
                confidence=confidence,
                source=SignalSource.ENSEMBLE,
                timestamp=datetime.now(),
                metadata={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "hold_score": hold_score,
                    "n_signals": len(symbol_signals),
                    "sources": [s.source.value for s in symbol_signals]
                }
            )

            # Set price targets (average of contributing signals)
            target_prices = [s.target_price for s in symbol_signals if s.target_price]
            if target_prices:
                aggregated.target_price = np.mean(target_prices)

            stop_losses = [s.stop_loss for s in symbol_signals if s.stop_loss]
            if stop_losses:
                aggregated.stop_loss = np.mean(stop_losses)

            aggregated_signals.append(aggregated)

        # Return highest confidence signal
        if aggregated_signals:
            return max(aggregated_signals, key=lambda s: s.confidence)

        return None


# =============================================================================
# Trade Executor
# =============================================================================

class TradeExecutor:
    """
    Executes trades based on signals.

    Handles:
    - Order creation
    - Position sizing
    - Risk checks
    - Execution routing
    """

    def __init__(self, config: SystemConfig):
        self.config = config
        self.pending_orders: List[Dict] = []

    def create_order(
        self,
        signal: TradeSignal,
        current_price: float,
        portfolio_value: float,
        current_positions: Dict[str, Position]
    ) -> Optional[Dict]:
        """
        Create order from signal.

        Args:
            signal: Trading signal
            current_price: Current market price
            portfolio_value: Total portfolio value
            current_positions: Current positions

        Returns:
            Order dict or None if order shouldn't be placed
        """
        # Check if already have position
        existing_position = current_positions.get(signal.symbol)

        if signal.action == "hold":
            return None

        # Calculate position size using Kelly Criterion approximation
        # f = (p * b - q) / b where p = win prob, q = loss prob, b = win/loss ratio
        win_prob = signal.confidence
        win_loss_ratio = self.config.take_profit_pct / self.config.stop_loss_pct
        kelly_fraction = (win_prob * win_loss_ratio - (1 - win_prob)) / win_loss_ratio
        kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%

        # Risk-based position size
        risk_capital = portfolio_value * self.config.risk_per_trade
        position_size = risk_capital / (current_price * self.config.stop_loss_pct)

        # Apply Kelly scaling
        position_size *= kelly_fraction / 0.25 if kelly_fraction > 0 else 0.5

        # Apply max position size limit
        max_position = portfolio_value * self.config.max_position_size / current_price
        position_size = min(position_size, max_position)

        if position_size <= 0:
            return None

        # Determine order type
        if signal.action == "buy":
            if existing_position and existing_position.side == "short":
                # Close short first
                order_type = "close_short"
                quantity = existing_position.quantity
            else:
                order_type = "open_long"
                quantity = position_size

        elif signal.action == "sell":
            if existing_position and existing_position.side == "long":
                # Close long first
                order_type = "close_long"
                quantity = existing_position.quantity
            else:
                order_type = "open_short"
                quantity = position_size

        else:
            return None

        # Calculate stop loss and take profit
        if signal.action == "buy":
            stop_loss = signal.stop_loss or current_price * (1 - self.config.stop_loss_pct)
            take_profit = signal.take_profit or current_price * (1 + self.config.take_profit_pct)
        else:
            stop_loss = signal.stop_loss or current_price * (1 + self.config.stop_loss_pct)
            take_profit = signal.take_profit or current_price * (1 - self.config.take_profit_pct)

        order = {
            "symbol": signal.symbol,
            "order_type": order_type,
            "quantity": quantity,
            "price": current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "signal_source": signal.source.value,
            "confidence": signal.confidence,
            "timestamp": datetime.now().isoformat()
        }

        return order

    def execute_order(
        self,
        order: Dict,
        state: SystemState
    ) -> Tuple[bool, Optional[Position], Optional[Dict]]:
        """
        Execute an order (simulated).

        Returns:
            Tuple of (success, new_position, trade_record)
        """
        # Simulate execution with slippage
        slippage = np.random.uniform(0.0001, 0.001)  # 0.01% to 0.1%
        if "long" in order["order_type"]:
            executed_price = order["price"] * (1 + slippage)
        else:
            executed_price = order["price"] * (1 - slippage)

        # Create position
        if "open" in order["order_type"]:
            side = "long" if "long" in order["order_type"] else "short"
            position = Position(
                symbol=order["symbol"],
                side=side,
                entry_price=executed_price,
                quantity=order["quantity"],
                entry_time=datetime.now(),
                stop_loss=order["stop_loss"],
                take_profit=order["take_profit"],
                signal_source=SignalSource(order["signal_source"])
            )

            trade_record = {
                "type": "open",
                "symbol": order["symbol"],
                "side": side,
                "price": executed_price,
                "quantity": order["quantity"],
                "timestamp": datetime.now().isoformat()
            }

            return True, position, trade_record

        elif "close" in order["order_type"]:
            # Close position
            existing = state.positions.get(order["symbol"])
            if existing:
                # Calculate PnL
                if existing.side == "long":
                    pnl = (executed_price - existing.entry_price) * existing.quantity
                else:
                    pnl = (existing.entry_price - executed_price) * existing.quantity

                trade_record = {
                    "type": "close",
                    "symbol": order["symbol"],
                    "side": existing.side,
                    "entry_price": existing.entry_price,
                    "exit_price": executed_price,
                    "quantity": existing.quantity,
                    "pnl": pnl,
                    "timestamp": datetime.now().isoformat()
                }

                return True, None, trade_record

        return False, None, None


# =============================================================================
# Risk Controller
# =============================================================================

class RiskController:
    """
    Real-time risk monitoring and control.

    Manages:
    - Position limits
    - Drawdown limits
    - Circuit breakers
    - Exposure monitoring
    """

    def __init__(self, config: SystemConfig):
        self.config = config
        self.max_daily_loss: float = 0.05  # 5% max daily loss
        self.max_drawdown: float = 0.15  # 15% max drawdown
        self.daily_pnl: float = 0.0
        self.peak_capital: float = config.initial_capital
        self.circuit_breaker_triggered: bool = False

    def check_risk_limits(self, state: SystemState) -> Dict[str, Any]:
        """
        Check all risk limits.

        Returns dict with risk status and any breaches.
        """
        breaches = []

        # Calculate total exposure
        total_exposure = sum(
            pos.quantity * pos.entry_price
            for pos in state.positions.values()
        ) / state.capital

        if total_exposure > self.config.max_total_exposure:
            breaches.append({
                "type": "exposure",
                "limit": self.config.max_total_exposure,
                "current": total_exposure
            })

        # Check drawdown
        current_drawdown = 1 - state.capital / self.peak_capital
        if current_drawdown > self.max_drawdown:
            breaches.append({
                "type": "drawdown",
                "limit": self.max_drawdown,
                "current": current_drawdown
            })
            self.circuit_breaker_triggered = True

        # Check daily loss
        if len(state.pnl_history) > 0:
            daily_pnl = sum(state.pnl_history[-252:]) / state.capital if len(state.pnl_history) >= 1 else 0
            if daily_pnl < -self.max_daily_loss:
                breaches.append({
                    "type": "daily_loss",
                    "limit": -self.max_daily_loss,
                    "current": daily_pnl
                })

        # Update peak capital
        if state.capital > self.peak_capital:
            self.peak_capital = state.capital

        return {
            "is_safe": len(breaches) == 0,
            "breaches": breaches,
            "total_exposure": total_exposure,
            "current_drawdown": current_drawdown,
            "circuit_breaker": self.circuit_breaker_triggered
        }

    def should_reduce_position(
        self,
        position: Position,
        current_price: float
    ) -> Tuple[bool, str]:
        """
        Check if position should be reduced or closed.

        Returns:
            Tuple of (should_reduce, reason)
        """
        # Check stop loss
        if position.side == "long":
            if current_price <= position.stop_loss:
                return True, "stop_loss"
            if current_price >= position.take_profit:
                return True, "take_profit"
        else:
            if current_price >= position.stop_loss:
                return True, "stop_loss"
            if current_price <= position.take_profit:
                return True, "take_profit"

        return False, ""


# =============================================================================
# Unified Trading Engine
# =============================================================================

class UnifiedTradingEngine:
    """
    Main unified trading engine orchestrating all components.

    Integrates:
    - Data feeds
    - Feature engineering
    - Signal generation (strategies, DL, NLP)
    - Trade execution
    - Risk management
    - Performance monitoring
    """

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()

        # Initialize components
        self.signal_aggregator = SignalAggregator(self.config)
        self.executor = TradeExecutor(self.config)
        self.risk_controller = RiskController(self.config)

        # System state
        self.state = SystemState(
            mode=self.config.mode,
            capital=self.config.initial_capital,
            positions={},
            pending_signals=[],
            recent_trades=[],
            pnl_history=[],
            last_update=datetime.now()
        )

        # Signal queues
        self._signal_queue: queue.Queue = queue.Queue()
        self._running = False
        self._lock = threading.Lock()

        # Performance tracking
        self.metrics = {
            "total_trades": 0,
            "winning_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0
        }

    def start(self):
        """Start the trading engine."""
        self._running = True

        # Start signal processing thread
        self._signal_thread = threading.Thread(target=self._process_signals, daemon=True)
        self._signal_thread.start()

        # Start position monitoring thread
        self._monitor_thread = threading.Thread(target=self._monitor_positions, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        """Stop the trading engine."""
        self._running = False

    def add_signal(self, signal: TradeSignal):
        """Add a trading signal to the queue."""
        self._signal_queue.put(signal)

    def update_market_data(
        self,
        symbol: str,
        price: float,
        volume: float,
        timestamp: Optional[datetime] = None
    ):
        """Update market data and check positions."""
        timestamp = timestamp or datetime.now()

        with self._lock:
            # Update unrealized PnL
            if symbol in self.state.positions:
                pos = self.state.positions[symbol]
                if pos.side == "long":
                    pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity

                # Check stop loss / take profit
                should_close, reason = self.risk_controller.should_reduce_position(pos, price)
                if should_close:
                    self._close_position(symbol, price, reason)

            self.state.last_update = timestamp

    def _process_signals(self):
        """Background thread for processing signals."""
        signal_batch: List[TradeSignal] = []

        while self._running:
            try:
                # Collect signals with timeout
                try:
                    signal = self._signal_queue.get(timeout=0.1)
                    signal_batch.append(signal)
                except queue.Empty:
                    pass

                # Process batch when we have signals or periodically
                if signal_batch and (len(signal_batch) >= 3 or time.time() % 5 < 0.1):
                    # Aggregate signals
                    aggregated = self.signal_aggregator.aggregate_signals(signal_batch)

                    if aggregated and aggregated.action != "hold":
                        self._execute_signal(aggregated)

                    signal_batch = []

            except Exception:
                time.sleep(1)

    def _execute_signal(self, signal: TradeSignal):
        """Execute a trading signal."""
        with self._lock:
            # Check risk limits
            risk_status = self.risk_controller.check_risk_limits(self.state)

            if not risk_status["is_safe"]:
                # Log risk breach
                return

            if self.state.circuit_breaker_active:
                return

            # Get current price (would come from market data in production)
            current_price = signal.target_price or 100.0

            # Create order
            order = self.executor.create_order(
                signal=signal,
                current_price=current_price,
                portfolio_value=self.state.capital,
                current_positions=self.state.positions
            )

            if order:
                # Execute order
                success, position, trade_record = self.executor.execute_order(order, self.state)

                if success:
                    if position:
                        self.state.positions[signal.symbol] = position
                    elif signal.symbol in self.state.positions:
                        del self.state.positions[signal.symbol]

                    if trade_record:
                        self.state.recent_trades.append(trade_record)
                        self._update_metrics(trade_record)

    def _close_position(self, symbol: str, price: float, reason: str):
        """Close a position."""
        if symbol not in self.state.positions:
            return

        pos = self.state.positions[symbol]

        # Calculate PnL
        if pos.side == "long":
            pnl = (price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - price) * pos.quantity

        # Update capital
        self.state.capital += pnl
        self.state.pnl_history.append(pnl)

        # Record trade
        trade_record = {
            "type": "close",
            "symbol": symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "quantity": pos.quantity,
            "pnl": pnl,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        self.state.recent_trades.append(trade_record)

        # Remove position
        del self.state.positions[symbol]

        self._update_metrics(trade_record)

    def _monitor_positions(self):
        """Background thread for monitoring positions."""
        while self._running:
            try:
                with self._lock:
                    # Check risk limits
                    risk_status = self.risk_controller.check_risk_limits(self.state)

                    if risk_status["circuit_breaker"]:
                        self.state.circuit_breaker_active = True

                time.sleep(1)

            except Exception:
                time.sleep(5)

    def _update_metrics(self, trade_record: Dict):
        """Update performance metrics."""
        self.metrics["total_trades"] += 1

        if "pnl" in trade_record:
            pnl = trade_record["pnl"]
            self.metrics["total_pnl"] += pnl

            if pnl > 0:
                self.metrics["winning_trades"] += 1

        # Update Sharpe ratio
        if len(self.state.pnl_history) > 20:
            returns = np.array(self.state.pnl_history[-252:])
            if np.std(returns) > 0:
                self.metrics["sharpe_ratio"] = np.mean(returns) / np.std(returns) * np.sqrt(252)

        # Update max drawdown
        cumulative = np.cumsum(self.state.pnl_history)
        if len(cumulative) > 0:
            peak = np.maximum.accumulate(cumulative + self.config.initial_capital)
            drawdown = (peak - (cumulative + self.config.initial_capital)) / peak
            self.metrics["max_drawdown"] = max(self.metrics["max_drawdown"], np.max(drawdown))

    def get_status(self) -> Dict:
        """Get current system status."""
        with self._lock:
            total_unrealized = sum(pos.unrealized_pnl for pos in self.state.positions.values())

            return {
                "mode": self.state.mode.value,
                "capital": self.state.capital,
                "unrealized_pnl": total_unrealized,
                "total_equity": self.state.capital + total_unrealized,
                "n_positions": len(self.state.positions),
                "positions": {
                    symbol: {
                        "side": pos.side,
                        "entry_price": pos.entry_price,
                        "quantity": pos.quantity,
                        "unrealized_pnl": pos.unrealized_pnl
                    }
                    for symbol, pos in self.state.positions.items()
                },
                "is_trading": self.state.is_trading_enabled,
                "circuit_breaker": self.state.circuit_breaker_active,
                "metrics": self.metrics,
                "last_update": self.state.last_update.isoformat()
            }

    def get_performance_report(self) -> Dict:
        """Get comprehensive performance report."""
        with self._lock:
            if not self.state.pnl_history:
                return {"message": "No trades yet"}

            pnl_array = np.array(self.state.pnl_history)

            return {
                "total_trades": self.metrics["total_trades"],
                "winning_trades": self.metrics["winning_trades"],
                "win_rate": self.metrics["winning_trades"] / max(1, self.metrics["total_trades"]),
                "total_pnl": self.metrics["total_pnl"],
                "average_pnl": np.mean(pnl_array),
                "pnl_std": np.std(pnl_array),
                "max_win": np.max(pnl_array) if len(pnl_array) > 0 else 0,
                "max_loss": np.min(pnl_array) if len(pnl_array) > 0 else 0,
                "sharpe_ratio": self.metrics["sharpe_ratio"],
                "max_drawdown": self.metrics["max_drawdown"],
                "profit_factor": abs(np.sum(pnl_array[pnl_array > 0]) / np.sum(pnl_array[pnl_array < 0])) if np.sum(pnl_array[pnl_array < 0]) != 0 else float('inf'),
                "recent_trades": self.state.recent_trades[-10:]
            }


# =============================================================================
# Factory Functions
# =============================================================================

def create_trading_system(
    mode: str = "paper",
    symbols: Optional[List[str]] = None,
    initial_capital: float = 100000.0,
    **kwargs
) -> UnifiedTradingEngine:
    """
    Factory function to create a configured trading system.

    Args:
        mode: Trading mode ('backtest', 'paper', 'live', 'research')
        symbols: List of symbols to trade
        initial_capital: Starting capital
        **kwargs: Additional configuration options

    Returns:
        Configured UnifiedTradingEngine
    """
    config = SystemConfig(
        mode=SystemMode(mode),
        symbols=symbols or ["BTC/USDT", "ETH/USDT"],
        initial_capital=initial_capital,
        **{k: v for k, v in kwargs.items() if hasattr(SystemConfig, k)}
    )

    return UnifiedTradingEngine(config)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create trading system
    engine = create_trading_system(
        mode="paper",
        symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        initial_capital=100000.0,
        max_position_size=0.25,
        risk_per_trade=0.02
    )

    print("Starting Unified Trading Engine...")
    engine.start()

    # Simulate signals
    print("\nSimulating trading signals...")

    test_signals = [
        TradeSignal("BTC/USDT", "buy", 0.75, SignalSource.STRATEGY, datetime.now(), target_price=45000),
        TradeSignal("BTC/USDT", "buy", 0.82, SignalSource.DEEP_LEARNING, datetime.now(), target_price=46000),
        TradeSignal("BTC/USDT", "hold", 0.55, SignalSource.NLP_SENTIMENT, datetime.now()),
        TradeSignal("ETH/USDT", "buy", 0.70, SignalSource.STRATEGY, datetime.now(), target_price=2500),
        TradeSignal("ETH/USDT", "buy", 0.78, SignalSource.DEEP_LEARNING, datetime.now(), target_price=2600),
    ]

    for signal in test_signals:
        engine.add_signal(signal)
        print(f"  Added signal: {signal.symbol} {signal.action} ({signal.source.value})")
        time.sleep(0.1)

    # Wait for processing
    time.sleep(2)

    # Get status
    print("\nSystem Status:")
    status = engine.get_status()
    print(f"  Mode: {status['mode']}")
    print(f"  Capital: ${status['capital']:,.2f}")
    print(f"  Positions: {status['n_positions']}")
    print(f"  Circuit Breaker: {status['circuit_breaker']}")

    for symbol, pos in status['positions'].items():
        print(f"    {symbol}: {pos['side']} @ {pos['entry_price']:.2f}")

    # Simulate market updates
    print("\nSimulating market updates...")
    engine.update_market_data("BTC/USDT", 46500, 1000)
    engine.update_market_data("ETH/USDT", 2650, 500)

    time.sleep(1)

    # Get performance report
    print("\nPerformance Report:")
    report = engine.get_performance_report()
    for key, value in report.items():
        if key != "recent_trades":
            if isinstance(value, float):
                print(f"  {key}: {value:.4f}")
            else:
                print(f"  {key}: {value}")

    # Stop engine
    print("\nStopping engine...")
    engine.stop()
    print("Done!")
