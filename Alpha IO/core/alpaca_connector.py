"""
Alpaca Exchange Connector.

US-friendly trading API supporting:
- Stocks (US markets)
- Crypto (24/7)
- Paper trading (free)
- Live trading
"""

from __future__ import annotations

import os
import json
import time
import hmac
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
import urllib.request
import urllib.parse
import urllib.error


# =============================================================================
# Configuration
# =============================================================================

class AlpacaEnvironment(Enum):
    """Alpaca environments."""
    PAPER = "paper"
    LIVE = "live"


@dataclass
class AlpacaConfig:
    """Alpaca API configuration."""
    api_key: str = ""
    api_secret: str = ""
    environment: AlpacaEnvironment = AlpacaEnvironment.PAPER
    timeout: float = 30.0
    max_retries: int = 3


# API Endpoints
ALPACA_ENDPOINTS = {
    AlpacaEnvironment.PAPER: {
        "base": "https://paper-api.alpaca.markets",
        "data": "https://data.alpaca.markets",
        "stream": "wss://stream.data.alpaca.markets",
    },
    AlpacaEnvironment.LIVE: {
        "base": "https://api.alpaca.markets",
        "data": "https://data.alpaca.markets",
        "stream": "wss://stream.data.alpaca.markets",
    },
}


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class AlpacaAccount:
    """Alpaca account information."""
    account_id: str
    status: str
    currency: str
    cash: float
    portfolio_value: float
    buying_power: float
    equity: float
    last_equity: float
    daytrading_buying_power: float
    pattern_day_trader: bool
    trading_blocked: bool
    transfers_blocked: bool
    account_blocked: bool


@dataclass
class AlpacaPosition:
    """Alpaca position."""
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_plpc: float
    current_price: float
    side: str


@dataclass
class AlpacaOrder:
    """Alpaca order."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    qty: float
    filled_qty: float
    status: str
    created_at: str
    filled_at: Optional[str]
    filled_avg_price: Optional[float]
    time_in_force: str


@dataclass
class AlpacaBar:
    """Alpaca OHLCV bar."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    vwap: float


# =============================================================================
# Alpaca Client
# =============================================================================

