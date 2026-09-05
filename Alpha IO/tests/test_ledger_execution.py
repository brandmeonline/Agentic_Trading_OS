import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.execution import (
    AlpacaExecutionAdapter,
    ExecutionConfig,
    ExecutionEngine,
    OrderSide,
    OrderType,
)
from core.ledger import TradingLedger
from core.order_intent import OrderIntentJournal


def _journal():
    """A throwaway durable intent journal.

    ATOS-P0-EXEC-002: live mode refuses to submit without one, so every
    live-mode engine in these tests must supply it.
    """
    return OrderIntentJournal(str(Path(tempfile.mkdtemp()) / "intents.sqlite"))


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


def test_ledger_persists_and_restores_order_fill_position_state(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    ledger = TradingLedger(initial_cash=100000, persist_path=str(ledger_file))

    ledger.record_order("buy-1", "BTC/USD", "buy", 10, "market")
    ledger.record_fill("buy-1", "BTC/USD", "buy", 10, 100)

    restored = TradingLedger(initial_cash=0, persist_path=str(ledger_file))

    assert restored.initial_cash == 100000
    assert restored.cash == 99000
    assert restored.orders["buy-1"].status == "filled"
    assert restored.orders["buy-1"].filled_quantity == 10
    assert restored.fills[0].notional == 1000
    assert restored.positions["BTC/USD"].quantity == 10
    assert restored.positions["BTC/USD"].avg_price == 100

    restored.record_order("sell-1", "BTC/USD", "sell", 4, "market")
    restored.record_fill("sell-1", "BTC/USD", "sell", 4, 110)

    reopened = TradingLedger(initial_cash=0, persist_path=str(ledger_file))
    assert reopened.cash == 99440
    assert reopened.positions["BTC/USD"].quantity == 6
    assert reopened.realized_pnl == 40


def test_ledger_sqlite_persists_and_restores_order_fill_position_state(tmp_path):
    ledger_file = tmp_path / "ledger.sqlite"
    ledger = TradingLedger(initial_cash=100000, sqlite_path=str(ledger_file))

    ledger.record_order("buy-1", "BTC/USD", "buy", 10, "market", metadata={"source": "test"})
    ledger.record_fill("buy-1", "BTC/USD", "buy", 10, 100)

    restored = TradingLedger(initial_cash=0, sqlite_path=str(ledger_file))

    assert restored.initial_cash == 100000
    assert restored.cash == 99000
    assert restored.orders["buy-1"].status == "filled"
    assert restored.orders["buy-1"].filled_quantity == 10
    assert restored.orders["buy-1"].metadata == {"source": "test"}
    assert restored.fills[0].notional == 1000
    assert restored.positions["BTC/USD"].quantity == 10
    assert restored.positions["BTC/USD"].avg_price == 100

    restored.record_order("sell-1", "BTC/USD", "sell", 4, "market")
    restored.record_fill("sell-1", "BTC/USD", "sell", 4, 110)

    reopened = TradingLedger(initial_cash=0, sqlite_path=str(ledger_file))
    assert reopened.cash == 99440
    assert reopened.positions["BTC/USD"].quantity == 6
    assert reopened.realized_pnl == 40


def test_ledger_corrupt_persisted_state_fails_closed(tmp_path):
    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text("{not-valid-json", encoding="utf-8")

    try:
        TradingLedger(initial_cash=100000, persist_path=str(ledger_file))
    except RuntimeError as exc:
        assert "Failed to load persisted trading ledger" in str(exc)
    else:
        raise AssertionError("Corrupt ledger state was ignored")


def test_ledger_corrupt_sqlite_state_fails_closed(tmp_path):
    ledger_file = tmp_path / "ledger.sqlite"
    ledger_file.write_text("not-a-sqlite-database", encoding="utf-8")

    try:
        TradingLedger(initial_cash=100000, sqlite_path=str(ledger_file))
    except RuntimeError as exc:
        assert "Failed to load SQLite trading ledger" in str(exc)
    else:
        raise AssertionError("Corrupt SQLite ledger state was ignored")


def test_ledger_rejects_dual_persistence_paths(tmp_path):
    try:
        TradingLedger(
            initial_cash=100000,
            persist_path=str(tmp_path / "ledger.json"),
            sqlite_path=str(tmp_path / "ledger.sqlite"),
        )
    except ValueError as exc:
        assert "Use either persist_path or sqlite_path" in str(exc)
    else:
        raise AssertionError("Ledger accepted competing persistence backends")


def test_ledger_rejects_overfills_and_mismatched_fills():
    ledger = TradingLedger(initial_cash=10000)
    ledger.record_order("buy-1", "BTC/USD", "buy", 5, "market")

    try:
        ledger.record_fill("buy-1", "BTC/USD", "buy", 6, 100)
    except ValueError as exc:
        assert "exceeds order quantity" in str(exc)
    else:
        raise AssertionError("Overfill was accepted")

    try:
        ledger.record_fill("buy-1", "ETH/USD", "buy", 1, 100)
    except ValueError as exc:
        assert "symbol does not match" in str(exc)
    else:
        raise AssertionError("Mismatched symbol fill was accepted")

    try:
        ledger.record_fill("buy-1", "BTC/USD", "sell", 1, 100)
    except ValueError as exc:
        assert "side does not match" in str(exc)
    else:
        raise AssertionError("Mismatched side fill was accepted")

    assert ledger.fills == []
    assert ledger.cash == 10000
    assert ledger.positions == {}


def test_ledger_reconciles_positions_against_broker_snapshot():
    ledger = TradingLedger(initial_cash=10000)
    ledger.record_order("buy-1", "BTC/USD", "buy", 5, "market")
    ledger.record_fill("buy-1", "BTC/USD", "buy", 5, 100)

    synced = ledger.reconcile_positions([
        {"symbol": "BTC/USD", "qty": 5, "avg_entry_price": 100, "side": "long"}
    ])
    assert synced["in_sync"] is True
    assert synced["discrepancies"] == []

    drifted = ledger.reconcile_positions([
        {"symbol": "BTC/USD", "qty": 4, "avg_entry_price": 101, "side": "long"},
        {"symbol": "ETH/USD", "qty": 1, "avg_entry_price": 200, "side": "long"},
    ])
    assert drifted["in_sync"] is False
    assert {item["symbol"] for item in drifted["discrepancies"]} == {"BTC/USD", "ETH/USD"}


def test_ledger_reconciliation_handles_short_broker_positions():
    ledger = TradingLedger(initial_cash=10000)
    ledger.record_order("sell-1", "ETH/USD", "sell", 3, "market")
    ledger.record_fill("sell-1", "ETH/USD", "sell", 3, 200)

    report = ledger.reconcile_positions([
        {"symbol": "ETH/USD", "qty": 3, "avg_entry_price": 200, "side": "short"}
    ])

    assert report["in_sync"] is True
    assert report["broker_positions"]["ETH/USD"]["quantity"] == -3


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


def test_execution_position_reads_are_ledger_authoritative():
    ledger = TradingLedger(initial_cash=10000)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=True, max_slippage_pct=0),
        ledger=ledger,
    )
    engine.set_price("ETH/USD", 200)
    order = engine.create_order("ETH/USD", OrderSide.BUY, 3, OrderType.MARKET)
    assert engine.submit_order(order).success is True

    engine.positions["ETH/USD"] = 999

    assert engine.get_position("ETH/USD") == 3
    assert engine.get_all_positions() == {"ETH/USD": 3}
    assert engine.get_statistics()["open_positions"] == 1


