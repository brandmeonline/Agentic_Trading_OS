"""
Trade Execution Engine with Order Management.

Provides sophisticated order management, execution algorithms,
and real-time position tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from enum import Enum
import uuid
import time
from collections import deque


class OrderType(Enum):
    """Types of orders."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class OrderSide(Enum):
    """Order side."""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """Order execution status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ExecutionAlgo(Enum):
    """Execution algorithms."""
    IMMEDIATE = "immediate"
    TWAP = "twap"  # Time-Weighted Average Price
    VWAP = "vwap"  # Volume-Weighted Average Price
    ICEBERG = "iceberg"
    SMART = "smart"


@dataclass
class Order:
    """Represents a trading order."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    asset: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    price: Optional[float] = None  # For limit orders
    stop_price: Optional[float] = None  # For stop orders
    trailing_pct: Optional[float] = None  # For trailing stops

    # Execution
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    fills: List[Dict] = field(default_factory=list)

    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # Metadata
    signal_id: Optional[str] = None
    strategy: Optional[str] = None
    notes: str = ""

    @property
    def is_active(self) -> bool:
        """Check if order is still active."""
        return self.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL]

    @property
    def remaining_quantity(self) -> float:
        """Get unfilled quantity."""
        return self.quantity - self.filled_quantity

    @property
    def avg_fill_price(self) -> float:
        """Calculate average fill price."""
        if not self.fills:
            return 0.0
        total_value = sum(f["quantity"] * f["price"] for f in self.fills)
        total_qty = sum(f["quantity"] for f in self.fills)
        return total_value / total_qty if total_qty > 0 else 0.0

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "asset": self.asset,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ExecutionConfig:
    """Execution engine configuration."""
    simulation_mode: bool = True
    default_algo: ExecutionAlgo = ExecutionAlgo.IMMEDIATE
    max_slippage_pct: float = 0.01  # 1%
    order_timeout_seconds: int = 300
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0

    # TWAP/VWAP settings
    algo_duration_minutes: int = 10
    algo_num_slices: int = 10

    # Iceberg settings
    iceberg_show_pct: float = 0.1  # Show 10% of order


@dataclass
class ExecutionResult:
    """Result of an execution attempt."""
    success: bool
    order: Order
    message: str = ""
    slippage: float = 0.0
    latency_ms: float = 0.0


