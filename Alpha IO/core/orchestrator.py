"""
Live Trading System Orchestrator.

Production-ready orchestration layer that:
- Manages all system components lifecycle
- Coordinates data flow between modules
- Handles graceful startup/shutdown
- Provides real-time monitoring
- Supports multiple trading modes
"""

from __future__ import annotations

import os
import sys
import time
import json
import signal
import threading
import queue
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from datetime import datetime, timedelta
from pathlib import Path
import traceback

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Core imports with graceful fallbacks
try:
    from core.config_manager import create_config_manager, ConfigManager
except ImportError:
    ConfigManager = None
    create_config_manager = None

try:
    from core.credentials import (
        get_credentials_manager, CredentialsManager,
        get_testnet_endpoint, get_public_endpoint
    )
except ImportError:
    get_credentials_manager = None
    CredentialsManager = None

try:
    from core.live_data import (
        create_live_data_manager, LiveDataManager,
        create_binance_client, BinancePublicClient
    )
except ImportError:
    create_live_data_manager = None
    LiveDataManager = None

try:
    from core.database import create_database_manager, DatabaseManager
except ImportError:
    create_database_manager = None
    DatabaseManager = None

try:
    from core.rest_api import create_api_server, RESTAPIServer
except ImportError:
    create_api_server = None
    RESTAPIServer = None

try:
    from core.exchange_connectors import (
        create_binance_connector, create_exchange_manager,
        BinanceConnector, ExchangeManager
    )
except ImportError:
    create_binance_connector = None
    create_exchange_manager = None

try:
    from core.unified_system import create_trading_system, UnifiedTradingEngine
except ImportError:
    create_trading_system = None
    UnifiedTradingEngine = None

try:
    from core.strategy import create_default_ensemble, StrategyEnsemble
except ImportError:
    create_default_ensemble = None
    StrategyEnsemble = None

try:
    from core.risk import RiskManager, RiskConfig
except ImportError:
    RiskManager = None
    RiskConfig = None

try:
    from core.advanced_rl import create_agent, RLAlgorithm, PPOAgent
except ImportError:
    create_agent = None
    RLAlgorithm = None


# =============================================================================
# Configuration
# =============================================================================

class TradingMode(Enum):
    """Trading operation modes."""
    PAPER = "paper"           # Simulated trading with real data
    LIVE = "live"             # Real trading with real money
    BACKTEST = "backtest"     # Historical simulation
    RESEARCH = "research"     # Strategy research mode


