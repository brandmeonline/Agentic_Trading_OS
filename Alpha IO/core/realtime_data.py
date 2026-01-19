"""
Real-Time Data Infrastructure.

Production-grade data infrastructure for trading systems:
- WebSocket feed management
- Data normalization and validation
- Feature store for ML features
- Stream processing pipeline
- Circuit breakers and fault tolerance
"""

from __future__ import annotations

import numpy as np
import json
import time
import threading
import queue
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable, Set
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from collections import deque
import hashlib


# =============================================================================
# Configuration
# =============================================================================

class DataSource(Enum):
    """Supported data sources."""
    BINANCE = "binance"
    COINBASE = "coinbase"
    KRAKEN = "kraken"
    FTX = "ftx"
    POLYGON = "polygon"
    ALPACA = "alpaca"
    CUSTOM = "custom"


class StreamType(Enum):
    """Types of data streams."""
    TRADE = "trade"
    ORDERBOOK = "orderbook"
    TICKER = "ticker"
    KLINE = "kline"
    LIQUIDATION = "liquidation"


@dataclass
class DataConfig:
    """Data infrastructure configuration."""
    buffer_size: int = 10000
    max_latency_ms: float = 100.0
    heartbeat_interval: float = 30.0
    reconnect_delay: float = 5.0
    max_reconnect_attempts: int = 10
    enable_compression: bool = True
    enable_caching: bool = True
    cache_ttl_seconds: int = 300
    batch_size: int = 100


@dataclass
class MarketTick:
    """Single market data tick."""
    symbol: str
    price: float
    volume: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"
    sequence: int = 0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "timestamp": self.timestamp,
            "source": self.source,
            "sequence": self.sequence
        }


@dataclass
class OrderBookSnapshot:
    """Order book snapshot."""
    symbol: str
    bids: List[Tuple[float, float]]  # (price, quantity) sorted desc
    asks: List[Tuple[float, float]]  # (price, quantity) sorted asc
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0

    @property
    def mid_price(self) -> float:
        if self.bids and self.asks:
            return (self.bids[0][0] + self.asks[0][0]) / 2
        return 0.0

    @property
    def spread(self) -> float:
        if self.bids and self.asks:
            return self.asks[0][0] - self.bids[0][0]
        return 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid > 0:
            return (self.spread / mid) * 10000
        return 0.0


# =============================================================================
# Connection State Management
# =============================================================================

class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class ConnectionMetrics:
    """Connection health metrics."""
    state: ConnectionState = ConnectionState.DISCONNECTED
    messages_received: int = 0
    messages_dropped: int = 0
    reconnect_count: int = 0
    last_message_time: float = 0.0
    avg_latency_ms: float = 0.0
    error_count: int = 0
    uptime_seconds: float = 0.0


# =============================================================================
# Circuit Breaker
# =============================================================================

