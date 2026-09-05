"""
Trade Execution Engine with Order Management.

Provides sophisticated order management, execution algorithms,
and real-time position tracking.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from enum import Enum
import uuid
import time
from collections import deque

from core.ledger import TradingLedger
from core.order_intent import (
    IntentPersistenceError,
    OrderIntent,
    OrderIntentJournal,
)

logger = logging.getLogger(__name__)


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
    """Order lifecycle state (ATOS-P0-EXEC-001).

    The ordering matters: an order is economically live from the moment an
    intent is persisted until the broker proves a terminal state. Anything
    ambiguous resolves to UNKNOWN or RECONCILIATION_REQUIRED, never to a
    state that looks safe.

    Legacy spellings (PENDING, SUBMITTED, PARTIAL, CANCELLED) are kept. Where
    a new name shares a legacy value it becomes an alias of it, so existing
    call sites and persisted records keep working unchanged.
    """

    # Pre-broker
    INTENT_CREATED = "intent_created"
    INTENT_PERSISTED = "intent_persisted"
    PENDING = "pending"           # legacy spelling of a pre-submit order
    SUBMITTING = "submitting"     # network call in flight; acceptance unknown

    # Broker acknowledged, economically live
    SUBMITTED = "submitted"       # legacy spelling of ACKNOWLEDGED
    ACKNOWLEDGED = "submitted"    # alias of SUBMITTED
    OPEN = "open"
    PARTIAL = "partial"
    PARTIALLY_FILLED = "partial"  # alias of PARTIAL
    CANCEL_REQUESTED = "cancel_requested"

    # Terminal, proven by the broker
    FILLED = "filled"
    CANCELLED = "cancelled"
    CANCELED = "cancelled"        # alias of CANCELLED
    REJECTED = "rejected"
    EXPIRED = "expired"

    # Ambiguous: the broker may or may not hold economic exposure for us
    UNKNOWN = "unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"


#: States from which the broker may still create or hold exposure for us.
#: Reservations must be retained while an order sits in any of these.
LIVE_ORDER_STATES = frozenset({
    OrderStatus.INTENT_PERSISTED,
    OrderStatus.PENDING,
    OrderStatus.SUBMITTING,
    OrderStatus.SUBMITTED,
    OrderStatus.OPEN,
    OrderStatus.PARTIAL,
    OrderStatus.CANCEL_REQUESTED,
    OrderStatus.UNKNOWN,
    OrderStatus.RECONCILIATION_REQUIRED,
})

#: States the broker has proven final.
TERMINAL_ORDER_STATES = frozenset({
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
})

#: States in which local belief and broker truth are not known to agree.
AMBIGUOUS_ORDER_STATES = frozenset({
    OrderStatus.UNKNOWN,
    OrderStatus.RECONCILIATION_REQUIRED,
})

#: Permitted forward transitions. Anything absent is rejected as an impossible
#: backward or lateral move, except a reconciliation correction, which is
#: applied through ``Order.reconcile_to`` and recorded as an explicit event.
_ALLOWED_TRANSITIONS: Dict[OrderStatus, frozenset] = {
    # An order that has not reached the broker can be abandoned locally:
    # nothing is ambiguous about an intent that was never submitted.
    OrderStatus.INTENT_CREATED: frozenset({
        OrderStatus.INTENT_PERSISTED, OrderStatus.REJECTED, OrderStatus.CANCELLED,
        OrderStatus.UNKNOWN,
    }),
    OrderStatus.INTENT_PERSISTED: frozenset({
        OrderStatus.SUBMITTING, OrderStatus.REJECTED, OrderStatus.CANCELLED,
        OrderStatus.EXPIRED, OrderStatus.UNKNOWN,
    }),
    OrderStatus.PENDING: frozenset({
        OrderStatus.INTENT_PERSISTED, OrderStatus.SUBMITTING, OrderStatus.SUBMITTED,
        OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN,
    }),
    OrderStatus.SUBMITTING: frozenset({
        OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIAL,
        OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN, OrderStatus.RECONCILIATION_REQUIRED,
    }),
    OrderStatus.SUBMITTED: frozenset({
        OrderStatus.OPEN, OrderStatus.PARTIAL, OrderStatus.FILLED,
        OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
        OrderStatus.EXPIRED, OrderStatus.UNKNOWN,
        OrderStatus.RECONCILIATION_REQUIRED,
    }),
    OrderStatus.OPEN: frozenset({
        OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN, OrderStatus.RECONCILIATION_REQUIRED,
    }),
    OrderStatus.PARTIAL: frozenset({
        OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCEL_REQUESTED,
        OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.UNKNOWN,
        OrderStatus.RECONCILIATION_REQUIRED,
    }),
    OrderStatus.CANCEL_REQUESTED: frozenset({
        # A fill can still land after a cancel request. Cancellation is only
        # terminal once the broker confirms it (ATOS-P0-EXEC-004).
        OrderStatus.CANCELLED, OrderStatus.PARTIAL, OrderStatus.FILLED,
        OrderStatus.EXPIRED, OrderStatus.UNKNOWN,
        OrderStatus.RECONCILIATION_REQUIRED,
    }),
    # Reconciliation resolves ambiguity in any direction.
    OrderStatus.UNKNOWN: frozenset({
        OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIAL,
        OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED,
        OrderStatus.EXPIRED, OrderStatus.CANCEL_REQUESTED,
        OrderStatus.UNKNOWN, OrderStatus.RECONCILIATION_REQUIRED,
    }),
    OrderStatus.RECONCILIATION_REQUIRED: frozenset({
        OrderStatus.RECONCILIATION_REQUIRED, OrderStatus.UNKNOWN,
    }),
    # Terminal states stay terminal. Only an explicit reconciliation
    # correction may move them, and that path records why.
    OrderStatus.FILLED: frozenset({OrderStatus.RECONCILIATION_REQUIRED}),
    OrderStatus.CANCELLED: frozenset({OrderStatus.RECONCILIATION_REQUIRED}),
    OrderStatus.REJECTED: frozenset({OrderStatus.RECONCILIATION_REQUIRED}),
    OrderStatus.EXPIRED: frozenset({OrderStatus.RECONCILIATION_REQUIRED}),
}


#: Float tolerance for quantity comparisons. Floats are the wrong type for
#: this comparison; ATOS-P1-NUM-001 replaces the capital-affecting boundary
#: with Decimal quantised to the venue's increment. Until then this tolerance
#: is deliberately tight, so a genuine overfill is not absorbed as rounding.
_QUANTITY_TOLERANCE = 1e-9


class InvalidOrderTransition(RuntimeError):
    """Raised when an order is asked to move backwards through its lifecycle."""


class BrokerRejection(Exception):
    """Raised by an adapter to positively assert the broker refused an order.

    This exists so that a *proven* rejection can be distinguished from a lost
    response. Adapters must raise it only when the broker said no. Every other
    exception is treated as ambiguous, because a timeout after acceptance is
    indistinguishable from a timeout before it.
    """


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

    # Broker identity (ATOS-P0-EXEC-001 / EXEC-003)
    # The client order ID is generated before any network call and is stable
    # across retries and restarts, so a lost response can be resolved by
    # asking the broker about this ID rather than by submitting again.
    client_order_id: str = field(default_factory=lambda: f"atos-{uuid.uuid4()}")
    broker_order_id: Optional[str] = None

    # Execution
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    filled_price: float = 0.0
    fees: float = 0.0
    fills: List[Dict] = field(default_factory=list)
    #: Append-only record of lifecycle transitions, including refusals and
    #: reconciliation corrections. This is the audit trail for EXEC-002.
    transitions: List[Dict] = field(default_factory=list)
    #: Why the order is ambiguous, when it is.
    ambiguity_reason: Optional[str] = None
    #: How many times the broker was asked to resolve this order.
    resolution_attempts: int = 0
    #: How many times submission was attempted for this client order ID.
    submit_attempts: int = 0

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
        """Whether the broker may still create or hold exposure for this order.

        ATOS-P0-EXEC-001: an order in an ambiguous state is active. Treating
        UNKNOWN as inactive would release a reservation while the broker may
        still be working the order, which is exactly how untracked exposure
        appears.
        """
        return self.status in LIVE_ORDER_STATES

    @property
    def is_ambiguous(self) -> bool:
        """Whether local belief and broker truth are not known to agree."""
        return self.status in AMBIGUOUS_ORDER_STATES

    @property
    def remaining_quantity(self) -> float:
        """Unfilled quantity, never negative."""
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def is_overfilled(self) -> bool:
        """Whether the broker reported more fill than we ever asked for."""
        return self.filled_quantity > self.quantity + _QUANTITY_TOLERANCE

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        """Whether ``new_status`` is a legal forward move from here."""
        if new_status is self.status and new_status in _ALLOWED_TRANSITIONS.get(
            self.status, frozenset()
        ):
            return True
        return new_status in _ALLOWED_TRANSITIONS.get(self.status, frozenset())

    def transition_to(
        self,
        new_status: OrderStatus,
        reason: str = "",
        at: Optional[datetime] = None,
    ) -> None:
        """Move the order forward, refusing impossible backward moves.

        Repeating the current state is a no-op rather than an error: brokers
        redeliver lifecycle events, and a duplicate must not corrupt state.
        """
        if new_status is self.status:
            self._record_transition(self.status, new_status, reason or "duplicate event", at)
            return
        if not self.can_transition_to(new_status):
            self._record_transition(
                self.status, new_status, f"REFUSED: {reason or 'illegal transition'}", at
            )
            raise InvalidOrderTransition(
                f"order {self.id}: {self.status.name} -> {new_status.name} is not a "
                f"legal transition ({reason or 'no reason given'})"
            )
        previous = self.status
        self.status = new_status
        if new_status not in AMBIGUOUS_ORDER_STATES:
            self.ambiguity_reason = None
        self._record_transition(previous, new_status, reason, at)

    def reconcile_to(self, new_status: OrderStatus, reason: str, at: Optional[datetime] = None) -> None:
        """Apply a broker-authoritative correction, bypassing the forward rule.

        This is the single sanctioned way to move an order backwards, and it
        always leaves an explicit event behind saying broker truth overrode
        local belief.
        """
        if not reason:
            raise ValueError("a reconciliation correction must state its reason")
        previous = self.status
        self.status = new_status
        if new_status not in AMBIGUOUS_ORDER_STATES:
            self.ambiguity_reason = None
        self._record_transition(previous, new_status, f"RECONCILIATION: {reason}", at)

    def mark_ambiguous(
        self,
        reason: str,
        status: OrderStatus = OrderStatus.UNKNOWN,
        at: Optional[datetime] = None,
    ) -> None:
        """Record that the broker's view of this order cannot be established."""
        self.ambiguity_reason = reason
        if self.status is status:
            self._record_transition(self.status, status, reason, at)
            return
        if self.can_transition_to(status):
            self.transition_to(status, reason, at)
        else:
            # Already terminal. Ambiguity about a terminal order is a
            # reconciliation problem, not a silent overwrite.
            self.reconcile_to(
                OrderStatus.RECONCILIATION_REQUIRED,
                f"ambiguity on terminal order: {reason}",
                at,
            )

    def _record_transition(
        self,
        previous: OrderStatus,
        new_status: OrderStatus,
        reason: str,
        at: Optional[datetime] = None,
    ) -> None:
        self.transitions.append({
            "from": previous.value,
            "to": new_status.value,
            "reason": reason,
            "at": (at or datetime.now()).isoformat(),
        })

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
            "remaining_quantity": self.remaining_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fees": self.fees,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "is_active": self.is_active,
            "is_ambiguous": self.is_ambiguous,
            "ambiguity_reason": self.ambiguity_reason,
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


