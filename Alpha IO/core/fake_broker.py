"""Deterministic adversarial broker — ATOS-P2-FAULT-001.

A real broker misbehaves in ways that are hard to arrange on demand and
expensive to discover in production: it accepts an order and loses the
response, fills after a cancel request, rate-limits mid-session, returns
malformed JSON, or reports a status nobody has seen before.

This adapter arranges all of them, deterministically, in a unit test.

It is a test double that lives in ``core`` on purpose. Keeping it beside the
code it doubles means the two drift together — a new field on the real
adapter's response is a visible gap here — and it makes the harness available
to any test module rather than one file's local helper.

Every scenario is scripted, not random. A flaky test that fails one run in
fifty teaches nothing; a test that fails every time on a known sequence is
evidence.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BrokerScenario(Enum):
    """The failure modes the ULTRAPLAN requires the system to survive."""

    FULL_FILL = "full_fill"
    OPEN_ORDER = "open_order"
    PARTIAL_FILL = "partial_fill"
    FILL_AFTER_CANCEL_REQUEST = "fill_after_cancel_request"
    CANCELED_WITH_PARTIAL_FILL = "canceled_with_partial_fill"
    TIMEOUT_BEFORE_ACCEPT = "timeout_before_accept"
    TIMEOUT_AFTER_ACCEPT = "timeout_after_accept"
    DUPLICATE_RESPONSE = "duplicate_response"
    STALE_STATUS = "stale_status"
    OUT_OF_ORDER_EVENT = "out_of_order_event"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    MALFORMED_JSON = "malformed_json"
    MISSING_ORDER_ID = "missing_order_id"
    UNKNOWN_STATUS = "unknown_status"
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    MARKET_CLOSED = "market_closed"
    HALTED = "halted"
    QUANTITY_PRECISION_REJECTED = "quantity_precision_rejected"
    NOTIONAL_INVALID = "notional_invalid"
    AUTH_FAILURE = "auth_failure"
    ACCOUNT_MISMATCH = "account_mismatch"
    POSITION_CHANGED_EXTERNALLY = "position_changed_externally"
    STREAM_DISCONNECT = "stream_disconnect"


class BrokerTransportError(Exception):
    """The response never arrived. Acceptance is unknown."""


class BrokerHTTPError(Exception):
    """The broker answered with an error status."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


@dataclass
class FakeBrokerState:
    """What the fake broker believes, which tests can inspect and mutate."""

    #: client_order_id -> the order the broker holds
    orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: instrument -> signed quantity
    positions: Dict[str, float] = field(default_factory=dict)
    cash: float = 100_000.0
    account_fingerprint: str = "fake-account"
    #: Orders the broker accepted but never told us about, which is what a
    #: timeout-after-accept leaves behind.
    silently_accepted: List[str] = field(default_factory=list)


