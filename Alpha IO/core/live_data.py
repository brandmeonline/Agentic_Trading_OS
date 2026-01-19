"""
Live Market Data Client.

Real-time market data from exchanges and free data sources:
- Binance WebSocket streams (public, no API key required)
- CoinGecko API (free tier)
- HTTP REST client with retry logic
- Data normalization and caching
"""

from __future__ import annotations

import asyncio
import json
import time
import threading
import queue
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple, Union
from enum import Enum
from datetime import datetime, timedelta
from collections import deque
import urllib.request
import urllib.parse
import urllib.error
import ssl
import socket

# WebSocket support
try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False
    print("Note: websocket-client not installed. Using fallback HTTP polling.")


# =============================================================================
# Configuration
# =============================================================================

class DataSource(Enum):
    """Data source providers."""
    BINANCE = "binance"
    COINGECKO = "coingecko"
    CRYPTOCOMPARE = "cryptocompare"


class StreamType(Enum):
    """WebSocket stream types."""
    TRADE = "trade"
    KLINE = "kline"
    TICKER = "ticker"
    DEPTH = "depth"
    AGG_TRADE = "aggTrade"


@dataclass
class LiveDataConfig:
    """Live data configuration."""
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10
    ping_interval: float = 30.0
    request_timeout: float = 10.0
    max_retries: int = 3
    retry_delay: float = 1.0
    cache_ttl_seconds: int = 5
    rate_limit_per_second: float = 10.0


@dataclass
class MarketTick:
    """Normalized market tick."""
    symbol: str
    price: float
    volume: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "source": self.source,
        }


