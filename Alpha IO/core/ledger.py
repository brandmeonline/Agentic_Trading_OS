"""
Canonical trading ledger.

Owns order, fill, cash, position, and exposure state for execution paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    """Authoritative in-memory order, fill, cash, and position ledger."""

    def __init__(self, initial_cash: float = 0.0):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.realized_pnl = 0.0
        self.orders: Dict[str, OrderRecord] = {}
        self.fills: List[FillRecord] = []
        self.positions: Dict[str, PositionRecord] = {}

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
        return record

    def mark_order_status(self, order_id: str, status: str) -> None:
        """Update order status."""
        order = self.orders.get(order_id)
        if not order:
            return
        order.status = status
        order.updated_at = datetime.now()

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

        order = self.orders.get(order_id)
        if order:
            previous_value = order.avg_fill_price * order.filled_quantity
            new_value = previous_value + fill.notional
            order.filled_quantity += quantity
            order.avg_fill_price = new_value / order.filled_quantity
            order.status = "filled" if order.filled_quantity >= order.quantity else "partial"
            order.updated_at = fill.timestamp

        self._apply_fill_to_position(fill)
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
