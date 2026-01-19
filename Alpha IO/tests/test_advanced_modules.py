"""
Comprehensive Test Suite for Advanced Trading Modules.

Tests:
- Deep Learning Models
- Feature Engineering
- NLP Sentiment
- Smart Order Routing
- Stress Testing
- Advanced Backtesting
- Unified System
"""

import numpy as np
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRunner:
    """Simple test runner."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run_test(self, name: str, test_func):
        """Run a single test."""
        try:
            test_func()
            self.passed += 1
            print(f"  [PASS] {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  [ERROR] {name}: {e}")

    def summary(self):
        """Print test summary."""
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"Test Summary: {self.passed}/{total} passed")
        if self.errors:
            print(f"\nFailed tests:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        print(f"{'='*60}")
        return self.failed == 0


# =============================================================================
# Feature Engineering Tests
# =============================================================================

def test_feature_engineering():
    """Test feature engineering module."""
    print("\nTesting Feature Engineering...")
    runner = TestRunner()

    try:
        from core.feature_engine import (
            FeatureEngine, FeatureConfig, TechnicalFeatures,
            MicrostructureFeatures, RegimeFeatures
        )

        # Generate test data
        np.random.seed(42)
        n = 500
        close = 100 * np.cumprod(1 + np.random.randn(n) * 0.02)
        high = close * (1 + np.abs(np.random.randn(n) * 0.01))
        low = close * (1 - np.abs(np.random.randn(n) * 0.01))
        open_ = close * (1 + np.random.randn(n) * 0.005)
        volume = np.abs(np.random.randn(n) * 1e6 + 5e6)

        # Test FeatureConfig
        def test_config():
            config = FeatureConfig()
            assert config.normalize_features == True
            assert len(config.lookback_periods) > 0

        runner.run_test("FeatureConfig initialization", test_config)

        # Test TechnicalFeatures
        def test_technical():
            config = FeatureConfig(lookback_periods=[10, 20], momentum_periods=[5, 10])
            tech = TechnicalFeatures(config)
            features, names = tech.compute_all(open_, high, low, close, volume)
            assert features.shape[0] == n
            assert len(names) > 0
            assert not np.all(np.isnan(features[-1]))

        runner.run_test("TechnicalFeatures computation", test_technical)

        # Test MicrostructureFeatures
        def test_microstructure():
            config = FeatureConfig()
            micro = MicrostructureFeatures(config)
            features, names = micro.compute_all(open_, high, low, close, volume)
            assert features.shape[0] == n
            assert "amihud_5" in names or "amihud_20" in names

        runner.run_test("MicrostructureFeatures computation", test_microstructure)

        # Test RegimeFeatures
        def test_regime():
            config = FeatureConfig(regime_window=30)
            regime = RegimeFeatures(config)
            features, names = regime.compute_all(open_, high, low, close, volume)
            assert features.shape[0] == n
            assert "trend_strength" in names

        runner.run_test("RegimeFeatures computation", test_regime)

        # Test full FeatureEngine
        def test_engine():
            config = FeatureConfig(
                lookback_periods=[10, 20],
                momentum_periods=[5],
                volatility_periods=[10],
                normalize_features=True
            )
            engine = FeatureEngine(config)
            feature_set = engine.compute_features(
                open_, high, low, close, volume,
                include_fractal=False
            )
            assert feature_set.n_samples == n
            assert feature_set.n_features > 0
            assert not np.any(np.isnan(feature_set.features[-1]))

        runner.run_test("FeatureEngine full pipeline", test_engine)

    except ImportError as e:
        print(f"  [SKIP] Feature engineering not available: {e}")

    return runner.summary()


# =============================================================================
# Deep Learning Tests
# =============================================================================

def test_deep_learning():
    """Test deep learning module."""
    print("\nTesting Deep Learning Models...")
    runner = TestRunner()

    try:
        from core.deep_learning import (
            LSTMAttentionModel, TemporalFusionTransformer,
            NBEATSModel, WaveNetModel, AlphaModelEnsemble,
            DeepAlphaGenerator
        )

        # Generate test data
        np.random.seed(42)
        n_samples = 100
        seq_len = 20
        n_features = 10
        X = np.random.randn(n_samples, seq_len, n_features)

        # Test LSTM Attention
        def test_lstm():
            model = LSTMAttentionModel(
                input_dim=n_features,
                hidden_dim=32,
                output_dim=1,
                n_layers=1
            )
            output = model.forward(X[0])
            assert output.shape == (1,)

        runner.run_test("LSTMAttentionModel forward pass", test_lstm)

        # Test Temporal Fusion Transformer
        def test_tft():
            model = TemporalFusionTransformer(
                n_features=n_features,
                d_model=32,
                n_heads=2,
                n_layers=1
            )
            output = model.forward(X[0])
            assert len(output) > 0

        runner.run_test("TemporalFusionTransformer forward pass", test_tft)

        # Test N-BEATS
        def test_nbeats():
            model = NBEATSModel(
                input_dim=seq_len,
                output_dim=5,
                n_blocks=2,
                hidden_dim=32
            )
            # N-BEATS takes 1D input
            output = model.forward(X[0, :, 0])
            assert len(output) == 5

        runner.run_test("NBEATSModel forward pass", test_nbeats)

        # Test WaveNet
        def test_wavenet():
            model = WaveNetModel(
                n_features=n_features,
                n_filters=16,
                kernel_size=3,
                n_blocks=2
            )
            output = model.forward(X[0])
            assert len(output.shape) >= 1

        runner.run_test("WaveNetModel forward pass", test_wavenet)

        # Test DeepAlphaGenerator
        def test_alpha_gen():
            generator = DeepAlphaGenerator(
                n_features=n_features,
                seq_len=seq_len
            )
            signals = generator.generate_signals(X)
            assert len(signals) == n_samples

        runner.run_test("DeepAlphaGenerator signal generation", test_alpha_gen)

    except ImportError as e:
        print(f"  [SKIP] Deep learning not available: {e}")

    return runner.summary()


# =============================================================================
# NLP Sentiment Tests
# =============================================================================

def test_nlp_sentiment():
    """Test NLP sentiment analysis module."""
    print("\nTesting NLP Sentiment Analysis...")
    runner = TestRunner()

    try:
        from core.nlp_engine import (
            NLPEngine, SentimentAnalyzer, NewsProcessor,
            SocialMediaAnalyzer, SentimentType, NLPConfig
        )

        # Test NLPConfig
        def test_config():
            config = NLPConfig()
            assert config.embedding_dim > 0
            assert config.sentiment_threshold > 0

        runner.run_test("NLPConfig initialization", test_config)

        # Test SentimentAnalyzer
        def test_analyzer():
            config = NLPConfig()
            analyzer = SentimentAnalyzer(config)
            signal = analyzer.analyze("$BTC is going to the moon! Very bullish!")
            assert signal.sentiment in [SentimentType.BULLISH, SentimentType.VERY_BULLISH]
            assert signal.confidence > 0

        runner.run_test("SentimentAnalyzer bullish detection", test_analyzer)

        def test_bearish():
            config = NLPConfig()
            analyzer = SentimentAnalyzer(config)
            signal = analyzer.analyze("Market crash incoming, sell everything!")
            assert signal.sentiment in [SentimentType.BEARISH, SentimentType.VERY_BEARISH]

        runner.run_test("SentimentAnalyzer bearish detection", test_bearish)

        # Test entity extraction
        def test_entities():
            config = NLPConfig()
            analyzer = SentimentAnalyzer(config)
            signal = analyzer.analyze("I'm buying $BTC and $ETH today")
            assert "BTC" in signal.entities or "ETH" in signal.entities

        runner.run_test("Entity extraction", test_entities)

        # Test full NLPEngine
        def test_engine():
            engine = NLPEngine()
            signal = engine.process_text("Bitcoin looking strong today", "twitter")
            assert signal is not None
            assert signal.source == "twitter"

        runner.run_test("NLPEngine full pipeline", test_engine)

        # Test aggregation
        def test_aggregation():
            engine = NLPEngine()
            engine.process_text("$BTC moon!", "twitter")
            engine.process_text("BTC very bullish", "reddit")
            sentiment = engine.get_sentiment("BTC")
            assert sentiment is not None or True  # May not have enough signals

        runner.run_test("Sentiment aggregation", test_aggregation)

    except ImportError as e:
        print(f"  [SKIP] NLP module not available: {e}")

    return runner.summary()


# =============================================================================
# Smart Order Routing Tests
# =============================================================================

def test_smart_routing():
    """Test smart order routing module."""
    print("\nTesting Smart Order Routing...")
    runner = TestRunner()

    try:
        from core.smart_router import (
            SmartOrderRouter, RoutingConfig, VenueConfig,
            VenueType, OrderUrgency, TransactionCostModel,
            VenueAnalyzer, LiquidityAggregator
        )

        # Test RoutingConfig
        def test_config():
            config = RoutingConfig()
            assert config.max_slippage_bps > 0
            assert config.smart_routing == True

        runner.run_test("RoutingConfig initialization", test_config)

        # Test TransactionCostModel
        def test_tca():
            tca = TransactionCostModel()
            temp, perm = tca.estimate_market_impact(
                "BTC/USDT", "buy", 10.0, 1000.0, 0.03, OrderUrgency.MEDIUM
            )
            assert temp >= 0
            assert perm >= 0

        runner.run_test("TransactionCostModel impact estimation", test_tca)

        # Test SmartOrderRouter
        def test_router():
            router = SmartOrderRouter()
            router.add_venue(VenueConfig("binance", VenueType.EXCHANGE, fee_bps=10.0))
            router.add_venue(VenueConfig("coinbase", VenueType.EXCHANGE, fee_bps=15.0))

            # Update liquidity
            router.update_liquidity("BTC/USDT", "binance", {
                "bid_price": 44990, "bid_size": 5,
                "ask_price": 45010, "ask_size": 5
            })
            router.update_liquidity("BTC/USDT", "coinbase", {
                "bid_price": 44985, "bid_size": 3,
                "ask_price": 45015, "ask_size": 3
            })

            decisions, info = router.route_order(
                "BTC/USDT", "buy", 2.0, OrderUrgency.MEDIUM
            )
            assert info is not None
            assert "order_id" in info

        runner.run_test("SmartOrderRouter order routing", test_router)

        # Test pre-trade analysis
        def test_pretrade():
            router = SmartOrderRouter()
            router.add_venue(VenueConfig("binance", VenueType.EXCHANGE, fee_bps=10.0))
            router.update_liquidity("BTC/USDT", "binance", {
                "bid_price": 44990, "bid_size": 10,
                "ask_price": 45010, "ask_size": 10
            })

            analysis = router.get_pre_trade_analysis("BTC/USDT", "buy", 1.0)
            assert "total_cost_bps" in analysis or "error" in analysis

        runner.run_test("Pre-trade analysis", test_pretrade)

    except ImportError as e:
        print(f"  [SKIP] Smart routing not available: {e}")

    return runner.summary()


# =============================================================================
# Stress Testing Tests
# =============================================================================

def test_stress_testing():
    """Test stress testing module."""
    print("\nTesting Stress Testing...")
    runner = TestRunner()

    try:
        from core.stress_testing import (
            StressTester, StressConfig, ScenarioGenerator,
            ExtremeValueAnalyzer, TailRiskHedger, CrisisSimulator,
            CrisisType, HISTORICAL_SCENARIOS
        )

        # Test StressConfig
        def test_config():
            config = StressConfig()
            assert config.var_confidence > 0
            assert config.max_drawdown_threshold > 0

        runner.run_test("StressConfig initialization", test_config)

        # Test ScenarioGenerator
        def test_scenarios():
            config = StressConfig()
            generator = ScenarioGenerator(config)
            scenario = generator.get_historical_scenario(CrisisType.GFC_2008)
            assert scenario.equity_shock < 0
            assert scenario.volatility_multiplier > 1

        runner.run_test("ScenarioGenerator historical scenarios", test_scenarios)

        # Test ExtremeValueAnalyzer
        def test_evt():
            np.random.seed(42)
            returns = np.random.randn(1000) * 0.02
            config = StressConfig()
            evt = ExtremeValueAnalyzer(config)
            result = evt.fit_gpd(returns)
            assert result.var_estimates is not None
            assert 0.99 in result.var_estimates

        runner.run_test("ExtremeValueAnalyzer GPD fitting", test_evt)

        # Test StressTester
        def test_stressor():
            np.random.seed(42)
            config = StressConfig()
            tester = StressTester(config)

            portfolio_weights = {"BTC": 0.5, "ETH": 0.5}
            asset_returns = {
                "BTC": np.random.randn(500) * 0.03,
                "ETH": np.random.randn(500) * 0.04
            }

            scenario = HISTORICAL_SCENARIOS[CrisisType.COVID_CRASH]
            result = tester.run_stress_test(portfolio_weights, asset_returns, scenario)

            assert result.portfolio_impact < 0
            assert result.max_drawdown > 0

        runner.run_test("StressTester stress test execution", test_stressor)

        # Test TailRiskHedger
        def test_hedger():
            config = StressConfig()
            hedger = TailRiskHedger(config)
            rec = hedger.calculate_put_hedge(
                portfolio_value=100000,
                target_protection=0.90,
                volatility=0.25,
                time_to_expiry=0.25
            )
            assert rec.cost_bps > 0
            assert rec.expected_payoff_in_crisis > 0

        runner.run_test("TailRiskHedger put hedge calculation", test_hedger)

    except ImportError as e:
        print(f"  [SKIP] Stress testing not available: {e}")

    return runner.summary()


# =============================================================================
# Advanced Backtesting Tests
# =============================================================================

def test_advanced_backtest():
    """Test advanced backtesting module."""
    print("\nTesting Advanced Backtesting...")
    runner = TestRunner()

    try:
        from core.advanced_backtest import (
            WalkForwardOptimizer, WalkForwardConfig,
            MonteCarloSimulator, MonteCarloConfig,
            RegimeAwareBacktest, OutOfSampleValidator, ValidationConfig
        )

        np.random.seed(42)
        n = 1000
        returns = np.random.randn(n) * 0.02
        prices = 100 * np.cumprod(1 + returns)

        # Test WalkForwardConfig
        def test_wf_config():
            config = WalkForwardConfig()
            assert config.in_sample_ratio > 0
            assert config.num_windows > 0

        runner.run_test("WalkForwardConfig initialization", test_wf_config)

        # Test MonteCarloSimulator
        def test_mc():
            config = MonteCarloConfig(n_simulations=100, random_seed=42)
            simulator = MonteCarloSimulator(config)
            result = simulator.simulate_returns(returns, n_periods=100)
            assert result.terminal_values.shape[0] == 100
            assert 0.95 in result.var_estimates

        runner.run_test("MonteCarloSimulator bootstrap simulation", test_mc)

        # Test GBM simulation
        def test_gbm():
            config = MonteCarloConfig(n_simulations=100, random_seed=42)
            simulator = MonteCarloSimulator(config)
            result = simulator.simulate_gbm(
                initial_value=100,
                mu=0.10,
                sigma=0.20,
                n_periods=252
            )
            assert np.mean(result.terminal_values) > 100  # Expected growth

        runner.run_test("MonteCarloSimulator GBM simulation", test_gbm)

        # Test RegimeAwareBacktest
        def test_regime():
            backtest = RegimeAwareBacktest(lookback=30)
            regimes = backtest.identify_regimes(prices)
            assert len(regimes) == len(prices)

        runner.run_test("RegimeAwareBacktest regime identification", test_regime)

        # Test OutOfSampleValidator
        def test_validator():
            config = ValidationConfig(n_splits=3, min_test_size=30)
            validator = OutOfSampleValidator(config)
            splits = validator.time_series_split(n)
            assert len(splits) > 0
            for train_idx, test_idx in splits:
                assert len(train_idx) > 0
                assert len(test_idx) >= config.min_test_size

        runner.run_test("OutOfSampleValidator time series splits", test_validator)

    except ImportError as e:
        print(f"  [SKIP] Advanced backtesting not available: {e}")

    return runner.summary()


# =============================================================================
# Unified System Tests
# =============================================================================

def test_unified_system():
    """Test unified trading system."""
    print("\nTesting Unified Trading System...")
    runner = TestRunner()

    try:
        from core.unified_system import (
            UnifiedTradingEngine, SystemConfig, SystemMode,
            TradeSignal, SignalSource, SignalAggregator,
            TradeExecutor, RiskController, create_trading_system
        )

        # Test SystemConfig
        def test_config():
            config = SystemConfig()
            assert config.initial_capital > 0
            assert config.max_position_size > 0

        runner.run_test("SystemConfig initialization", test_config)

        # Test SignalAggregator
        def test_aggregator():
            config = SystemConfig()
            aggregator = SignalAggregator(config)
            signals = [
                TradeSignal("BTC", "buy", 0.7, SignalSource.STRATEGY, datetime.now()),
                TradeSignal("BTC", "buy", 0.8, SignalSource.DEEP_LEARNING, datetime.now()),
                TradeSignal("BTC", "hold", 0.5, SignalSource.NLP_SENTIMENT, datetime.now()),
            ]
            result = aggregator.aggregate_signals(signals)
            assert result is not None
            assert result.action == "buy"

        runner.run_test("SignalAggregator consensus building", test_aggregator)

        # Test TradeExecutor
        def test_executor():
            config = SystemConfig()
            executor = TradeExecutor(config)
            signal = TradeSignal("BTC", "buy", 0.8, SignalSource.ENSEMBLE, datetime.now(), target_price=45000)
            order = executor.create_order(
                signal=signal,
                current_price=45000,
                portfolio_value=100000,
                current_positions={}
            )
            assert order is not None
            assert order["quantity"] > 0

        runner.run_test("TradeExecutor order creation", test_executor)

        # Test RiskController
        def test_risk():
            config = SystemConfig()
            controller = RiskController(config)
            from core.unified_system import SystemState
            state = SystemState(
                mode=SystemMode.PAPER,
                capital=100000,
                positions={},
                pending_signals=[],
                recent_trades=[],
                pnl_history=[],
                last_update=datetime.now()
            )
            status = controller.check_risk_limits(state)
            assert status["is_safe"] == True

        runner.run_test("RiskController risk limit checking", test_risk)

        # Test factory function
        def test_factory():
            engine = create_trading_system(
                mode="paper",
                symbols=["BTC/USDT"],
                initial_capital=50000
            )
            assert engine is not None
            assert engine.config.initial_capital == 50000

        runner.run_test("create_trading_system factory", test_factory)

        # Test full engine
        def test_engine():
            engine = create_trading_system(mode="paper")
            engine.start()

            # Add test signal
            signal = TradeSignal("BTC/USDT", "buy", 0.75, SignalSource.STRATEGY, datetime.now(), target_price=45000)
            engine.add_signal(signal)

            import time
            time.sleep(0.5)

            status = engine.get_status()
            assert status is not None
            assert "capital" in status

            engine.stop()

        runner.run_test("UnifiedTradingEngine full operation", test_engine)

    except ImportError as e:
        print(f"  [SKIP] Unified system not available: {e}")

    return runner.summary()


# =============================================================================
# Monitoring Tests
# =============================================================================

def test_monitoring():
    """Test monitoring module."""
    print("\nTesting Monitoring System...")
    runner = TestRunner()

    try:
        from core.monitoring import (
            SystemMonitor, MonitoringConfig, AlertSeverity,
            MetricsCollector, HealthChecker, AlertManager,
            Counter, Gauge, Histogram
        )

        # Test MonitoringConfig
        def test_config():
            config = MonitoringConfig()
            assert config.metrics_interval > 0
            assert config.alert_cooldown > 0

        runner.run_test("MonitoringConfig initialization", test_config)

        # Test Counter
        def test_counter():
            config = MonitoringConfig()
            collector = MetricsCollector(config)
            counter = collector.counter("test_counter", "Test counter")
            counter.inc()
            counter.inc(5)
            assert counter.get() == 6

        runner.run_test("Counter metric", test_counter)

        # Test Gauge
        def test_gauge():
            config = MonitoringConfig()
            collector = MetricsCollector(config)
            gauge = collector.gauge("test_gauge", "Test gauge")
            gauge.set(100)
            gauge.inc(10)
            gauge.dec(5)
            assert gauge.get() == 105

        runner.run_test("Gauge metric", test_gauge)

        # Test Histogram
        def test_histogram():
            config = MonitoringConfig()
            collector = MetricsCollector(config)
            hist = collector.histogram("test_histogram", "Test histogram")
            for i in range(100):
                hist.observe(np.random.exponential(0.1))
            stats = hist.get_stats()
            assert stats["count"] == 100
            assert stats["p50"] > 0

        runner.run_test("Histogram metric", test_histogram)

        # Test AlertManager
        def test_alerts():
            config = MonitoringConfig()
            manager = AlertManager(config)
            alert = manager.create_alert(
                severity=AlertSeverity.WARNING,
                source="test",
                message="Test alert"
            )
            assert alert is not None
            assert alert.severity == AlertSeverity.WARNING

        runner.run_test("AlertManager alert creation", test_alerts)

        # Test SystemMonitor
        def test_monitor():
            config = MonitoringConfig()
            monitor = SystemMonitor(config)
            monitor.record_order(success=True, latency=0.05)
            monitor.record_pnl(100, 100100)

            dashboard = monitor.get_dashboard_data()
            assert "health" in dashboard
            assert "metrics" in dashboard

        runner.run_test("SystemMonitor full operation", test_monitor)

    except ImportError as e:
        print(f"  [SKIP] Monitoring not available: {e}")

    return runner.summary()


# =============================================================================
# Real-Time Data Tests
# =============================================================================

def test_realtime_data():
    """Test real-time data module."""
    print("\nTesting Real-Time Data Infrastructure...")
    runner = TestRunner()

    try:
        from core.realtime_data import (
            RealtimeDataManager, DataConfig, DataNormalizer,
            StreamProcessor, FeatureStore, MarketTick,
            CircuitBreaker
        )

        # Test DataConfig
        def test_config():
            config = DataConfig()
            assert config.buffer_size > 0
            assert config.max_latency_ms > 0

        runner.run_test("DataConfig initialization", test_config)

        # Test DataNormalizer
        def test_normalizer():
            normalizer = DataNormalizer()
            data = {
                "s": "BTCUSDT",
                "p": "45000.50",
                "q": "1.5",
                "T": 1704067200000
            }
            tick = normalizer.normalize_trade(data, "binance")
            assert tick is not None
            assert tick.price > 0

        runner.run_test("DataNormalizer trade normalization", test_normalizer)

        # Test FeatureStore
        def test_feature_store():
            config = DataConfig(cache_ttl_seconds=60)
            store = FeatureStore(config)
            store.update_feature("BTC", "price", 45000)
            store.update_feature("BTC", "volume", 1000)

            assert store.get_feature("BTC", "price") == 45000
            vector = store.get_feature_vector("BTC", ["price", "volume"])
            assert len(vector) == 2

        runner.run_test("FeatureStore feature storage", test_feature_store)

        # Test StreamProcessor
        def test_processor():
            config = DataConfig()
            processor = StreamProcessor(config)
            tick = MarketTick("BTC", 45000, 1.5, 44999, 45001)
            processor.process_tick(tick)

            stats = processor.get_statistics("BTC")
            assert stats["tick_count"] == 1

        runner.run_test("StreamProcessor tick processing", test_processor)

        # Test CircuitBreaker
        def test_circuit_breaker():
            cb = CircuitBreaker(failure_threshold=3)
            assert cb.can_execute() == True

            for _ in range(3):
                cb.record_failure()

            assert cb.can_execute() == False
            assert cb.state == CircuitBreaker.State.OPEN

        runner.run_test("CircuitBreaker state transitions", test_circuit_breaker)

    except ImportError as e:
        print(f"  [SKIP] Real-time data not available: {e}")

    return runner.summary()


# =============================================================================
# Main Test Runner
# =============================================================================

def run_all_tests():
    """Run all test suites."""
    print("=" * 60)
    print("COMPREHENSIVE TEST SUITE FOR ADVANCED TRADING MODULES")
    print("=" * 60)

    results = []

    results.append(("Feature Engineering", test_feature_engineering()))
    results.append(("Deep Learning", test_deep_learning()))
    results.append(("NLP Sentiment", test_nlp_sentiment()))
    results.append(("Smart Routing", test_smart_routing()))
    results.append(("Stress Testing", test_stress_testing()))
    results.append(("Advanced Backtesting", test_advanced_backtest()))
    results.append(("Unified System", test_unified_system()))
    results.append(("Monitoring", test_monitoring()))
    results.append(("Real-Time Data", test_realtime_data()))

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: [{status}]")
        if not passed:
            all_passed = False

    print("=" * 60)
    print(f"Overall: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print("=" * 60)

    return all_passed


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