class CircuitBreaker:
    """
    Circuit breaker for fault tolerance.

    States:
    - CLOSED: Normal operation
    - OPEN: Blocking all requests
    - HALF_OPEN: Testing recovery
    """

    class State(Enum):
        CLOSED = "closed"
        OPEN = "open"
        HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_requests: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_requests = half_open_requests

        self.state = self.State.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """Check if operation can proceed."""
        with self._lock:
            if self.state == self.State.CLOSED:
                return True

            if self.state == self.State.OPEN:
                # Check if recovery timeout has passed
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = self.State.HALF_OPEN
                    self.success_count = 0
                    return True
                return False

            if self.state == self.State.HALF_OPEN:
                return self.success_count < self.half_open_requests

        return False

    def record_success(self):
        """Record successful operation."""
        with self._lock:
            if self.state == self.State.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_requests:
                    self.state = self.State.CLOSED
                    self.failure_count = 0
            elif self.state == self.State.CLOSED:
                self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        """Record failed operation."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == self.State.HALF_OPEN:
                self.state = self.State.OPEN
            elif self.state == self.State.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    self.state = self.State.OPEN


# =============================================================================
# Data Normalizer
# =============================================================================

class DataNormalizer:
    """
    Normalizes data from different exchanges to common format.

    Handles:
    - Symbol standardization
    - Timestamp normalization
    - Price/quantity precision
    - Data validation
    """

    # Symbol mappings (exchange -> standard)
    SYMBOL_MAPS = {
        "binance": {
            "BTCUSDT": "BTC/USDT",
            "ETHUSDT": "ETH/USDT",
            "ADAUSDT": "ADA/USDT",
        },
        "coinbase": {
            "BTC-USD": "BTC/USD",
            "ETH-USD": "ETH/USD",
        }
    }

    def __init__(self):
        self.precision_cache: Dict[str, int] = {}

    def normalize_symbol(self, symbol: str, source: str) -> str:
        """Normalize symbol to standard format."""
        source_map = self.SYMBOL_MAPS.get(source.lower(), {})
        return source_map.get(symbol, symbol)

    def normalize_timestamp(self, timestamp: Any, source: str) -> float:
        """Normalize timestamp to Unix epoch seconds."""
        if isinstance(timestamp, (int, float)):
            # Check if milliseconds
            if timestamp > 1e12:
                return timestamp / 1000.0
            return float(timestamp)
        elif isinstance(timestamp, str):
            # Try parsing ISO format
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                return dt.timestamp()
            except ValueError:
                return time.time()
        elif isinstance(timestamp, datetime):
            return timestamp.timestamp()
        return time.time()

    def normalize_price(self, price: Any, symbol: str) -> float:
        """Normalize and validate price."""
        try:
            price = float(price)
            if price <= 0:
                return 0.0
            return price
        except (ValueError, TypeError):
            return 0.0

    def normalize_quantity(self, quantity: Any, symbol: str) -> float:
        """Normalize and validate quantity."""
        try:
            quantity = float(quantity)
            if quantity < 0:
                return 0.0
            return quantity
        except (ValueError, TypeError):
            return 0.0

    def normalize_trade(self, data: Dict, source: str) -> Optional[MarketTick]:
        """Normalize trade data from exchange format."""
        try:
            # Extract fields (varies by exchange)
            if source.lower() == "binance":
                symbol = data.get("s", "")
                price = data.get("p", 0)
                quantity = data.get("q", 0)
                timestamp = data.get("T", time.time() * 1000)
            elif source.lower() == "coinbase":
                symbol = data.get("product_id", "")
                price = data.get("price", 0)
                quantity = data.get("size", 0)
                timestamp = data.get("time", "")
            else:
                # Generic format
                symbol = data.get("symbol", "")
                price = data.get("price", 0)
                quantity = data.get("quantity", data.get("volume", data.get("size", 0)))
                timestamp = data.get("timestamp", time.time())

            return MarketTick(
                symbol=self.normalize_symbol(symbol, source),
                price=self.normalize_price(price, symbol),
                volume=self.normalize_quantity(quantity, symbol),
                timestamp=self.normalize_timestamp(timestamp, source),
                source=source
            )
        except Exception:
            return None

    def normalize_orderbook(self, data: Dict, source: str) -> Optional[OrderBookSnapshot]:
        """Normalize orderbook data."""
        try:
            if source.lower() == "binance":
                symbol = data.get("s", "")
                bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
                asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
                timestamp = data.get("T", time.time() * 1000)
            else:
                symbol = data.get("symbol", "")
                bids = [(float(b[0]), float(b[1])) for b in data.get("bids", [])]
                asks = [(float(a[0]), float(a[1])) for a in data.get("asks", [])]
                timestamp = data.get("timestamp", time.time())

            # Sort bids descending, asks ascending
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            return OrderBookSnapshot(
                symbol=self.normalize_symbol(symbol, source),
                bids=bids[:20],  # Limit depth
                asks=asks[:20],
                timestamp=self.normalize_timestamp(timestamp, source),
                sequence=data.get("sequence", 0)
            )
        except Exception:
            return None


# =============================================================================
# WebSocket Feed (Simulated for portability)
# =============================================================================

class WebSocketFeed:
    """
    WebSocket feed manager.

    Note: This is a simulated implementation for demonstration.
    In production, use websocket-client or aiohttp for real connections.
    """

    def __init__(
        self,
        source: DataSource,
        symbols: List[str],
        config: DataConfig,
        on_message: Callable[[Dict], None],
        on_error: Optional[Callable[[Exception], None]] = None
    ):
        self.source = source
        self.symbols = symbols
        self.config = config
        self.on_message = on_message
        self.on_error = on_error

        self.normalizer = DataNormalizer()
        self.circuit_breaker = CircuitBreaker()
        self.metrics = ConnectionMetrics()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def connect(self):
        """Start the feed connection."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self.metrics.state = ConnectionState.CONNECTING

            # Start simulation thread
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def disconnect(self):
        """Stop the feed connection."""
        with self._lock:
            self._running = False
            self.metrics.state = ConnectionState.DISCONNECTED

    def _run(self):
        """Main feed loop (simulated)."""
        self.metrics.state = ConnectionState.CONNECTED
        start_time = time.time()

        # Simulate market data
        prices = {symbol: 100.0 for symbol in self.symbols}

        while self._running:
            try:
                if not self.circuit_breaker.can_execute():
                    time.sleep(1)
                    continue

                # Generate simulated tick
                for symbol in self.symbols:
                    # Random walk
                    prices[symbol] *= (1 + np.random.randn() * 0.001)

                    tick = MarketTick(
                        symbol=symbol,
                        price=prices[symbol],
                        volume=np.random.exponential(1000),
                        bid=prices[symbol] * 0.9999,
                        ask=prices[symbol] * 1.0001,
                        timestamp=time.time(),
                        source=self.source.value
                    )

                    self.on_message(tick.to_dict())
                    self.metrics.messages_received += 1
                    self.metrics.last_message_time = time.time()

                self.circuit_breaker.record_success()
                self.metrics.uptime_seconds = time.time() - start_time

                # Rate limit simulation
                time.sleep(0.1)

            except Exception as e:
                self.circuit_breaker.record_failure()
                self.metrics.error_count += 1
                if self.on_error:
                    self.on_error(e)
                time.sleep(self.config.reconnect_delay)

    def get_metrics(self) -> ConnectionMetrics:
        """Get connection metrics."""
        return self.metrics