def test_close_position_uses_ledger_quantity_not_legacy_cache():
    ledger = TradingLedger(initial_cash=10000)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=True, max_slippage_pct=0),
        ledger=ledger,
    )
    engine.set_price("ETH/USD", 200)
    order = engine.create_order("ETH/USD", OrderSide.BUY, 3, OrderType.MARKET)
    assert engine.submit_order(order).success is True

    engine.positions["ETH/USD"] = 999
    close_result = engine.close_position("ETH/USD", price=210)

    assert close_result is not None
    assert close_result.success is True
    assert close_result.order.quantity == 3
    assert engine.get_position("ETH/USD") == 0
    assert engine.get_all_positions() == {}
    assert engine.positions == {}
    assert ledger.realized_pnl == 30


def test_live_execution_adapter_records_fills_in_ledger():
    class FakeBrokerAdapter:
        def __init__(self):
            self.submitted = []

        def submit_order(self, order):
            self.submitted.append(order)
            return {
                "status": "filled",
                "filled_quantity": order.quantity,
                "average_price": 250,
                "fills": [
                    {
                        "quantity": order.quantity,
                        "price": 250,
                        "timestamp": "2026-06-23T12:00:00",
                    }
                ],
            }

    ledger = TradingLedger(initial_cash=10000)
    adapter = FakeBrokerAdapter()
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        ledger=ledger,
        broker_adapter=adapter,
        intent_journal=_journal(),
    )

    order = engine.create_order("ETH/USD", OrderSide.BUY, 2, OrderType.MARKET)
    result = engine.submit_order(order)

    assert result.success is True
    assert result.message == "Live order accepted"
    assert adapter.submitted == [order]
    assert order.status.value == "filled"
    assert ledger.orders[order.id].status == "filled"
    assert ledger.positions["ETH/USD"].quantity == 2
    assert ledger.positions["ETH/USD"].avg_price == 250
    assert ledger.cash == 9500
    assert engine.get_all_positions() == {"ETH/USD": 2}