class AlpacaExecutionAdapter:
    """Adapter that lets ExecutionEngine submit orders through AlpacaClient."""

    def __init__(self, client: Any, default_time_in_force: str = "day"):
        self.client = client
        self.default_time_in_force = default_time_in_force
        self._external_order_ids: Dict[str, str] = {}

    def submit_order(self, order: Order) -> Dict[str, Any]:
        """Place an execution order through Alpaca and normalize the response.

        ATOS-P0-EXEC-003: the broker is given ``order.client_order_id`` - the
        full stable UUID - not ``order.id``, which is an eight-character slice.
        The idempotency key has to be the one we can prove unique and can
        reproduce after a restart, otherwise a lookup after a lost response
        may match somebody else's order or none at all.
        """
        alpaca_order = self.client.place_order(
            symbol=order.asset,
            qty=order.quantity,
            side=order.side.value,
            order_type=order.order_type.value,
            time_in_force=self.default_time_in_force,
            limit_price=order.price,
            stop_price=order.stop_price,
            client_order_id=order.client_order_id,
        )
        response = self._order_to_response(alpaca_order)
        external_order_id = response.get("external_order_id")
        if external_order_id:
            self._external_order_ids[order.id] = external_order_id
            self._external_order_ids[order.client_order_id] = external_order_id
        return response

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Ask the broker what became of the order carrying this client ID.

        Returns None only when the broker positively reports no such order.
        Any failure to reach the broker propagates, because "I could not ask"
        must never be read as "there is nothing there".
        """
        lookup = getattr(self.client, "get_order_by_client_order_id", None)
        if not callable(lookup):
            raise NotImplementedError(
                "broker client cannot look orders up by client order ID; "
                "an ambiguous order cannot be safely resolved"
            )
        found = lookup(client_order_id)
        if found is None:
            return None
        return self._order_to_response(found)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel by external broker order id when known."""
        external_order_id = self._external_order_ids.get(order_id, order_id)
        return bool(self.client.cancel_order(external_order_id))

    def get_positions(self) -> Any:
        """Expose broker positions for ledger reconciliation."""
        return self.client.get_positions()

    def get_order(self, order_id: str) -> Dict[str, Any]:
        """Fetch and normalize an Alpaca order."""
        external_order_id = self._external_order_ids.get(order_id, order_id)
        return self._order_to_response(self.client.get_order(external_order_id))

    def _order_to_response(self, alpaca_order: Any) -> Dict[str, Any]:
        """Normalize an Alpaca order dataclass/object for ExecutionEngine."""
        data = self._to_dict(alpaca_order)
        external_order_id = data.get("order_id") or data.get("id")
        return {
            "external_order_id": external_order_id,
            # The engine reads "broker_order_id"; emitting only
            # "external_order_id" meant the broker's own identifier was
            # silently dropped, leaving nothing to reconcile against.
            "broker_order_id": external_order_id,
            "client_order_id": data.get("client_order_id"),
            "symbol": data.get("symbol"),
            "side": data.get("side"),
            "type": data.get("order_type") or data.get("type"),
            "status": data.get("status"),
            "quantity": data.get("qty") or data.get("quantity"),
            "filled_quantity": data.get("filled_qty", data.get("filled_quantity", 0.0)),
            "average_price": data.get("filled_avg_price", data.get("average_price", 0.0)) or 0.0,
            "filled_at": data.get("filled_at"),
        }

    def _to_dict(self, value: Any) -> Dict[str, Any]:
        """Convert Alpaca dataclasses or objects to dictionaries."""
        if isinstance(value, dict):
            return value
        if is_dataclass(value):
            return asdict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }


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

    def __init__(
        self,
        config: Optional[ExecutionConfig] = None,
        ledger: Optional[TradingLedger] = None,
        broker_adapter: Optional[Any] = None,
        intent_journal: Optional[OrderIntentJournal] = None,
        session_id: Optional[str] = None,
    ):
        self.config = config or ExecutionConfig()
        self.ledger = ledger
        self.broker_adapter = broker_adapter
        # ATOS-P0-EXEC-002: the write-ahead log of orders that may exist.
        # Live mode without one is refused in submit_order, because a crash
        # between intent and submission would leave nothing to recover from.
        self.intent_journal = intent_journal
        self.session_id = session_id or (
            intent_journal.session_id if intent_journal else f"session-{uuid.uuid4()}"
        )

        # Order management
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []

        # Position tracking
        self.positions: Dict[str, float] = {}  # asset -> net quantity

        # Execution statistics
        self.total_orders: int = 0
        self.filled_orders: int = 0
        self.rejected_orders: int = 0
        self.intent_persistence_failures: int = 0
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
        if self.ledger:
            self.ledger.record_order(
                order_id=order.id,
                symbol=order.asset,
                side=order.side.value,
                quantity=order.quantity,
                order_type=order.order_type.value,
                price=order.price,
                status=order.status.value,
                metadata={
                    "signal_id": signal_id,
                    "strategy": strategy,
                },
            )

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

        # ATOS-P0-EXEC-003: never resubmit an order whose broker state is
        # unknown. The broker may already be working it; a second submission
        # would create a second economic position. Resolution goes through
        # resolve_ambiguous_order(), which asks by client order ID.
        if order.is_ambiguous:
            logger.error(
                "Refusing to resubmit order %s (client id %s): state is %s. "
                "Resolve it against the broker first.",
                order.id, order.client_order_id, order.status.name,
            )
            return ExecutionResult(
                success=False,
                order=order,
                message=(
                    f"Refusing to resubmit: order state is {order.status.name}. "
                    "Resolve it by client order ID before retrying "
                    "(ATOS-P0-EXEC-003)."
                ),
            )

        # A terminal order is finished. Resubmitting one is a caller bug that
        # would duplicate exposure.
        if order.status in TERMINAL_ORDER_STATES:
            return ExecutionResult(
                success=False,
                order=order,
                message=(
                    f"Refusing to resubmit: order is already {order.status.name}."
                ),
            )

        order.submit_attempts += 1

        # Validate order
        if order.quantity <= 0:
            order.transition_to(OrderStatus.REJECTED, "local validation: invalid quantity")
            self.rejected_orders += 1
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False,
                order=order,
                message="Invalid quantity"
            )

        # ATOS-P0-EXEC-002: the intent must be durable before anything can
        # create a real order. If this write fails in live mode we do not
        # submit, because a crash would leave no record that the broker might
        # be holding the order.
        is_live = not self.config.simulation_mode
        if is_live:
            if self.intent_journal is None:
                order.transition_to(
                    OrderStatus.REJECTED,
                    "live execution requires a durable order intent journal",
                )
                self.rejected_orders += 1
                self._mark_ledger_status(order)
                return ExecutionResult(
                    success=False,
                    order=order,
                    message=(
                        "Refusing to submit: live execution requires a durable "
                        "order intent journal (ATOS-P0-EXEC-002)."
                    ),
                )
            try:
                self._persist_intent(order)
            except IntentPersistenceError as exc:
                # Nothing reached the broker, so no exposure exists. The order
                # is provably refused - but the persistence failure itself is
                # an operational fault, surfaced here and escalated to a
                # trading freeze by ATOS-P1-PERSIST-001.
                logger.error(
                    "Refusing to submit order %s: intent could not be persisted (%s)",
                    order.id, exc,
                )
                self.intent_persistence_failures += 1
                order.transition_to(
                    OrderStatus.REJECTED, f"intent persistence failed: {exc}"
                )
                self.rejected_orders += 1
                return ExecutionResult(
                    success=False,
                    order=order,
                    message=(
                        "Refusing to submit: the order intent could not be durably "
                        f"recorded ({exc}). No order was sent."
                    ),
                )

        # ATOS-P0-EXEC-001: intent first, then in-flight. The old code jumped
        # straight to SUBMITTED, asserting a broker acknowledgement that had
        # not happened yet.
        if order.can_transition_to(OrderStatus.INTENT_PERSISTED):
            order.transition_to(OrderStatus.INTENT_PERSISTED, "order intent recorded")
        if order.can_transition_to(OrderStatus.SUBMITTING):
            order.transition_to(OrderStatus.SUBMITTING, "handing to execution path")
        order.submitted_at = datetime.now()
        self._mark_ledger_status(order)

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

    def _persist_intent(self, order: Order) -> OrderIntent:
        """Write the order intent to the journal before any broker call.

        Raises IntentPersistenceError if the write does not reach stable
        storage, in which case the caller must not submit.
        """
        notional = None
        reference_price = order.price or self.get_price(order.asset)
        if reference_price:
            notional = abs(order.quantity * reference_price)

        intent = OrderIntent(
            client_order_id=order.client_order_id,
            internal_order_id=order.id,
            session_id=self.session_id,
            instrument=order.asset,
            side=order.side.value,
            quantity=order.quantity,
            order_type=order.order_type.value,
            status=OrderStatus.INTENT_PERSISTED.value,
            price=order.price,
            stop_price=order.stop_price,
            notional=notional,
            # The worst case this order can add if it fills completely. Risk
            # recovery needs the number the engine approved, not one
            # recomputed later against different prices or config.
            expected_max_exposure_delta=notional,
            risk_approval_hash=getattr(order, "risk_approval_hash", None),
            strategy=order.strategy,
            signal_id=order.signal_id,
        )
        return self.intent_journal.record_intent(intent)

    def _journal_transition(self, order: Order, reason: str) -> None:
        """Mirror a lifecycle change into the durable journal.

        A journal write failure here is logged but does not raise: the order
        already exists at the broker, and throwing would lose the in-memory
        state too. The counter makes the gap visible, and
        ATOS-P1-PERSIST-001 turns a critical write failure into a freeze.
        """
        if self.intent_journal is None:
            return
        try:
            self.intent_journal.record_transition(
                client_order_id=order.client_order_id,
                to_status=order.status.value,
                reason=reason,
                filled_quantity=order.filled_quantity,
                broker_order_id=order.broker_order_id,
            )
        except IntentPersistenceError as exc:
            self.intent_persistence_failures += 1
            logger.error(
                "Order %s reached %s but the journal write failed (%s). Local "
                "state and durable state now disagree; reconcile before adding risk.",
                order.id, order.status.name, exc,
            )

    def resolve_ambiguous_order(self, order: Order) -> ExecutionResult:
        """Ask the broker what became of an order whose state we lost.

        ATOS-P0-EXEC-003. This is the only sanctioned way out of UNKNOWN. It
        asks by client order ID - the key the broker was given before the
        network call - so the answer is about *our* order and no other.

        Three outcomes:

        * the broker has it -> attach to it, adopt its state, no new order;
        * the broker positively reports no such order -> the submission never
          landed, and only then may the order be resubmitted;
        * we cannot ask, or the answer disagrees with our intent -> the order
          stays ambiguous and escalates to RECONCILIATION_REQUIRED.

        A failure to reach the broker is never read as absence.
        """
        lookup = getattr(self.broker_adapter, "get_order_by_client_order_id", None)
        if not callable(lookup):
            order.mark_ambiguous(
                "broker adapter cannot look up by client order ID",
                OrderStatus.RECONCILIATION_REQUIRED,
            )
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False, order=order,
                message=(
                    "Cannot resolve this order: the adapter has no lookup by "
                    "client order ID. Resolve manually before adding risk."
                ),
            )

        self._record_resolution_attempt(order)
        try:
            found = lookup(order.client_order_id)
        except Exception as exc:
            logger.exception(
                "Lookup of client order ID %s failed; the order stays UNKNOWN",
                order.client_order_id,
            )
            order.mark_ambiguous(f"broker lookup failed: {type(exc).__name__}")
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False, order=order,
                message=(
                    "Broker lookup failed. The order remains UNKNOWN; a failed "
                    "question is not a negative answer."
                ),
            )

        if found is None:
            # The broker positively reports no order under this ID, so the
            # submission never landed and no exposure was created.
            logger.info(
                "Broker reports no order for client ID %s; submission never landed",
                order.client_order_id,
            )
            order.reconcile_to(
                OrderStatus.INTENT_PERSISTED,
                "broker confirmed no order exists for this client order ID",
            )
            self._journal_transition(order, "resolved: broker has no such order")
            return ExecutionResult(
                success=False, order=order,
                message="Broker has no such order; safe to resubmit with the same client order ID.",
            )

        data = self._normalize_adapter_payload(found)
        mismatch = self._intent_mismatch(order, data)
        if mismatch:
            logger.error(
                "Client order ID %s is attached to an order that does not match "
                "our intent (%s). Freezing.", order.client_order_id, mismatch,
            )
            order.reconcile_to(
                OrderStatus.RECONCILIATION_REQUIRED,
                f"client order ID collision or mismatch: {mismatch}",
            )
            order.ambiguity_reason = "client order ID mismatch"
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False, order=order,
                message=f"Client order ID resolves to a different order: {mismatch}",
            )

        # The broker has our order. Adopt its truth rather than resubmitting.
        logger.info(
            "Attached to existing broker order for client ID %s", order.client_order_id
        )
        order.reconcile_to(
            OrderStatus.UNKNOWN, "attaching to the broker's existing order"
        )
        return self._apply_live_execution_response(order, data)

    def _intent_mismatch(self, order: Order, data: Dict[str, Any]) -> Optional[str]:
        """Describe how a broker order differs from what we intended, if it does."""
        symbol = data.get("symbol") or data.get("instrument")
        if symbol and str(symbol) != str(order.asset):
            return f"instrument {symbol!r} != intended {order.asset!r}"
        side = data.get("side")
        if side and str(side).lower() != order.side.value:
            return f"side {side!r} != intended {order.side.value!r}"
        quantity = data.get("quantity") or data.get("qty")
        if quantity not in (None, ""):
            try:
                if abs(float(quantity) - order.quantity) > _QUANTITY_TOLERANCE:
                    return f"quantity {quantity} != intended {order.quantity}"
            except (TypeError, ValueError):
                return f"unreadable quantity {quantity!r}"
        return None

    def _record_resolution_attempt(self, order: Order) -> None:
        """Persist that we asked the broker about this order, and when."""
        order.resolution_attempts += 1
        if self.intent_journal is None:
            return
        try:
            self.intent_journal.record_transition(
                client_order_id=order.client_order_id,
                to_status=order.status.value,
                reason=f"broker lookup attempt #{order.resolution_attempts}",
                filled_quantity=order.filled_quantity,
                broker_order_id=order.broker_order_id,
            )
        except IntentPersistenceError as exc:
            self.intent_persistence_failures += 1
            logger.error("Could not journal a resolution attempt: %s", exc)

    def _execute_immediate(self, order: Order) -> ExecutionResult:
        """Execute order immediately at market."""
        if self.config.simulation_mode:
            return self._simulate_fill(order)

        if self.broker_adapter:
            return self._execute_live(order)

        order.transition_to(OrderStatus.REJECTED, "no live execution adapter configured")
        self.rejected_orders += 1
        self._mark_ledger_status(order)
        return ExecutionResult(
            success=False,
            order=order,
            message="Live execution adapter is not configured"
        )

    def _execute_live(self, order: Order) -> ExecutionResult:
        """Submit an order to a live broker adapter and mirror accepted fills."""
        submit = getattr(self.broker_adapter, "submit_order", None)
        if not callable(submit):
            order.transition_to(
                OrderStatus.REJECTED, "adapter does not implement submit_order"
            )
            self.rejected_orders += 1
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False,
                order=order,
                message="Live execution adapter does not implement submit_order"
            )

        # ATOS-P0-EXEC-001: the network call is in flight. From here until a
        # response is parsed, the broker may or may not hold an order for us.
        if order.can_transition_to(OrderStatus.SUBMITTING):
            order.transition_to(OrderStatus.SUBMITTING, "submitting to broker")
            self._mark_ledger_status(order)

        try:
            response = submit(order)
        except BrokerRejection as exc:
            # The adapter positively asserts the broker refused the order. This
            # is the only exception that proves no exposure was created.
            logger.warning("Broker rejected order %s: %s", order.id, exc)
            order.transition_to(OrderStatus.REJECTED, f"broker rejection: {exc}")
            self.rejected_orders += 1
            self._mark_ledger_status(order)
            if self.on_reject:
                self.on_reject(order, str(exc))
            return ExecutionResult(
                success=False,
                order=order,
                message=f"Broker rejected order: {exc}",
            )
        except Exception as exc:
            # A lost response is indistinguishable from a pre-acceptance
            # failure. Calling this REJECTED would drop a reservation while
            # the broker works a live order, so the order stays UNKNOWN and
            # keeps its exposure until reconciliation resolves it by client
            # order ID.
            logger.exception(
                "Order %s (client id %s) is in an unknown state: the submit call "
                "raised %s. Do NOT resubmit; reconcile by client order ID.",
                order.id, order.client_order_id, type(exc).__name__,
            )
            order.mark_ambiguous(
                f"submit raised {type(exc).__name__}; broker acceptance unproven"
            )
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False,
                order=order,
                message=(
                    "Order state UNKNOWN after transport failure. The broker may "
                    "hold this order. Reconcile by client order ID before any retry."
                ),
            )

        return self._apply_live_execution_response(order, response)

    def _apply_live_execution_response(self, order: Order, response: Any) -> ExecutionResult:
        """Fold a broker response into local order state.

        ATOS-P0-EXEC-001. Three rules govern this method:

        * broker truth about *quantity* is never clamped to fit local belief;
          an overfill is a mismatch that freezes, not a rounding artefact;
        * lifecycle events are idempotent, because brokers redeliver them and
          a duplicate must not double-count a fill;
        * a stale event that reports less progress than we already know is
          ignored rather than allowed to rewind cumulative state.
        """
        data = self._normalize_adapter_payload(response)
        order.submitted_at = order.submitted_at or datetime.now()

        # Keep the broker's own identifier so the order can be looked up later.
        broker_id = data.get("broker_order_id") or data.get("order_id") or data.get("id")
        if broker_id:
            order.broker_order_id = str(broker_id)

        reported_status = self._coerce_order_status(
            data.get("status", OrderStatus.SUBMITTED.value)
        )
        reported_filled = self._coerce_float(
            data.get("filled_quantity", data.get("filled_qty", data.get("executed_quantity", 0.0)))
        )
        avg_price = self._coerce_float(
            data.get("avg_fill_price", data.get("average_price", data.get("filled_price", 0.0)))
        )
        fees = self._coerce_float(data.get("fees", data.get("fee", data.get("commission", 0.0))))

        # --- overfill: broker holds more than we ever asked for -------------
        if reported_filled > order.quantity + _QUANTITY_TOLERANCE:
            logger.error(
                "Order %s (client id %s): broker reports filled %s against requested %s. "
                "Freezing for reconciliation; exposure exceeds intent.",
                order.id, order.client_order_id, reported_filled, order.quantity,
            )
            order.filled_quantity = reported_filled  # record the truth, do not clamp it
            order.filled_price = avg_price
            order.fees = fees
            order.reconcile_to(
                OrderStatus.RECONCILIATION_REQUIRED,
                f"overfill: broker filled {reported_filled} vs requested {order.quantity}",
            )
            order.ambiguity_reason = "overfill"
            self._mark_ledger_status(order)
            return ExecutionResult(
                success=False,
                order=order,
                message=(
                    f"Broker filled {reported_filled} against a requested {order.quantity}. "
                    "Order frozen pending reconciliation."
                ),
            )

        # --- stale or duplicate event ---------------------------------------
        newly_filled = reported_filled - order.filled_quantity
        if newly_filled < -_QUANTITY_TOLERANCE:
            # The broker is telling us about less fill than we already have.
            # That is an out-of-order delivery, not a reversal.
            logger.warning(
                "Order %s: ignoring stale event reporting filled %s below known %s",
                order.id, reported_filled, order.filled_quantity,
            )
            order._record_transition(
                order.status, order.status,
                f"stale event ignored (reported {reported_filled} < known {order.filled_quantity})",
            )
            return ExecutionResult(
                success=order.status in LIVE_ORDER_STATES or order.status is OrderStatus.FILLED,
                order=order,
                message="Stale broker event ignored",
            )

        had_new_fill = newly_filled > _QUANTITY_TOLERANCE

        if had_new_fill:
            order.filled_quantity = reported_filled
            order.filled_price = avg_price
            order.fees = fees
            order.filled_at = self._parse_timestamp(data.get("filled_at")) or datetime.now()

            fills = data.get("fills") or []
            normalized_fills = [self._normalize_adapter_payload(fill) for fill in fills]
            if not normalized_fills and avg_price > 0:
                normalized_fills = [{
                    "quantity": newly_filled,
                    "price": avg_price,
                    "timestamp": order.filled_at.isoformat(),
                }]

            order.fills = [
                {
                    "quantity": self._coerce_float(fill.get("quantity", fill.get("qty", 0.0))),
                    "price": self._coerce_float(fill.get("price", fill.get("fill_price", avg_price))),
                    "timestamp": fill.get("timestamp", order.filled_at.isoformat()),
                }
                for fill in normalized_fills
                if self._coerce_float(fill.get("quantity", fill.get("qty", 0.0))) > 0
            ]

        # --- resolve the state ----------------------------------------------
        # A broker that says "accepted" while reporting a complete fill has
        # told us two things; the quantity is the more specific one.
        target = reported_status
        if reported_status in {OrderStatus.SUBMITTED, OrderStatus.OPEN} and reported_filled > 0:
            target = (
                OrderStatus.FILLED
                if reported_filled >= order.quantity - _QUANTITY_TOLERANCE
                else OrderStatus.PARTIAL
            )

        if target is OrderStatus.UNKNOWN:
            order.mark_ambiguous(
                f"broker returned an unreadable status: {data.get('status')!r}"
            )
        elif target is not order.status:
            try:
                order.transition_to(target, "broker lifecycle event")
            except InvalidOrderTransition as exc:
                # The broker asserts something our state machine calls
                # impossible (e.g. rejected after a partial fill). That is a
                # genuine disagreement, not something to paper over.
                logger.error("Order %s: %s", order.id, exc)
                order.reconcile_to(
                    OrderStatus.RECONCILIATION_REQUIRED,
                    f"broker reported {target.name} from {order.status.name}",
                )
                order.ambiguity_reason = "impossible broker transition"
                self._mark_ledger_status(order)
                return ExecutionResult(
                    success=False,
                    order=order,
                    message=f"Broker state disagrees with local lifecycle: {exc}",
                )
        else:
            order._record_transition(order.status, target, "duplicate broker event")

        if had_new_fill:
            self._update_position(order)
            self._record_ledger_fill(order)
            # ATOS-P0-EXEC-002: journal the fill directly. _record_ledger_fill
            # is a no-op without a ledger, and the durable lifecycle record
            # must not depend on that optional projection.
            self._journal_transition(
                order, f"fill: cumulative {order.filled_quantity} of {order.quantity}"
            )
            if order.status is OrderStatus.FILLED:
                self.filled_orders += 1
            self.total_volume += newly_filled
            if self.on_fill:
                for fill in order.fills:
                    self.on_fill(order, fill)
        else:
            self._mark_ledger_status(order)

        if order.status is OrderStatus.REJECTED:
            self.rejected_orders += 1
            if self.on_reject:
                self.on_reject(order, "broker rejected order")

        success = order.status in {
            OrderStatus.SUBMITTED,
            OrderStatus.OPEN,
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
        }
        if order.is_ambiguous:
            message = (
                "Order state UNKNOWN. The broker may hold this order; "
                "reconcile by client order ID before any retry."
            )
        elif success:
            message = "Live order accepted"
        else:
            message = "Live order rejected"
        return ExecutionResult(success=success, order=order, message=message)

    def _normalize_adapter_payload(self, payload: Any) -> Dict[str, Any]:
        """Convert adapter responses to a plain dictionary."""
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return payload
        if is_dataclass(payload):
            return asdict(payload)
        to_dict = getattr(payload, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        return {
            key: value
            for key, value in vars(payload).items()
            if not key.startswith("_")
        }

    def _coerce_order_status(self, raw_status: Any) -> OrderStatus:
        """Map broker status spellings onto execution order statuses."""
        if isinstance(raw_status, OrderStatus):
            return raw_status
        if isinstance(raw_status, Enum):
            raw_status = raw_status.value

        normalized = str(raw_status or "").strip().lower()
        aliases = {
            "accepted": OrderStatus.SUBMITTED,
            "new": OrderStatus.SUBMITTED,
            "submitted": OrderStatus.SUBMITTED,
            "pending_new": OrderStatus.SUBMITTED,
            "open": OrderStatus.OPEN,
            "partially_filled": OrderStatus.PARTIAL,
            "partial": OrderStatus.PARTIAL,
            "partial_fill": OrderStatus.PARTIAL,
            "filled": OrderStatus.FILLED,
            "done": OrderStatus.FILLED,
            "done_for_day": OrderStatus.EXPIRED,
            "pending_cancel": OrderStatus.CANCEL_REQUESTED,
            "cancel_requested": OrderStatus.CANCEL_REQUESTED,
            "cancelled": OrderStatus.CANCELLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.EXPIRED,
            "suspended": OrderStatus.UNKNOWN,
            "unknown": OrderStatus.UNKNOWN,
        }
        # ATOS-P0-EXEC-001: an unrecognised status is not an accepted order.
        # The previous default mapped anything unfamiliar to SUBMITTED, which
        # turned "we cannot read the broker's answer" into "the broker took
        # it". Unknown must stay unknown so reconciliation resolves it.
        resolved = aliases.get(normalized)
        if resolved is None:
            logger.warning(
                "Unrecognised broker order status %r; treating as UNKNOWN", raw_status
            )
            return OrderStatus.UNKNOWN
        return resolved

    def _coerce_float(self, value: Any) -> float:
        """Convert broker numeric fields to floats."""
        if value in (None, ""):
            return 0.0
        return float(value)

    def _parse_timestamp(self, value: Any) -> Optional[datetime]:
        """Parse broker timestamp fields when provided."""
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

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
            order.transition_to(OrderStatus.FILLED, "simulated fill")
            order.filled_at = datetime.now()
            self._update_position(order)
            self._record_ledger_fill(order)
            self.filled_orders += 1
            self.total_volume += total_qty

            return ExecutionResult(
                success=True,
                order=order,
                message=f"TWAP execution complete: {self.config.algo_num_slices} slices"
            )

        order.transition_to(OrderStatus.REJECTED, "simulation rejected order")
        self.rejected_orders += 1
        self._mark_ledger_status(order)
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
            order.transition_to(OrderStatus.FILLED, "simulated fill")
            order.filled_at = datetime.now()
            self._update_position(order)
            self._record_ledger_fill(order)
            self.filled_orders += 1
            self.total_volume += order.filled_quantity

            return ExecutionResult(
                success=True,
                order=order,
                message="Iceberg execution complete"
            )

        order.transition_to(OrderStatus.PARTIAL, "simulated partial fill")
        if order.filled_quantity > 0:
            self._record_ledger_fill(order)
        self._mark_ledger_status(order)
        return ExecutionResult(
            success=False,
            order=order,
            message=f"Iceberg partial fill: {order.filled_quantity}/{order.quantity}"
        )

    def _simulate_fill(self, order: Order) -> ExecutionResult:
        """Simulate order fill."""
        execution_price = self._get_execution_price(order)

        if execution_price is None:
            order.transition_to(OrderStatus.REJECTED, "simulation rejected order")
            self.rejected_orders += 1
            self._mark_ledger_status(order)
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
        order.transition_to(OrderStatus.FILLED, "simulated fill")
        order.filled_at = datetime.now()

        # Calculate slippage
        slippage = abs(fill_price - execution_price) / execution_price
        self.total_slippage += slippage

        # Update position
        self._update_position(order)
        self._record_ledger_fill(order)

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
        """Update legacy position cache after fill."""
        current_pos = self.positions.get(order.asset, 0)

        if order.side == OrderSide.BUY:
            self.positions[order.asset] = current_pos + order.filled_quantity
        else:
            self.positions[order.asset] = current_pos - order.filled_quantity

        if self.positions.get(order.asset) == 0:
            self.positions.pop(order.asset, None)

    def _record_ledger_fill(self, order: Order) -> None:
        """Record order fills in the canonical ledger."""
        if not self.ledger:
            return

        recorded_qty = 0.0
        ledger_order = self.ledger.orders.get(order.id)
        if ledger_order:
            recorded_qty = ledger_order.filled_quantity

        new_qty = order.filled_quantity - recorded_qty
        if new_qty <= 0:
            self._mark_ledger_status(order)
            return

        fill_price = order.avg_fill_price or order.filled_price
        if fill_price <= 0:
            self._mark_ledger_status(order)
            return

        self.ledger.record_fill(
            order_id=order.id,
            symbol=order.asset,
            side=order.side.value,
            quantity=new_qty,
            price=fill_price,
            timestamp=order.filled_at or datetime.now(),
        )
        self._sync_positions_from_ledger()

    def _mark_ledger_status(self, order: Order) -> None:
        """Mirror order status to the canonical ledger and the intent journal.

        ATOS-P0-EXEC-002 requires every lifecycle transition to be persisted,
        not only the terminal one: recovery reconstructs what may exist from
        this history.
        """
        if self.ledger:
            self.ledger.mark_order_status(order.id, order.status.value)
        # Journalled unconditionally: the ledger is a projection, the journal
        # is the durable record recovery reads.
        self._journal_transition(order, order.ambiguity_reason or "lifecycle update")

    def _sync_positions_from_ledger(self) -> None:
        """Keep the legacy position cache aligned with the canonical ledger."""
        if not self.ledger:
            return
        self.positions = {
            symbol: record.quantity
            for symbol, record in self.ledger.positions.items()
            if record.quantity != 0
        }

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        order = self.orders.get(order_id)
        if order and order.is_active:
            if not self.config.simulation_mode and self.broker_adapter:
                cancel = getattr(self.broker_adapter, "cancel_order", None)
                if not callable(cancel):
                    return False
                try:
                    if not cancel(order_id):
                        return False
                except Exception:
                    logger.exception("Live execution adapter failed while cancelling order %s", order_id)
                    return False
            # ATOS-P0-EXEC-004 will split this into CANCEL_REQUESTED and a
            # broker-confirmed terminal state. Routed through the validated
            # transition here so the lifecycle rules already apply.
            order.transition_to(OrderStatus.CANCELLED, "cancel acknowledged by adapter")
            self._mark_ledger_status(order)
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
        if self.ledger:
            position = self.ledger.positions.get(asset)
            return position.quantity if position else 0.0
        return self.positions.get(asset, 0)

    def get_all_positions(self) -> Dict[str, float]:
        """Get all positions."""
        if self.ledger:
            return {
                symbol: position.quantity
                for symbol, position in self.ledger.positions.items()
                if position.quantity != 0
            }
        return dict(self.positions)

    def reconcile_with_broker(
        self,
        broker_positions: Optional[Any] = None,
        quantity_tolerance: float = 1e-8,
        price_tolerance: float = 1e-6,
    ) -> Dict[str, Any]:
        """Compare canonical ledger positions with broker/account positions."""
        if not self.ledger:
            raise RuntimeError("Cannot reconcile positions without a canonical ledger")

        if broker_positions is None:
            get_positions = getattr(self.broker_adapter, "get_positions", None)
            if not callable(get_positions):
                raise RuntimeError("Broker adapter does not implement get_positions")
            broker_positions = get_positions()

        return self.ledger.reconcile_positions(
            broker_positions,
            quantity_tolerance=quantity_tolerance,
            price_tolerance=price_tolerance,
        )

    def close_position(self, asset: str, price: Optional[float] = None) -> Optional[ExecutionResult]:
        """Close entire position for an asset."""
        position = self.get_position(asset)
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
            "open_positions": len([p for p in self.get_all_positions().values() if p != 0]),
        }

    def check_expired_orders(self) -> List[Order]:
        """Check and expire timed-out orders."""
        expired = []
        now = datetime.now()

        for order in self.orders.values():
            if not (order.is_active and order.expires_at and now > order.expires_at):
                continue
            if order.is_ambiguous:
                # ATOS-P0-EXEC-001: a local clock cannot retire an order whose
                # broker state is unknown. Only reconciliation can.
                logger.warning(
                    "Order %s passed its expiry while ambiguous (%s); leaving it "
                    "for reconciliation rather than marking EXPIRED.",
                    order.id, order.ambiguity_reason,
                )
                continue
            order.transition_to(OrderStatus.EXPIRED, "local expiry deadline passed")
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