# =============================================================================
# Stream Processor
# =============================================================================

class StreamProcessor:
    """
    Real-time stream processing pipeline.

    Handles:
    - Data buffering
    - Aggregation (OHLCV)
    - Filtering
    - Event triggering
    """

    def __init__(self, config: DataConfig):
        self.config = config

        self._buffers: Dict[str, deque] = {}
        self._aggregators: Dict[str, Dict] = {}
        self._callbacks: List[Callable[[str, Any], None]] = []
        self._lock = threading.Lock()

    def process_tick(self, tick: MarketTick):
        """Process incoming tick."""
        with self._lock:
            symbol = tick.symbol

            # Initialize buffer if needed
            if symbol not in self._buffers:
                self._buffers[symbol] = deque(maxlen=self.config.buffer_size)
                self._aggregators[symbol] = self._new_aggregator()

            # Add to buffer
            self._buffers[symbol].append(tick)

            # Update aggregator
            self._update_aggregator(symbol, tick)

            # Trigger callbacks
            self._trigger_callbacks("tick", tick)

    def _new_aggregator(self) -> Dict:
        """Create new OHLCV aggregator."""
        return {
            "open": None,
            "high": float('-inf'),
            "low": float('inf'),
            "close": None,
            "volume": 0.0,
            "vwap_num": 0.0,
            "vwap_den": 0.0,
            "count": 0,
            "start_time": None
        }

    def _update_aggregator(self, symbol: str, tick: MarketTick):
        """Update OHLCV aggregator with new tick."""
        agg = self._aggregators[symbol]

        if agg["open"] is None:
            agg["open"] = tick.price
            agg["start_time"] = tick.timestamp

        agg["high"] = max(agg["high"], tick.price)
        agg["low"] = min(agg["low"], tick.price)
        agg["close"] = tick.price
        agg["volume"] += tick.volume
        agg["vwap_num"] += tick.price * tick.volume
        agg["vwap_den"] += tick.volume
        agg["count"] += 1

    def get_ohlcv(self, symbol: str) -> Optional[Dict]:
        """Get current OHLCV bar."""
        with self._lock:
            if symbol not in self._aggregators:
                return None

            agg = self._aggregators[symbol]
            if agg["open"] is None:
                return None

            vwap = agg["vwap_num"] / agg["vwap_den"] if agg["vwap_den"] > 0 else agg["close"]

            return {
                "symbol": symbol,
                "open": agg["open"],
                "high": agg["high"],
                "low": agg["low"],
                "close": agg["close"],
                "volume": agg["volume"],
                "vwap": vwap,
                "count": agg["count"],
                "start_time": agg["start_time"]
            }

    def reset_aggregator(self, symbol: str):
        """Reset OHLCV aggregator for new period."""
        with self._lock:
            if symbol in self._aggregators:
                ohlcv = self.get_ohlcv(symbol)
                self._aggregators[symbol] = self._new_aggregator()
                if ohlcv:
                    self._trigger_callbacks("bar", ohlcv)

    def get_recent_ticks(self, symbol: str, n: int = 100) -> List[MarketTick]:
        """Get recent ticks for symbol."""
        with self._lock:
            if symbol not in self._buffers:
                return []
            return list(self._buffers[symbol])[-n:]

    def register_callback(self, callback: Callable[[str, Any], None]):
        """Register event callback."""
        self._callbacks.append(callback)

    def _trigger_callbacks(self, event_type: str, data: Any):
        """Trigger registered callbacks."""
        for callback in self._callbacks:
            try:
                callback(event_type, data)
            except Exception:
                pass

    def get_statistics(self, symbol: str) -> Dict[str, float]:
        """Get streaming statistics for symbol."""
        with self._lock:
            if symbol not in self._buffers:
                return {}

            ticks = list(self._buffers[symbol])
            if not ticks:
                return {}

            prices = [t.price for t in ticks]
            volumes = [t.volume for t in ticks]

            return {
                "mean_price": np.mean(prices),
                "std_price": np.std(prices),
                "min_price": np.min(prices),
                "max_price": np.max(prices),
                "total_volume": np.sum(volumes),
                "mean_volume": np.mean(volumes),
                "tick_count": len(ticks)
            }


