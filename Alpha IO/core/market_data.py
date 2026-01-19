"""
Market Data Feed Abstraction.

Provides unified interface for consuming market data from various sources
including live feeds, historical data, and simulated data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any, Iterator
from enum import Enum
import time
from collections import deque
import numpy as np


class DataSource(Enum):
    """Available data sources."""
    SIMULATED = "simulated"
    CSV = "csv"
    API = "api"
    WEBSOCKET = "websocket"


class TimeFrame(Enum):
    """Standard timeframes."""
    TICK = "tick"
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


@dataclass
class OHLCV:
    """Standard OHLCV bar data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class Quote:
    """Real-time quote data."""
    timestamp: datetime
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pct(self) -> float:
        return self.spread / self.mid if self.mid > 0 else 0


@dataclass
class Trade:
    """Trade/tick data."""
    timestamp: datetime
    price: float
    size: float
    side: str  # "buy" or "sell"


class MarketDataFeed(ABC):
    """
    Abstract base class for market data feeds.

    Provides unified interface for different data sources.
    """

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.callbacks: List[Callable[[str, Any], None]] = []
        self.is_running = False
        self.last_update: Dict[str, datetime] = {}

    @abstractmethod
    def connect(self) -> bool:
        """Connect to data source."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from data source."""
        pass

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Optional[OHLCV]:
        """Get latest OHLCV bar for symbol."""
        pass

    @abstractmethod
    def get_historical_bars(
        self,
        symbol: str,
        timeframe: TimeFrame,
        count: int,
        end_time: Optional[datetime] = None
    ) -> List[OHLCV]:
        """Get historical bars."""
        pass

    def subscribe(self, callback: Callable[[str, Any], None]) -> None:
        """Subscribe to data updates."""
        self.callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[str, Any], None]) -> None:
        """Unsubscribe from data updates."""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def _notify(self, symbol: str, data: Any) -> None:
        """Notify subscribers of new data."""
        for callback in self.callbacks:
            try:
                callback(symbol, data)
            except Exception as e:
                print(f"Callback error: {e}")


class SimulatedDataFeed(MarketDataFeed):
    """
    Simulated market data feed for testing and backtesting.

    Generates realistic price movements using geometric Brownian motion.
    """

    def __init__(
        self,
        symbols: List[str],
        initial_prices: Optional[Dict[str, float]] = None,
        volatility: float = 0.02,
        drift: float = 0.0001
    ):
        super().__init__(symbols)
        self.initial_prices = initial_prices or {s: 100.0 for s in symbols}
        self.volatility = volatility
        self.drift = drift

        self.current_prices: Dict[str, float] = dict(self.initial_prices)
        self.price_history: Dict[str, List[OHLCV]] = {s: [] for s in symbols}
        self.current_time = datetime.now()

    def connect(self) -> bool:
        self.is_running = True
        return True

    def disconnect(self) -> None:
        self.is_running = False

    def _generate_bar(self, symbol: str, timeframe: TimeFrame = TimeFrame.M1) -> OHLCV:
        """Generate a new OHLCV bar using GBM."""
        price = self.current_prices[symbol]

        # Generate 60 ticks for minute bar
        num_ticks = 60
        ticks = [price]

        for _ in range(num_ticks):
            # Geometric Brownian Motion
            dt = 1 / (252 * 24 * 60)  # Minute fraction of year
            dW = np.random.normal(0, np.sqrt(dt))
            price = price * np.exp((self.drift - 0.5 * self.volatility**2) * dt + self.volatility * dW)
            ticks.append(price)

        bar = OHLCV(
            timestamp=self.current_time,
            open=ticks[0],
            high=max(ticks),
            low=min(ticks),
            close=ticks[-1],
            volume=np.random.randint(1000, 10000) * price / 100
        )

        self.current_prices[symbol] = ticks[-1]
        self.price_history[symbol].append(bar)

        return bar

    def get_latest_bar(self, symbol: str) -> Optional[OHLCV]:
        if symbol not in self.symbols:
            return None
        return self._generate_bar(symbol)

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: TimeFrame,
        count: int,
        end_time: Optional[datetime] = None
    ) -> List[OHLCV]:
        """Generate historical bars."""
        bars = []
        temp_price = self.initial_prices.get(symbol, 100.0)
        temp_time = end_time or datetime.now()

        for i in range(count):
            # Go back in time
            temp_time -= timedelta(minutes=1)

            # Simple random walk for historical
            returns = np.random.normal(self.drift, self.volatility, 60)
            prices = [temp_price]
            for r in returns:
                prices.append(prices[-1] * (1 + r))

            bar = OHLCV(
                timestamp=temp_time,
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=np.random.randint(1000, 10000) * temp_price / 100
            )
            bars.append(bar)
            temp_price = prices[-1]

        return list(reversed(bars))

    def advance_time(self, minutes: int = 1) -> None:
        """Advance simulation time."""
        self.current_time += timedelta(minutes=minutes)

    def run_simulation(self, num_bars: int, callback: Optional[Callable] = None) -> None:
        """Run simulation for specified number of bars."""
        for _ in range(num_bars):
            for symbol in self.symbols:
                bar = self.get_latest_bar(symbol)
                if callback:
                    callback(symbol, bar)
                self._notify(symbol, bar)
            self.advance_time()