class ExecutionEngine:
    """
    Sophisticated trade execution engine.

    Features:
    - Multiple order types (market, limit, stop, trailing stop)
    - Execution algorithms (TWAP, VWAP, Iceberg)
    - Order lifecycle management
    - Slippage tracking and control
    - Position tracking
    """

    def __init__(self, config: Optional[ExecutionConfig] = None):
        self.config = config or ExecutionConfig()

        # Order management
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []

        # Position tracking
        self.positions: Dict[str, float] = {}  # asset -> net quantity

        # Execution statistics
        self.total_orders: int = 0
        self.filled_orders: int = 0
        self.rejected_orders: int = 0
        self.total_slippage: float = 0.0
        self.total_volume: float = 0.0

        # Callbacks
        self.on_fill: Optional[Callable[[Order, Dict], None]] = None
        self.on_reject: Optional[Callable[[Order, str], None]] = None

        # Price feed (for simulation)
        self._price_feed: Dict[str, float] = {}

    def set_price(self, asset: str, price: float) -> None:
        """Set current price for an asset (simulation)."""
        self._price_feed[asset] = price

    def get_price(self, asset: str) -> Optional[float]:
        """Get current price for an asset."""
        return self._price_feed.get(asset)

    def create_order(
        self,
        asset: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        signal_id: Optional[str] = None,
        strategy: Optional[str] = None,
        expires_in_seconds: Optional[int] = None,
    ) -> Order:
        """
        Create a new order.

        Args:
            asset: Asset symbol
            side: Buy or sell
            quantity: Order quantity
            order_type: Type of order
            price: Limit price (for limit orders)
            stop_price: Stop trigger price
            signal_id: Associated signal ID
            strategy: Strategy name
            expires_in_seconds: Order expiration time

        Returns:
            Created Order object
        """
        order = Order(
            asset=asset,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            stop_price=stop_price,
            signal_id=signal_id,
            strategy=strategy,
        )

        if expires_in_seconds:
            order.expires_at = datetime.now() + timedelta(seconds=expires_in_seconds)

        self.orders[order.id] = order
        self.total_orders += 1

        return order

    def submit_order(self, order: Order, algo: Optional[ExecutionAlgo] = None) -> ExecutionResult:
        """
        Submit an order for execution.

        Args:
            order: Order to submit
            algo: Execution algorithm to use

        Returns:
            ExecutionResult with status
        """
        algo = algo or self.config.default_algo
        start_time = time.time()

        # Validate order
        if order.quantity <= 0:
            order.status = OrderStatus.REJECTED
            self.rejected_orders += 1
            return ExecutionResult(
                success=False,
                order=order,
                message="Invalid quantity"
            )

        order.status = OrderStatus.SUBMITTED
        order.submitted_at = datetime.now()

        # Execute based on algorithm
        if algo == ExecutionAlgo.IMMEDIATE:
            result = self._execute_immediate(order)
        elif algo == ExecutionAlgo.TWAP:
            result = self._execute_twap(order)
        elif algo == ExecutionAlgo.ICEBERG:
            result = self._execute_iceberg(order)
        else:
            result = self._execute_immediate(order)

        result.latency_ms = (time.time() - start_time) * 1000
        return result

    def _execute_immediate(self, order: Order) -> ExecutionResult:
        """Execute order immediately at market."""
        if self.config.simulation_mode:
            return self._simulate_fill(order)

        # Real execution would go here
        return ExecutionResult(
            success=False,
            order=order,
            message="Real execution not implemented"
        )

    def _execute_twap(self, order: Order) -> ExecutionResult:
        """Execute using Time-Weighted Average Price algorithm."""
        slice_qty = order.quantity / self.config.algo_num_slices
        slice_interval = (self.config.algo_duration_minutes * 60) / self.config.algo_num_slices

        total_value = 0.0
        total_qty = 0.0

        for i in range(self.config.algo_num_slices):
            # In simulation, execute slice immediately
            if self.config.simulation_mode:
                current_price = self._get_execution_price(order)
                if current_price is None:
                    continue

                # Add some price variation for realism
                import random
                price_variation = random.uniform(-0.002, 0.002)
                fill_price = current_price * (1 + price_variation)

                fill = {
                    "quantity": slice_qty,
                    "price": fill_price,
                    "timestamp": datetime.now().isoformat(),
                    "slice": i + 1,
                }
                order.fills.append(fill)
                total_value += slice_qty * fill_price
                total_qty += slice_qty

        if total_qty > 0:
            order.filled_quantity = total_qty
            order.filled_price = total_value / total_qty
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now()
            self._update_position(order)
            self.filled_orders += 1
            self.total_volume += total_qty

            return ExecutionResult(
                success=True,
                order=order,
                message=f"TWAP execution complete: {self.config.algo_num_slices} slices"
            )

        order.status = OrderStatus.REJECTED
        self.rejected_orders += 1
        return ExecutionResult(
            success=False,
            order=order,
            message="TWAP execution failed: no price available"
        )

    def _execute_iceberg(self, order: Order) -> ExecutionResult:
        """Execute using Iceberg algorithm (showing partial size)."""
        show_qty = order.quantity * self.config.iceberg_show_pct
        remaining = order.quantity

        while remaining > 0:
            slice_qty = min(show_qty, remaining)

            if self.config.simulation_mode:
                current_price = self._get_execution_price(order)
                if current_price is None:
                    break

                fill = {
                    "quantity": slice_qty,
                    "price": current_price,
                    "timestamp": datetime.now().isoformat(),
                }
                order.fills.append(fill)
                remaining -= slice_qty

        order.filled_quantity = order.quantity - remaining
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now()
            self._update_position(order)
            self.filled_orders += 1
            self.total_volume += order.filled_quantity

            return ExecutionResult(
                success=True,
                order=order,
                message="Iceberg execution complete"
            )

        order.status = OrderStatus.PARTIAL
        return ExecutionResult(
            success=False,
            order=order,
            message=f"Iceberg partial fill: {order.filled_quantity}/{order.quantity}"
        )

    def _simulate_fill(self, order: Order) -> ExecutionResult:
        """Simulate order fill."""
        execution_price = self._get_execution_price(order)

        if execution_price is None:
            order.status = OrderStatus.REJECTED
            self.rejected_orders += 1
            return ExecutionResult(
                success=False,
                order=order,
                message=f"No price available for {order.asset}"
            )

        # Apply slippage
        import random
        slippage_factor = random.uniform(0, self.config.max_slippage_pct)
        if order.side == OrderSide.BUY:
            fill_price = execution_price * (1 + slippage_factor)
        else:
            fill_price = execution_price * (1 - slippage_factor)

        # Create fill
        fill = {
            "quantity": order.quantity,
            "price": fill_price,
            "timestamp": datetime.now().isoformat(),
        }
        order.fills.append(fill)

        # Update order
        order.filled_quantity = order.quantity
        order.filled_price = fill_price
        order.status = OrderStatus.FILLED
        order.filled_at = datetime.now()

        # Calculate slippage
        slippage = abs(fill_price - execution_price) / execution_price
        self.total_slippage += slippage

        # Update position
        self._update_position(order)

        # Update statistics
        self.filled_orders += 1
        self.total_volume += order.quantity

        # Trigger callback
        if self.on_fill:
            self.on_fill(order, fill)

        return ExecutionResult(
            success=True,
            order=order,
            message="Order filled",
            slippage=slippage
        )

    def _get_execution_price(self, order: Order) -> Optional[float]:
        """Get execution price for an order."""
        base_price = self._price_feed.get(order.asset)

        if base_price is None:
            return None

        if order.order_type == OrderType.MARKET:
            return base_price
        elif order.order_type == OrderType.LIMIT:
            if order.price is None:
                return None
            if order.side == OrderSide.BUY and base_price <= order.price:
                return order.price
            elif order.side == OrderSide.SELL and base_price >= order.price:
                return order.price
            return None
        elif order.order_type == OrderType.STOP:
            if order.stop_price is None:
                return None
            if order.side == OrderSide.BUY and base_price >= order.stop_price:
                return base_price
            elif order.side == OrderSide.SELL and base_price <= order.stop_price:
                return base_price
            return None

        return base_price

    def _update_position(self, order: Order) -> None:
        """Update position after fill."""
        current_pos = self.positions.get(order.asset, 0)

        if order.side == OrderSide.BUY:
            self.positions[order.asset] = current_pos + order.filled_quantity
        else:
            self.positions[order.asset] = current_pos - order.filled_quantity

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        order = self.orders.get(order_id)
        if order and order.is_active:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)

    def get_active_orders(self, asset: Optional[str] = None) -> List[Order]:
        """Get all active orders, optionally filtered by asset."""
        orders = [o for o in self.orders.values() if o.is_active]
        if asset:
            orders = [o for o in orders if o.asset == asset]
        return orders

    def get_position(self, asset: str) -> float:
        """Get current position for an asset."""
        return self.positions.get(asset, 0)

    def get_all_positions(self) -> Dict[str, float]:
        """Get all positions."""
        return dict(self.positions)

    def close_position(self, asset: str, price: Optional[float] = None) -> Optional[ExecutionResult]:
        """Close entire position for an asset."""
        position = self.positions.get(asset, 0)
        if position == 0:
            return None

        side = OrderSide.SELL if position > 0 else OrderSide.BUY
        quantity = abs(position)

        order = self.create_order(
            asset=asset,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            strategy="close_position"
        )

        if price:
            self.set_price(asset, price)

        return self.submit_order(order)

    def get_statistics(self) -> Dict:
        """Get execution statistics."""
        fill_rate = self.filled_orders / self.total_orders if self.total_orders > 0 else 0
        avg_slippage = self.total_slippage / self.filled_orders if self.filled_orders > 0 else 0

        return {
            "total_orders": self.total_orders,
            "filled_orders": self.filled_orders,
            "rejected_orders": self.rejected_orders,
            "fill_rate": f"{fill_rate:.1%}",
            "avg_slippage": f"{avg_slippage:.4%}",
            "total_volume": round(self.total_volume, 2),
            "active_orders": len(self.get_active_orders()),
            "open_positions": len([p for p in self.positions.values() if p != 0]),
        }

    def check_expired_orders(self) -> List[Order]:
        """Check and expire timed-out orders."""
        expired = []
        now = datetime.now()

        for order in self.orders.values():
            if order.is_active and order.expires_at and now > order.expires_at:
                order.status = OrderStatus.EXPIRED
                expired.append(order)

        return expired


