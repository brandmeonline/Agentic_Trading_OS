"""Durable loss and drawdown anchors — ATOS-P1-RISK-002.

Invariant:

    A daily or total drawdown trip survives a restart.

A loss limit that a process restart clears is not a loss limit. It is a
suggestion that holds until something goes wrong enough to crash, which is
exactly when it needs to hold. The same applies to the high-water mark behind
total drawdown, and to the anchor the daily limit is measured from: recompute
either at startup and the account's worst day silently becomes its first day.

Three things make this correct rather than merely persistent:

* **The day is UTC.** ``datetime.now().date()`` rolls over at local midnight,
  which moves with the deploy host's timezone. A daily limit that resets at a
  different moment depending on where the process runs is not a daily limit.

* **The opening anchor is captured once.** On the first observation of a new
  trading day, and never again that day. Re-anchoring mid-day to the current
  equity resets the loss measurement to zero without anything having
  recovered.

* **A trip is sticky.** Clearing one is an explicit, recorded act, not
  something that happens because the process came back up.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def utc_trading_date(now: Optional[datetime] = None) -> date:
    """The trading date in UTC.

    Deliberately not local time: a limit that rolls over at the deploy host's
    midnight is a different limit on every host.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(timezone.utc).date()


@dataclass
class RiskAnchors:
    """The durable state behind every loss and drawdown decision."""

    trading_date: date
    #: Equity at the first observation of this trading day. Never re-anchored.
    day_opening_equity: Optional[float] = None
    #: Highest equity ever observed, the denominator of total drawdown.
    high_water_equity: Optional[float] = None
    #: Realized P&L accumulated today.
    daily_realized_pnl: float = 0.0
    #: The account's worst observed equity, for reporting.
    lowest_equity: Optional[float] = None
    #: Consecutive losing trades, when this affects permission.
    loss_streak: int = 0
    #: Trip name -> why and when it fired.
    active_trips: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def daily_loss(self) -> float:
        """Today's loss as a positive number, zero if up."""
        return abs(min(0.0, self.daily_realized_pnl))

    def daily_drawdown_fraction(self) -> Optional[float]:
        """Today's loss as a fraction of the day's opening equity."""
        if not self.day_opening_equity:
            return None
        return self.daily_loss / self.day_opening_equity

    def total_drawdown_fraction(self, current_equity: Optional[float]) -> Optional[float]:
        """Distance below the high-water mark, as a fraction of it."""
        if not self.high_water_equity or current_equity is None:
            return None
        if self.high_water_equity <= 0:
            return None
        return max(0.0, (self.high_water_equity - current_equity) / self.high_water_equity)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trading_date": self.trading_date.isoformat(),
            "day_opening_equity": self.day_opening_equity,
            "high_water_equity": self.high_water_equity,
            "daily_realized_pnl": self.daily_realized_pnl,
            "daily_loss": self.daily_loss,
            "lowest_equity": self.lowest_equity,
            "loss_streak": self.loss_streak,
            "active_trips": dict(self.active_trips),
            "updated_at": self.updated_at,
        }


