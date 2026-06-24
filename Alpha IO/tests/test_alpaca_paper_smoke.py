import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.alpaca_connector import create_alpaca_paper_client
from core.execution import AlpacaExecutionAdapter
from core.ledger import TradingLedger


HAS_ALPACA_PAPER_CREDS = bool(
    os.environ.get("ALPACA_API_KEY")
    and os.environ.get("ALPACA_API_SECRET")
)


@pytest.mark.skipif(
    not HAS_ALPACA_PAPER_CREDS,
    reason="ALPACA_API_KEY and ALPACA_API_SECRET are required for paper broker smoke tests",
)
def test_alpaca_paper_account_and_position_reconciliation_smoke():
    client = create_alpaca_paper_client(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_API_SECRET"],
    )
    account = client.get_account()
    adapter = AlpacaExecutionAdapter(client)
    positions = adapter.get_positions()
    ledger = TradingLedger(initial_cash=account.cash)

    report = ledger.reconcile_positions(positions)

    assert account.status
    assert account.currency
    assert isinstance(positions, list)
    assert "in_sync" in report
    assert "discrepancies" in report