@dataclass
class OHLCV:
    """OHLCV candle data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_array(self) -> List[float]:
        return [self.timestamp, self.open, self.high, self.low, self.close, self.volume]


# =============================================================================
# HTTP Client with Retry
# =============================================================================

class HTTPClient:
    """HTTP client with retry logic and rate limiting."""

    def __init__(self, config: Optional[LiveDataConfig] = None):
        self.config = config or LiveDataConfig()
        self._last_request_time = 0.0
        self._request_interval = 1.0 / self.config.rate_limit_per_second
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()

        # SSL context
        self._ssl_context = ssl.create_default_context()

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Make GET request with retry."""
        # Build URL with params
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        # Check cache
        cache_key = hashlib.md5(url.encode()).hexdigest()
        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return cached

        # Rate limiting
        self._rate_limit()

        # Make request with retry
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                request = urllib.request.Request(url)
                request.add_header("User-Agent", "AgenticTradingOS/1.0")

                if headers:
                    for key, value in headers.items():
                        request.add_header(key, value)

                with urllib.request.urlopen(
                    request,
                    timeout=self.config.request_timeout,
                    context=self._ssl_context
                ) as response:
                    data = json.loads(response.read().decode())

                    # Cache result
                    if use_cache:
                        self._set_cached(cache_key, data)

                    return data

            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 429:  # Rate limited
                    time.sleep(self.config.retry_delay * (attempt + 1) * 2)
                elif e.code >= 500:  # Server error
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    raise

            except (urllib.error.URLError, socket.timeout) as e:
                last_error = e
                time.sleep(self.config.retry_delay * (attempt + 1))

        raise last_error or Exception("Request failed after retries")

    def _rate_limit(self):
        """Enforce rate limiting."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._request_interval:
                time.sleep(self._request_interval - elapsed)
            self._last_request_time = time.time()

    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key in self._cache:
            value, expiry = self._cache[key]
            if expiry > time.time():
                return value
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any):
        """Set cached value."""
        expiry = time.time() + self.config.cache_ttl_seconds
        self._cache[key] = (value, expiry)


# =============================================================================
# Binance Public Data Client
# =============================================================================

class BinancePublicClient:
    """Binance public API client (no API key required)."""

    BASE_URL = "https://api.binance.com/api/v3"
    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, config: Optional[LiveDataConfig] = None):
        self.config = config or LiveDataConfig()
        self.http = HTTPClient(config)
        self._ws: Optional[Any] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
        self._callbacks: Dict[str, List[Callable]] = {}
        self._subscriptions: List[str] = []

    # REST API Methods
    def get_ticker(self, symbol: str) -> MarketTick:
        """Get current ticker price."""
        data = self.http.get(
            f"{self.BASE_URL}/ticker/24hr",
            params={"symbol": symbol.replace("/", "")}
        )

        return MarketTick(
            symbol=symbol,
            price=float(data["lastPrice"]),
            volume=float(data["volume"]),
            bid=float(data["bidPrice"]),
            ask=float(data["askPrice"]),
            source="binance",
        )

    def get_all_tickers(self) -> List[MarketTick]:
        """Get all ticker prices."""
        data = self.http.get(f"{self.BASE_URL}/ticker/price")

        tickers = []
        for item in data:
            tickers.append(MarketTick(
                symbol=item["symbol"],
                price=float(item["price"]),
                volume=0.0,
                source="binance",
            ))
        return tickers

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Get order book depth."""
        data = self.http.get(
            f"{self.BASE_URL}/depth",
            params={"symbol": symbol.replace("/", ""), "limit": limit}
        )

        return {
            "symbol": symbol,
            "bids": [(float(b[0]), float(b[1])) for b in data["bids"]],
            "asks": [(float(a[0]), float(a[1])) for a in data["asks"]],
            "timestamp": datetime.now(),
        }

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades."""
        data = self.http.get(
            f"{self.BASE_URL}/trades",
            params={"symbol": symbol.replace("/", ""), "limit": limit}
        )

        trades = []
        for t in data:
            trades.append({
                "id": t["id"],
                "price": float(t["price"]),
                "quantity": float(t["qty"]),
                "time": t["time"],
                "is_buyer_maker": t["isBuyerMaker"],
            })
        return trades

    def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[OHLCV]:
        """Get candlestick data."""
        params = {
            "symbol": symbol.replace("/", ""),
            "interval": interval,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        data = self.http.get(f"{self.BASE_URL}/klines", params=params)

        candles = []
        for k in data:
            candles.append(OHLCV(
                timestamp=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))
        return candles

    def get_exchange_info(self) -> Dict[str, Any]:
        """Get exchange trading rules and symbol info."""
        return self.http.get(f"{self.BASE_URL}/exchangeInfo")

    # WebSocket Methods
    def start_websocket(self):
        """Start WebSocket connection."""
        if not HAS_WEBSOCKET:
            print("WebSocket not available, using HTTP polling fallback")
            return

        self._running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()

    def stop_websocket(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._ws:
            self._ws.close()

    def subscribe_trade(self, symbol: str, callback: Callable[[Dict], None]):
        """Subscribe to trade stream."""
        stream = f"{symbol.lower().replace('/', '')}@trade"
        self._subscribe(stream, callback)

    def subscribe_kline(self, symbol: str, interval: str, callback: Callable[[Dict], None]):
        """Subscribe to kline/candlestick stream."""
        stream = f"{symbol.lower().replace('/', '')}@kline_{interval}"
        self._subscribe(stream, callback)

    def subscribe_ticker(self, symbol: str, callback: Callable[[Dict], None]):
        """Subscribe to mini ticker stream."""
        stream = f"{symbol.lower().replace('/', '')}@miniTicker"
        self._subscribe(stream, callback)

    def subscribe_depth(self, symbol: str, callback: Callable[[Dict], None]):
        """Subscribe to order book depth stream."""
        stream = f"{symbol.lower().replace('/', '')}@depth20@100ms"
        self._subscribe(stream, callback)

    def _subscribe(self, stream: str, callback: Callable):
        """Add subscription."""
        if stream not in self._callbacks:
            self._callbacks[stream] = []
            self._subscriptions.append(stream)

            # Send subscribe message if connected
            if self._ws and self._ws.sock and self._ws.sock.connected:
                msg = {"method": "SUBSCRIBE", "params": [stream], "id": len(self._subscriptions)}
                self._ws.send(json.dumps(msg))

        self._callbacks[stream].append(callback)

    def _ws_loop(self):
        """WebSocket connection loop."""
        while self._running:
            try:
                # Build combined stream URL
                if self._subscriptions:
                    streams = "/".join(self._subscriptions)
                    url = f"{self.WS_URL}/{streams}"
                else:
                    url = self.WS_URL

                self._ws = websocket.WebSocketApp(
                    url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )

                self._ws.run_forever(
                    ping_interval=self.config.ping_interval,
                    ping_timeout=10,
                )

            except Exception as e:
                print(f"WebSocket error: {e}")

            if self._running:
                print(f"Reconnecting in {self.config.reconnect_delay}s...")
                time.sleep(self.config.reconnect_delay)

    def _on_open(self, ws):
        """WebSocket opened callback."""
        print("Binance WebSocket connected")

        # Subscribe to streams
        if self._subscriptions:
            msg = {
                "method": "SUBSCRIBE",
                "params": self._subscriptions,
                "id": 1
            }
            ws.send(json.dumps(msg))

    def _on_message(self, ws, message):
        """WebSocket message callback."""
        try:
            data = json.loads(message)

            # Handle combined stream format
            if "stream" in data:
                stream = data["stream"]
                payload = data["data"]
            else:
                # Single stream format
                stream = data.get("s", "").lower() + "@" + data.get("e", "")
                payload = data

            # Find matching callbacks
            for sub_stream, callbacks in self._callbacks.items():
                if sub_stream in str(stream) or stream in sub_stream:
                    for callback in callbacks:
                        try:
                            callback(payload)
                        except Exception as e:
                            print(f"Callback error: {e}")

        except json.JSONDecodeError:
            pass

    def _on_error(self, ws, error):
        """WebSocket error callback."""
        print(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket closed callback."""
        print(f"WebSocket closed: {close_status_code} - {close_msg}")