class RiskAnchorStore:
    """Durable home for the anchors, with a same-day restart guarantee."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS risk_anchors (
                    trading_date TEXT PRIMARY KEY,
                    day_opening_equity REAL,
                    high_water_equity REAL,
                    daily_realized_pnl REAL NOT NULL DEFAULT 0,
                    lowest_equity REAL,
                    loss_streak INTEGER NOT NULL DEFAULT 0,
                    active_trips TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS risk_anchor_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_date TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL
                );
            """)

    # -- reads -----------------------------------------------------------

    def load(self, trading_date: date) -> Optional[RiskAnchors]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM risk_anchors WHERE trading_date = ?",
                (trading_date.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        return RiskAnchors(
            trading_date=date.fromisoformat(row["trading_date"]),
            day_opening_equity=row["day_opening_equity"],
            high_water_equity=row["high_water_equity"],
            daily_realized_pnl=row["daily_realized_pnl"],
            lowest_equity=row["lowest_equity"],
            loss_streak=row["loss_streak"],
            active_trips=json.loads(row["active_trips"] or "{}"),
            updated_at=row["updated_at"],
        )

    def latest_high_water(self) -> Optional[float]:
        """The high-water mark across every recorded day.

        Total drawdown is measured from the account's best ever, not from the
        best of whatever day happens to be loaded.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(high_water_equity) AS hw FROM risk_anchors"
            ).fetchone()
        return row["hw"] if row and row["hw"] is not None else None

    def events(self, trading_date: Optional[date] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if trading_date is None:
                rows = conn.execute(
                    "SELECT * FROM risk_anchor_events ORDER BY seq"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM risk_anchor_events WHERE trading_date = ? "
                    "ORDER BY seq",
                    (trading_date.isoformat(),),
                ).fetchall()
        return [dict(row) for row in rows]

    # -- writes ----------------------------------------------------------

    def save(self, anchors: RiskAnchors) -> None:
        anchors.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO risk_anchors (
                    trading_date, day_opening_equity, high_water_equity,
                    daily_realized_pnl, lowest_equity, loss_streak,
                    active_trips, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_date) DO UPDATE SET
                    day_opening_equity = excluded.day_opening_equity,
                    high_water_equity = excluded.high_water_equity,
                    daily_realized_pnl = excluded.daily_realized_pnl,
                    lowest_equity = excluded.lowest_equity,
                    loss_streak = excluded.loss_streak,
                    active_trips = excluded.active_trips,
                    updated_at = excluded.updated_at
                """,
                (
                    anchors.trading_date.isoformat(), anchors.day_opening_equity,
                    anchors.high_water_equity, anchors.daily_realized_pnl,
                    anchors.lowest_equity, anchors.loss_streak,
                    json.dumps(anchors.active_trips), anchors.updated_at,
                ),
            )

    def record_event(self, trading_date: date, event: str, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO risk_anchor_events (trading_date, event, detail, at) "
                "VALUES (?, ?, ?, ?)",
                (
                    trading_date.isoformat(), event, detail,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


class DurableRiskAnchors:
    """The anchors plus the rules that keep them honest.

    This is what a risk manager talks to. It owns the day-rollover decision,
    the once-per-day anchoring, and the stickiness of trips.
    """

    def __init__(
        self,
        store: RiskAnchorStore,
        max_daily_drawdown: float = 0.05,
        max_total_drawdown: float = 0.20,
    ) -> None:
        self.store = store
        self.max_daily_drawdown = max_daily_drawdown
        self.max_total_drawdown = max_total_drawdown
        self.anchors = self._load_or_start(utc_trading_date())

    def _load_or_start(self, trading_date: date) -> RiskAnchors:
        existing = self.store.load(trading_date)
        if existing is not None:
            logger.info(
                "Restored risk anchors for %s: opening equity %s, %d active trip(s)",
                trading_date, existing.day_opening_equity, len(existing.active_trips),
            )
            return existing
        anchors = RiskAnchors(
            trading_date=trading_date,
            high_water_equity=self.store.latest_high_water(),
        )
        self.store.save(anchors)
        self.store.record_event(trading_date, "day_started")
        return anchors

    def roll_to(self, trading_date: date) -> None:
        """Move to a new trading day, carrying the high-water mark forward."""
        if trading_date == self.anchors.trading_date:
            return
        carried = self.anchors.high_water_equity
        self.store.record_event(
            self.anchors.trading_date, "day_ended",
            f"final daily pnl {self.anchors.daily_realized_pnl}",
        )
        existing = self.store.load(trading_date)
        if existing is not None:
            self.anchors = existing
            return
        self.anchors = RiskAnchors(
            trading_date=trading_date,
            high_water_equity=carried,
            # The loss streak follows the account, not the calendar.
            loss_streak=self.anchors.loss_streak,
        )
        self.store.save(self.anchors)
        self.store.record_event(trading_date, "day_started")

    def observe_equity(self, equity: float, now: Optional[datetime] = None) -> None:
        """Record an equity observation, preferring broker truth.

        The day's opening equity is set on the first observation of the day
        and never moved again. Re-anchoring mid-day would reset the loss
        measurement without anything having recovered.
        """
        self.roll_to(utc_trading_date(now))
        anchors = self.anchors

        if anchors.day_opening_equity is None:
            anchors.day_opening_equity = equity
            self.store.record_event(
                anchors.trading_date, "day_opening_equity_set", f"{equity}"
            )

        if anchors.high_water_equity is None or equity > anchors.high_water_equity:
            anchors.high_water_equity = equity

        if anchors.lowest_equity is None or equity < anchors.lowest_equity:
            anchors.lowest_equity = equity

        self._evaluate_trips(equity)
        self.store.save(anchors)

    def record_realized_pnl(self, pnl: float, now: Optional[datetime] = None) -> None:
        self.roll_to(utc_trading_date(now))
        self.anchors.daily_realized_pnl += pnl
        if pnl < 0:
            self.anchors.loss_streak += 1
        elif pnl > 0:
            self.anchors.loss_streak = 0
        self.store.save(self.anchors)

    def _evaluate_trips(self, equity: float) -> None:
        anchors = self.anchors

        daily = anchors.daily_drawdown_fraction()
        if daily is not None and daily >= self.max_daily_drawdown:
            self.trip(
                "daily_drawdown",
                f"daily loss {daily:.2%} reached the {self.max_daily_drawdown:.2%} limit",
            )

        total = anchors.total_drawdown_fraction(equity)
        if total is not None and total >= self.max_total_drawdown:
            self.trip(
                "total_drawdown",
                f"drawdown {total:.2%} from the high-water mark "
                f"{anchors.high_water_equity} reached the "
                f"{self.max_total_drawdown:.2%} limit",
            )

    def trip(self, name: str, reason: str) -> None:
        """Fire a risk trip. Idempotent, and durable immediately."""
        if name in self.anchors.active_trips:
            return
        self.anchors.active_trips[name] = {
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.store.save(self.anchors)
        self.store.record_event(self.anchors.trading_date, f"trip:{name}", reason)
        logger.error("RISK TRIP %s: %s", name, reason)

    def clear_trip(self, name: str, reason: str) -> bool:
        """Clear a trip. Explicit, reasoned, and recorded.

        A restart does not do this. Nothing automatic does.
        """
        if not reason:
            raise ValueError("clearing a risk trip must state its reason")
        if name not in self.anchors.active_trips:
            return False
        self.anchors.active_trips.pop(name)
        self.store.save(self.anchors)
        self.store.record_event(
            self.anchors.trading_date, f"trip_cleared:{name}", reason
        )
        logger.warning("Risk trip %s cleared: %s", name, reason)
        return True

    @property
    def active_trips(self) -> List[str]:
        return sorted(self.anchors.active_trips)

    @property
    def tripped(self) -> bool:
        return bool(self.anchors.active_trips)

    def may_trade(self) -> tuple:
        """Whether risk state permits new positions."""
        if self.tripped:
            return False, "active risk trips: " + ", ".join(self.active_trips)
        return True, ""

    def report(self) -> Dict[str, Any]:
        return {
            "max_daily_drawdown": self.max_daily_drawdown,
            "max_total_drawdown": self.max_total_drawdown,
            "tripped": self.tripped,
            "active_trips": self.active_trips,
            "anchors": self.anchors.to_dict(),
        }