def test_live_execution_without_adapter_fails_closed():
    ledger = TradingLedger(initial_cash=10000)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        ledger=ledger,
        intent_journal=_journal(),
    )

    order = engine.create_order("ETH/USD", OrderSide.BUY, 2, OrderType.MARKET)
    result = engine.submit_order(order)

    assert result.success is False
    assert result.message == "Live execution adapter is not configured"
    assert order.status.value == "rejected"
    assert ledger.orders[order.id].status == "rejected"
    assert ledger.positions == {}


def test_alpaca_execution_adapter_maps_client_order_response():
    class FakeAlpacaClient:
        def __init__(self):
            self.placed = []
            self.cancelled = []

        def place_order(self, **kwargs):
            self.placed.append(kwargs)
            return {
                "order_id": "alpaca-123",
                "client_order_id": kwargs["client_order_id"],
                "symbol": kwargs["symbol"],
                "side": kwargs["side"],
                "order_type": kwargs["order_type"],
                "qty": kwargs["qty"],
                "filled_qty": kwargs["qty"],
                "status": "filled",
                "filled_avg_price": 101.5,
                "filled_at": "2026-06-23T12:00:00",
            }

        def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            return True

        def get_positions(self):
            return [{"symbol": "BTC/USD", "qty": 1, "avg_entry_price": 101.5, "side": "long"}]

    client = FakeAlpacaClient()
    adapter = AlpacaExecutionAdapter(client)
    ledger = TradingLedger(initial_cash=1000)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        ledger=ledger,
        broker_adapter=adapter,
        intent_journal=_journal(),
    )

    order = engine.create_order("BTC/USD", OrderSide.BUY, 1, OrderType.LIMIT, price=102)
    result = engine.submit_order(order)
    cancelled = adapter.cancel_order(order.id)

    assert result.success is True
    assert client.placed == [{
        "symbol": "BTC/USD",
        "qty": 1,
        "side": "buy",
        "order_type": "limit",
        "time_in_force": "day",
        "limit_price": 102,
        "stop_price": None,
        "client_order_id": order.id,
    }]
    assert cancelled is True
    assert client.cancelled == ["alpaca-123"]
    assert ledger.positions["BTC/USD"].quantity == 1
    assert ledger.positions["BTC/USD"].avg_price == 101.5


def test_execution_engine_reconciles_ledger_with_broker_adapter_positions():
    class FakeBrokerAdapter:
        def get_positions(self):
            return [{"symbol": "ETH/USD", "qty": 2, "avg_entry_price": 200, "side": "long"}]

    ledger = TradingLedger(initial_cash=10000)
    ledger.record_order("buy-1", "ETH/USD", "buy", 2, "market")
    ledger.record_fill("buy-1", "ETH/USD", "buy", 2, 200)
    engine = ExecutionEngine(
        ExecutionConfig(simulation_mode=False),
        ledger=ledger,
        broker_adapter=FakeBrokerAdapter(),
        intent_journal=_journal(),
    )

    report = engine.reconcile_with_broker()

    assert report["in_sync"] is True
    assert report["discrepancies"] == []
