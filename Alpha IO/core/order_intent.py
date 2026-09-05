"""Durable pre-submit order intent journal — ATOS-P0-EXEC-002.

Invariant:

    The ledger knows a real order may exist before the network call that could
    create one.

This is a write-ahead log, not a snapshot. The trading ledger persists by
rewriting its whole state, which is fine for a projection but useless for
crash recovery at the moment that matters: the microsecond between deciding to
submit and the broker receiving it. If the process dies there, the only thing
that can tell recovery an order might exist is a record written *before* the
call, keyed by the same client order ID the broker was given.

Durability is real here. The journal commits with ``synchronous = FULL`` so a
returned write has reached the platform's stable storage, and every write is
append-only: a transition never overwrites the intent that preceded it.

Recovery reads :meth:`OrderIntentJournal.unresolved_intents` and asks the
broker about each client order ID. Anything the journal cannot prove terminal
is, by definition, still potentially live.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Statuses that prove the broker will not act on an intent again. Anything
#: else leaves the intent unresolved and therefore potentially live.
TERMINAL_INTENT_STATUSES = frozenset({
    "filled", "cancelled", "canceled", "rejected", "expired",
})


class IntentPersistenceError(RuntimeError):
    """Raised when an intent or transition could not be durably recorded.

    Callers in live mode must treat this as a hard stop: an order that cannot
    be written down must not be submitted, because a crash would leave no
    record that the broker might be holding it.
    """


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OrderIntent:
    """Everything needed to recognise an order we may have created.

    The fields are the ULTRAPLAN's EXEC-002 list. ``risk_approval_hash`` and
    ``expected_max_exposure_delta`` are recorded so that recovery can tell not
    only that an order exists but what the risk engine believed when it
    approved it — a config change between submission and recovery must not
    silently re-authorise it.
    """

    client_order_id: str
    internal_order_id: str
    session_id: str
    instrument: str
    side: str
    quantity: float
    order_type: str
    status: str = "intent_persisted"

    price: Optional[float] = None
    stop_price: Optional[float] = None
    notional: Optional[float] = None
    expected_max_exposure_delta: Optional[float] = None
    risk_approval_hash: Optional[str] = None

    strategy: Optional[str] = None
    agent_id: Optional[str] = None
    signal_id: Optional[str] = None

    broker_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status.lower() in TERMINAL_INTENT_STATUSES

    @property
    def is_unresolved(self) -> bool:
        """Whether the broker might still be holding this order."""
        return not self.is_terminal

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OrderIntentJournal:
    """Append-only, crash-durable record of orders that may exist.

    Thread-safe: a lock serialises writers, because two strategies racing to
    submit must not interleave a transition between another order's intent and
    its own.
    """

    def __init__(
        self,
        path: str,
        session_id: Optional[str] = None,
        busy_timeout_seconds: float = 30.0,
    ) -> None:
        self.path = Path(path)
        self.session_id = session_id or f"session-{uuid.uuid4()}"
        # How long to wait for a competing writer before giving up. The
        # default is generous because a live order intent is worth waiting
        # for; tests that deliberately hold a lock pass something short.
        self.busy_timeout_seconds = busy_timeout_seconds
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- storage ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=self.busy_timeout_seconds)
        conn.row_factory = sqlite3.Row
        # FULL, not NORMAL: a returned commit must have reached stable
        # storage. This is the whole point of the journal.
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS order_intents (
                        client_order_id TEXT PRIMARY KEY,
                        internal_order_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        instrument TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        order_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        price REAL,
                        stop_price REAL,
                        notional REAL,
                        expected_max_exposure_delta REAL,
                        risk_approval_hash TEXT,
                        strategy TEXT,
                        agent_id TEXT,
                        signal_id TEXT,
                        broker_order_id TEXT,
                        filled_quantity REAL NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE IF NOT EXISTS order_intent_transitions (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_order_id TEXT NOT NULL,
                        from_status TEXT,
                        to_status TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        filled_quantity REAL,
                        broker_order_id TEXT,
                        at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_intent_status
                        ON order_intents(status);
                    CREATE INDEX IF NOT EXISTS idx_transitions_client
                        ON order_intent_transitions(client_order_id, seq);
                """)
        except (sqlite3.DatabaseError, OSError) as exc:
            raise IntentPersistenceError(
                f"Could not open the order intent journal at {self.path}"
            ) from exc

    # -- writes ----------------------------------------------------------

    def record_intent(self, intent: OrderIntent) -> OrderIntent:
        """Durably record an intent before any broker call.

        Returns only once the write is committed. Raises
        :class:`IntentPersistenceError` otherwise, and the caller must not
        submit.
        """
        if not intent.client_order_id:
            raise ValueError("an intent must carry a client order ID")
        if intent.quantity <= 0:
            raise ValueError("an intent must carry a positive quantity")

        with self._lock:
            try:
                with self._connect() as conn:
                    existing = conn.execute(
                        "SELECT quantity, side, instrument FROM order_intents "
                        "WHERE client_order_id = ?",
                        (intent.client_order_id,),
                    ).fetchone()
                    if existing is not None:
                        # EXEC-003: the same client ID must never describe two
                        # different economic orders.
                        if (
                            abs(existing["quantity"] - intent.quantity) > 1e-9
                            or existing["side"] != intent.side
                            or existing["instrument"] != intent.instrument
                        ):
                            raise IntentPersistenceError(
                                f"client order ID {intent.client_order_id} already "
                                "describes a different order; refusing to overwrite"
                            )
                        return self._row_to_intent(
                            conn.execute(
                                "SELECT * FROM order_intents WHERE client_order_id = ?",
                                (intent.client_order_id,),
                            ).fetchone()
                        )

                    conn.execute(
                        """
                        INSERT INTO order_intents (
                            client_order_id, internal_order_id, session_id, instrument,
                            side, quantity, order_type, status, price, stop_price,
                            notional, expected_max_exposure_delta, risk_approval_hash,
                            strategy, agent_id, signal_id, broker_order_id,
                            filled_quantity, created_at, updated_at, metadata
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            intent.client_order_id, intent.internal_order_id,
                            intent.session_id, intent.instrument, intent.side,
                            intent.quantity, intent.order_type, intent.status,
                            intent.price, intent.stop_price, intent.notional,
                            intent.expected_max_exposure_delta,
                            intent.risk_approval_hash, intent.strategy,
                            intent.agent_id, intent.signal_id, intent.broker_order_id,
                            intent.filled_quantity, intent.created_at,
                            intent.updated_at, json.dumps(intent.metadata),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO order_intent_transitions (
                            client_order_id, from_status, to_status, reason,
                            filled_quantity, broker_order_id, at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            intent.client_order_id, None, intent.status,
                            "intent persisted before submission",
                            intent.filled_quantity, intent.broker_order_id,
                            intent.created_at,
                        ),
                    )
            except IntentPersistenceError:
                raise
            except (sqlite3.DatabaseError, OSError) as exc:
                raise IntentPersistenceError(
                    f"Could not durably record intent {intent.client_order_id}"
                ) from exc
        return intent

    def record_transition(
        self,
        client_order_id: str,
        to_status: str,
        reason: str = "",
        from_status: Optional[str] = None,
        filled_quantity: Optional[float] = None,
        broker_order_id: Optional[str] = None,
    ) -> None:
        """Append a lifecycle transition and update the intent's head state."""
        with self._lock:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT status FROM order_intents WHERE client_order_id = ?",
                        (client_order_id,),
                    ).fetchone()
                    if row is None:
                        raise IntentPersistenceError(
                            f"no recorded intent for client order ID {client_order_id}; "
                            "a lifecycle event arrived for an order we never wrote down"
                        )
                    now = _utcnow()
                    conn.execute(
                        """
                        INSERT INTO order_intent_transitions (
                            client_order_id, from_status, to_status, reason,
                            filled_quantity, broker_order_id, at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            client_order_id, from_status or row["status"], to_status,
                            reason, filled_quantity, broker_order_id, now,
                        ),
                    )
                    sets = ["status = ?", "updated_at = ?"]
                    params: List[Any] = [to_status, now]
                    if filled_quantity is not None:
                        sets.append("filled_quantity = ?")
                        params.append(filled_quantity)
                    if broker_order_id is not None:
                        sets.append("broker_order_id = ?")
                        params.append(broker_order_id)
                    params.append(client_order_id)
                    conn.execute(
                        f"UPDATE order_intents SET {', '.join(sets)} "
                        "WHERE client_order_id = ?",
                        params,
                    )
            except IntentPersistenceError:
                raise
            except (sqlite3.DatabaseError, OSError) as exc:
                raise IntentPersistenceError(
                    f"Could not durably record transition for {client_order_id}"
                ) from exc

    # -- reads -----------------------------------------------------------

    @staticmethod
    def _row_to_intent(row: sqlite3.Row) -> OrderIntent:
        data = dict(row)
        data["metadata"] = json.loads(data.get("metadata") or "{}")
        return OrderIntent(**data)

    def get(self, client_order_id: str) -> Optional[OrderIntent]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM order_intents WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return self._row_to_intent(row) if row else None

    def unresolved_intents(self) -> List[OrderIntent]:
        """Every intent the journal cannot prove terminal.

        This is the recovery worklist. Each one must be resolved against the
        broker by client order ID before the system may add new risk.
        """
        placeholders = ",".join("?" for _ in TERMINAL_INTENT_STATUSES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM order_intents WHERE LOWER(status) NOT IN ({placeholders}) "
                "ORDER BY created_at",
                tuple(TERMINAL_INTENT_STATUSES),
            ).fetchall()
        return [self._row_to_intent(row) for row in rows]

    def transitions_for(self, client_order_id: str) -> List[Dict[str, Any]]:
        """The full append-only history of one order, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM order_intent_transitions WHERE client_order_id = ? "
                "ORDER BY seq",
                (client_order_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def all_intents(self) -> List[OrderIntent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM order_intents ORDER BY created_at"
            ).fetchall()
        return [self._row_to_intent(row) for row in rows]