class CSVDataFeed(MarketDataFeed):
    """
    CSV file based data feed for backtesting.

    Reads historical data from CSV files.
    """

    def __init__(self, symbols: List[str], data_dir: str = "data"):
        super().__init__(symbols)
        self.data_dir = data_dir
        self.data: Dict[str, List[OHLCV]] = {}
        self.current_index: Dict[str, int] = {}

    def connect(self) -> bool:
        """Load CSV data for all symbols."""
        import os

        for symbol in self.symbols:
            filepath = os.path.join(self.data_dir, f"{symbol}_prices.csv")
            if os.path.exists(filepath):
                self.data[symbol] = self._load_csv(filepath)
                self.current_index[symbol] = 0
            else:
                print(f"Warning: No data file for {symbol}")

        self.is_running = True
        return len(self.data) > 0

    def _load_csv(self, filepath: str) -> List[OHLCV]:
        """Load OHLCV data from CSV."""
        import csv
        bars = []

        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    bar = OHLCV(
                        timestamp=datetime.fromisoformat(row.get('Datetime', row.get('timestamp', ''))),
                        open=float(row.get('Open', row.get('open', 0))),
                        high=float(row.get('High', row.get('high', 0))),
                        low=float(row.get('Low', row.get('low', 0))),
                        close=float(row.get('Close', row.get('close', 0))),
                        volume=float(row.get('Volume', row.get('volume', 0)))
                    )
                    bars.append(bar)
                except (ValueError, KeyError) as e:
                    continue

        return bars

    def disconnect(self) -> None:
        self.is_running = False

    def get_latest_bar(self, symbol: str) -> Optional[OHLCV]:
        if symbol not in self.data:
            return None

        idx = self.current_index.get(symbol, 0)
        if idx < len(self.data[symbol]):
            bar = self.data[symbol][idx]
            self.current_index[symbol] = idx + 1
            return bar
        return None

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: TimeFrame,
        count: int,
        end_time: Optional[datetime] = None
    ) -> List[OHLCV]:
        if symbol not in self.data:
            return []

        data = self.data[symbol]
        if end_time:
            # Find bars before end_time
            data = [b for b in data if b.timestamp <= end_time]

        return data[-count:] if len(data) >= count else data

    def reset(self) -> None:
        """Reset to beginning of data."""
        for symbol in self.symbols:
            self.current_index[symbol] = 0


class DataAggregator:
    """
    Aggregates tick/bar data into larger timeframes.

    Converts lower timeframe data to higher timeframes.
    """

    def __init__(self, target_timeframe: TimeFrame):
        self.target_timeframe = target_timeframe
        self.buffer: Dict[str, List[OHLCV]] = {}
        self.current_bar: Dict[str, OHLCV] = {}

        # Determine bars needed for aggregation
        self.bars_per_period = {
            TimeFrame.M5: 5,
            TimeFrame.M15: 15,
            TimeFrame.H1: 60,
            TimeFrame.H4: 240,
            TimeFrame.D1: 1440,
        }.get(target_timeframe, 1)

    def add_bar(self, symbol: str, bar: OHLCV) -> Optional[OHLCV]:
        """
        Add a bar and return aggregated bar if complete.

        Returns aggregated bar when enough bars collected.
        """
        if symbol not in self.buffer:
            self.buffer[symbol] = []

        self.buffer[symbol].append(bar)

        if len(self.buffer[symbol]) >= self.bars_per_period:
            return self._aggregate(symbol)

        return None

    def _aggregate(self, symbol: str) -> OHLCV:
        """Aggregate buffered bars into single bar."""
        bars = self.buffer[symbol]

        agg_bar = OHLCV(
            timestamp=bars[0].timestamp,
            open=bars[0].open,
            high=max(b.high for b in bars),
            low=min(b.low for b in bars),
            close=bars[-1].close,
            volume=sum(b.volume for b in bars)
        )

        self.buffer[symbol] = []
        return agg_bar


class DataCache:
    """
    In-memory cache for market data with TTL.

    Reduces API calls and improves performance.
    """

    def __init__(self, max_size: int = 10000, ttl_seconds: int = 60):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.timestamps: Dict[str, datetime] = {}

    def get(self, key: str) -> Optional[Any]:
        """Get cached data if not expired."""
        if key not in self.cache:
            return None

        if datetime.now() - self.timestamps[key] > timedelta(seconds=self.ttl_seconds):
            del self.cache[key]
            del self.timestamps[key]
            return None

        return self.cache[key]

    def set(self, key: str, value: Any) -> None:
        """Cache data with timestamp."""
        # Evict oldest if at capacity
        if len(self.cache) >= self.max_size:
            oldest = min(self.timestamps, key=self.timestamps.get)
            del self.cache[oldest]
            del self.timestamps[oldest]

        self.cache[key] = value
        self.timestamps[key] = datetime.now()

    def clear(self) -> None:
        """Clear all cached data."""
        self.cache.clear()
        self.timestamps.clear()


if __name__ == "__main__":
    # Test simulated data feed
    print("Testing Simulated Data Feed:")
    feed = SimulatedDataFeed(
        symbols=["BTC", "ETH"],
        initial_prices={"BTC": 45000, "ETH": 2500},
        volatility=0.03
    )

    feed.connect()

    print("\nGenerating bars:")
    for _ in range(5):
        for symbol in feed.symbols:
            bar = feed.get_latest_bar(symbol)
            print(f"  {symbol}: O={bar.open:.2f} H={bar.high:.2f} L={bar.low:.2f} C={bar.close:.2f}")
        feed.advance_time()

    print("\nHistorical bars for BTC:")
    historical = feed.get_historical_bars("BTC", TimeFrame.M1, 10)
    for bar in historical[-5:]:
        print(f"  {bar.timestamp}: C={bar.close:.2f}, V={bar.volume:.0f}")

    feed.disconnect()