# =============================================================================
# CoinGecko Free API Client
# =============================================================================

class CoinGeckoClient:
    """CoinGecko free API client (no API key required)."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Mapping of symbols to CoinGecko IDs
    SYMBOL_MAP = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "BNB": "binancecoin",
        "SOL": "solana",
        "XRP": "ripple",
        "ADA": "cardano",
        "DOGE": "dogecoin",
        "DOT": "polkadot",
        "AVAX": "avalanche-2",
        "MATIC": "matic-network",
        "LINK": "chainlink",
        "UNI": "uniswap",
        "ATOM": "cosmos",
        "LTC": "litecoin",
    }

    def __init__(self, config: Optional[LiveDataConfig] = None):
        self.config = config or LiveDataConfig()
        self.http = HTTPClient(config)

    def get_price(self, symbols: List[str], vs_currency: str = "usd") -> Dict[str, float]:
        """Get current prices for multiple symbols."""
        # Convert symbols to CoinGecko IDs
        ids = [self.SYMBOL_MAP.get(s.upper(), s.lower()) for s in symbols]

        data = self.http.get(
            f"{self.BASE_URL}/simple/price",
            params={
                "ids": ",".join(ids),
                "vs_currencies": vs_currency,
            }
        )

        # Convert back to symbols
        result = {}
        for symbol in symbols:
            coin_id = self.SYMBOL_MAP.get(symbol.upper(), symbol.lower())
            if coin_id in data:
                result[symbol.upper()] = data[coin_id][vs_currency]

        return result

    def get_market_data(
        self,
        symbols: List[str],
        vs_currency: str = "usd"
    ) -> List[Dict[str, Any]]:
        """Get detailed market data."""
        ids = [self.SYMBOL_MAP.get(s.upper(), s.lower()) for s in symbols]

        data = self.http.get(
            f"{self.BASE_URL}/coins/markets",
            params={
                "ids": ",".join(ids),
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "sparkline": "false",
            }
        )

        return data

    def get_historical_data(
        self,
        symbol: str,
        vs_currency: str = "usd",
        days: int = 30
    ) -> List[OHLCV]:
        """Get historical OHLCV data."""
        coin_id = self.SYMBOL_MAP.get(symbol.upper(), symbol.lower())

        data = self.http.get(
            f"{self.BASE_URL}/coins/{coin_id}/ohlc",
            params={
                "vs_currency": vs_currency,
                "days": days,
            }
        )

        candles = []
        for ohlc in data:
            candles.append(OHLCV(
                timestamp=int(ohlc[0]),
                open=float(ohlc[1]),
                high=float(ohlc[2]),
                low=float(ohlc[3]),
                close=float(ohlc[4]),
                volume=0.0,  # CoinGecko OHLC doesn't include volume
            ))

        return candles

    def get_trending(self) -> List[Dict[str, Any]]:
        """Get trending coins."""
        data = self.http.get(f"{self.BASE_URL}/search/trending")
        return data.get("coins", [])


# =============================================================================
# Unified Live Data Manager
# =============================================================================

class LiveDataManager:
    """Unified manager for live market data."""

    def __init__(self, config: Optional[LiveDataConfig] = None):
        self.config = config or LiveDataConfig()

        # Initialize clients
        self.binance = BinancePublicClient(config)
        self.coingecko = CoinGeckoClient(config)

        # Data storage
        self._tickers: Dict[str, MarketTick] = {}
        self._klines: Dict[str, List[OHLCV]] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def start(self):
        """Start live data streams."""
        self.binance.start_websocket()

    def stop(self):
        """Stop live data streams."""
        self.binance.stop_websocket()

    def get_ticker(self, symbol: str, source: DataSource = DataSource.BINANCE) -> MarketTick:
        """Get current ticker."""
        if source == DataSource.BINANCE:
            return self.binance.get_ticker(symbol)
        elif source == DataSource.COINGECKO:
            base = symbol.split("/")[0]
            prices = self.coingecko.get_price([base])
            return MarketTick(
                symbol=symbol,
                price=prices.get(base.upper(), 0.0),
                volume=0.0,
                source="coingecko",
            )
        raise ValueError(f"Unknown source: {source}")

    def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 500,
        source: DataSource = DataSource.BINANCE
    ) -> List[OHLCV]:
        """Get historical candles."""
        if source == DataSource.BINANCE:
            return self.binance.get_klines(symbol, interval, limit)
        elif source == DataSource.COINGECKO:
            # Map interval to days
            days_map = {"1h": 1, "4h": 7, "1d": 30, "1w": 180}
            days = days_map.get(interval, 30)
            base = symbol.split("/")[0]
            return self.coingecko.get_historical_data(base, days=days)
        raise ValueError(f"Unknown source: {source}")

    def subscribe_ticker(self, symbol: str, callback: Callable[[MarketTick], None]):
        """Subscribe to ticker updates."""
        def wrapper(data):
            tick = MarketTick(
                symbol=symbol,
                price=float(data.get("c", 0)),
                volume=float(data.get("v", 0)),
                source="binance_ws",
            )
            with self._lock:
                self._tickers[symbol] = tick
            callback(tick)

        self.binance.subscribe_ticker(symbol, wrapper)

    def subscribe_kline(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[OHLCV], None]
    ):
        """Subscribe to kline updates."""
        def wrapper(data):
            k = data.get("k", {})
            candle = OHLCV(
                timestamp=int(k.get("t", 0)),
                open=float(k.get("o", 0)),
                high=float(k.get("h", 0)),
                low=float(k.get("l", 0)),
                close=float(k.get("c", 0)),
                volume=float(k.get("v", 0)),
            )
            callback(candle)

        self.binance.subscribe_kline(symbol, interval, wrapper)

    def get_multi_source_price(self, symbol: str) -> Dict[str, float]:
        """Get price from multiple sources for comparison."""
        prices = {}

        # Binance
        try:
            tick = self.binance.get_ticker(symbol)
            prices["binance"] = tick.price
        except Exception:
            pass

        # CoinGecko
        try:
            base = symbol.split("/")[0]
            cg_prices = self.coingecko.get_price([base])
            prices["coingecko"] = cg_prices.get(base.upper(), 0.0)
        except Exception:
            pass

        return prices


# =============================================================================
# Factory Functions
# =============================================================================

def create_live_data_manager(
    rate_limit: float = 10.0,
    cache_ttl: int = 5
) -> LiveDataManager:
    """Create live data manager."""
    config = LiveDataConfig(
        rate_limit_per_second=rate_limit,
        cache_ttl_seconds=cache_ttl,
    )
    return LiveDataManager(config)


def create_binance_client() -> BinancePublicClient:
    """Create Binance public client."""
    return BinancePublicClient()


def create_coingecko_client() -> CoinGeckoClient:
    """Create CoinGecko client."""
    return CoinGeckoClient()


# =============================================================================
# Testing
# =============================================================================

def test_live_data():
    """Test live data clients."""
    print("Testing Live Data Clients...")
    print("(Using real API calls to public endpoints)\n")

    # Test HTTP Client
    print("1. Testing HTTP Client...")
    http = HTTPClient()

    try:
        # Simple ping test
        data = http.get("https://api.binance.com/api/v3/ping")
        print("   ✓ Binance API reachable")
    except Exception as e:
        print(f"   ✗ Binance API error: {e}")
        return False

    # Test Binance Client
    print("\n2. Testing Binance Public Client...")
    binance = create_binance_client()

    try:
        # Get BTC ticker
        ticker = binance.get_ticker("BTCUSDT")
        print(f"   ✓ BTC/USDT Price: ${ticker.price:,.2f}")

        # Get order book
        orderbook = binance.get_orderbook("BTCUSDT", limit=5)
        print(f"   ✓ Order book: {len(orderbook['bids'])} bids, {len(orderbook['asks'])} asks")

        # Get recent trades
        trades = binance.get_recent_trades("BTCUSDT", limit=5)
        print(f"   ✓ Recent trades: {len(trades)} trades")

        # Get klines
        klines = binance.get_klines("BTCUSDT", "1h", limit=10)
        print(f"   ✓ Klines: {len(klines)} candles")
        print(f"     Latest close: ${klines[-1].close:,.2f}")

    except Exception as e:
        print(f"   ✗ Binance error: {e}")

    # Test CoinGecko Client
    print("\n3. Testing CoinGecko Client...")
    coingecko = create_coingecko_client()

    try:
        # Get prices
        prices = coingecko.get_price(["BTC", "ETH", "SOL"])
        print(f"   ✓ Prices from CoinGecko:")
        for symbol, price in prices.items():
            print(f"     {symbol}: ${price:,.2f}")

        # Get trending
        trending = coingecko.get_trending()
        if trending:
            print(f"   ✓ Trending coins: {len(trending)}")
            print(f"     Top trending: {trending[0]['item']['name']}")

    except Exception as e:
        print(f"   ✗ CoinGecko error: {e}")

    # Test Live Data Manager
    print("\n4. Testing Live Data Manager...")
    manager = create_live_data_manager()

    try:
        # Get multi-source price
        multi_prices = manager.get_multi_source_price("BTC/USDT")
        print(f"   ✓ Multi-source BTC prices:")
        for source, price in multi_prices.items():
            print(f"     {source}: ${price:,.2f}")

        # Calculate spread
        if len(multi_prices) >= 2:
            prices_list = list(multi_prices.values())
            spread = abs(prices_list[0] - prices_list[1])
            spread_pct = spread / prices_list[0] * 100
            print(f"   ✓ Cross-source spread: ${spread:.2f} ({spread_pct:.4f}%)")

    except Exception as e:
        print(f"   ✗ Manager error: {e}")

    # Test WebSocket (brief)
    print("\n5. Testing WebSocket Stream (5 seconds)...")
    if HAS_WEBSOCKET:
        received_data = []

        def on_tick(data):
            received_data.append(data)

        try:
            binance.subscribe_ticker("BTCUSDT", on_tick)
            binance.start_websocket()

            time.sleep(5)
            binance.stop_websocket()

            print(f"   ✓ Received {len(received_data)} ticker updates")
            if received_data:
                print(f"     Latest: ${float(received_data[-1].get('c', 0)):,.2f}")

        except Exception as e:
            print(f"   ✗ WebSocket error: {e}")
    else:
        print("   ⊘ WebSocket not available (install websocket-client)")

    print("\n✓ Live data tests completed!")
    return True


if __name__ == "__main__":
    test_live_data()