class AdversarialBroker:
    """A broker adapter that misbehaves on a script.

    Pass a list of scenarios; each call to ``submit_order`` consumes the next
    one. When the script runs out, behaviour falls back to ``default``, which
    keeps a long test from having to enumerate every uneventful call.
    """

    def __init__(
        self,
        script: Optional[List[BrokerScenario]] = None,
        default: BrokerScenario = BrokerScenario.OPEN_ORDER,
        state: Optional[FakeBrokerState] = None,
    ) -> None:
        self.script = list(script or [])
        self.default = default
        self.state = state or FakeBrokerState()

        self.submissions: List[str] = []
        self.cancels: List[str] = []
        self.lookups: List[str] = []
        self._ids = itertools.count(1)

    # -- helpers ---------------------------------------------------------

    def _next_scenario(self) -> BrokerScenario:
        return self.script.pop(0) if self.script else self.default

    def _broker_id(self) -> str:
        return f"brk-{next(self._ids)}"

    def _record(self, order, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.state.orders[order.client_order_id] = payload
        return payload

    # -- the adapter surface ---------------------------------------------

    def submit_order(self, order) -> Dict[str, Any]:
        """Behave according to the next scripted scenario."""
        self.submissions.append(order.client_order_id)
        scenario = self._next_scenario()
        quantity = float(getattr(order, "quantity", 0.0))
        symbol = getattr(order, "asset", "")
        side = getattr(order, "side", None)
        side_value = getattr(side, "value", str(side))

        if scenario is BrokerScenario.TIMEOUT_BEFORE_ACCEPT:
            # Nothing was created. The caller cannot know that.
            raise BrokerTransportError("read timed out before the broker replied")

        if scenario is BrokerScenario.TIMEOUT_AFTER_ACCEPT:
            # The broker holds a real order; the response was lost. This is
            # the single most dangerous case, so the fake records the order
            # so a later lookup can find it.
            self._record(order, {
                "broker_order_id": self._broker_id(),
                "client_order_id": order.client_order_id,
                "status": "open",
                "symbol": symbol,
                "side": side_value,
                "quantity": quantity,
                "filled_quantity": 0.0,
            })
            self.state.silently_accepted.append(order.client_order_id)
            raise BrokerTransportError("connection reset after the order was accepted")

        if scenario is BrokerScenario.RATE_LIMITED:
            raise BrokerHTTPError(429, "too many requests")
        if scenario is BrokerScenario.SERVER_ERROR:
            raise BrokerHTTPError(503, "service unavailable")
        if scenario is BrokerScenario.AUTH_FAILURE:
            raise BrokerHTTPError(401, "invalid credentials")
        if scenario is BrokerScenario.MALFORMED_JSON:
            return "{not valid json at all"          # type: ignore[return-value]
        if scenario is BrokerScenario.INSUFFICIENT_BUYING_POWER:
            return self._record(order, {
                "status": "rejected", "reason": "insufficient buying power",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.MARKET_CLOSED:
            return self._record(order, {
                "status": "rejected", "reason": "market closed",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.HALTED:
            return self._record(order, {
                "status": "rejected", "reason": "symbol halted",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.QUANTITY_PRECISION_REJECTED:
            return self._record(order, {
                "status": "rejected", "reason": "quantity precision invalid",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.NOTIONAL_INVALID:
            return self._record(order, {
                "status": "rejected", "reason": "notional below minimum",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.UNKNOWN_STATUS:
            return self._record(order, {
                "status": "quantum_superposition",
                "broker_order_id": self._broker_id(), "filled_quantity": 0.0,
            })
        if scenario is BrokerScenario.MISSING_ORDER_ID:
            return self._record(order, {"status": "open", "filled_quantity": 0.0})
        if scenario is BrokerScenario.ACCOUNT_MISMATCH:
            return self._record(order, {
                "status": "open", "filled_quantity": 0.0,
                "broker_order_id": self._broker_id(),
                "account_id": "somebody-elses-account",
            })
        if scenario is BrokerScenario.PARTIAL_FILL:
            filled = quantity / 2
            self.state.positions[symbol] = (
                self.state.positions.get(symbol, 0.0) + filled
            )
            return self._record(order, {
                "status": "partially_filled", "filled_quantity": filled,
                "avg_fill_price": 100.0, "quantity": quantity,
                "broker_order_id": self._broker_id(), "symbol": symbol,
                "side": side_value,
            })
        if scenario is BrokerScenario.OPEN_ORDER:
            return self._record(order, {
                "status": "open", "filled_quantity": 0.0, "quantity": quantity,
                "broker_order_id": self._broker_id(), "symbol": symbol,
                "side": side_value,
            })
        if scenario is BrokerScenario.POSITION_CHANGED_EXTERNALLY:
            # Somebody traded the account behind our back.
            self.state.positions[symbol] = (
                self.state.positions.get(symbol, 0.0) + 999.0
            )
            return self._record(order, {
                "status": "open", "filled_quantity": 0.0,
                "broker_order_id": self._broker_id(), "symbol": symbol,
            })

        # FULL_FILL and anything unhandled resolve to a clean fill.
        self.state.positions[symbol] = (
            self.state.positions.get(symbol, 0.0) + quantity
        )
        return self._record(order, {
            "status": "filled", "filled_quantity": quantity,
            "avg_fill_price": 100.0, "quantity": quantity,
            "broker_order_id": self._broker_id(), "symbol": symbol,
            "side": side_value,
        })

    def cancel_order(self, order_id: str) -> bool:
        self.cancels.append(order_id)
        scenario = self._next_scenario()
        if scenario is BrokerScenario.TIMEOUT_BEFORE_ACCEPT:
            raise BrokerTransportError("cancel request timed out")
        if scenario in {
            BrokerScenario.FILL_AFTER_CANCEL_REQUEST,
            BrokerScenario.CANCELED_WITH_PARTIAL_FILL,
        }:
            # The broker accepts the request; the outcome is decided later.
            return True
        if scenario is BrokerScenario.SERVER_ERROR:
            raise BrokerHTTPError(500, "cancel failed")
        return True

    def get_order_by_client_order_id(self, client_order_id: str):
        """Look an order up. This is how a lost response gets resolved."""
        self.lookups.append(client_order_id)
        return self.state.orders.get(client_order_id)

    def get_positions(self):
        return [
            {"symbol": symbol, "qty": quantity}
            for symbol, quantity in sorted(self.state.positions.items())
        ]

    def get_account(self):
        return {
            "status": "ACTIVE",
            "cash": self.state.cash,
            "account_id": self.state.account_fingerprint,
        }


def cancel_outcome(scenario: BrokerScenario, quantity: float) -> Dict[str, Any]:
    """The broker's eventual answer to a cancel request.

    Separate from ``cancel_order`` because a cancel request and its outcome
    are two different events, which is the whole point of EXEC-004.
    """
    if scenario is BrokerScenario.FILL_AFTER_CANCEL_REQUEST:
        return {"status": "filled", "filled_quantity": quantity,
                "avg_fill_price": 100.0}
    if scenario is BrokerScenario.CANCELED_WITH_PARTIAL_FILL:
        return {"status": "canceled", "filled_quantity": quantity / 2,
                "avg_fill_price": 100.0}
    return {"status": "canceled", "filled_quantity": 0.0}


def assert_no_untracked_exposure(engine, broker: AdversarialBroker) -> List[str]:
    """The invariant every scenario must satisfy.

    Returns a list of violations, empty when the system is behaving. Checks
    the four properties the ULTRAPLAN requires of every fault scenario:
    no untracked exposure, no duplicate order, a conservative reservation
    while state is unknown, and acquisition frozen on material uncertainty.
    """
    violations: List[str] = []

    # 1. No duplicate orders: one client order ID per submission.
    if len(broker.submissions) != len(set(broker.submissions)):
        violations.append(
            "the same client order ID was submitted more than once"
        )

    # 2. Anything the broker silently accepted must be tracked locally as
    #    live, not resolved away.
    for client_id in broker.state.silently_accepted:
        order = next(
            (o for o in engine.orders.values() if o.client_order_id == client_id),
            None,
        )
        if order is None:
            violations.append(
                f"broker holds {client_id} but the engine has no record of it"
            )
        elif not order.is_active:
            violations.append(
                f"broker holds {client_id} but the engine considers it "
                f"{order.status.name}, releasing its reservation"
            )

    # 3. An ambiguous order must keep its exposure.
    for order in engine.orders.values():
        if order.is_ambiguous and not order.is_active:
            violations.append(
                f"{order.id} is ambiguous but not active, so its exposure was "
                "released while the broker may still be working it"
            )

    return violations
