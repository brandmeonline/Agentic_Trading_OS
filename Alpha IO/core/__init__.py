"""
Core trading engine modules.

This package contains the fundamental components of the trading system:
- Agent: Reinforcement learning trading agent
- Risk: Position sizing and risk management
- Signals: Signal generation, memory, and routing
- Multi-agent fusion: Ensemble decision making
- Execution: Order management and trade execution
"""

__all__ = []

# Import with graceful fallback for missing dependencies
try:
    from core.agent import TradingAgent, AgentConfig, LearningAlgorithm
    __all__.extend(["TradingAgent", "AgentConfig", "LearningAlgorithm"])
except ImportError as e:
    TradingAgent = None
    AgentConfig = None
    LearningAlgorithm = None

try:
    from core.risk import RiskManager, RiskConfig, RiskLevel
    __all__.extend(["RiskManager", "RiskConfig", "RiskLevel"])
except ImportError as e:
    RiskManager = None
    RiskConfig = None
    RiskLevel = None

try:
    from core.execution import ExecutionEngine, ExecutionConfig, Order, OrderType, OrderSide
    __all__.extend(["ExecutionEngine", "ExecutionConfig", "Order", "OrderType", "OrderSide"])
except ImportError as e:
    ExecutionEngine = None
    ExecutionConfig = None

try:
    from core.signal_memory import SignalMemory
    __all__.append("SignalMemory")
except ImportError:
    SignalMemory = None

try:
    from core.signal_router import SignalRouter
    __all__.append("SignalRouter")
except ImportError:
    SignalRouter = None

try:
    from core.asymmetry_index import AsymmetryIndex
    __all__.append("AsymmetryIndex")
except ImportError:
    AsymmetryIndex = None

try:
    from core.auto_tuner import AutoTuner
    __all__.append("AutoTuner")
except ImportError:
    AutoTuner = None

try:
    from core.multi_agent_fusion import AgentSwarm
    __all__.append("AgentSwarm")
except ImportError:
    AgentSwarm = None

try:
    from core.multi_agent_fusion_memory import MemoryVotingSwarm
    __all__.append("MemoryVotingSwarm")
except ImportError:
    MemoryVotingSwarm = None

try:
    from core.precision_trade_planner import map_signal_to_trade
    __all__.append("map_signal_to_trade")
except ImportError:
    map_signal_to_trade = None

try:
    from core.alpha_leak_agent import AlphaLeakAgent
    __all__.append("AlphaLeakAgent")
except ImportError:
    AlphaLeakAgent = None

# Strategy framework
try:
    from core.strategy import (
        Strategy, StrategyOutput, StrategySignal, StrategyEnsemble,
        MomentumStrategy, MeanReversionStrategy, TrendFollowingStrategy,
        VolatilityBreakoutStrategy, create_default_ensemble
    )
    __all__.extend([
        "Strategy", "StrategyOutput", "StrategySignal", "StrategyEnsemble",
        "MomentumStrategy", "MeanReversionStrategy", "TrendFollowingStrategy",
        "VolatilityBreakoutStrategy", "create_default_ensemble"
    ])
except ImportError:
    Strategy = None
    StrategyEnsemble = None

# Market data
try:
    from core.market_data import (
        MarketDataFeed, SimulatedDataFeed, CSVDataFeed,
        OHLCV, Quote, TimeFrame, DataAggregator
    )
    __all__.extend([
        "MarketDataFeed", "SimulatedDataFeed", "CSVDataFeed",
        "OHLCV", "Quote", "TimeFrame", "DataAggregator"
    ])
except ImportError:
    MarketDataFeed = None
    SimulatedDataFeed = None

# Backtesting
try:
    from core.backtest_engine import (
        BacktestEngine, BacktestConfig, BacktestResult,
        BacktestMetrics, run_quick_backtest
    )
    __all__.extend([
        "BacktestEngine", "BacktestConfig", "BacktestResult",
        "BacktestMetrics", "run_quick_backtest"
    ])
except ImportError:
    BacktestEngine = None
    BacktestConfig = None

# Portfolio optimization
try:
    from core.portfolio import (
        PortfolioOptimizer, OptimizationMethod, PortfolioWeights,
        OptimizationConfig, RebalanceEngine
    )
    __all__.extend([
        "PortfolioOptimizer", "OptimizationMethod", "PortfolioWeights",
        "OptimizationConfig", "RebalanceEngine"
    ])
except ImportError:
    PortfolioOptimizer = None
    OptimizationMethod = None

# Performance analytics
try:
    from core.analytics import (
        PerformanceAnalyzer, ReturnMetrics, RiskMetrics,
        RatioMetrics, TradeMetrics
    )
    __all__.extend([
        "PerformanceAnalyzer", "ReturnMetrics", "RiskMetrics",
        "RatioMetrics", "TradeMetrics"
    ])
except ImportError:
    PerformanceAnalyzer = None

# Deep Learning Alpha Models
try:
    from core.deep_learning import (
        DeepAlphaGenerator, AlphaModelEnsemble,
        LSTMAttentionModel, TemporalFusionTransformer,
        NBEATSModel, WaveNetModel
    )
    __all__.extend([
        "DeepAlphaGenerator", "AlphaModelEnsemble",
        "LSTMAttentionModel", "TemporalFusionTransformer",
        "NBEATSModel", "WaveNetModel"
    ])