# =============================================================================
# Feature Store
# =============================================================================

class FeatureStore:
    """
    Real-time feature store for ML models.

    Provides:
    - Feature computation and caching
    - Point-in-time feature retrieval
    - Feature versioning
    - TTL-based cache management
    """

    def __init__(self, config: DataConfig):
        self.config = config

        self._features: Dict[str, Dict[str, Any]] = {}  # symbol -> feature_name -> value
        self._timestamps: Dict[str, Dict[str, float]] = {}  # symbol -> feature_name -> timestamp
        self._history: Dict[str, deque] = {}  # symbol -> feature history
        self._lock = threading.Lock()

    def update_feature(self, symbol: str, feature_name: str, value: Any):
        """Update a feature value."""
        with self._lock:
            if symbol not in self._features:
                self._features[symbol] = {}
                self._timestamps[symbol] = {}
                self._history[symbol] = deque(maxlen=self.config.buffer_size)

            self._features[symbol][feature_name] = value
            self._timestamps[symbol][feature_name] = time.time()

            # Store in history
            self._history[symbol].append({
                "timestamp": time.time(),
                "feature": feature_name,
                "value": value
            })

    def get_feature(self, symbol: str, feature_name: str) -> Optional[Any]:
        """Get current feature value."""
        with self._lock:
            if symbol not in self._features:
                return None

            if feature_name not in self._features[symbol]:
                return None

            # Check TTL
            timestamp = self._timestamps[symbol].get(feature_name, 0)
            if time.time() - timestamp > self.config.cache_ttl_seconds:
                return None

            return self._features[symbol][feature_name]

    def get_features(self, symbol: str) -> Dict[str, Any]:
        """Get all current features for symbol."""
        with self._lock:
            if symbol not in self._features:
                return {}

            current_time = time.time()
            result = {}

            for name, value in self._features[symbol].items():
                timestamp = self._timestamps[symbol].get(name, 0)
                if current_time - timestamp <= self.config.cache_ttl_seconds:
                    result[name] = value

            return result

    def get_feature_vector(self, symbol: str, feature_names: List[str]) -> np.ndarray:
        """Get feature vector for ML model input."""
        with self._lock:
            values = []
            for name in feature_names:
                value = self.get_feature(symbol, name)
                if value is None:
                    values.append(0.0)
                elif isinstance(value, (int, float)):
                    values.append(float(value))
                else:
                    values.append(0.0)
            return np.array(values)

    def get_feature_history(
        self,
        symbol: str,
        feature_name: str,
        n: int = 100
    ) -> List[Tuple[float, Any]]:
        """Get historical feature values."""
        with self._lock:
            if symbol not in self._history:
                return []

            history = [
                (h["timestamp"], h["value"])
                for h in self._history[symbol]
                if h["feature"] == feature_name
            ]

            return history[-n:]

    def compute_derived_features(self, symbol: str):
        """Compute derived features from raw data."""
        features = self.get_features(symbol)

        # Moving averages
        if "price" in features:
            price_history = self.get_feature_history(symbol, "price", 20)
            if len(price_history) >= 20:
                prices = [p[1] for p in price_history]
                self.update_feature(symbol, "sma_20", np.mean(prices))
                self.update_feature(symbol, "std_20", np.std(prices))

        # Volume features
        if "volume" in features:
            vol_history = self.get_feature_history(symbol, "volume", 20)
            if len(vol_history) >= 20:
                volumes = [v[1] for v in vol_history]
                self.update_feature(symbol, "volume_sma_20", np.mean(volumes))

        # Momentum
        if "price" in features:
            price_history = self.get_feature_history(symbol, "price", 10)
            if len(price_history) >= 10:
                prices = [p[1] for p in price_history]
                momentum = (prices[-1] - prices[0]) / prices[0] if prices[0] != 0 else 0
                self.update_feature(symbol, "momentum_10", momentum)

    def clear_expired(self):
        """Clear expired features."""
        with self._lock:
            current_time = time.time()

            for symbol in list(self._features.keys()):
                expired = []
                for name, timestamp in self._timestamps[symbol].items():
                    if current_time - timestamp > self.config.cache_ttl_seconds:
                        expired.append(name)

                for name in expired:
                    del self._features[symbol][name]
                    del self._timestamps[symbol][name]


