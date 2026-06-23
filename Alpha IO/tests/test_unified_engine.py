import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
