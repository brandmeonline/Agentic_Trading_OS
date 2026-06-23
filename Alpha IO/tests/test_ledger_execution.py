import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import ExecutionConfig, ExecutionEngine, OrderSide, OrderType
from core.ledger import TradingLedger


def test_ledger_tracks_cash_positions_and_realized_pnl():
    ledger = TradingLedger(initial_cash=100000)

    ledger.record_order("buy-1", "BTC/USD", "buy", 10, "market")
    ledger.record_fill("buy-1", "BTC/USD", "buy", 10, 100)
    assert ledger.cash == 99000
    assert ledger.positions["BTC/USD"].quantity == 10
    assert ledger.positions["BTC/USD"].avg_price == 100

    ledger.record_order("sell-1", "BTC/USD", "sell", 4, "market")
    ledger.record_fill("sell-1", "BTC/USD", "sell", 4, 110)
    assert ledger.cash == 99440
    assert ledger.positions["BTC/USD"].quantity == 6
    assert ledger.realized_pnl == 40

    ledger.record_order("sell-2", "BTC/USD", "sell", 10, "market")
    ledger.record_fill("sell-2", "BTC/USD", "sell", 10, 90)
    assert ledger.positions["BTC/USD"].quantity == -4
    assert ledger.positions["BTC/USD"].avg_price == 90
    assert ledger.realized_pnl == -20

    ledger.record_order("buy-2", "BTC/USD", "buy", 4, "market")
    ledger.record_fill("buy-2", "BTC/USD", "buy", 4, 80)
    assert "BTC/USD" not in ledger.positions
    assert ledger.realized_pnl == 20


def test_execution_engine_records_fills_in_ledger():
    ledger = TradingLedger(initial_cash=10000)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=True, max_slippage_pct=0),
        ledger=ledger,
    )
    engine.set_price("ETH/USD", 200)

    order = engine.create_order("ETH/USD", OrderSide.BUY, 3, OrderType.MARKET)
    result = engine.submit_order(order)

    assert result.success is True
    assert ledger.orders[order.id].status == "filled"
    assert ledger.orders[order.id].filled_quantity == 3
    assert ledger.positions["ETH/USD"].quantity == 3
    assert ledger.cash == 9400


def test_execution_engine_records_rejection_and_cancel_statuses():
    ledger = TradingLedger(initial_cash=10000)
    engine = ExecutionEngine(ExecutionConfig(simulation_mode=True), ledger=ledger)

    no_price_order = engine.create_order("MISSING/USD", OrderSide.BUY, 1, OrderType.MARKET)
    rejection = engine.submit_order(no_price_order)
    assert rejection.success is False
    assert ledger.orders[no_price_order.id].status == "rejected"

    pending_order = engine.create_order("ETH/USD", OrderSide.BUY, 1, OrderType.LIMIT, price=100)
    assert engine.cancel_order(pending_order.id) is True
    assert ledger.orders[pending_order.id].status == "cancelled"
