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
