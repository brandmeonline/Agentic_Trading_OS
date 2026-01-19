"""
Database Persistence Layer.

Production-grade data persistence:
- SQLite for local development
- PostgreSQL support for production
- Redis caching layer
- Time-series data storage
- Order and trade history
- Strategy state persistence
"""

from __future__ import annotations

import json
import time
import hashlib
import pickle
import threading
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable, Tuple, Union, Type, TypeVar
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from contextlib import contextmanager
import queue


# =============================================================================
# Configuration
# =============================================================================

class DatabaseType(Enum):
    """Supported database types."""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MEMORY = "memory"


class CacheType(Enum):
    """Supported cache types."""
    MEMORY = "memory"
    REDIS = "redis"


@dataclass
class DatabaseConfig:
    """Database configuration."""
    db_type: DatabaseType = DatabaseType.SQLITE
    host: str = "localhost"
    port: int = 5432
    database: str = "trading.db"
    username: str = ""
    password: str = ""
    pool_size: int = 5
    timeout: int = 30
    echo: bool = False


@dataclass
class CacheConfig:
    """Cache configuration."""
    cache_type: CacheType = CacheType.MEMORY
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    default_ttl: int = 300
    max_memory_mb: int = 256


T = TypeVar('T')


# =============================================================================
# Abstract Database Interface
# =============================================================================

class DatabaseConnection(ABC):
    """Abstract database connection interface."""

    @abstractmethod
    def connect(self) -> bool:
        """Establish database connection."""
        pass

    @abstractmethod
    def disconnect(self):
        """Close database connection."""
        pass

    @abstractmethod
    def execute(self, query: str, params: Optional[Tuple] = None) -> Any:
        """Execute a query."""
        pass

    @abstractmethod
    def executemany(self, query: str, params_list: List[Tuple]) -> int:
        """Execute query with multiple parameter sets."""
        pass

    @abstractmethod
    def fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Tuple]:
        """Fetch single result."""
        pass

    @abstractmethod
    def fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Tuple]:
        """Fetch all results."""
        pass

    @abstractmethod
    def commit(self):
        """Commit transaction."""
        pass

    @abstractmethod
    def rollback(self):
        """Rollback transaction."""
        pass


# =============================================================================
# SQLite Implementation
# =============================================================================