# Convenience functions
def create_market_order(
    engine: ExecutionEngine,
    asset: str,
    side: str,
    quantity: float,
    **kwargs
) -> ExecutionResult:
    """Create and submit a market order."""
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    order = engine.create_order(
        asset=asset,
        side=order_side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        **kwargs
    )
    return engine.submit_order(order)


def create_limit_order(
    engine: ExecutionEngine,
    asset: str,
    side: str,
    quantity: float,
    price: float,
    **kwargs
) -> Order:
    """Create a limit order (not immediately submitted)."""
    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
    return engine.create_order(
        asset=asset,
        side=order_side,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        price=price,
        **kwargs
    )


if __name__ == "__main__":
    # Test the execution engine
    engine = ExecutionEngine(ExecutionConfig(simulation_mode=True))

    # Set prices
    engine.set_price("BTC", 45000)
    engine.set_price("ETH", 2500)

    # Test market order
    print("Testing market order...")
    result = create_market_order(engine, "BTC", "buy", 0.5)
    print(f"  Result: {result.success}, Price: {result.order.filled_price:.2f}")

    # Test TWAP execution
    print("\nTesting TWAP execution...")
    order = engine.create_order("ETH", OrderSide.BUY, 10, OrderType.MARKET)
    result = engine.submit_order(order, algo=ExecutionAlgo.TWAP)
    print(f"  Result: {result.success}, Avg Price: {result.order.avg_fill_price:.2f}")
    print(f"  Fills: {len(result.order.fills)}")

    # Test position tracking
    print("\nPositions:", engine.get_all_positions())

    # Test statistics
    print("\nStatistics:")
    for key, value in engine.get_statistics().items():
        print(f"  {key}: {value}")