class AlpacaClient:
    """Alpaca Trading API client."""

    def __init__(self, config: AlpacaConfig):
        self.config = config
        self._endpoints = ALPACA_ENDPOINTS[config.environment]
        self._connected = False

    @property
    def headers(self) -> Dict[str, str]:
        """Get authentication headers."""
        return {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.api_secret,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        base_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to Alpaca API."""
        base = base_url or self._endpoints["base"]
        url = f"{base}{endpoint}"

        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(url, method=method)

        for key, value in self.headers.items():
            request.add_header(key, value)

        if data:
            request.data = json.dumps(data).encode()

        for attempt in range(self.config.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:  # Rate limited
                    time.sleep(2 ** attempt)
                    continue
                error_body = e.read().decode() if e.fp else ""
                raise Exception(f"Alpaca API error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                if attempt < self.config.max_retries - 1:
                    time.sleep(1)
                    continue
                raise Exception(f"Connection error: {e.reason}")

        raise Exception("Max retries exceeded")

    # =========================================================================
    # Account Methods
    # =========================================================================

    def get_account(self) -> AlpacaAccount:
        """Get account information."""
        data = self._request("GET", "/v2/account")
        return AlpacaAccount(
            account_id=data["id"],
            status=data["status"],
            currency=data["currency"],
            cash=float(data["cash"]),
            portfolio_value=float(data["portfolio_value"]),
            buying_power=float(data["buying_power"]),
            equity=float(data["equity"]),
            last_equity=float(data["last_equity"]),
            daytrading_buying_power=float(data.get("daytrading_buying_power", 0)),
            pattern_day_trader=data.get("pattern_day_trader", False),
            trading_blocked=data.get("trading_blocked", False),
            transfers_blocked=data.get("transfers_blocked", False),
            account_blocked=data.get("account_blocked", False),
        )

    def get_positions(self) -> List[AlpacaPosition]:
        """Get all positions."""
        data = self._request("GET", "/v2/positions")
        positions = []
        for p in data:
            positions.append(AlpacaPosition(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                market_value=float(p["market_value"]),
                cost_basis=float(p["cost_basis"]),
                unrealized_pl=float(p["unrealized_pl"]),
                unrealized_plpc=float(p["unrealized_plpc"]),
                current_price=float(p["current_price"]),
                side=p["side"],
            ))
        return positions

    def get_position(self, symbol: str) -> Optional[AlpacaPosition]:
        """Get position for symbol."""
        try:
            p = self._request("GET", f"/v2/positions/{symbol}")
            return AlpacaPosition(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                avg_entry_price=float(p["avg_entry_price"]),
                market_value=float(p["market_value"]),
                cost_basis=float(p["cost_basis"]),
                unrealized_pl=float(p["unrealized_pl"]),
                unrealized_plpc=float(p["unrealized_plpc"]),
                current_price=float(p["current_price"]),
                side=p["side"],
            )
        except Exception:
            return None

    # =========================================================================
    # Order Methods
    # =========================================================================

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> AlpacaOrder:
        """Place an order."""
        data = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }

        if limit_price:
            data["limit_price"] = str(limit_price)
        if stop_price:
            data["stop_price"] = str(stop_price)
        if client_order_id:
            data["client_order_id"] = client_order_id

        result = self._request("POST", "/v2/orders", data=data)
        return self._parse_order(result)

    def get_order(self, order_id: str) -> AlpacaOrder:
        """Get order by ID."""
        result = self._request("GET", f"/v2/orders/{order_id}")
        return self._parse_order(result)

    def get_orders(self, status: str = "open", limit: int = 50) -> List[AlpacaOrder]:
        """Get orders."""
        result = self._request("GET", "/v2/orders", params={"status": status, "limit": limit})
        return [self._parse_order(o) for o in result]

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        try:
            self._request("DELETE", f"/v2/orders/{order_id}")
            return True
        except Exception:
            return False

    def cancel_all_orders(self) -> int:
        """Cancel all open orders."""
        result = self._request("DELETE", "/v2/orders")
        return len(result) if isinstance(result, list) else 0

    def _parse_order(self, data: Dict) -> AlpacaOrder:
        """Parse order response."""
        return AlpacaOrder(
            order_id=data["id"],
            client_order_id=data.get("client_order_id", ""),
            symbol=data["symbol"],
            side=data["side"],
            order_type=data["type"],
            qty=float(data["qty"]),
            filled_qty=float(data.get("filled_qty", 0)),
            status=data["status"],
            created_at=data["created_at"],
            filled_at=data.get("filled_at"),
            filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
            time_in_force=data["time_in_force"],
        )

    # =========================================================================
    # Market Data Methods
    # =========================================================================

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Hour",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 100,
        asset_class: str = "us_equity"
    ) -> List[AlpacaBar]:
        """Get historical bars."""
        params = {
            "timeframe": timeframe,
            "limit": limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        # Use appropriate endpoint for asset class
        if asset_class == "crypto":
            endpoint = f"/v1beta3/crypto/us/bars"
            params["symbols"] = symbol
        else:
            endpoint = f"/v2/stocks/{symbol}/bars"

        result = self._request("GET", endpoint, params=params, base_url=self._endpoints["data"])

        bars = []
        # Handle different response formats
        if "bars" in result:
            bar_data = result["bars"].get(symbol, []) if isinstance(result["bars"], dict) else result["bars"]
        else:
            bar_data = result if isinstance(result, list) else []

        for b in bar_data:
            bars.append(AlpacaBar(
                timestamp=b["t"],
                open=float(b["o"]),
                high=float(b["h"]),
                low=float(b["l"]),
                close=float(b["c"]),
                volume=float(b["v"]),
                trade_count=int(b.get("n", 0)),
                vwap=float(b.get("vw", 0)),
            ))

        return bars

    def get_latest_quote(self, symbol: str, asset_class: str = "us_equity") -> Dict[str, Any]:
        """Get latest quote for symbol."""
        if asset_class == "crypto":
            endpoint = f"/v1beta3/crypto/us/latest/quotes"
            params = {"symbols": symbol}
        else:
            endpoint = f"/v2/stocks/{symbol}/quotes/latest"
            params = {}

        return self._request("GET", endpoint, params=params, base_url=self._endpoints["data"])

    def get_latest_trade(self, symbol: str, asset_class: str = "us_equity") -> Dict[str, Any]:
        """Get latest trade for symbol."""
        if asset_class == "crypto":
            endpoint = f"/v1beta3/crypto/us/latest/trades"
            params = {"symbols": symbol}
        else:
            endpoint = f"/v2/stocks/{symbol}/trades/latest"
            params = {}

        return self._request("GET", endpoint, params=params, base_url=self._endpoints["data"])

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def connect(self) -> bool:
        """Test connection and validate credentials."""
        try:
            account = self.get_account()
            self._connected = True
            print(f"Connected to Alpaca ({self.config.environment.value})")
            print(f"  Account: {account.account_id}")
            print(f"  Status: {account.status}")
            print(f"  Equity: ${account.equity:,.2f}")
            print(f"  Buying Power: ${account.buying_power:,.2f}")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected


# =============================================================================
# Factory Functions
# =============================================================================

def create_alpaca_client(
    api_key: str = "",
    api_secret: str = "",
    paper: bool = True
) -> AlpacaClient:
    """Create Alpaca client."""
    # Try environment variables if not provided
    if not api_key:
        api_key = os.environ.get("ALPACA_API_KEY", "")
    if not api_secret:
        api_secret = os.environ.get("ALPACA_API_SECRET", "")

    config = AlpacaConfig(
        api_key=api_key,
        api_secret=api_secret,
        environment=AlpacaEnvironment.PAPER if paper else AlpacaEnvironment.LIVE,
    )
    return AlpacaClient(config)


def create_alpaca_paper_client(api_key: str, api_secret: str) -> AlpacaClient:
    """Create Alpaca paper trading client."""
    return create_alpaca_client(api_key, api_secret, paper=True)


# =============================================================================
# Testing
# =============================================================================

def test_alpaca_client(api_key: str, api_secret: str):
    """Test Alpaca client with provided credentials."""
    print("\n" + "="*60)
    print("  Alpaca API Test")
    print("="*60)

    client = create_alpaca_paper_client(api_key, api_secret)

    # Test connection
    print("\n1. Testing connection...")
    if not client.connect():
        print("   ✗ Connection failed")
        return False

    print("   ✓ Connected successfully")

    # Test account
    print("\n2. Getting account info...")
    try:
        account = client.get_account()
        print(f"   ✓ Portfolio value: ${account.portfolio_value:,.2f}")
        print(f"   ✓ Cash: ${account.cash:,.2f}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test positions
    print("\n3. Getting positions...")
    try:
        positions = client.get_positions()
        print(f"   ✓ Open positions: {len(positions)}")
        for pos in positions[:3]:
            print(f"     {pos.symbol}: {pos.qty} @ ${pos.current_price:.2f}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test market data
    print("\n4. Getting market data...")
    try:
        bars = client.get_bars("AAPL", timeframe="1Hour", limit=5)
        print(f"   ✓ Got {len(bars)} bars for AAPL")
        if bars:
            print(f"     Latest close: ${bars[-1].close:.2f}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Test crypto data
    print("\n5. Getting crypto data...")
    try:
        bars = client.get_bars("BTC/USD", timeframe="1Hour", limit=5, asset_class="crypto")
        print(f"   ✓ Got {len(bars)} bars for BTC/USD")
        if bars:
            print(f"     Latest close: ${bars[-1].close:.2f}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    print("\n" + "="*60)
    print("  Alpaca API Test Complete")
    print("="*60)

    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        test_alpaca_client(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python alpaca_connector.py <API_KEY> <API_SECRET>")