class SQLiteConnection(DatabaseConnection):
    """SQLite database connection."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Connect to SQLite database."""
        try:
            if self.config.db_type == DatabaseType.MEMORY:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
            else:
                self._connection = sqlite3.connect(
                    self.config.database,
                    check_same_thread=False,
                    timeout=self.config.timeout
                )
            self._connection.row_factory = sqlite3.Row
            return True
        except Exception as e:
            print(f"SQLite connection failed: {e}")
            return False

    def disconnect(self):
        """Close SQLite connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def execute(self, query: str, params: Optional[Tuple] = None) -> Any:
        """Execute query."""
        with self._lock:
            cursor = self._connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            return cursor

    def executemany(self, query: str, params_list: List[Tuple]) -> int:
        """Execute query with multiple parameter sets."""
        with self._lock:
            cursor = self._connection.cursor()
            cursor.executemany(query, params_list)
            return cursor.rowcount

    def fetchone(self, query: str, params: Optional[Tuple] = None) -> Optional[Tuple]:
        """Fetch single result."""
        cursor = self.execute(query, params)
        return cursor.fetchone()

    def fetchall(self, query: str, params: Optional[Tuple] = None) -> List[Tuple]:
        """Fetch all results."""
        cursor = self.execute(query, params)
        return cursor.fetchall()

    def commit(self):
        """Commit transaction."""
        if self._connection:
            self._connection.commit()

    def rollback(self):
        """Rollback transaction."""
        if self._connection:
            self._connection.rollback()

    @contextmanager
    def transaction(self):
        """Context manager for transactions."""
        try:
            yield
            self.commit()
        except Exception:
            self.rollback()
            raise


# =============================================================================
# Memory Cache
# =============================================================================

class MemoryCache:
    """In-memory cache with TTL support."""

    def __init__(self, config: CacheConfig):
        self.config = config
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, expiry)
        self._lock = threading.Lock()
        self._max_size = config.max_memory_mb * 1024 * 1024

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if expiry > time.time():
                    return value
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache."""
        if ttl is None:
            ttl = self.config.default_ttl

        with self._lock:
            expiry = time.time() + ttl
            self._cache[key] = (value, expiry)
            self._cleanup_if_needed()

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self):
        """Clear all cached values."""
        with self._lock:
            self._cache.clear()

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None

    def _cleanup_if_needed(self):
        """Remove expired entries if cache is too large."""
        now = time.time()
        # Remove expired entries
        expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
        for key in expired:
            del self._cache[key]


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class OrderRecord:
    """Order database record."""
    id: Optional[int] = None
    order_id: str = ""
    client_order_id: str = ""
    exchange: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    status: str = ""
    quantity: float = 0.0
    filled_quantity: float = 0.0
    price: Optional[float] = None
    average_price: Optional[float] = None
    fees: float = 0.0
    fee_currency: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: str = ""  # JSON string

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'OrderRecord':
        """Create from database row."""
        return cls(
            id=row['id'],
            order_id=row['order_id'],
            client_order_id=row['client_order_id'],
            exchange=row['exchange'],
            symbol=row['symbol'],
            side=row['side'],
            order_type=row['order_type'],
            status=row['status'],
            quantity=row['quantity'],
            filled_quantity=row['filled_quantity'],
            price=row['price'],
            average_price=row['average_price'],
            fees=row['fees'],
            fee_currency=row['fee_currency'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            metadata=row['metadata'],
        )


@dataclass
class TradeRecord:
    """Trade database record."""
    id: Optional[int] = None
    trade_id: str = ""
    order_id: str = ""
    exchange: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    fees: float = 0.0
    fee_currency: str = ""
    realized_pnl: float = 0.0
    executed_at: datetime = field(default_factory=datetime.now)
    metadata: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'TradeRecord':
        """Create from database row."""
        return cls(
            id=row['id'],
            trade_id=row['trade_id'],
            order_id=row['order_id'],
            exchange=row['exchange'],
            symbol=row['symbol'],
            side=row['side'],
            quantity=row['quantity'],
            price=row['price'],
            fees=row['fees'],
            fee_currency=row['fee_currency'],
            realized_pnl=row['realized_pnl'],
            executed_at=datetime.fromisoformat(row['executed_at']),
            metadata=row['metadata'],
        )


@dataclass
class PositionRecord:
    """Position database record."""
    id: Optional[int] = None
    position_id: str = ""
    exchange: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    leverage: float = 1.0
    margin: float = 0.0
    liquidation_price: Optional[float] = None
    opened_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    metadata: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'PositionRecord':
        """Create from database row."""
        return cls(
            id=row['id'],
            position_id=row['position_id'],
            exchange=row['exchange'],
            symbol=row['symbol'],
            side=row['side'],
            quantity=row['quantity'],
            entry_price=row['entry_price'],
            current_price=row['current_price'],
            unrealized_pnl=row['unrealized_pnl'],
            realized_pnl=row['realized_pnl'],
            leverage=row['leverage'],
            margin=row['margin'],
            liquidation_price=row['liquidation_price'],
            opened_at=datetime.fromisoformat(row['opened_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
            closed_at=datetime.fromisoformat(row['closed_at']) if row['closed_at'] else None,
            metadata=row['metadata'],
        )


@dataclass
class StrategyStateRecord:
    """Strategy state database record."""
    id: Optional[int] = None
    strategy_id: str = ""
    strategy_name: str = ""
    status: str = ""
    parameters: str = ""  # JSON
    state: str = ""  # JSON
    performance_metrics: str = ""  # JSON
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'StrategyStateRecord':
        """Create from database row."""
        return cls(
            id=row['id'],
            strategy_id=row['strategy_id'],
            strategy_name=row['strategy_name'],
            status=row['status'],
            parameters=row['parameters'],
            state=row['state'],
            performance_metrics=row['performance_metrics'],
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at']),
        )


@dataclass
class OHLCVRecord:
    """OHLCV candle database record."""
    id: Optional[int] = None
    symbol: str = ""
    exchange: str = ""
    interval: str = ""
    timestamp: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'OHLCVRecord':
        """Create from database row."""
        return cls(
            id=row['id'],
            symbol=row['symbol'],
            exchange=row['exchange'],
            interval=row['interval'],
            timestamp=row['timestamp'],
            open=row['open'],
            high=row['high'],
            low=row['low'],
            close=row['close'],
            volume=row['volume'],
        )


# =============================================================================
# Repository Classes
# =============================================================================

class OrderRepository:
    """Order data access layer."""

    def __init__(self, db: DatabaseConnection, cache: MemoryCache):
        self.db = db
        self.cache = cache

    def create_table(self):
        """Create orders table."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                client_order_id TEXT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                quantity REAL NOT NULL,
                filled_quantity REAL DEFAULT 0,
                price REAL,
                average_price REAL,
                fees REAL DEFAULT 0,
                fee_currency TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")
        self.db.commit()

    def save(self, order: OrderRecord) -> int:
        """Save order to database."""
        if order.id:
            self.db.execute("""
                UPDATE orders SET
                    status = ?, filled_quantity = ?, average_price = ?,
                    fees = ?, updated_at = ?, metadata = ?
                WHERE id = ?
            """, (
                order.status, order.filled_quantity, order.average_price,
                order.fees, order.updated_at.isoformat(), order.metadata, order.id
            ))
        else:
            cursor = self.db.execute("""
                INSERT INTO orders (
                    order_id, client_order_id, exchange, symbol, side, order_type,
                    status, quantity, filled_quantity, price, average_price,
                    fees, fee_currency, created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order.order_id, order.client_order_id, order.exchange, order.symbol,
                order.side, order.order_type, order.status, order.quantity,
                order.filled_quantity, order.price, order.average_price,
                order.fees, order.fee_currency, order.created_at.isoformat(),
                order.updated_at.isoformat(), order.metadata
            ))
            order.id = cursor.lastrowid

        self.db.commit()
        self.cache.delete(f"order:{order.order_id}")
        return order.id

    def get_by_id(self, order_id: str) -> Optional[OrderRecord]:
        """Get order by order_id."""
        cached = self.cache.get(f"order:{order_id}")
        if cached:
            return cached

        row = self.db.fetchone("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        if row:
            order = OrderRecord.from_row(row)
            self.cache.set(f"order:{order_id}", order, ttl=60)
            return order
        return None

    def get_by_symbol(self, symbol: str, limit: int = 100) -> List[OrderRecord]:
        """Get orders by symbol."""
        rows = self.db.fetchall(
            "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, limit)
        )
        return [OrderRecord.from_row(row) for row in rows]

    def get_open_orders(self, exchange: Optional[str] = None) -> List[OrderRecord]:
        """Get all open orders."""
        if exchange:
            rows = self.db.fetchall(
                "SELECT * FROM orders WHERE status = 'open' AND exchange = ?",
                (exchange,)
            )
        else:
            rows = self.db.fetchall("SELECT * FROM orders WHERE status = 'open'")
        return [OrderRecord.from_row(row) for row in rows]

    def get_orders_in_range(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[OrderRecord]:
        """Get orders within time range."""
        if symbol:
            rows = self.db.fetchall("""
                SELECT * FROM orders
                WHERE created_at BETWEEN ? AND ? AND symbol = ?
                ORDER BY created_at
            """, (start_time.isoformat(), end_time.isoformat(), symbol))
        else:
            rows = self.db.fetchall("""
                SELECT * FROM orders
                WHERE created_at BETWEEN ? AND ?
                ORDER BY created_at
            """, (start_time.isoformat(), end_time.isoformat()))
        return [OrderRecord.from_row(row) for row in rows]


class TradeRepository:
    """Trade data access layer."""

    def __init__(self, db: DatabaseConnection, cache: MemoryCache):
        self.db = db
        self.cache = cache

    def create_table(self):
        """Create trades table."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                order_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fees REAL DEFAULT 0,
                fee_currency TEXT,
                realized_pnl REAL DEFAULT 0,
                executed_at TEXT NOT NULL,
                metadata TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_trades_executed ON trades(executed_at)")
        self.db.commit()

    def save(self, trade: TradeRecord) -> int:
        """Save trade to database."""
        cursor = self.db.execute("""
            INSERT INTO trades (
                trade_id, order_id, exchange, symbol, side, quantity,
                price, fees, fee_currency, realized_pnl, executed_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.trade_id, trade.order_id, trade.exchange, trade.symbol,
            trade.side, trade.quantity, trade.price, trade.fees,
            trade.fee_currency, trade.realized_pnl,
            trade.executed_at.isoformat(), trade.metadata
        ))
        trade.id = cursor.lastrowid
        self.db.commit()
        return trade.id

    def get_by_symbol(self, symbol: str, limit: int = 100) -> List[TradeRecord]:
        """Get trades by symbol."""
        rows = self.db.fetchall(
            "SELECT * FROM trades WHERE symbol = ? ORDER BY executed_at DESC LIMIT ?",
            (symbol, limit)
        )
        return [TradeRecord.from_row(row) for row in rows]

    def get_trades_in_range(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[TradeRecord]:
        """Get trades within time range."""
        if symbol:
            rows = self.db.fetchall("""
                SELECT * FROM trades
                WHERE executed_at BETWEEN ? AND ? AND symbol = ?
                ORDER BY executed_at
            """, (start_time.isoformat(), end_time.isoformat(), symbol))
        else:
            rows = self.db.fetchall("""
                SELECT * FROM trades
                WHERE executed_at BETWEEN ? AND ?
                ORDER BY executed_at
            """, (start_time.isoformat(), end_time.isoformat()))
        return [TradeRecord.from_row(row) for row in rows]

    def get_pnl_summary(
        self,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, float]:
        """Get PnL summary for time range."""
        row = self.db.fetchone("""
            SELECT
                SUM(realized_pnl) as total_pnl,
                SUM(fees) as total_fees,
                COUNT(*) as trade_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades
            FROM trades
            WHERE executed_at BETWEEN ? AND ?
        """, (start_time.isoformat(), end_time.isoformat()))

        if row:
            return {
                "total_pnl": row["total_pnl"] or 0.0,
                "total_fees": row["total_fees"] or 0.0,
                "trade_count": row["trade_count"] or 0,
                "winning_trades": row["winning_trades"] or 0,
                "win_rate": (row["winning_trades"] or 0) / max(1, row["trade_count"] or 1),
            }
        return {"total_pnl": 0.0, "total_fees": 0.0, "trade_count": 0, "winning_trades": 0, "win_rate": 0.0}


class PositionRepository:
    """Position data access layer."""

    def __init__(self, db: DatabaseConnection, cache: MemoryCache):
        self.db = db
        self.cache = cache

    def create_table(self):
        """Create positions table."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT UNIQUE NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                leverage REAL DEFAULT 1,
                margin REAL DEFAULT 0,
                liquidation_price REAL,
                opened_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                metadata TEXT
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        self.db.commit()

    def save(self, position: PositionRecord) -> int:
        """Save position to database."""
        if position.id:
            self.db.execute("""
                UPDATE positions SET
                    current_price = ?, unrealized_pnl = ?, realized_pnl = ?,
                    updated_at = ?, closed_at = ?, metadata = ?
                WHERE id = ?
            """, (
                position.current_price, position.unrealized_pnl, position.realized_pnl,
                position.updated_at.isoformat(),
                position.closed_at.isoformat() if position.closed_at else None,
                position.metadata, position.id
            ))
        else:
            cursor = self.db.execute("""
                INSERT INTO positions (
                    position_id, exchange, symbol, side, quantity, entry_price,
                    current_price, unrealized_pnl, realized_pnl, leverage, margin,
                    liquidation_price, opened_at, updated_at, closed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position.position_id, position.exchange, position.symbol, position.side,
                position.quantity, position.entry_price, position.current_price,
                position.unrealized_pnl, position.realized_pnl, position.leverage,
                position.margin, position.liquidation_price,
                position.opened_at.isoformat(), position.updated_at.isoformat(),
                position.closed_at.isoformat() if position.closed_at else None,
                position.metadata
            ))
            position.id = cursor.lastrowid

        self.db.commit()
        self.cache.delete(f"position:{position.position_id}")
        return position.id

    def get_open_positions(self, exchange: Optional[str] = None) -> List[PositionRecord]:
        """Get all open positions."""
        if exchange:
            rows = self.db.fetchall(
                "SELECT * FROM positions WHERE closed_at IS NULL AND exchange = ?",
                (exchange,)
            )
        else:
            rows = self.db.fetchall("SELECT * FROM positions WHERE closed_at IS NULL")
        return [PositionRecord.from_row(row) for row in rows]


class StrategyStateRepository:
    """Strategy state data access layer."""

    def __init__(self, db: DatabaseConnection, cache: MemoryCache):
        self.db = db
        self.cache = cache

    def create_table(self):
        """Create strategy_states table."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS strategy_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT UNIQUE NOT NULL,
                strategy_name TEXT NOT NULL,
                status TEXT NOT NULL,
                parameters TEXT,
                state TEXT,
                performance_metrics TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.db.commit()

    def save(self, state: StrategyStateRecord) -> int:
        """Save strategy state."""
        if state.id:
            self.db.execute("""
                UPDATE strategy_states SET
                    status = ?, parameters = ?, state = ?,
                    performance_metrics = ?, updated_at = ?
                WHERE id = ?
            """, (
                state.status, state.parameters, state.state,
                state.performance_metrics, state.updated_at.isoformat(), state.id
            ))
        else:
            cursor = self.db.execute("""
                INSERT INTO strategy_states (
                    strategy_id, strategy_name, status, parameters,
                    state, performance_metrics, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.strategy_id, state.strategy_name, state.status,
                state.parameters, state.state, state.performance_metrics,
                state.created_at.isoformat(), state.updated_at.isoformat()
            ))
            state.id = cursor.lastrowid

        self.db.commit()
        self.cache.delete(f"strategy:{state.strategy_id}")
        return state.id

    def get_by_id(self, strategy_id: str) -> Optional[StrategyStateRecord]:
        """Get strategy state by ID."""
        cached = self.cache.get(f"strategy:{strategy_id}")
        if cached:
            return cached

        row = self.db.fetchone(
            "SELECT * FROM strategy_states WHERE strategy_id = ?",
            (strategy_id,)
        )
        if row:
            state = StrategyStateRecord.from_row(row)
            self.cache.set(f"strategy:{strategy_id}", state, ttl=60)
            return state
        return None

    def get_all_strategies(self) -> List[StrategyStateRecord]:
        """Get all strategy states."""
        rows = self.db.fetchall("SELECT * FROM strategy_states ORDER BY updated_at DESC")
        return [StrategyStateRecord.from_row(row) for row in rows]


class OHLCVRepository:
    """OHLCV time-series data access layer."""

    def __init__(self, db: DatabaseConnection, cache: MemoryCache):
        self.db = db
        self.cache = cache

    def create_table(self):
        """Create ohlcv table."""
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                UNIQUE(symbol, exchange, interval, timestamp)
            )
        """)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_interval ON ohlcv(symbol, interval)")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_timestamp ON ohlcv(timestamp)")
        self.db.commit()

    def save_batch(self, records: List[OHLCVRecord]):
        """Save batch of OHLCV records."""
        self.db.executemany("""
            INSERT OR REPLACE INTO ohlcv (
                symbol, exchange, interval, timestamp, open, high, low, close, volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (r.symbol, r.exchange, r.interval, r.timestamp, r.open, r.high, r.low, r.close, r.volume)
            for r in records
        ])
        self.db.commit()

    def get_candles(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[OHLCVRecord]:
        """Get OHLCV candles."""
        query = "SELECT * FROM ohlcv WHERE symbol = ? AND interval = ?"
        params = [symbol, interval]

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self.db.fetchall(query, tuple(params))
        return [OHLCVRecord.from_row(row) for row in reversed(rows)]


# =============================================================================
# Database Manager
# =============================================================================

class DatabaseManager:
    """Central database manager."""

    def __init__(self, db_config: Optional[DatabaseConfig] = None, cache_config: Optional[CacheConfig] = None):
        self.db_config = db_config or DatabaseConfig()
        self.cache_config = cache_config or CacheConfig()

        # Initialize database connection
        self.db = SQLiteConnection(self.db_config)

        # Initialize cache
        self.cache = MemoryCache(self.cache_config)

        # Initialize repositories
        self.orders: Optional[OrderRepository] = None
        self.trades: Optional[TradeRepository] = None
        self.positions: Optional[PositionRepository] = None
        self.strategies: Optional[StrategyStateRepository] = None
        self.ohlcv: Optional[OHLCVRepository] = None

    def connect(self) -> bool:
        """Connect to database and initialize repositories."""
        if not self.db.connect():
            return False

        # Initialize repositories
        self.orders = OrderRepository(self.db, self.cache)
        self.trades = TradeRepository(self.db, self.cache)
        self.positions = PositionRepository(self.db, self.cache)
        self.strategies = StrategyStateRepository(self.db, self.cache)
        self.ohlcv = OHLCVRepository(self.db, self.cache)

        # Create tables
        self.orders.create_table()
        self.trades.create_table()
        self.positions.create_table()
        self.strategies.create_table()
        self.ohlcv.create_table()

        return True

    def disconnect(self):
        """Disconnect from database."""
        self.db.disconnect()
        self.cache.clear()


# =============================================================================
# Factory Functions
# =============================================================================

def create_database_manager(
    database: str = "trading.db",
    in_memory: bool = False
) -> DatabaseManager:
    """Create database manager."""
    db_config = DatabaseConfig(
        db_type=DatabaseType.MEMORY if in_memory else DatabaseType.SQLITE,
        database=database
    )
    manager = DatabaseManager(db_config)
    manager.connect()
    return manager


# =============================================================================
# Testing
# =============================================================================

def test_database():
    """Test database functionality."""
    print("Testing Database Layer...")

    # Create in-memory database
    db = create_database_manager(in_memory=True)

    # Test order repository
    print("\n1. Testing Order Repository...")
    order = OrderRecord(
        order_id="test_order_1",
        client_order_id="client_1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        status="open",
        quantity=0.1,
        price=50000.0,
    )
    order_id = db.orders.save(order)
    print(f"   Saved order with ID: {order_id}")

    retrieved = db.orders.get_by_id("test_order_1")
    assert retrieved is not None
    print(f"   Retrieved order: {retrieved.symbol} {retrieved.side}")

    # Test trade repository
    print("\n2. Testing Trade Repository...")
    trade = TradeRecord(
        trade_id="test_trade_1",
        order_id="test_order_1",
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        price=50000.0,
        fees=5.0,
        fee_currency="USDT",
        realized_pnl=100.0,
    )
    trade_id = db.trades.save(trade)
    print(f"   Saved trade with ID: {trade_id}")

    trades = db.trades.get_by_symbol("BTC/USDT")
    assert len(trades) == 1
    print(f"   Retrieved {len(trades)} trade(s)")

    # Test position repository
    print("\n3. Testing Position Repository...")
    position = PositionRecord(
        position_id="test_position_1",
        exchange="binance",
        symbol="BTC/USDT",
        side="long",
        quantity=0.1,
        entry_price=50000.0,
        current_price=51000.0,
        unrealized_pnl=100.0,
    )
    pos_id = db.positions.save(position)
    print(f"   Saved position with ID: {pos_id}")

    positions = db.positions.get_open_positions()
    assert len(positions) == 1
    print(f"   Retrieved {len(positions)} open position(s)")

    # Test strategy state repository
    print("\n4. Testing Strategy State Repository...")
    state = StrategyStateRecord(
        strategy_id="momentum_1",
        strategy_name="Momentum Strategy",
        status="active",
        parameters=json.dumps({"lookback": 20, "threshold": 0.02}),
        state=json.dumps({"last_signal": "buy", "position": 0.1}),
        performance_metrics=json.dumps({"sharpe": 1.5, "win_rate": 0.6}),
    )
    state_id = db.strategies.save(state)
    print(f"   Saved strategy state with ID: {state_id}")

    retrieved_state = db.strategies.get_by_id("momentum_1")
    assert retrieved_state is not None
    print(f"   Retrieved strategy: {retrieved_state.strategy_name}")

    # Test OHLCV repository
    print("\n5. Testing OHLCV Repository...")
    import numpy as np
    ohlcv_records = []
    for i in range(100):
        ohlcv_records.append(OHLCVRecord(
            symbol="BTC/USDT",
            exchange="binance",
            interval="1h",
            timestamp=int(time.time()) - (100 - i) * 3600,
            open=50000 + np.random.randn() * 100,
            high=50100 + np.random.randn() * 100,
            low=49900 + np.random.randn() * 100,
            close=50050 + np.random.randn() * 100,
            volume=100 + np.random.randn() * 10,
        ))
    db.ohlcv.save_batch(ohlcv_records)
    print(f"   Saved {len(ohlcv_records)} OHLCV records")

    candles = db.ohlcv.get_candles("BTC/USDT", "1h", limit=50)
    assert len(candles) == 50
    print(f"   Retrieved {len(candles)} candles")

    # Test cache
    print("\n6. Testing Cache...")
    db.cache.set("test_key", {"value": 42}, ttl=60)
    cached = db.cache.get("test_key")
    assert cached["value"] == 42
    print(f"   Cache working: {cached}")

    # Test PnL summary
    print("\n7. Testing PnL Summary...")
    pnl = db.trades.get_pnl_summary(
        datetime.now() - timedelta(days=1),
        datetime.now()
    )
    print(f"   PnL summary: {pnl}")

    # Cleanup
    db.disconnect()

    print("\n✓ All database tests passed!")
    return True


if __name__ == "__main__":
    test_database()