except ImportError:
    DeepAlphaGenerator = None
    AlphaModelEnsemble = None

# Feature Engineering
try:
    from core.feature_engine import (
        FeatureEngine, FeatureConfig, FeatureSet,
        TechnicalFeatures, MicrostructureFeatures,
        CrossAssetFeatures, RegimeFeatures
    )
    __all__.extend([
        "FeatureEngine", "FeatureConfig", "FeatureSet",
        "TechnicalFeatures", "MicrostructureFeatures",
        "CrossAssetFeatures", "RegimeFeatures"
    ])
except ImportError:
    FeatureEngine = None
    FeatureConfig = None

# NLP Sentiment Analysis
try:
    from core.nlp_engine import (
        NLPEngine, SentimentAnalyzer, NewsProcessor,
        SocialMediaAnalyzer, SentimentSignal
    )
    __all__.extend([
        "NLPEngine", "SentimentAnalyzer", "NewsProcessor",
        "SocialMediaAnalyzer", "SentimentSignal"
    ])
except ImportError:
    NLPEngine = None
    SentimentAnalyzer = None

# Real-Time Data Infrastructure
try:
    from core.realtime_data import (
        RealtimeDataManager, WebSocketFeed, FeatureStore,
        DataNormalizer, StreamProcessor
    )
    __all__.extend([
        "RealtimeDataManager", "WebSocketFeed", "FeatureStore",
        "DataNormalizer", "StreamProcessor"
    ])
except ImportError:
    RealtimeDataManager = None
    WebSocketFeed = None

# Smart Order Routing
try:
    from core.smart_router import (
        SmartOrderRouter, VenueAnalyzer, LiquidityAggregator,
        ExecutionOptimizer, TransactionCostModel
    )
    __all__.extend([
        "SmartOrderRouter", "VenueAnalyzer", "LiquidityAggregator",
        "ExecutionOptimizer", "TransactionCostModel"
    ])
except ImportError:
    SmartOrderRouter = None
    VenueAnalyzer = None

# Production Monitoring
try:
    from core.monitoring import (
        SystemMonitor, AlertManager, MetricsCollector,
        HealthChecker, PerformanceProfiler
    )
    __all__.extend([
        "SystemMonitor", "AlertManager", "MetricsCollector",
        "HealthChecker", "PerformanceProfiler"
    ])
except ImportError:
    SystemMonitor = None
    AlertManager = None

# Advanced Backtesting
try:
    from core.advanced_backtest import (
        WalkForwardOptimizer, MonteCarloSimulator,
        RegimeAwareBacktest, OutOfSampleValidator
    )
    __all__.extend([
        "WalkForwardOptimizer", "MonteCarloSimulator",
        "RegimeAwareBacktest", "OutOfSampleValidator"
    ])
except ImportError:
    WalkForwardOptimizer = None
    MonteCarloSimulator = None

# Stress Testing & Tail Risk
try:
    from core.stress_testing import (
        StressTester, TailRiskHedger, ScenarioGenerator,
        ExtremeValueAnalyzer, CrisisSimulator
    )
    __all__.extend([
        "StressTester", "TailRiskHedger", "ScenarioGenerator",
        "ExtremeValueAnalyzer", "CrisisSimulator"
    ])
except ImportError:
    StressTester = None
    TailRiskHedger = None

# Unified Trading System
try:
    from core.unified_system import (
        UnifiedTradingEngine, SystemConfig, SystemMode,
        TradeSignal, SignalSource, SignalAggregator,
        TradeExecutor, RiskController, create_trading_system
    )
    __all__.extend([
        "UnifiedTradingEngine", "SystemConfig", "SystemMode",
        "TradeSignal", "SignalSource", "SignalAggregator",
        "TradeExecutor", "RiskController", "create_trading_system"
    ])
except ImportError:
    UnifiedTradingEngine = None
    create_trading_system = None

# Exchange Connectors
try:
    from core.exchange_connectors import (
        ExchangeConnector, BinanceConnector, CoinbaseConnector,
        ExchangeManager, ExchangeConfig, ExchangeType,
        OrderRequest, Order, Balance, Position,
        create_binance_connector, create_coinbase_connector, create_exchange_manager
    )
    __all__.extend([
        "ExchangeConnector", "BinanceConnector", "CoinbaseConnector",
        "ExchangeManager", "ExchangeConfig", "ExchangeType",
        "OrderRequest", "Order", "Balance", "Position",
        "create_binance_connector", "create_coinbase_connector", "create_exchange_manager"
    ])
except ImportError:
    ExchangeConnector = None
    BinanceConnector = None
    CoinbaseConnector = None
    ExchangeManager = None

# REST API Server
try:
    from core.rest_api import (
        RESTAPIServer, APIConfig, APIRequest, APIResponse,
        WebSocketHandler, TradingAPIHandlers,
        create_api_server, create_websocket_handler, generate_openapi_spec
    )
    __all__.extend([
        "RESTAPIServer", "APIConfig", "APIRequest", "APIResponse",
        "WebSocketHandler", "TradingAPIHandlers",
        "create_api_server", "create_websocket_handler", "generate_openapi_spec"
    ])
