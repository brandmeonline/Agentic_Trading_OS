"""Broker-authoritative exposure — ATOS-P1-RISK-001.

Invariant:

    Pre-trade risk uses conservative reconciled broker exposure plus
    outstanding order exposure.

    effective_exposure = broker_position_exposure
                       + outstanding_acquisition_order_exposure

Local bookkeeping is a projection. It is useful, it is fast, and it is wrong
whenever anything happened that the process did not perform itself: a manual
trade, a fill received while down, a partial that arrived out of order. Sizing
the next order against a projection means sizing it against a number nobody
has checked.

Two distinctions this module insists on:

* **Reserved is not filled.** An open buy for $500 is not a $500 position, but
  it is $500 of capacity that must not be sold twice. Collapsing them into one
  number - which the previous ``asset_exposure`` dict did - makes it
  impossible to say how much is real and how much is merely promised.

* **A breach is not a small order.** When exposure already exceeds the cap,
  the answer is not to size the next order down to what fits. There is nothing
  that fits. The correct output is a breach that freezes acquisition, and
  clamping it to zero hides that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExposureBreach:
    """A limit that current exposure already exceeds."""

    scope: str            # "asset:AAPL" or "portfolio"
    limit: float
    actual: float
    detail: str

    @property
    def excess(self) -> float:
        return self.actual - self.limit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "limit": self.limit,
            "actual": self.actual,
            "excess": self.excess,
            "detail": self.detail,
        }


@dataclass
class ExposureView:
    """What the broker holds, plus what our outstanding orders could add.

    All values are notional in account currency. ``positions`` are signed;
    everything derived from them is explicit about gross versus net, because
    a long and a short of the same size net to zero while consuming twice the
    capacity.
    """

    #: instrument -> signed position notional, from the broker.
    positions: Dict[str, float] = field(default_factory=dict)
    #: instrument -> notional of outstanding orders that would *add* exposure.
    #: Reduce-only and closing orders do not belong here: they cannot increase
    #: what we hold, so counting them would block risk-reducing activity.
    outstanding_acquisitions: Dict[str, float] = field(default_factory=dict)
    #: Broker equity. In live mode this, not configured capital, is the base
    #: for percentage limits.
    equity: Optional[float] = None
    #: When the broker data was taken. Stale data cannot authorise new risk.
    as_of: Optional[datetime] = None
    #: False when any part of the broker fetch failed.
    complete: bool = True
    #: True when reconciliation proved local and broker agree.
    reconciled: bool = False

    def position_exposure(self, asset: str) -> float:
        return abs(float(self.positions.get(asset, 0.0)))

    def outstanding_exposure(self, asset: str) -> float:
        return abs(float(self.outstanding_acquisitions.get(asset, 0.0)))

    def effective_exposure(self, asset: str) -> float:
        """What this instrument could cost us if every open order fills."""
        return self.position_exposure(asset) + self.outstanding_exposure(asset)

    @property
    def instruments(self) -> List[str]:
        return sorted(set(self.positions) | set(self.outstanding_acquisitions))

    @property
    def gross_exposure(self) -> float:
        """Total capacity consumed: longs and shorts both count."""
        return sum(self.effective_exposure(a) for a in self.instruments)

    @property
    def net_exposure(self) -> float:
        """Directional exposure: longs and shorts offset."""
        return sum(float(v) for v in self.positions.values())

    @property
    def usable(self) -> bool:
        """Whether this view may support a decision to add risk."""
        return self.complete and self.reconciled

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "equity": self.equity,
            "complete": self.complete,
            "reconciled": self.reconciled,
            "usable": self.usable,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "by_instrument": {
                asset: {
                    "position": self.position_exposure(asset),
                    "outstanding": self.outstanding_exposure(asset),
                    "effective": self.effective_exposure(asset),
                }
                for asset in self.instruments
            },
        }


class BrokerAuthoritativeExposure:
    """Evaluates limits against broker truth rather than local belief."""

    def __init__(
        self,
        max_asset_concentration: float,
        max_portfolio_exposure: float,
        configured_capital: float,
        live: bool = False,
    ) -> None:
        self.max_asset_concentration = max_asset_concentration
        self.max_portfolio_exposure = max_portfolio_exposure
        self.configured_capital = configured_capital
        self.live = live
        self.view = ExposureView()

    def update(self, view: ExposureView) -> None:
        self.view = view

    def capital_base(self) -> Optional[float]:
        """The number percentage limits are taken against.

        In live mode this must be broker equity. Sizing against a configured
        ``initial_capital`` after the account has moved means every percentage
        limit is computed from a number that stopped being true.

        Returns None in live mode when broker equity is unavailable, because
        there is then no honest base to compute a limit from.
        """
        if not self.live:
            return self.configured_capital
        if self.view.equity is None:
            return None
        return self.view.equity

    def asset_limit(self) -> Optional[float]:
        base = self.capital_base()
        return None if base is None else base * self.max_asset_concentration

    def portfolio_limit(self) -> Optional[float]:
        base = self.capital_base()
        return None if base is None else base * self.max_portfolio_exposure

    def breaches(self) -> List[ExposureBreach]:
        """Limits that current exposure already exceeds.

        These are reported, never clamped away. A breach means acquisition
        stops until it is resolved, not that the next order is made smaller.
        """
        asset_limit = self.asset_limit()
        portfolio_limit = self.portfolio_limit()
        if asset_limit is None or portfolio_limit is None:
            return [ExposureBreach(
                scope="portfolio",
                limit=0.0,
                actual=self.view.gross_exposure,
                detail=(
                    "no usable capital base: live mode requires broker equity, "
                    "and none was reported"
                ),
            )]

        found: List[ExposureBreach] = []
        for asset in self.view.instruments:
            actual = self.view.effective_exposure(asset)
            if actual > asset_limit:
                found.append(ExposureBreach(
                    scope=f"asset:{asset}",
                    limit=asset_limit,
                    actual=actual,
                    detail=(
                        f"{asset} effective exposure {actual:.2f} exceeds the "
                        f"per-asset limit {asset_limit:.2f}"
                    ),
                ))

        gross = self.view.gross_exposure
        if gross > portfolio_limit:
            found.append(ExposureBreach(
                scope="portfolio",
                limit=portfolio_limit,
                actual=gross,
                detail=(
                    f"portfolio gross exposure {gross:.2f} exceeds the limit "
                    f"{portfolio_limit:.2f}"
                ),
            ))
        return found

    def headroom(self, asset: str) -> float:
        """How much more of this instrument may be acquired, never negative.

        Zero means no capacity. It does not mean "no problem": callers must
        consult :meth:`breaches` to tell "full" from "over".
        """
        asset_limit = self.asset_limit()
        portfolio_limit = self.portfolio_limit()
        if asset_limit is None or portfolio_limit is None:
            return 0.0
        by_asset = asset_limit - self.view.effective_exposure(asset)
        by_portfolio = portfolio_limit - self.view.gross_exposure
        return max(0.0, min(by_asset, by_portfolio))

    def may_acquire(self, asset: str, notional: float) -> tuple:
        """Whether ``notional`` more of ``asset`` is permitted.

        Returns ``(allowed, reason)``. Every refusal explains itself, because
        "no" without a reason is indistinguishable from a bug.
        """
        if notional <= 0:
            return False, "a non-positive notional cannot be acquired"

        if self.live and not self.view.complete:
            return False, (
                "broker exposure data is incomplete; new risk cannot be sized "
                "against state we failed to fetch"
            )
        if self.live and not self.view.reconciled:
            return False, (
                "broker exposure is not reconciled; local and broker views are "
                "not known to agree"
            )

        existing = self.breaches()
        if existing:
            return False, (
                "exposure limits are already breached: "
                + "; ".join(b.detail for b in existing)
            )

        available = self.headroom(asset)
        if notional > available:
            return False, (
                f"{notional:.2f} exceeds the remaining headroom {available:.2f} "
                f"for {asset}"
            )
        return True, ""

    def report(self) -> Dict[str, Any]:
        return {
            "live": self.live,
            "capital_base": self.capital_base(),
            "asset_limit": self.asset_limit(),
            "portfolio_limit": self.portfolio_limit(),
            "breaches": [b.to_dict() for b in self.breaches()],
            "view": self.view.to_dict(),
        }


def view_from_reconciliation(
    positions: Dict[str, float],
    prices: Dict[str, float],
    outstanding_acquisitions: Optional[Dict[str, float]] = None,
    equity: Optional[float] = None,
    complete: bool = True,
    reconciled: bool = False,
) -> ExposureView:
    """Build a view from broker quantities and marks.

    An instrument with no price is deliberately omitted rather than valued at
    zero: an unpriced position is unknown exposure, and recording it as zero
    would understate risk. The caller sees it missing from ``instruments`` and
    should treat the view as incomplete.
    """
    notionals: Dict[str, float] = {}
    missing_prices = []
    for asset, quantity in positions.items():
        price = prices.get(asset)
        if price is None:
            missing_prices.append(asset)
            continue
        notionals[asset] = float(quantity) * float(price)

    if missing_prices:
        logger.warning(
            "No mark for %s; the exposure view is incomplete rather than zero",
            ", ".join(sorted(missing_prices)),
        )

    return ExposureView(
        positions=notionals,
        outstanding_acquisitions=dict(outstanding_acquisitions or {}),
        equity=equity,
        as_of=datetime.now(timezone.utc),
        complete=complete and not missing_prices,
        reconciled=reconciled,
    )
