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
import logging
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

try:
    from core.alpaca_connector import (
        create_alpaca_client, AlpacaClient, AlpacaConfig, AlpacaEnvironment
    )
except ImportError:
    create_alpaca_client = None
    AlpacaClient = None


# =============================================================================
# Configuration
# =============================================================================

from core.live_authorization import (
    LiveAuthorizationGate,
    LiveAuthorizationRequest,
)
from core.reconciliation import (
    BrokerSnapshot,
    LocalSnapshot,
    ReconciliationEngine,
)
from core.readiness import (
    ReadinessReport,
    evaluate_readiness,
    liveness,
)
from core.runtime_state import (
    LiveStartupSequence,
    RuntimeState,
    RuntimeStateMachine,
    StartupAborted,
    build_live_startup_checks,
)

logger = logging.getLogger(__name__)


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


class ExchangeType(Enum):
    """Supported exchange types."""
    BINANCE = "binance"
    ALPACA = "alpaca"
    COINBASE = "coinbase"


@dataclass
class OrchestratorConfig:
    """Orchestrator configuration."""
    mode: TradingMode = TradingMode.PAPER
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    initial_capital: float = 100000.0
    base_currency: str = "USDT"

    # Exchange settings
    exchange: ExchangeType = ExchangeType.ALPACA  # Default to Alpaca (US-friendly)
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""

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

    def is_alive(self) -> bool:
        """Whether events published now would actually be processed.

        ATOS-P2-DEPLOY-001 needs this: a bus whose processor thread has died
        still accepts publish() without complaint, so every subsequent event
        goes into a queue nobody reads. Readiness has to be able to notice.
        """
        return bool(
            self._running
            and self._processor_thread is not None
            and self._processor_thread.is_alive()
        )

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

        # ATOS-P0-REC-001: the runtime state machine decides what the system
        # is allowed to do. It starts in PAPER; reaching LIVE_ACTIVE requires
        # the full startup gauntlet to produce evidence.
        self.runtime = RuntimeStateMachine(
            RuntimeState.BACKTEST if self.config.mode == TradingMode.BACKTEST
            else RuntimeState.RESEARCH if self.config.mode == TradingMode.RESEARCH
            else RuntimeState.PAPER
        )
        self.last_startup_report = None
        self.last_reconciliation_report = None
        self.last_authorization_decision = None
        # ATOS-P0-AUTH-001 will source this from validated config. Until
        # then it is unset, and an unset fingerprint is itself a mismatch.
        self.expected_account_fingerprint = getattr(
            self.config, "expected_account_fingerprint", None
        )

        # Core components
        self.event_bus = EventBus()
        self.config_manager: Optional[ConfigManager] = None
        self.credentials: Optional[CredentialsManager] = None
        self.database: Optional[DatabaseManager] = None
        self.live_data: Optional[LiveDataManager] = None
        self.api_server: Optional[RESTAPIServer] = None
        self.exchange: Optional[BinanceConnector] = None
        self.alpaca_client: Optional[AlpacaClient] = None  # Alpaca support
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
            if self.config.exchange == ExchangeType.ALPACA and create_alpaca_client:
                # Use Alpaca (US-friendly)
                paper = self.config.mode != TradingMode.LIVE
                self.alpaca_client = create_alpaca_client(
                    api_key=self.config.alpaca_api_key,
                    api_secret=self.config.alpaca_api_secret,
                    paper=paper
                )
                if self.alpaca_client.connect():
                    env = "paper" if paper else "live"
                    print(f"      ✓ Connected to Alpaca ({env})")
                else:
                    print("      ⚠ Alpaca connection failed (check credentials)")
            elif self.config.exchange == ExchangeType.BINANCE and create_binance_connector:
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
                    # ATOS-P2-API-001: authentication is unconditional.
                    # This used to be `mode == LIVE`, which left the whole
                    # control plane - including POST /api/v1/orders and
                    # strategy start/stop - unauthenticated in paper mode,
                    # which is the default. Paper is where promotion evidence
                    # comes from, so an unauthenticated party who can place
                    # paper orders can corrupt the case for going live.
                    enable_auth=True
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

        # ATOS-P0-REC-001: a live start must pass the full evidence gauntlet.
        # A restart with empty books is not evidence that the broker holds
        # nothing, so nothing reaches LIVE_ACTIVE without reconciliation.
        if not self._run_startup_sequence():
            print(
                f"✗ Startup blocked - runtime state is "
                f"{self.runtime.state.value.upper()}."
            )
            if self.last_startup_report and self.last_startup_report.aborted_at:
                failure = self.last_startup_report.failures[-1]
                print(f"  Failed check: {failure.name}")
                print(f"  Reason: {failure.detail}")
            print("  The system will not add risk until this is resolved.")
            return False

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

    def _run_startup_sequence(self) -> bool:
        """Run the fail-closed startup checks for the configured mode.

        Returns True when the system may proceed. In live mode that means
        every one of the ULTRAPLAN's fourteen checks produced evidence.
        """
        live = self.config.mode == TradingMode.LIVE
        try:
            checks = build_live_startup_checks(
                config_loader=self._check_config,
                authorization=self._check_live_authorization,
                storage=self._check_durable_storage,
                replay=self._check_local_replay,
                unresolved_intents=self._check_unresolved_intents,
                broker_auth=self._check_broker_auth,
                account=self._check_broker_account,
                positions=self._check_broker_positions,
                open_orders=self._check_broker_open_orders,
                recent_fills=self._check_broker_recent_fills,
                reconciliation=self._check_reconciliation,
                risk_anchors=self._check_risk_anchors,
                market_data=self._check_market_data_health,
                capital_tier=self._check_capital_tier,
            )
            sequence = LiveStartupSequence(self.runtime, checks, live=live)
            report = sequence.run()
        except StartupAborted as exc:
            print(f"  Startup sequence refused: {exc}")
            self.runtime.require_recovery(f"startup sequence invalid: {exc}")
            return False

        self.last_startup_report = report
        return report.final_state not in (
            RuntimeState.FROZEN,
            RuntimeState.RECOVERY_REQUIRED,
            RuntimeState.HALTED,
        )

    # -- startup evidence checks -----------------------------------------
    #
    # Each returns (passed, detail). Several are deliberately unimplemented
    # and fail closed: the capability they are meant to verify does not exist
    # yet, and reporting "no evidence" is the honest answer. The ULTRAPLAN
    # issue that supplies each one is named in its message.

    def _check_config(self):
        if self.config_manager is None and self.config is None:
            return False, "no configuration loaded"
        return True, f"mode={self.config.mode.value}"

    def _check_live_authorization(self):
        """Evaluate the fifteen live-activation conditions.

        ATOS-P0-AUTH-001. The request is assembled from what the orchestrator
        can actually establish. Anything it cannot establish stays at its
        default, which is "not satisfied" - so a missing capability refuses
        rather than waves through.
        """
        request = self._build_authorization_request()
        decision = LiveAuthorizationGate().authorize(request)
        self.last_authorization_decision = decision
        if decision.authorized:
            return True, "all fifteen live activation conditions are satisfied"
        first = decision.first_failure
        return False, f"{decision.summary()} - {decision.failures.get(first, '')}"

    def _build_authorization_request(self) -> LiveAuthorizationRequest:
        """Collect the evidence the orchestrator has for a live activation."""
        journal = getattr(self, "intent_journal", None)
        unresolved = (
            [i.client_order_id for i in journal.unresolved_intents()]
            if journal is not None else []
        )
        reconciliation = getattr(self, "last_reconciliation_report", None)
        broker_fingerprint = None
        if reconciliation is not None and reconciliation.may_acquire:
            broker_fingerprint = self.expected_account_fingerprint

        return LiveAuthorizationRequest(
            explicit_live_flag=bool(getattr(self.config, "explicit_live_flag", False)),
            human_risk_acknowledgement=getattr(
                self.config, "live_risk_acknowledgement", None
            ),
            environment_designation=getattr(
                self.config, "environment_designation", None
            ),
            credential_present=bool(self.credentials is not None),
            credential_expires_at=getattr(self.config, "credential_expires_at", None),
            credential_source=getattr(self.config, "credential_source", None),
            database_healthy=self.database is not None,
            state_replay_succeeded=bool(
                getattr(self, "state_replay_succeeded", False)
            ),
            reconciliation_matched=bool(
                reconciliation is not None and reconciliation.may_acquire
            ),
            market_data_healthy=self.live_data is not None,
            config_hash=getattr(self.config, "config_hash", None),
            promoted_config_hashes=frozenset(
                getattr(self.config, "promoted_config_hashes", ()) or ()
            ),
            capital_tier_limit=getattr(self.config, "capital_tier_limit", None),
            active_risk_trips=list(getattr(self, "active_risk_trips", []) or []),
            unresolved_order_ids=unresolved,
            expected_account_fingerprint=self.expected_account_fingerprint,
            broker_account_fingerprint=broker_fingerprint,
            session_id=getattr(self, "session_id", None),
            session_id_persisted=bool(getattr(self, "session_id_persisted", False)),
        )

    def _check_durable_storage(self):
        if self.database is None:
            return False, "durable database is not initialised"
        return True, "database initialised"

    def _check_local_replay(self):
        # ATOS-P2-FAULT-002 supplies deterministic event replay.
        return False, (
            "local state replay is not implemented yet (ATOS-P2-FAULT-002); "
            "starting from an unreplayed book could assume a flat portfolio"
        )

    def _check_unresolved_intents(self):
        # ATOS-P0-EXEC-002 built the journal; wiring the orchestrator to one
        # is part of that worklist being consumed here.
        journal = getattr(self, "intent_journal", None)
        if journal is None:
            return False, (
                "no order intent journal attached, so unresolved orders cannot "
                "be enumerated (ATOS-P0-EXEC-002)"
            )
        unresolved = journal.unresolved_intents()
        if unresolved:
            ids = ", ".join(i.client_order_id for i in unresolved[:5])
            return False, f"{len(unresolved)} unresolved order intent(s): {ids}"
        return True, "no unresolved order intents"

    def _check_broker_auth(self):
        if self.alpaca_client is None and self.exchange is None:
            return False, "no broker client is connected"
        return True, "broker client present"

    def _check_broker_account(self):
        client = self.alpaca_client
        if client is None:
            return False, "no broker client to query for account state"
        try:
            account = client.get_account()
        except Exception as exc:
            return False, f"account fetch failed: {type(exc).__name__}"
        if not account:
            return False, "broker returned no account"
        return True, f"account status {account.get('status', 'unknown')}"

    def _check_broker_positions(self):
        client = self.alpaca_client
        if client is None:
            return False, "no broker client to query for positions"
        try:
            positions = client.get_positions()
        except Exception as exc:
            return False, f"position fetch failed: {type(exc).__name__}"
        return True, f"{len(positions or [])} broker position(s)"

    def _check_broker_open_orders(self):
        client = self.alpaca_client
        if client is None:
            return False, "no broker client to query for open orders"
        try:
            orders = client.get_orders(status="open")
        except Exception as exc:
            return False, f"open order fetch failed: {type(exc).__name__}"
        return True, f"{len(orders or [])} open broker order(s)"

    def _check_broker_recent_fills(self):
        client = self.alpaca_client
        if client is None:
            return False, "no broker client to query for recent fills"
        try:
            client.get_orders(status="closed", limit=50)
        except Exception as exc:
            return False, f"recent fill fetch failed: {type(exc).__name__}"
        return True, "recent fills fetched"

    def _check_reconciliation(self):
        """Compare what we believe against what the broker holds.

        ATOS-P0-REC-002. Where a component is missing, the snapshot is marked
        incomplete rather than empty, so a system with no way to look becomes
        a mismatch rather than a clean match against nothing.
        """
        local_snapshot, broker_snapshot = self._build_reconciliation_snapshots()
        engine = ReconciliationEngine(
            expected_account_fingerprint=self.expected_account_fingerprint
        )
        report = engine.reconcile(local_snapshot, broker_snapshot)
        self.last_reconciliation_report = report

        if report.may_acquire:
            return True, "local and broker state agree"
        return False, report.summary()

    def _build_reconciliation_snapshots(self):
        """Assemble both sides of the comparison from whatever exists.

        Every fetch that cannot be performed clears a completeness flag. The
        reconciliation engine treats an incomplete snapshot as a mismatch, so
        a missing capability fails closed instead of reading as agreement.
        """
        fingerprint = self.expected_account_fingerprint or ""

        broker_snapshot = BrokerSnapshot(account_fingerprint=fingerprint)
        client = self.alpaca_client
        if client is None:
            broker_snapshot.account_complete = False
            broker_snapshot.positions_complete = False
            broker_snapshot.open_orders_complete = False
            broker_snapshot.fills_complete = False
        else:
            try:
                account = client.get_account() or {}
                broker_snapshot.cash = float(account.get("cash", 0.0) or 0.0)
                broker_snapshot.equity = float(
                    account.get("portfolio_value", 0.0) or 0.0
                )
                broker_snapshot.buying_power = float(
                    account.get("buying_power", 0.0) or 0.0
                )
            except Exception:
                logger.exception("Could not fetch broker account for reconciliation")
                broker_snapshot.account_complete = False

            try:
                for position in client.get_positions() or []:
                    symbol = getattr(position, "symbol", None) or position.get("symbol")
                    qty = getattr(position, "qty", None)
                    if qty is None and isinstance(position, dict):
                        qty = position.get("qty", position.get("quantity"))
                    if symbol is not None and qty is not None:
                        broker_snapshot.positions[str(symbol)] = float(qty)
            except Exception:
                logger.exception("Could not fetch broker positions for reconciliation")
                broker_snapshot.positions_complete = False

            try:
                seen = set()
                for order in client.get_orders(status="open") or []:
                    client_id = (
                        getattr(order, "client_order_id", None)
                        or (order.get("client_order_id") if isinstance(order, dict) else None)
                    )
                    if not client_id:
                        continue
                    if client_id in seen:
                        broker_snapshot.duplicate_client_ids.append(client_id)
                    seen.add(client_id)
                    broker_snapshot.open_orders[client_id] = (
                        order if isinstance(order, dict) else vars(order)
                    )
            except Exception:
                logger.exception("Could not fetch broker orders for reconciliation")
                broker_snapshot.open_orders_complete = False

            try:
                client.get_orders(status="closed", limit=50)
            except Exception:
                logger.exception("Could not fetch broker fills for reconciliation")
                broker_snapshot.fills_complete = False

        local_snapshot = LocalSnapshot(account_fingerprint=fingerprint)
        ledger = getattr(self, "ledger", None)
        if ledger is not None:
            try:
                local_snapshot.cash = float(ledger.cash)
                local_snapshot.positions = {
                    symbol: float(record.quantity)
                    for symbol, record in ledger.positions.items()
                }
            except Exception:
                logger.exception("Could not read the local ledger for reconciliation")

        journal = getattr(self, "intent_journal", None)
        if journal is not None:
            for intent in journal.unresolved_intents():
                local_snapshot.unknown_orders[intent.client_order_id] = intent.to_dict()

        return local_snapshot, broker_snapshot

    def _check_risk_anchors(self):
        # ATOS-P1-RISK-002 supplies durable drawdown anchors.
        return False, (
            "durable risk anchors are not implemented yet (ATOS-P1-RISK-002); "
            "a restart would reset the daily loss limit"
        )

    def _check_market_data_health(self):
        # ATOS-P1-DATA-001 supplies per-symbol feed health.
        if self.live_data is None:
            return False, "no live data manager attached"
        return True, "live data manager attached"

    def _check_capital_tier(self):
        # ATOS-P3-CAP-001 supplies the persisted promotion ladder.
        return False, (
            "no persisted capital tier (ATOS-P3-CAP-001); initial_capital is a "
            "number, not spend authority"
        )

    # -- readiness --------------------------------------------------------
    #
    # ATOS-P2-DEPLOY-001. Liveness is answered by the process responding at
    # all; readiness is answered here, and the two must not be confused. Most
    # of the evidence is the startup gauntlet's, reused: a requirement that
    # had to hold before the system started is not one that stops mattering
    # once it has.

    def readiness_evidence(self) -> Dict[str, Any]:
        """Everything the readiness probe can establish right now."""
        return {
            "persistence_healthy": self._check_durable_storage,
            "broker_auth_healthy": self._check_broker_auth,
            "expected_account_confirmed": self._check_broker_account,
            "reconciliation_fresh_and_matched": self._check_reconciliation,
            "data_healthy": self._check_market_data_health,
            "no_unresolved_order_intents": self._check_unresolved_intents,
            "no_risk_trip": self._check_risk_anchors,
            "capital_tier_valid": self._check_capital_tier,
            "strategy_promotion_valid": self._check_strategy_promotion,
            "event_loops_healthy": self._check_event_loops,
            "execution_adapter_healthy": self._check_execution_adapter,
        }

    def readiness(self) -> ReadinessReport:
        """Whether this process may add new risk, and what is stopping it."""
        return evaluate_readiness(
            self.readiness_evidence(),
            live=self.config.mode == TradingMode.LIVE,
        )

    def liveness(self) -> Dict[str, Any]:
        """Whether the process is answering. Says nothing about safety."""
        return liveness(self.state.start_time)

    def _check_strategy_promotion(self):
        # ATOS-P3-TUNE-001 supplies champion/challenger promotion records.
        if self.strategies is None:
            return False, "no strategy ensemble loaded"
        return False, (
            "no promotion record for the running strategy "
            "(ATOS-P3-TUNE-001); a loaded strategy is not an approved one"
        )

    def _check_event_loops(self):
        """Threads the system needs in order to notice anything.

        A dead price thread is the quiet failure: the system keeps answering,
        keeps its last known prices, and trades on them.
        """
        if not self._running:
            return False, "the system is not running"

        dead = []
        if self._price_thread is None or not self._price_thread.is_alive():
            dead.append("price")
        if self.config.enable_strategies and (
            self._strategy_thread is None or not self._strategy_thread.is_alive()
        ):
            dead.append("strategy")
        if self.event_bus is not None and not self.event_bus.is_alive():
            dead.append("event bus")

        if dead:
            return False, "not running: " + ", ".join(dead)
        return True, "price, strategy and event loops are alive"

    def _check_execution_adapter(self):
        client = self.alpaca_client
        if client is None and self.exchange is None:
            return False, "no execution adapter is attached"
        if client is not None and not getattr(client, "connected", False):
            return False, "the Alpaca adapter is attached but not connected"
        return True, "an execution adapter is connected"

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
    exchange: str = "alpaca",
    alpaca_api_key: str = "",
    alpaca_api_secret: str = "",
    **kwargs
) -> TradingOrchestrator:
    """Create trading orchestrator.

    Args:
        mode: Trading mode (paper, live, backtest, research)
        symbols: List of trading symbols
        initial_capital: Starting capital
        exchange: Exchange type (alpaca, binance, coinbase)
        alpaca_api_key: Alpaca API key
        alpaca_api_secret: Alpaca API secret
    """
    # Default symbols based on exchange
    if symbols is None:
        if exchange == "alpaca":
            symbols = ["AAPL", "SPY", "BTC/USD"]  # Alpaca symbols
        else:
            symbols = ["BTC/USDT", "ETH/USDT"]  # Binance symbols

    config = OrchestratorConfig(
        mode=TradingMode(mode),
        symbols=symbols,
        initial_capital=initial_capital,
        exchange=ExchangeType(exchange),
        alpaca_api_key=alpaca_api_key,
        alpaca_api_secret=alpaca_api_secret,
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