except ImportError:
    RESTAPIServer = None
    create_api_server = None

# Database Persistence
try:
    from core.database import (
        DatabaseManager, DatabaseConfig, DatabaseConnection,
        OrderRepository, TradeRepository, PositionRepository,
        StrategyStateRepository, OHLCVRepository,
        OrderRecord, TradeRecord, PositionRecord, StrategyStateRecord, OHLCVRecord,
        create_database_manager
    )
    __all__.extend([
        "DatabaseManager", "DatabaseConfig", "DatabaseConnection",
        "OrderRepository", "TradeRepository", "PositionRepository",
        "StrategyStateRepository", "OHLCVRepository",
        "OrderRecord", "TradeRecord", "PositionRecord", "StrategyStateRecord", "OHLCVRecord",
        "create_database_manager"
    ])
except ImportError:
    DatabaseManager = None
    create_database_manager = None

# Advanced Reinforcement Learning
try:
    from core.advanced_rl import (
        RLAgent, DQNAgent, PPOAgent, A2CAgent, SACAgent,
        RLConfig, RLAlgorithm, Experience, Trajectory,
        ReplayBuffer, PrioritizedReplayBuffer,
        TradingEnvironment, create_agent, create_trading_env
    )
    __all__.extend([
        "RLAgent", "DQNAgent", "PPOAgent", "A2CAgent", "SACAgent",
        "RLConfig", "RLAlgorithm", "Experience", "Trajectory",
        "ReplayBuffer", "PrioritizedReplayBuffer",
        "TradingEnvironment", "create_agent", "create_trading_env"
    ])
except ImportError:
    RLAgent = None
    DQNAgent = None
    PPOAgent = None
    A2CAgent = None

# Audit Logging
try:
    from core.audit_log import (
        Logger, TradingLogger, LogConfig, LogLevel, LogCategory,
        LogEntry, AuditEntry, AuditAction,
        LogAnalyzer, create_logger, create_trading_logger, get_logger
    )
    __all__.extend([
        "Logger", "TradingLogger", "LogConfig", "LogLevel", "LogCategory",
        "LogEntry", "AuditEntry", "AuditAction",
        "LogAnalyzer", "create_logger", "create_trading_logger", "get_logger"
    ])
except ImportError:
    Logger = None
    TradingLogger = None
    create_logger = None

# Configuration Manager
try:
    from core.config_manager import (
        ConfigManager, ConfigSchema, ConfigField, ConfigStore,
        Environment, TradingConfig, ExchangeConfig as ExchangeConfigDC, DatabaseConfig as DatabaseConfigDC,
        create_config_manager, get_config, set_config,
        create_trading_schema, create_exchange_schema, create_database_schema
    )
    __all__.extend([
        "ConfigManager", "ConfigSchema", "ConfigField", "ConfigStore",
        "Environment", "TradingConfig",
        "create_config_manager", "get_config", "set_config",
        "create_trading_schema", "create_exchange_schema", "create_database_schema"
    ])
except ImportError:
    ConfigManager = None
    create_config_manager = None

# Credentials Manager
try:
    from core.credentials import (
        CredentialsManager, Credential, CredentialConfig,
        get_credentials_manager, create_credentials_manager,
        get_testnet_endpoint, get_public_endpoint,
        TESTNET_ENDPOINTS, PUBLIC_ENDPOINTS
    )
    __all__.extend([
        "CredentialsManager", "Credential", "CredentialConfig",
        "get_credentials_manager", "create_credentials_manager",
        "get_testnet_endpoint", "get_public_endpoint",
        "TESTNET_ENDPOINTS", "PUBLIC_ENDPOINTS"
    ])
except ImportError:
    CredentialsManager = None
    get_credentials_manager = None

# Live Data Client
try:
    from core.live_data import (
        LiveDataManager, BinancePublicClient, CoinGeckoClient,
        HTTPClient, LiveDataConfig, MarketTick, OHLCV,
        create_live_data_manager, create_binance_client, create_coingecko_client
    )
    __all__.extend([
        "LiveDataManager", "BinancePublicClient", "CoinGeckoClient",
        "HTTPClient", "LiveDataConfig", "MarketTick", "OHLCV",
        "create_live_data_manager", "create_binance_client", "create_coingecko_client"
    ])
except ImportError:
    LiveDataManager = None
    BinancePublicClient = None
    CoinGeckoClient = None

# System Orchestrator
try:
    from core.orchestrator import (
        TradingOrchestrator, OrchestratorConfig, TradingMode,
        SystemStatus, SystemState, EventBus, Event, EventType,
        create_orchestrator
    )
    __all__.extend([
        "TradingOrchestrator", "OrchestratorConfig", "TradingMode",
        "SystemStatus", "SystemState", "EventBus", "Event", "EventType",
        "create_orchestrator"
    ])
except ImportError:
    TradingOrchestrator = None
    create_orchestrator = None