# =============================================================================
# Real-Time Data Manager
# =============================================================================

class RealtimeDataManager:
    """
    Main real-time data orchestrator.

    Coordinates:
    - Multiple data feeds
    - Stream processing
    - Feature computation
    - Data distribution
    """

    def __init__(self, config: Optional[DataConfig] = None):
        self.config = config or DataConfig()

        self.normalizer = DataNormalizer()
        self.processor = StreamProcessor(self.config)
        self.feature_store = FeatureStore(self.config)

        self._feeds: Dict[str, WebSocketFeed] = {}
        self._subscribers: Dict[str, List[Callable]] = {}
        self._running = False
        self._lock = threading.Lock()

    def add_feed(
        self,
        source: DataSource,
        symbols: List[str],
        name: Optional[str] = None
    ):
        """Add a data feed."""
        feed_name = name or source.value

        def on_message(data: Dict):
            self._handle_message(source.value, data)

        def on_error(error: Exception):
            self._handle_error(feed_name, error)

        feed = WebSocketFeed(
            source=source,
            symbols=symbols,
            config=self.config,
            on_message=on_message,
            on_error=on_error
        )

        self._feeds[feed_name] = feed

    def start(self):
        """Start all feeds."""
        self._running = True

        for feed in self._feeds.values():
            feed.connect()

        # Start feature computation thread
        self._feature_thread = threading.Thread(target=self._feature_loop, daemon=True)
        self._feature_thread.start()

    def stop(self):
        """Stop all feeds."""
        self._running = False

        for feed in self._feeds.values():
            feed.disconnect()

    def _handle_message(self, source: str, data: Dict):
        """Handle incoming message."""
        # Normalize to tick
        tick = self.normalizer.normalize_trade(data, source)

        if tick:
            # Process through stream processor
            self.processor.process_tick(tick)

            # Update feature store
            self.feature_store.update_feature(tick.symbol, "price", tick.price)
            self.feature_store.update_feature(tick.symbol, "volume", tick.volume)
            self.feature_store.update_feature(tick.symbol, "bid", tick.bid)
            self.feature_store.update_feature(tick.symbol, "ask", tick.ask)

            # Notify subscribers
            self._notify_subscribers(tick.symbol, tick)

    def _handle_error(self, feed_name: str, error: Exception):
        """Handle feed error."""
        # Log error (would use proper logging in production)
        pass

    def _feature_loop(self):
        """Background feature computation loop."""
        while self._running:
            try:
                # Compute derived features for all symbols
                for symbol in list(self.feature_store._features.keys()):
                    self.feature_store.compute_derived_features(symbol)

                # Clear expired features
                self.feature_store.clear_expired()

                time.sleep(1)

            except Exception:
                time.sleep(5)

    def subscribe(self, symbol: str, callback: Callable[[MarketTick], None]):
        """Subscribe to symbol updates."""
        with self._lock:
            if symbol not in self._subscribers:
                self._subscribers[symbol] = []
            self._subscribers[symbol].append(callback)

    def unsubscribe(self, symbol: str, callback: Callable):
        """Unsubscribe from symbol updates."""
        with self._lock:
            if symbol in self._subscribers:
                self._subscribers[symbol] = [
                    cb for cb in self._subscribers[symbol]
                    if cb != callback
                ]

    def _notify_subscribers(self, symbol: str, tick: MarketTick):
        """Notify subscribers of update."""
        with self._lock:
            callbacks = self._subscribers.get(symbol, [])

        for callback in callbacks:
            try:
                callback(tick)
            except Exception:
                pass

    def get_latest_tick(self, symbol: str) -> Optional[MarketTick]:
        """Get latest tick for symbol."""
        ticks = self.processor.get_recent_ticks(symbol, 1)
        return ticks[0] if ticks else None

    def get_ohlcv(self, symbol: str) -> Optional[Dict]:
        """Get current OHLCV bar."""
        return self.processor.get_ohlcv(symbol)

    def get_features(self, symbol: str) -> Dict[str, Any]:
        """Get all features for symbol."""
        return self.feature_store.get_features(symbol)

    def get_feature_vector(self, symbol: str, features: List[str]) -> np.ndarray:
        """Get feature vector for ML model."""
        return self.feature_store.get_feature_vector(symbol, features)

    def get_statistics(self, symbol: str) -> Dict[str, float]:
        """Get streaming statistics."""
        return self.processor.get_statistics(symbol)

    def get_feed_metrics(self) -> Dict[str, ConnectionMetrics]:
        """Get metrics for all feeds."""
        return {name: feed.get_metrics() for name, feed in self._feeds.items()}

    def health_check(self) -> Dict[str, Any]:
        """Check system health."""
        metrics = self.get_feed_metrics()

        all_connected = all(
            m.state == ConnectionState.CONNECTED
            for m in metrics.values()
        )

        total_messages = sum(m.messages_received for m in metrics.values())
        total_errors = sum(m.error_count for m in metrics.values())

        return {
            "healthy": all_connected and total_errors < 10,
            "feeds_connected": sum(1 for m in metrics.values() if m.state == ConnectionState.CONNECTED),
            "feeds_total": len(metrics),
            "total_messages": total_messages,
            "total_errors": total_errors,
            "metrics": {name: {
                "state": m.state.value,
                "messages": m.messages_received,
                "errors": m.error_count,
                "uptime": m.uptime_seconds
            } for name, m in metrics.items()}
        }


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create data manager
    config = DataConfig(
        buffer_size=1000,
        cache_ttl_seconds=60
    )
    manager = RealtimeDataManager(config)

    # Add simulated feed
    manager.add_feed(
        source=DataSource.BINANCE,
        symbols=["BTC/USDT", "ETH/USDT"],
        name="binance_main"
    )

    # Subscribe to updates
    def on_tick(tick: MarketTick):
        print(f"Tick: {tick.symbol} @ {tick.price:.2f}")

    manager.subscribe("BTC/USDT", on_tick)

    # Start feeds
    print("Starting data feeds...")
    manager.start()

    # Run for a bit
    try:
        for i in range(10):
            time.sleep(1)

            # Get statistics
            stats = manager.get_statistics("BTC/USDT")
            if stats:
                print(f"\nBTC/USDT Stats:")
                print(f"  Mean: {stats.get('mean_price', 0):.2f}")
                print(f"  Std: {stats.get('std_price', 0):.4f}")
                print(f"  Ticks: {stats.get('tick_count', 0)}")

            # Get features
            features = manager.get_features("BTC/USDT")
            if features:
                print(f"  Price: {features.get('price', 0):.2f}")
                print(f"  SMA20: {features.get('sma_20', 0):.2f}")

    except KeyboardInterrupt:
        pass

    # Stop feeds
    print("\nStopping data feeds...")
    manager.stop()

    # Health check
    health = manager.health_check()
    print(f"\nHealth: {'Healthy' if health['healthy'] else 'Unhealthy'}")
    print(f"Messages processed: {health['total_messages']}")
