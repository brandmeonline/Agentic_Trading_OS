"""Deterministic authority boundary — ATOS-P1-AGENT-001.

Invariant:

    All probabilistic and agentic output passes through one deterministic
    pre-trade policy boundary that no agent can bypass.

The required flow::

    data -> features -> models/agents -> normalized TradeProposal
         -> deterministic validation -> portfolio/risk
         -> execution intent -> broker

An RL policy, an LLM, a swarm vote and a hand-written strategy are all the
same kind of thing here: sources of *proposals*. None of them is an execution
authority. The distinction matters because probabilistic components fail in
ways deterministic ones do not — a NaN confidence, a size with an extra zero,
a symbol that does not exist, a proposal from a model version nobody approved
— and the boundary is where those stop.

Two properties make this a boundary rather than a suggestion:

* **Risk may reduce a proposal to nothing. A proposal may never enlarge
  itself.** The boundary composes one way. Anything an agent returns is an
  upper bound on what happens, never a floor.

* **A proposal is not an order.** It carries no broker fields and cannot be
  submitted. Turning one into an execution intent is a separate, deliberate
  step that runs the risk engine first.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Direction(Enum):
    BUY = "buy"
    SELL = "sell"
    FLATTEN = "flatten"


class Eligibility(Enum):
    """How far a proposal is allowed to travel.

    A model that has not earned live eligibility can still propose; its
    proposals simply stop at the shadow or paper boundary. That is how a
    candidate accumulates evidence without risking capital.
    """

    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


_ELIGIBILITY_RANK = {
    Eligibility.SHADOW: 0,
    Eligibility.PAPER: 1,
    Eligibility.LIVE: 2,
}


class ProposalRejected(ValueError):
    """A proposal that cannot be normalized into anything safe."""


@dataclass
class TradeProposal:
    """What an agent is allowed to say.

    Deliberately carries no broker fields: no client order ID, no order type,
    no time in force. A proposal cannot be submitted, only considered.
    """

    instrument: str
    direction: Direction
    confidence: float

    #: Exactly one of these expresses size. desired_notional is an absolute
    #: amount; target_exposure is a destination, which is how a flatten and a
    #: partial reduction are expressed.
    desired_notional: Optional[float] = None
    target_exposure: Optional[float] = None

    proposal_id: str = field(default_factory=lambda: f"prop-{uuid.uuid4()}")
    agent_id: str = "unknown"
    agent_version: str = "unknown"
    strategy_id: str = "unknown"
    model_training_version: Optional[str] = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    horizon: Optional[timedelta] = None
    expires_at: Optional[datetime] = None

    rationale: str = ""
    feature_provenance: Dict[str, Any] = field(default_factory=dict)
    eligibility: Eligibility = Eligibility.SHADOW

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        reference = now or datetime.now(timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= reference

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "instrument": self.instrument,
            "direction": self.direction.value,
            "confidence": self.confidence,
            "desired_notional": self.desired_notional,
            "target_exposure": self.target_exposure,
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "strategy_id": self.strategy_id,
            "model_training_version": self.model_training_version,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "rationale": self.rationale,
            "feature_provenance": dict(self.feature_provenance),
            "eligibility": self.eligibility.value,
        }


@dataclass
class BoundaryDecision:
    """What the deterministic layer decided about one proposal."""

    proposal: TradeProposal
    approved: bool
    approved_notional: float = 0.0
    reasons: List[str] = field(default_factory=list)
    reductions: List[str] = field(default_factory=list)

    @property
    def was_reduced(self) -> bool:
        return bool(self.reductions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal": self.proposal.to_dict(),
            "approved": self.approved,
            "approved_notional": self.approved_notional,
            "reasons": list(self.reasons),
            "reductions": list(self.reductions),
        }


class DeterministicTradeBoundary:
    """The one place agent output becomes a candidate for execution.

    Normalizes first, because a malformed proposal must be rejected before
    anything downstream reads its fields. Then applies deterministic policy,
    which may only ever shrink the request.
    """

    def __init__(
        self,
        allowed_instruments: Optional[frozenset] = None,
        max_proposal_notional: Optional[float] = None,
        min_confidence: float = 0.0,
        promoted_agents: Optional[frozenset] = None,
        mode: Eligibility = Eligibility.PAPER,
        risk_check: Optional[Any] = None,
    ) -> None:
        self.allowed_instruments = allowed_instruments
        self.max_proposal_notional = max_proposal_notional
        self.min_confidence = min_confidence
        self.promoted_agents = promoted_agents
        self.mode = mode
        #: Callable (instrument, notional) -> (allowed, reason). Typically
        #: BrokerAuthoritativeExposure.may_acquire.
        self.risk_check = risk_check

    # -- normalization ---------------------------------------------------

    def normalize(self, proposal: Any) -> TradeProposal:
        """Reject anything that is not a well-formed proposal.

        This runs before any policy, because policy on a NaN is meaningless.
        """
        if not isinstance(proposal, TradeProposal):
            raise ProposalRejected(
                f"expected a TradeProposal, got {type(proposal).__name__}; "
                "agents may not submit raw dictionaries or orders"
            )

        if not proposal.instrument or not isinstance(proposal.instrument, str):
            raise ProposalRejected("proposal has no instrument")

        if not isinstance(proposal.direction, Direction):
            raise ProposalRejected(
                f"direction {proposal.direction!r} is not a Direction"
            )

        confidence = proposal.confidence
        if confidence is None or not isinstance(confidence, (int, float)):
            raise ProposalRejected(f"confidence {confidence!r} is not a number")
        if isinstance(confidence, bool):
            raise ProposalRejected("confidence must be a number, not a boolean")
        if math.isnan(confidence) or math.isinf(confidence):
            raise ProposalRejected(f"confidence {confidence} is not finite")
        if not 0.0 <= confidence <= 1.0:
            raise ProposalRejected(
                f"confidence {confidence} is outside [0, 1]; a model that "
                "reports impossible confidence is not calibrated"
            )

        for name, value in (
            ("desired_notional", proposal.desired_notional),
            ("target_exposure", proposal.target_exposure),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProposalRejected(f"{name} {value!r} is not a number")
            if math.isnan(value) or math.isinf(value):
                raise ProposalRejected(f"{name} {value} is not finite")

        if proposal.desired_notional is None and proposal.target_exposure is None:
            raise ProposalRejected(
                "a proposal must express size, as desired_notional or "
                "target_exposure"
            )
        if proposal.desired_notional is not None and proposal.desired_notional < 0:
            raise ProposalRejected(
                f"desired_notional {proposal.desired_notional} is negative; "
                "direction expresses side, not sign"
            )

        return proposal

    # -- policy ----------------------------------------------------------

    def evaluate(
        self,
        proposal: Any,
        current_exposure: float = 0.0,
        now: Optional[datetime] = None,
    ) -> BoundaryDecision:
        """Normalize, then apply deterministic policy.

        Returns a decision whose approved_notional is at most what the
        proposal asked for. Never more.
        """
        try:
            clean = self.normalize(proposal)
        except ProposalRejected as exc:
            placeholder = proposal if isinstance(proposal, TradeProposal) else None
            logger.warning("Rejected malformed proposal: %s", exc)
            return BoundaryDecision(
                proposal=placeholder or _unusable_proposal(),
                approved=False,
                reasons=[f"malformed proposal: {exc}"],
            )

        decision = BoundaryDecision(proposal=clean, approved=False)
        requested = self._requested_notional(clean, current_exposure)

        if clean.is_expired(now):
            decision.reasons.append("proposal has expired")
            return decision

        if _ELIGIBILITY_RANK[clean.eligibility] < _ELIGIBILITY_RANK[self.mode]:
            decision.reasons.append(
                f"proposal is {clean.eligibility.value}-eligible but the system "
                f"is running {self.mode.value}; it may observe, not execute"
            )
            return decision

        if self.promoted_agents is not None:
            key = f"{clean.agent_id}:{clean.agent_version}"
            if key not in self.promoted_agents:
                decision.reasons.append(
                    f"agent {key} has no promotion evidence"
                )
                return decision

        if self.allowed_instruments is not None:
            if clean.instrument not in self.allowed_instruments:
                decision.reasons.append(
                    f"{clean.instrument} is not in the allowed instrument set"
                )
                return decision

        if clean.confidence < self.min_confidence:
            decision.reasons.append(
                f"confidence {clean.confidence} is below the {self.min_confidence} "
                "threshold"
            )
            return decision

        if requested <= 0:
            decision.reasons.append("proposal resolves to no change in exposure")
            return decision

        approved = requested

        # Deterministic policy may only shrink.
        if self.max_proposal_notional is not None:
            if approved > self.max_proposal_notional:
                decision.reductions.append(
                    f"capped from {approved:.2f} to {self.max_proposal_notional:.2f} "
                    "by the per-proposal limit"
                )
                approved = self.max_proposal_notional

        if self.risk_check is not None:
            allowed, reason = self.risk_check(clean.instrument, approved)
            if not allowed:
                decision.reasons.append(f"risk engine refused: {reason}")
                return decision

        decision.approved = True
        decision.approved_notional = approved
        decision.reasons.append(
            f"approved {approved:.2f} of {clean.instrument}"
        )
        if approved > requested:  # pragma: no cover - defended below by tests
            raise AssertionError(
                "the boundary enlarged a proposal, which must never happen"
            )
        return decision

    def _requested_notional(
        self, proposal: TradeProposal, current_exposure: float
    ) -> float:
        """What the proposal is asking to add, as a non-negative amount."""
        if proposal.direction is Direction.FLATTEN:
            return abs(current_exposure)
        if proposal.desired_notional is not None:
            return abs(proposal.desired_notional)
        if proposal.target_exposure is not None:
            return abs(abs(proposal.target_exposure) - abs(current_exposure))
        return 0.0


def _unusable_proposal() -> TradeProposal:
    """A stand-in so a rejection can still be reported as a decision."""
    return TradeProposal(
        instrument="<malformed>",
        direction=Direction.FLATTEN,
        confidence=0.0,
        target_exposure=0.0,
        eligibility=Eligibility.SHADOW,
    )


def arbitrate(proposals: List[TradeProposal]) -> List[TradeProposal]:
    """Collapse competing proposals so agents cannot double-order.

    Two agents proposing the same trade must produce one order, and two
    agents proposing opposite trades must produce none — otherwise a
    disagreement becomes two independent positions, which is the worst
    possible reading of it.

    Fuller governance (calibration, quorum, provenance) is ATOS-P3-AGENT-001;
    this is the deduplication the execution boundary needs today.
    """
    by_instrument: Dict[str, List[TradeProposal]] = {}
    for proposal in proposals:
        by_instrument.setdefault(proposal.instrument, []).append(proposal)

    resolved: List[TradeProposal] = []
    for instrument, group in sorted(by_instrument.items()):
        directions = {p.direction for p in group}
        if Direction.BUY in directions and Direction.SELL in directions:
            logger.warning(
                "Agents disagree on %s (%d proposals, both directions); "
                "emitting none",
                instrument, len(group),
            )
            continue
        # Highest confidence wins; ties resolve on the earliest proposal so
        # the outcome does not depend on dict ordering.
        best = min(group, key=lambda p: (-p.confidence, p.created_at, p.proposal_id))
        resolved.append(best)
    return resolved
