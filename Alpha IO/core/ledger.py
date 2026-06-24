"""
Canonical trading ledger.

Owns order, fill, cash, position, and exposure state for execution paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class OrderRecord:
    """Order lifecycle record."""
    order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    price: Optional[float] = None
    status: str = "pending"
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize order record."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "price": self.price,
            "status": self.status,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass
class FillRecord:
    """Confirmed execution fill."""
    fill_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def notional(self) -> float:
        """Fill notional value."""
        return self.quantity * self.price

    def to_dict(self) -> Dict[str, Any]:
        """Serialize fill record."""
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "notional": self.notional,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class PositionRecord:
    """Net position projection built from fills."""
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def side(self) -> str:
        """Position side."""
        if self.quantity > 0:
            return "long"
        if self.quantity < 0:
            return "short"
        return "flat"

    @property
    def exposure(self) -> float:
        """Absolute notional exposure at average entry price."""
        return abs(self.quantity) * self.avg_price

    def to_dict(self) -> Dict[str, Any]:
        """Serialize position record."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
            "realized_pnl": self.realized_pnl,
            "exposure": self.exposure,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class PortfolioSnapshot:
    """Point-in-time ledger projection."""
    cash: float
    realized_pnl: float
    positions: Dict[str, PositionRecord]
    open_orders: List[OrderRecord]

    @property
    def exposure_by_symbol(self) -> Dict[str, float]:
        """Exposure by symbol."""
        return {
            symbol: position.exposure
            for symbol, position in self.positions.items()
            if position.quantity != 0
        }

    @property
    def total_exposure(self) -> float:
        """Total absolute exposure."""
        return sum(self.exposure_by_symbol.values())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot."""
        return {
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "positions": {
                symbol: position.to_dict()
                for symbol, position in self.positions.items()
            },
            "open_orders": [order.to_dict() for order in self.open_orders],
            "exposure_by_symbol": self.exposure_by_symbol,
            "total_exposure": self.total_exposure,
        }


class TradingLedger:
    """Authoritative order, fill, cash, and position ledger."""

    def __init__(
        self,
        initial_cash: float = 0.0,
        persist_path: Optional[str] = None,
        sqlite_path: Optional[str] = None,
    ):
        if persist_path and sqlite_path:
            raise ValueError("Use either persist_path or sqlite_path, not both")

        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.realized_pnl = 0.0
        self.orders: Dict[str, OrderRecord] = {}
        self.fills: List[FillRecord] = []
        self.positions: Dict[str, PositionRecord] = {}
        self.persist_path = Path(persist_path) if persist_path else None
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None

        if self.sqlite_path:
            self._load_from_sqlite(self.sqlite_path)
        elif self.persist_path and self.persist_path.exists():
            self._load_from_path(self.persist_path)

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> datetime:
        """Parse ISO datetimes from persisted state."""
        if not value:
            return datetime.now()
        return datetime.fromisoformat(value)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize full ledger state."""
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "realized_pnl": self.realized_pnl,
            "orders": {
                order_id: order.to_dict()
                for order_id, order in self.orders.items()
            },
            "fills": [fill.to_dict() for fill in self.fills],
            "positions": {
                symbol: position.to_dict()
                for symbol, position in self.positions.items()
            },
        }

    def _load_from_path(self, path: Path) -> None:
        """Load ledger state from disk."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._hydrate_from_dict(data)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Failed to load persisted trading ledger: {path}") from exc

    def _hydrate_from_dict(self, data: Dict[str, Any]) -> None:
        """Hydrate ledger state from serialized data."""
        self.initial_cash = float(data["initial_cash"])
        self.cash = float(data["cash"])
        self.realized_pnl = float(data.get("realized_pnl", 0.0))

        self.orders = {
            order_id: OrderRecord(
                order_id=record["order_id"],
                symbol=record["symbol"],
                side=record["side"],
                quantity=float(record["quantity"]),
                order_type=record["order_type"],
                price=record.get("price"),
                status=record.get("status", "pending"),
                filled_quantity=float(record.get("filled_quantity", 0.0)),
                avg_fill_price=float(record.get("avg_fill_price", 0.0)),
                created_at=self._parse_datetime(record.get("created_at")),
                updated_at=self._parse_datetime(record.get("updated_at")),
                metadata=dict(record.get("metadata", {})),
            )
            for order_id, record in data.get("orders", {}).items()
        }
        self.fills = [
            FillRecord(
                fill_id=record["fill_id"],
                order_id=record["order_id"],
                symbol=record["symbol"],
                side=record["side"],
                quantity=float(record["quantity"]),
                price=float(record["price"]),
                timestamp=self._parse_datetime(record.get("timestamp")),
            )
            for record in data.get("fills", [])
        ]
        self.positions = {
            symbol: PositionRecord(
                symbol=record["symbol"],
                quantity=float(record.get("quantity", 0.0)),
                avg_price=float(record.get("avg_price", 0.0)),
                realized_pnl=float(record.get("realized_pnl", 0.0)),
                updated_at=self._parse_datetime(record.get("updated_at")),
            )
            for symbol, record in data.get("positions", {}).items()
            if float(record.get("quantity", 0.0)) != 0
        }

    def _connect_sqlite(self, path: Path) -> sqlite3.Connection:
        """Open a SQLite connection for transactional ledger storage."""
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_sqlite_schema(self, conn: sqlite3.Connection) -> None:
        """Create ledger tables if needed."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ledger_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                initial_cash REAL NOT NULL,
                cash REAL NOT NULL,
                realized_pnl REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_orders (
                order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                filled_quantity REAL NOT NULL,
                avg_fill_price REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ledger_positions (
                symbol TEXT PRIMARY KEY,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

    def _load_from_sqlite(self, path: Path) -> None:
        """Load ledger state from SQLite, creating an empty store if needed."""
        try:
            with self._connect_sqlite(path) as conn:
                self._ensure_sqlite_schema(conn)
                state = conn.execute("SELECT * FROM ledger_state WHERE id = 1").fetchone()
                if not state:
                    self._save_to_sqlite(path)
                    return

                orders = {
                    row["order_id"]: {
                        "order_id": row["order_id"],
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "quantity": row["quantity"],
                        "order_type": row["order_type"],
                        "price": row["price"],
                        "status": row["status"],
                        "filled_quantity": row["filled_quantity"],
                        "avg_fill_price": row["avg_fill_price"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "metadata": json.loads(row["metadata"] or "{}"),
                    }
                    for row in conn.execute("SELECT * FROM ledger_orders")
                }
                fills = [
                    {
                        "fill_id": row["fill_id"],
                        "order_id": row["order_id"],
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "quantity": row["quantity"],
                        "price": row["price"],
                        "timestamp": row["timestamp"],
                    }
                    for row in conn.execute("SELECT * FROM ledger_fills ORDER BY rowid")
                ]
                positions = {
                    row["symbol"]: {
                        "symbol": row["symbol"],
                        "quantity": row["quantity"],
                        "avg_price": row["avg_price"],
                        "realized_pnl": row["realized_pnl"],
                        "updated_at": row["updated_at"],
                    }
                    for row in conn.execute("SELECT * FROM ledger_positions")
                }
                self._hydrate_from_dict({
                    "initial_cash": state["initial_cash"],
                    "cash": state["cash"],
                    "realized_pnl": state["realized_pnl"],
                    "orders": orders,
                    "fills": fills,
                    "positions": positions,
                })
        except (sqlite3.DatabaseError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Failed to load SQLite trading ledger: {path}") from exc

    def _save_to_sqlite(self, path: Path) -> None:
        """Persist ledger state to SQLite in a single transaction."""
        try:
            with self._connect_sqlite(path) as conn:
                self._ensure_sqlite_schema(conn)
                conn.execute("BEGIN")
                conn.execute("DELETE FROM ledger_state")
                conn.execute("DELETE FROM ledger_orders")
                conn.execute("DELETE FROM ledger_fills")
                conn.execute("DELETE FROM ledger_positions")
                conn.execute(
                    "INSERT INTO ledger_state (id, initial_cash, cash, realized_pnl) VALUES (1, ?, ?, ?)",
                    (self.initial_cash, self.cash, self.realized_pnl),
                )
                conn.executemany(
                    """
                    INSERT INTO ledger_orders (
                        order_id, symbol, side, quantity, order_type, price, status,
                        filled_quantity, avg_fill_price, created_at, updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            order.order_id,
                            order.symbol,
                            order.side,
                            order.quantity,
                            order.order_type,
                            order.price,
                            order.status,
                            order.filled_quantity,
                            order.avg_fill_price,
                            order.created_at.isoformat(),
                            order.updated_at.isoformat(),
                            json.dumps(order.metadata),
                        )
                        for order in self.orders.values()
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO ledger_fills (
                        fill_id, order_id, symbol, side, quantity, price, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            fill.fill_id,
                            fill.order_id,
                            fill.symbol,
                            fill.side,
                            fill.quantity,
                            fill.price,
                            fill.timestamp.isoformat(),
                        )
                        for fill in self.fills
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO ledger_positions (
                        symbol, quantity, avg_price, realized_pnl, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            position.symbol,
                            position.quantity,
                            position.avg_price,
                            position.realized_pnl,
                            position.updated_at.isoformat(),
                        )
                        for position in self.positions.values()
                    ],
                )
        except (sqlite3.DatabaseError, OSError) as exc:
            raise RuntimeError(f"Failed to save SQLite trading ledger: {path}") from exc

    def save(self, path: Optional[Path] = None) -> None:
        """Persist ledger state atomically."""
        if self.sqlite_path and path is None:
            self._save_to_sqlite(self.sqlite_path)
            return

        target = path or self.persist_path
        if not target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f".{target.name}.tmp")
        temp_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(temp_path, target)

    def _persist_if_configured(self) -> None:
        """Persist state when this ledger has a configured path."""
        if self.sqlite_path:
            self._save_to_sqlite(self.sqlite_path)
        elif self.persist_path:
            self.save(self.persist_path)

    def _normalize_external_position(self, raw: Any) -> Dict[str, float]:
        """Normalize a broker position object or dictionary."""
        if isinstance(raw, dict):
            symbol = raw.get("symbol")
            quantity = raw.get("quantity", raw.get("qty", 0.0))
            avg_price = raw.get("avg_price", raw.get("avg_entry_price", 0.0))
            side = raw.get("side")
        else:
            symbol = getattr(raw, "symbol", None)
            quantity = getattr(raw, "quantity", getattr(raw, "qty", 0.0))
            avg_price = getattr(raw, "avg_price", getattr(raw, "avg_entry_price", 0.0))
            side = getattr(raw, "side", None)

        signed_quantity = float(quantity)
        if side == "short" and signed_quantity > 0:
            signed_quantity *= -1

        return {
            "symbol": str(symbol),
            "quantity": signed_quantity,
            "avg_price": float(avg_price or 0.0),
        }

    def reconcile_positions(
        self,
        broker_positions: Any,
        quantity_tolerance: float = 1e-8,
        price_tolerance: float = 1e-6,
    ) -> Dict[str, Any]:
        """Compare ledger positions with broker/account positions."""
        if isinstance(broker_positions, dict):
            iterable = broker_positions.values()
        else:
            iterable = broker_positions or []

        broker_projection = {
            normalized["symbol"]: normalized
            for normalized in (self._normalize_external_position(pos) for pos in iterable)
            if normalized["symbol"] and normalized["symbol"] != "None"
        }
        ledger_projection = {
            symbol: {
                "symbol": symbol,
                "quantity": position.quantity,
                "avg_price": position.avg_price,
            }
            for symbol, position in self.positions.items()
        }

        discrepancies = []
        for symbol in sorted(set(ledger_projection) | set(broker_projection)):
            ledger_position = ledger_projection.get(symbol, {"symbol": symbol, "quantity": 0.0, "avg_price": 0.0})
            broker_position = broker_projection.get(symbol, {"symbol": symbol, "quantity": 0.0, "avg_price": 0.0})
            quantity_delta = broker_position["quantity"] - ledger_position["quantity"]
            price_delta = broker_position["avg_price"] - ledger_position["avg_price"]
            if abs(quantity_delta) > quantity_tolerance or abs(price_delta) > price_tolerance:
                discrepancies.append({
                    "symbol": symbol,
                    "ledger_quantity": ledger_position["quantity"],
                    "broker_quantity": broker_position["quantity"],
                    "quantity_delta": quantity_delta,
                    "ledger_avg_price": ledger_position["avg_price"],
                    "broker_avg_price": broker_position["avg_price"],
                    "avg_price_delta": price_delta,
                })

        return {
            "in_sync": not discrepancies,
            "discrepancies": discrepancies,
            "ledger_positions": ledger_projection,
            "broker_positions": broker_projection,
        }

    def record_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float] = None,
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrderRecord:
        """Record a newly created order."""
        record = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            status=status,
            metadata=metadata or {},
        )
        self.orders[order_id] = record
        self._persist_if_configured()
        return record

    def mark_order_status(self, order_id: str, status: str) -> None:
        """Update order status."""
        order = self.orders.get(order_id)
        if not order:
            return
        order.status = status
        order.updated_at = datetime.now()
        self._persist_if_configured()

    def record_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        timestamp: Optional[datetime] = None,
    ) -> FillRecord:
        """Record a confirmed fill and update cash/position projections."""
        if quantity <= 0:
            raise ValueError("Fill quantity must be positive")
        if price <= 0:
            raise ValueError("Fill price must be positive")

        order = self.orders.get(order_id)
        if order:
            if symbol != order.symbol:
                raise ValueError("Fill symbol does not match order symbol")
            if side != order.side:
                raise ValueError("Fill side does not match order side")
            if order.filled_quantity + quantity > order.quantity + 1e-9:
                raise ValueError("Fill quantity exceeds order quantity")

        fill = FillRecord(
            fill_id=f"{order_id}-{len(self.fills) + 1}",
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            timestamp=timestamp or datetime.now(),
        )
        self.fills.append(fill)

        if order:
            previous_value = order.avg_fill_price * order.filled_quantity
            new_value = previous_value + fill.notional
            order.filled_quantity += quantity
            order.avg_fill_price = new_value / order.filled_quantity
            order.status = "filled" if order.filled_quantity >= order.quantity else "partial"
            order.updated_at = fill.timestamp

        self._apply_fill_to_position(fill)
        self._persist_if_configured()
        return fill

    def _apply_fill_to_position(self, fill: FillRecord) -> None:
        """Update cash and position from a fill."""
        signed_qty = fill.quantity if fill.side == "buy" else -fill.quantity
        self.cash -= fill.notional if fill.side == "buy" else -fill.notional

        position = self.positions.get(fill.symbol)
        if not position:
            position = PositionRecord(symbol=fill.symbol)
            self.positions[fill.symbol] = position

        current_qty = position.quantity
        new_qty = current_qty + signed_qty

        if current_qty == 0 or (current_qty > 0 and signed_qty > 0) or (current_qty < 0 and signed_qty < 0):
            total_abs_qty = abs(current_qty) + abs(signed_qty)
            if total_abs_qty > 0:
                position.avg_price = (
                    (abs(current_qty) * position.avg_price) + (abs(signed_qty) * fill.price)
                ) / total_abs_qty
        else:
            closed_qty = min(abs(current_qty), abs(signed_qty))
            if current_qty > 0:
                realized = (fill.price - position.avg_price) * closed_qty
            else:
                realized = (position.avg_price - fill.price) * closed_qty

            position.realized_pnl += realized
            self.realized_pnl += realized

            if abs(signed_qty) > abs(current_qty):
                position.avg_price = fill.price
            elif new_qty == 0:
                position.avg_price = 0.0

        position.quantity = new_qty
        position.updated_at = fill.timestamp

        if position.quantity == 0:
            self.positions.pop(fill.symbol, None)

    def snapshot(self) -> PortfolioSnapshot:
        """Return a point-in-time portfolio projection."""
        open_orders = [
            order for order in self.orders.values()
            if order.status in {"pending", "submitted", "partial"}
        ]
        return PortfolioSnapshot(
            cash=self.cash,
            realized_pnl=self.realized_pnl,
            positions=dict(self.positions),
            open_orders=open_orders,
        )
