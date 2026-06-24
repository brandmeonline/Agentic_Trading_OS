import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import ExecutionConfig, ExecutionEngine
from core.unified_system import (
    SignalSource,
    SystemConfig,
    SystemMode,
    TradeSignal,
    UnifiedTradingEngine,
)


def test_low_volume_signal_flushes_on_interval_and_shutdown_joins_threads():
    engine = UnifiedTradingEngine(SystemConfig(
        mode=SystemMode.PAPER,
        initial_capital=100000,
        signal_flush_interval_seconds=0.1,
        signal_batch_size=3,
        max_signal_queue_size=10,
    ))

    engine.start()
    accepted = engine.add_signal(TradeSignal(
        symbol="BTC/USDT",
        action="buy",
        confidence=0.9,
        source=SignalSource.MANUAL,
        timestamp=datetime.now(),
        target_price=100.0,
    ))

    assert accepted is True

    deadline = time.time() + 2
    while time.time() < deadline and "BTC/USDT" not in engine.state.positions:
        time.sleep(0.02)

    engine.stop()

    assert "BTC/USDT" in engine.state.positions
    status = engine.get_status()
    assert "BTC/USDT" in status["ledger"]["positions"]
    assert status["ledger"]["positions"]["BTC/USDT"]["side"] == "long"
    assert not engine._signal_thread.is_alive()
    assert not engine._monitor_thread.is_alive()


def test_full_signal_queue_rejects_without_blocking():
    engine = UnifiedTradingEngine(SystemConfig(max_signal_queue_size=1))

    signal = TradeSignal(
        symbol="BTC/USDT",
        action="buy",
        confidence=0.9,
        source=SignalSource.MANUAL,
        timestamp=datetime.now(),
        target_price=100.0,
    )

    assert engine.add_signal(signal) is True
    assert engine.add_signal(signal) is False
    assert engine.state.error_count == 1


def test_live_mode_requires_explicit_execution_engine():
    config = SystemConfig(mode=SystemMode.LIVE)

    try:
        UnifiedTradingEngine(config)
    except RuntimeError as exc:
        assert "Live mode requires" in str(exc)
    else:
        raise AssertionError("Live mode started without explicit execution engine")


def test_unified_engine_restores_persisted_ledger_positions(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    config = SystemConfig(
        mode=SystemMode.PAPER,
        initial_capital=10000,
        ledger_persist_path=str(ledger_file),
    )
    engine = UnifiedTradingEngine(config)
    engine.ledger.record_order("buy-1", "BTC/USDT", "buy", 2, "market")
    engine.ledger.record_fill("buy-1", "BTC/USDT", "buy", 2, 100)

    restored = UnifiedTradingEngine(config)

    assert restored.ledger.cash == 9800
    assert restored.state.capital == 9800
    assert restored.state.positions["BTC/USDT"].side == "long"
    assert restored.state.positions["BTC/USDT"].quantity == 2
    assert restored.state.positions["BTC/USDT"].entry_price == 100


def test_unified_engine_restores_sqlite_ledger_positions(tmp_path):
    ledger_file = tmp_path / "ledger.sqlite"
    config = SystemConfig(
        mode=SystemMode.PAPER,
        initial_capital=10000,
        ledger_sqlite_path=str(ledger_file),
    )
    engine = UnifiedTradingEngine(config)
    engine.ledger.record_order("buy-1", "BTC/USDT", "buy", 2, "market")
    engine.ledger.record_fill("buy-1", "BTC/USDT", "buy", 2, 100)

    restored = UnifiedTradingEngine(config)

    assert restored.ledger.cash == 9800
    assert restored.state.capital == 9800
    assert restored.state.positions["BTC/USDT"].side == "long"
    assert restored.state.positions["BTC/USDT"].quantity == 2
    assert restored.state.positions["BTC/USDT"].entry_price == 100


def test_unified_engine_exposes_broker_position_reconciliation():
    engine = UnifiedTradingEngine(SystemConfig(mode=SystemMode.PAPER, initial_capital=10000))
    engine.ledger.record_order("buy-1", "BTC/USDT", "buy", 2, "market")
    engine.ledger.record_fill("buy-1", "BTC/USDT", "buy", 2, 100)

    report = engine.reconcile_broker_positions([
        {"symbol": "BTC/USDT", "qty": 1, "avg_entry_price": 100, "side": "long"}
    ])

    assert report["in_sync"] is False
    assert report["discrepancies"][0]["symbol"] == "BTC/USDT"
    assert report["discrepancies"][0]["quantity_delta"] == -1


def test_broker_reconciliation_drift_disables_live_trading():
    class DriftedBrokerAdapter:
        def get_positions(self):
            return [{"symbol": "BTC/USDT", "qty": 1, "avg_entry_price": 100, "side": "long"}]

    execution_engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        broker_adapter=DriftedBrokerAdapter(),
    )
    engine = UnifiedTradingEngine(
        SystemConfig(mode=SystemMode.LIVE, initial_capital=10000),
        execution_engine=execution_engine,
    )
    engine.ledger.record_order("buy-1", "BTC/USDT", "buy", 2, "market")
    engine.ledger.record_fill("buy-1", "BTC/USDT", "buy", 2, 100)

    report = engine.run_broker_reconciliation()
    status = engine.get_status()

    assert report["in_sync"] is False
    assert report["action"] == "trading_disabled_until_reconciled"
    assert engine.state.circuit_breaker_active is True
    assert engine.state.is_trading_enabled is False
    assert status["broker_reconciliation"]["discrepancies"][0]["symbol"] == "BTC/USDT"


def test_live_monitor_runs_broker_reconciliation_automatically():
    class SyncedBrokerAdapter:
        def __init__(self):
            self.calls = 0

        def get_positions(self):
            self.calls += 1
            return []

    adapter = SyncedBrokerAdapter()
    execution_engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        broker_adapter=adapter,
    )
    engine = UnifiedTradingEngine(
        SystemConfig(
            mode=SystemMode.LIVE,
            broker_reconciliation_interval_seconds=0.0,
        ),
        execution_engine=execution_engine,
    )

    engine.start()
    try:
        deadline = time.time() + 2
        while time.time() < deadline and adapter.calls == 0:
            time.sleep(0.02)
    finally:
        engine.stop()

    assert adapter.calls >= 1
    assert engine.last_broker_reconciliation["in_sync"] is True
    assert engine.state.circuit_breaker_active is False