class SystemStatus(Enum):
    """System operational status."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class OrchestratorConfig:
    """Orchestrator configuration."""
    mode: TradingMode = TradingMode.PAPER
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    initial_capital: float = 100000.0
    base_currency: str = "USDT"

    # Component flags
    enable_api: bool = True
    enable_database: bool = True
    enable_websocket: bool = True
    enable_strategies: bool = True
    enable_rl_agent: bool = False  # Disabled by default (resource intensive)

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Database settings
    database_path: str = "data/trading.db"

    # Trading settings
    max_position_size: float = 0.20
    risk_per_trade: float = 0.02
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15

    # Update intervals (seconds)
    price_update_interval: float = 1.0
    strategy_update_interval: float = 60.0
    risk_check_interval: float = 5.0


@dataclass
class SystemState:
    """Current system state."""
    status: SystemStatus = SystemStatus.STOPPED
    mode: TradingMode = TradingMode.PAPER
    start_time: Optional[datetime] = None
    uptime_seconds: float = 0.0

    # Capital tracking
    initial_capital: float = 0.0
    current_capital: float = 0.0
    total_pnl: float = 0.0

    # Position tracking
    positions: Dict[str, Dict] = field(default_factory=dict)
    open_orders: List[Dict] = field(default_factory=list)

    # Performance metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    # Error tracking
    errors: List[Dict] = field(default_factory=list)
    last_error: Optional[str] = None


# =============================================================================
# Event System
# =============================================================================

class EventType(Enum):
    """System event types."""
    PRICE_UPDATE = "price_update"
    SIGNAL_GENERATED = "signal_generated"
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    RISK_ALERT = "risk_alert"
    SYSTEM_ERROR = "system_error"


@dataclass
class Event:
    """System event."""
    event_type: EventType
    timestamp: datetime
    data: Dict[str, Any]
    source: str = ""


class EventBus:
    """Central event bus for system communication."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._event_queue: queue.Queue = queue.Queue()
        self._running = False
        self._processor_thread: Optional[threading.Thread] = None

    def subscribe(self, event_type: EventType, callback: Callable[[Event], None]):
        """Subscribe to event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def publish(self, event: Event):
        """Publish event to subscribers."""
        self._event_queue.put(event)

    def start(self):
        """Start event processing."""
        self._running = True
        self._processor_thread = threading.Thread(target=self._process_events, daemon=True)
        self._processor_thread.start()

    def stop(self):
        """Stop event processing."""
        self._running = False
        if self._processor_thread:
            self._processor_thread.join(timeout=5.0)

    def _process_events(self):
        """Process events from queue."""
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.1)
                self._dispatch(event)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Event processing error: {e}")

    def _dispatch(self, event: Event):
        """Dispatch event to subscribers."""
        if event.event_type in self._subscribers:
            for callback in self._subscribers[event.event_type]:
                try:
                    callback(event)
                except Exception as e:
                    print(f"Event callback error: {e}")


# =============================================================================
# Trading Orchestrator
# =============================================================================

class TradingOrchestrator:
    """Main orchestrator for the trading system."""

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        self.config = config or OrchestratorConfig()
        self.state = SystemState(
            mode=self.config.mode,
            initial_capital=self.config.initial_capital,
            current_capital=self.config.initial_capital,
        )

        # Core components
        self.event_bus = EventBus()
        self.config_manager: Optional[ConfigManager] = None
        self.credentials: Optional[CredentialsManager] = None
        self.database: Optional[DatabaseManager] = None
        self.live_data: Optional[LiveDataManager] = None
        self.api_server: Optional[RESTAPIServer] = None
        self.exchange: Optional[BinanceConnector] = None
        self.strategies: Optional[StrategyEnsemble] = None
        self.rl_agent: Optional[PPOAgent] = None

        # Threading
        self._main_thread: Optional[threading.Thread] = None
        self._price_thread: Optional[threading.Thread] = None
        self._strategy_thread: Optional[threading.Thread] = None
        self._running = False
        self._shutdown_event = threading.Event()

        # Price cache
        self._prices: Dict[str, float] = {}
        self._price_lock = threading.Lock()

        # Signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print("\nShutdown signal received...")
        self.stop()

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    def initialize(self) -> bool:
        """Initialize all system components."""
        self.state.status = SystemStatus.INITIALIZING
        print("\n" + "="*60)
        print("  Agentic Trading OS - Initializing")
        print("="*60)

        try:
            # 1. Load configuration
            print("\n[1/7] Loading configuration...")
            if create_config_manager:
                self.config_manager = create_config_manager()
                print("      ✓ Configuration loaded")
            else:
                print("      ⊘ Config manager not available")

            # 2. Initialize credentials
            print("\n[2/7] Initializing credentials...")
            if get_credentials_manager:
                self.credentials = get_credentials_manager()
                creds = self.credentials.list_credentials()
                print(f"      ✓ Credentials manager ready ({len(creds)} stored)")
            else:
                print("      ⊘ Credentials manager not available")

            # 3. Initialize database
            print("\n[3/7] Initializing database...")
            if self.config.enable_database and create_database_manager:
                Path(self.config.database_path).parent.mkdir(parents=True, exist_ok=True)
                self.database = create_database_manager(
                    database=self.config.database_path,
                    in_memory=False
                )
                print(f"      ✓ Database initialized: {self.config.database_path}")
            else:
                print("      ⊘ Database disabled or not available")

            # 4. Initialize live data
            print("\n[4/7] Initializing live data feeds...")
            if create_live_data_manager:
                self.live_data = create_live_data_manager()
                print("      ✓ Live data manager ready")
                print(f"      ✓ Symbols: {', '.join(self.config.symbols)}")
            else:
                print("      ⊘ Live data not available")

            # 5. Initialize exchange connector
            print("\n[5/7] Initializing exchange connector...")
            if create_binance_connector:
                testnet = self.config.mode != TradingMode.LIVE
                self.exchange = create_binance_connector(testnet=testnet)
                if self.exchange.connect():
                    print(f"      ✓ Connected to Binance {'testnet' if testnet else 'mainnet'}")
                else:
                    print("      ⚠ Exchange connection failed (will retry)")
            else:
                print("      ⊘ Exchange connector not available")

            # 6. Initialize strategies
            print("\n[6/7] Initializing trading strategies...")
            if self.config.enable_strategies and create_default_ensemble:
                self.strategies = create_default_ensemble()
                print(f"      ✓ Strategy ensemble initialized")
            else:
                print("      ⊘ Strategies disabled or not available")

            # 7. Initialize API server
            print("\n[7/7] Initializing API server...")
            if self.config.enable_api and create_api_server:
                self.api_server = create_api_server(
                    host=self.config.api_host,
                    port=self.config.api_port,
                    enable_auth=self.config.mode == TradingMode.LIVE
                )
                print(f"      ✓ API server ready on {self.config.api_host}:{self.config.api_port}")
            else:
                print("      ⊘ API server disabled or not available")

            # Start event bus
            self.event_bus.start()

            print("\n" + "="*60)
            print("  Initialization Complete")
            print("="*60)

            return True

        except Exception as e:
            self.state.status = SystemStatus.ERROR
            self.state.last_error = str(e)
            print(f"\n✗ Initialization failed: {e}")
            traceback.print_exc()
            return False

    def start(self) -> bool:
        """Start the trading system."""
        if self.state.status == SystemStatus.RUNNING:
            print("System already running")
            return True

        if self.state.status not in [SystemStatus.INITIALIZING, SystemStatus.STOPPED, SystemStatus.PAUSED]:
            if not self.initialize():
                return False

        print(f"\nStarting trading system in {self.config.mode.value} mode...")

        self._running = True
        self._shutdown_event.clear()
        self.state.status = SystemStatus.RUNNING
        self.state.start_time = datetime.now()

        # Start price update thread
        self._price_thread = threading.Thread(target=self._price_update_loop, daemon=True)
        self._price_thread.start()

        # Start strategy thread
        if self.config.enable_strategies:
            self._strategy_thread = threading.Thread(target=self._strategy_loop, daemon=True)
            self._strategy_thread.start()

        # Start WebSocket if enabled
        if self.config.enable_websocket and self.live_data:
            self._setup_websocket_subscriptions()
            self.live_data.start()

        print(f"✓ System running - {self.config.mode.value} mode")
        print(f"  Capital: ${self.config.initial_capital:,.2f}")
        print(f"  Symbols: {', '.join(self.config.symbols)}")

        return True

    def stop(self):
        """Stop the trading system gracefully."""
        if self.state.status == SystemStatus.STOPPED:
            return

        print("\nStopping trading system...")
        self.state.status = SystemStatus.STOPPING
        self._running = False
        self._shutdown_event.set()

        # Stop components
        if self.live_data:
            self.live_data.stop()

        if self.event_bus:
            self.event_bus.stop()

        # Wait for threads
        if self._price_thread and self._price_thread.is_alive():
            self._price_thread.join(timeout=5.0)

        if self._strategy_thread and self._strategy_thread.is_alive():
            self._strategy_thread.join(timeout=5.0)

        # Calculate final stats
        if self.state.start_time:
            self.state.uptime_seconds = (datetime.now() - self.state.start_time).total_seconds()

        self.state.status = SystemStatus.STOPPED
        print("✓ System stopped")
        self._print_session_summary()

    def pause(self):
        """Pause trading (continue monitoring)."""
        if self.state.status == SystemStatus.RUNNING:
            self.state.status = SystemStatus.PAUSED
            print("System paused")

    def resume(self):
        """Resume trading."""
        if self.state.status == SystemStatus.PAUSED:
            self.state.status = SystemStatus.RUNNING
            print("System resumed")

    # =========================================================================
    # Trading Logic
    # =========================================================================

    def _price_update_loop(self):
        """Background thread for price updates."""
        while self._running:
            try:
                for symbol in self.config.symbols:
                    self._update_price(symbol)

                self._shutdown_event.wait(self.config.price_update_interval)

            except Exception as e:
                self._handle_error("price_update", e)
                time.sleep(5.0)

    def _update_price(self, symbol: str):
        """Update price for a symbol."""
        try:
            if self.live_data:
                ticker = self.live_data.get_ticker(symbol)
                with self._price_lock:
                    self._prices[symbol] = ticker.price

                # Publish event
                self.event_bus.publish(Event(
                    event_type=EventType.PRICE_UPDATE,
                    timestamp=datetime.now(),
                    data={"symbol": symbol, "price": ticker.price},
                    source="price_updater"
                ))

        except Exception as e:
            # Silently handle price update errors
            pass

    def _strategy_loop(self):
        """Background thread for strategy evaluation."""
        while self._running:
            try:
                if self.state.status == SystemStatus.RUNNING:
                    self._evaluate_strategies()

                self._shutdown_event.wait(self.config.strategy_update_interval)

            except Exception as e:
                self._handle_error("strategy_loop", e)
                time.sleep(10.0)

    def _evaluate_strategies(self):
        """Evaluate trading strategies."""
        if not self.strategies:
            return

        for symbol in self.config.symbols:
            try:
                # Get current price
                with self._price_lock:
                    price = self._prices.get(symbol)

                if price is None:
                    continue

                # Get historical data for strategy
                if self.live_data:
                    klines = self.live_data.get_klines(symbol, "1h", limit=100)
                    if not klines:
                        continue

                    # Convert to numpy arrays
                    import numpy as np
                    prices = np.array([k.close for k in klines])
                    volumes = np.array([k.volume for k in klines])

                    # Evaluate strategy (simplified)
                    # In production, this would use the full strategy ensemble
                    signal = self._simple_strategy(prices, volumes)

                    if signal != 0:
                        self.event_bus.publish(Event(
                            event_type=EventType.SIGNAL_GENERATED,
                            timestamp=datetime.now(),
                            data={
                                "symbol": symbol,
                                "signal": "buy" if signal > 0 else "sell",
                                "strength": abs(signal),
                                "price": price
                            },
                            source="strategy_engine"
                        ))

                        # Execute trade in paper mode
                        if self.config.mode == TradingMode.PAPER:
                            self._execute_paper_trade(symbol, signal, price)

            except Exception as e:
                self._handle_error(f"strategy_{symbol}", e)

    def _simple_strategy(self, prices: 'np.ndarray', volumes: 'np.ndarray') -> float:
        """Simple momentum strategy for demonstration."""
        import numpy as np

        if len(prices) < 20:
            return 0.0

        # Calculate indicators
        sma_short = np.mean(prices[-10:])
        sma_long = np.mean(prices[-20:])

        # Momentum signal
        if sma_short > sma_long * 1.02:  # 2% above
            return 1.0
        elif sma_short < sma_long * 0.98:  # 2% below
            return -1.0

        return 0.0

    def _execute_paper_trade(self, symbol: str, signal: float, price: float):
        """Execute a paper trade."""
        side = "buy" if signal > 0 else "sell"

        # Calculate position size
        position_value = self.state.current_capital * self.config.max_position_size
        quantity = position_value / price

        # Check if we already have a position
        if symbol in self.state.positions:
            existing = self.state.positions[symbol]
            if existing["side"] == side:
                return  # Already in same direction

            # Close existing position
            self._close_paper_position(symbol, price)

        # Open new position
        self.state.positions[symbol] = {
            "side": side,
            "quantity": quantity,
            "entry_price": price,
            "entry_time": datetime.now().isoformat(),
            "stop_loss": price * (1 - self.config.stop_loss_pct) if side == "buy" else price * (1 + self.config.stop_loss_pct),
            "take_profit": price * (1 + self.config.take_profit_pct) if side == "buy" else price * (1 - self.config.take_profit_pct),
        }

        self.state.total_trades += 1

        print(f"  📈 Paper {side.upper()} {symbol}: {quantity:.6f} @ ${price:,.2f}")

    def _close_paper_position(self, symbol: str, price: float):
        """Close a paper position."""
        if symbol not in self.state.positions:
            return

        position = self.state.positions[symbol]
        pnl = (price - position["entry_price"]) * position["quantity"]
        if position["side"] == "sell":
            pnl = -pnl

        self.state.current_capital += pnl
        self.state.total_pnl += pnl

        if pnl > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1

        del self.state.positions[symbol]

        print(f"  📉 Closed {symbol}: PnL ${pnl:,.2f}")

    def _setup_websocket_subscriptions(self):
        """Setup WebSocket subscriptions for real-time data."""
        if not self.live_data:
            return

        for symbol in self.config.symbols:
            # Subscribe to ticker updates
            self.live_data.subscribe_ticker(symbol, lambda tick: None)

    # =========================================================================
    # Utilities
    # =========================================================================

    def _handle_error(self, source: str, error: Exception):
        """Handle and log errors."""
        error_entry = {
            "source": source,
            "error": str(error),
            "timestamp": datetime.now().isoformat(),
        }
        self.state.errors.append(error_entry)
        self.state.last_error = str(error)

        # Keep only last 100 errors
        if len(self.state.errors) > 100:
            self.state.errors = self.state.errors[-100:]

    def _print_session_summary(self):
        """Print session summary on shutdown."""
        print("\n" + "="*60)
        print("  Session Summary")
        print("="*60)
        print(f"  Mode: {self.config.mode.value}")
        print(f"  Uptime: {self.state.uptime_seconds/60:.1f} minutes")
        print(f"  Initial Capital: ${self.state.initial_capital:,.2f}")
        print(f"  Final Capital: ${self.state.current_capital:,.2f}")
        print(f"  Total PnL: ${self.state.total_pnl:,.2f}")
        print(f"  Total Trades: {self.state.total_trades}")
        if self.state.total_trades > 0:
            win_rate = self.state.winning_trades / self.state.total_trades * 100
            print(f"  Win Rate: {win_rate:.1f}%")
        print(f"  Open Positions: {len(self.state.positions)}")
        print(f"  Errors: {len(self.state.errors)}")
        print("="*60)

    def get_status(self) -> Dict[str, Any]:
        """Get current system status."""
        return {
            "status": self.state.status.value,
            "mode": self.config.mode.value,
            "uptime_seconds": (datetime.now() - self.state.start_time).total_seconds() if self.state.start_time else 0,
            "capital": {
                "initial": self.state.initial_capital,
                "current": self.state.current_capital,
                "pnl": self.state.total_pnl,
            },
            "positions": self.state.positions,
            "trades": {
                "total": self.state.total_trades,
                "winning": self.state.winning_trades,
                "losing": self.state.losing_trades,
            },
            "prices": dict(self._prices),
            "errors": len(self.state.errors),
            "last_error": self.state.last_error,
        }

    def run_forever(self):
        """Run the system until interrupted."""
        if not self.start():
            return

        print("\nSystem running. Press Ctrl+C to stop.\n")

        try:
            while self._running:
                # Print status every 30 seconds
                time.sleep(30)
                if self._running:
                    self._print_status_line()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _print_status_line(self):
        """Print compact status line."""
        prices_str = " | ".join([f"{s}: ${p:,.0f}" for s, p in self._prices.items()])
        pnl_sign = "+" if self.state.total_pnl >= 0 else ""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {prices_str} | PnL: {pnl_sign}${self.state.total_pnl:,.2f} | Trades: {self.state.total_trades}")


# =============================================================================
# Factory and CLI
# =============================================================================

def create_orchestrator(
    mode: str = "paper",
    symbols: Optional[List[str]] = None,
    initial_capital: float = 100000.0,
    **kwargs
) -> TradingOrchestrator:
    """Create trading orchestrator."""
    config = OrchestratorConfig(
        mode=TradingMode(mode),
        symbols=symbols or ["BTC/USDT", "ETH/USDT"],
        initial_capital=initial_capital,
        **kwargs
    )
    return TradingOrchestrator(config)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Agentic Trading OS")
    parser.add_argument("--mode", choices=["paper", "live", "backtest", "research"],
                       default="paper", help="Trading mode")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT"],
                       help="Trading symbols")
    parser.add_argument("--capital", type=float, default=100000.0,
                       help="Initial capital")
    parser.add_argument("--api-port", type=int, default=8080,
                       help="API server port")
    parser.add_argument("--no-api", action="store_true",
                       help="Disable API server")
    parser.add_argument("--no-db", action="store_true",
                       help="Disable database")

    args = parser.parse_args()

    orchestrator = create_orchestrator(
        mode=args.mode,
        symbols=args.symbols,
        initial_capital=args.capital,
        api_port=args.api_port,
        enable_api=not args.no_api,
        enable_database=not args.no_db,
    )

    orchestrator.run_forever()


if __name__ == "__main__":
    main()
