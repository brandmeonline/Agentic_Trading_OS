"""Swarm arbitration governance — ATOS-P3-AGENT-001.

Invariant:

    A swarm produces at most one proposal per instrument and horizon, from
    identified agents whose scoring has no side effects, and no amount of
    agreement lets it past a hard risk limit.

The README advertises swarm-based decision arbitration. What existed was
``AgentSwarm.vote``: three agents, a majority rule, and a dict. That shape has
four problems, and none of them shows up until money is involved.

* **Agreement is not evidence.** Three agents built from the same class,
  trained on the same data, agreeing, is one agent counted three times.
  Calibration is tracked per agent and an agent with no track record cannot
  make a proposal live-eligible, however loudly it agrees.

* **Disagreement is dangerous, not neutral.** Two agents proposing opposite
  trades on the same instrument, each routed independently, produce two
  positions. The correct reading of a disagreement is "no trade", and it has
  to be enforced at the point where votes become a proposal.

* **Scoring must be pure.** An agent that places an order, mutates shared
  state or writes a file *while being asked what it thinks* has already acted
  before anyone decided whether to let it. The harness here calls each scorer
  twice and compares, and passes inputs it can detect mutation of.

* **A quorum is not an override.** The one thing a unanimous swarm must not
  be able to do is exceed a risk limit. Hard limits are evaluated after
  arbitration and cannot be voted on.

The last requirement is provenance: every vote that contributed, the proposal
it produced, and the order that followed, joined by ids that survive a
restart. Without it, "why did the system buy this" has no answer.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.trade_proposal import Direction, TradeProposal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class UnidentifiedAgent(ValueError):
    """A vote arrived without a usable agent identity."""


#: Placeholders that are not identities. "unknown" is the default on
#: TradeProposal, which makes it the value you get when nobody set one.
_NON_IDENTITIES = frozenset({"", "unknown", "none", "null", "n/a", "-"})


@dataclass(frozen=True)
class AgentIdentity:
    """Who voted, and which version of them.

    The version is not decoration. Calibration is per agent-version: an agent
    retrained last night has no track record, whatever its name has achieved.
    """

    agent_id: str
    version: str
    domain: str = "general"

    def __post_init__(self) -> None:
        for name, value in (("agent_id", self.agent_id),
                            ("version", self.version)):
            if not isinstance(value, str) or value.strip().lower() in _NON_IDENTITIES:
                raise UnidentifiedAgent(
                    f"{name} {value!r} is not an identity; a vote from an "
                    "unidentified agent cannot be calibrated or attributed"
                )

    @property
    def key(self) -> str:
        return f"{self.agent_id}:{self.version}"


# ---------------------------------------------------------------------------
# Untrusted text
# ---------------------------------------------------------------------------

#: Phrasings that only appear in text trying to be read as an instruction.
_INJECTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior|above|the)\s+\w*\s*instructions?"),
    re.compile(r"(?i)disregard\s+(all\s+)?(previous|prior|the)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\b"),
    re.compile(r"(?i)\bsystem\s*(prompt|message)\s*:"),
    re.compile(r"(?i)\b(override|bypass|disable)\s+(the\s+)?(risk|limit|guard|check|safety)"),
    re.compile(r"(?i)\bnew\s+(rules?|instructions?)\s*:"),
    re.compile(r"(?i)</?(system|assistant|user)>"),
    re.compile(r"(?i)\bact\s+as\b"),
)

#: How much free text an agent may attach at all.
MAX_RATIONALE_CHARS = 2000

SANITISED = "[removed]"


def injection_markers(text: str) -> List[str]:
    """Phrases in this text that read as an attempt to change the rules."""
    if not isinstance(text, str):
        return []
    return [m.group(0) for pattern in _INJECTION_PATTERNS
            for m in pattern.finditer(text)]


def sanitize_text(text: Any) -> str:
    """Make agent free text safe to store and display.

    Agent rationale is data. It is written by a model, it may quote a news
    article, and a news article can contain anything. The text is truncated,
    stripped of control characters, and has instruction-shaped phrases
    replaced — so that when it is later shown to a person, pasted into a
    report, or fed to another model, it is a description rather than a
    directive.
    """
    if text is None:
        return ""
    text = str(text)[:MAX_RATIONALE_CHARS]
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ch >= " ")
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub(SANITISED, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------


class VoteRejected(ValueError):
    """A vote that will not be counted."""


#: An absolute ceiling, independent of any configured limit. A proposal above
#: this is not an aggressive proposal, it is a broken one.
ABSURD_NOTIONAL = 1e12


@dataclass(frozen=True)
class Vote:
    """One agent's opinion, recorded so it can be attributed later."""

    agent: AgentIdentity
    instrument: str
    direction: Direction
    confidence: float
    horizon: timedelta
    desired_notional: Optional[float] = None
    rationale: str = ""
    cast_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    vote_id: str = field(default_factory=lambda: f"vote-{uuid.uuid4()}")

    def digest(self) -> str:
        """A hash of the opinion, so a recorded vote cannot be edited later."""
        payload = "|".join([
            self.agent.key, self.instrument, self.direction.value,
            f"{self.confidence:.6f}", str(int(self.horizon.total_seconds())),
            "" if self.desired_notional is None else f"{self.desired_notional:.6f}",
            self.cast_at.isoformat(),
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vote_id": self.vote_id,
            "agent": self.agent.key,
            "domain": self.agent.domain,
            "instrument": self.instrument,
            "direction": self.direction.value,
            "confidence": self.confidence,
            "horizon_seconds": self.horizon.total_seconds(),
            "desired_notional": self.desired_notional,
            "rationale": self.rationale,
            "cast_at": self.cast_at.isoformat(),
            "digest": self.digest(),
        }


def validate_vote(
    vote: Any,
    universe: Optional[frozenset] = None,
    max_age: timedelta = timedelta(minutes=5),
    now: Optional[datetime] = None,
) -> Vote:
    """Reject anything that is not a usable vote, and sanitise what is.

    Runs before arbitration, because arbitrating over a NaN produces a number
    and no error. Every rejection names the field, because an agent that keeps
    sending bad votes is a bug someone has to find.
    """
    if not isinstance(vote, Vote):
        raise VoteRejected(
            f"expected a Vote, got {type(vote).__name__}; agents may not "
            "submit raw dictionaries"
        )

    if not vote.instrument or not isinstance(vote.instrument, str):
        raise VoteRejected("vote has no instrument")
    if universe is not None and vote.instrument not in universe:
        raise VoteRejected(
            f"{vote.instrument} is not a supported instrument"
        )

    if not isinstance(vote.direction, Direction):
        raise VoteRejected(f"direction {vote.direction!r} is not a Direction")

    confidence = vote.confidence
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VoteRejected(f"confidence {confidence!r} is not a number")
    if math.isnan(confidence) or math.isinf(confidence):
        raise VoteRejected(f"confidence {confidence} is not finite")
    if not 0.0 <= confidence <= 1.0:
        raise VoteRejected(
            f"confidence {confidence} is outside [0, 1]; an agent reporting "
            "impossible confidence is not calibrated, it is broken"
        )

    notional = vote.desired_notional
    if notional is not None:
        if isinstance(notional, bool) or not isinstance(notional, (int, float)):
            raise VoteRejected(f"desired_notional {notional!r} is not a number")
        if math.isnan(notional) or math.isinf(notional):
            raise VoteRejected(f"desired_notional {notional} is not finite")
        if notional < 0:
            raise VoteRejected(
                f"desired_notional {notional} is negative; direction expresses "
                "side, not sign"
            )
        if notional > ABSURD_NOTIONAL:
            raise VoteRejected(
                f"desired_notional {notional:.3g} exceeds the absolute "
                f"{ABSURD_NOTIONAL:.0g} ceiling; this is a broken agent, not "
                "an aggressive one"
            )

    if not isinstance(vote.horizon, timedelta) or vote.horizon <= timedelta(0):
        raise VoteRejected(f"horizon {vote.horizon!r} is not a positive duration")

    reference = now or datetime.now(timezone.utc)
    cast_at = vote.cast_at
    if cast_at.tzinfo is None:
        cast_at = cast_at.replace(tzinfo=timezone.utc)
    age = reference - cast_at
    if age > max_age:
        raise VoteRejected(
            f"vote is {age.total_seconds():.0f}s old, beyond the "
            f"{max_age.total_seconds():.0f}s limit; a stale opinion is about "
            "a market that has moved"
        )
    if age < -timedelta(seconds=5):
        raise VoteRejected(
            "vote is timestamped in the future; a clock problem is not a "
            "reason to trust it"
        )

    markers = injection_markers(vote.rationale)
    if markers:
        logger.warning(
            "Agent %s attached instruction-shaped text to a vote: %s",
            vote.agent.key, markers[:3],
        )

    # Return a sanitised copy rather than mutating: the caller's object may be
    # the agent's own, and quietly editing it hides what the agent sent.
    return Vote(
        agent=vote.agent, instrument=vote.instrument, direction=vote.direction,
        confidence=float(confidence), horizon=vote.horizon,
        desired_notional=None if notional is None else float(notional),
        rationale=sanitize_text(vote.rationale),
        cast_at=cast_at, vote_id=vote.vote_id,
    )


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


class SideEffectDetected(AssertionError):
    """An agent did something while being asked what it thought."""


def scoring_side_effects(
    scorer: Callable[[Dict[str, Any]], Any],
    observation: Dict[str, Any],
) -> List[str]:
    """Reasons this scorer is not safe to call during a vote.

    Two checks, and the second is the one that catches real bugs: call it
    twice on equal inputs and compare the outputs, and give it a copy of the
    observation and see whether the copy came back changed. An agent that
    mutates the shared observation has changed what every later agent sees.
    """
    import copy

    problems: List[str] = []

    first_input = copy.deepcopy(observation)
    second_input = copy.deepcopy(observation)

    try:
        first = scorer(first_input)
    except Exception as exc:
        return [f"scoring raised {type(exc).__name__}: {exc}"]
    try:
        second = scorer(second_input)
    except Exception as exc:
        return [f"scoring raised on the second call: {type(exc).__name__}: {exc}"]

    if repr(first) != repr(second):
        problems.append(
            f"two calls on equal inputs returned {first!r} and {second!r}; "
            "scoring must be deterministic or a vote cannot be reproduced"
        )
    if first_input != observation or second_input != observation:
        problems.append(
            "scoring mutated the observation it was given, which changes what "
            "every later agent in the swarm sees"
        )
    return problems


def assert_pure(
    scorer: Callable[[Dict[str, Any]], Any],
    observation: Dict[str, Any],
    name: str = "scorer",
) -> None:
    problems = scoring_side_effects(scorer, observation)
    if problems:
        raise SideEffectDetected(f"{name}: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

#: Below this many resolved votes, an agent's hit rate is noise.
MINIMUM_RESOLVED_VOTES = 50
#: A Brier score above this is worse than always saying 0.5.
MAX_BRIER = 0.25


@dataclass
class Calibration:
    """How well one agent's confidence has matched reality."""

    agent_key: str
    resolved: int = 0
    correct: int = 0
    brier_sum: float = 0.0

    def record(self, confidence: float, was_right: bool) -> None:
        self.resolved += 1
        self.correct += 1 if was_right else 0
        outcome = 1.0 if was_right else 0.0
        self.brier_sum += (confidence - outcome) ** 2

    @property
    def hit_rate(self) -> float:
        return self.correct / self.resolved if self.resolved else 0.0

    @property
    def brier(self) -> float:
        """Lower is better. 0.25 is what always saying 0.5 scores."""
        return self.brier_sum / self.resolved if self.resolved else 1.0

    def problems(self) -> List[str]:
        problems: List[str] = []
        if self.resolved < MINIMUM_RESOLVED_VOTES:
            problems.append(
                f"only {self.resolved} resolved vote(s); "
                f"{MINIMUM_RESOLVED_VOTES} are needed before a hit rate means "
                "anything"
            )
        elif self.brier > MAX_BRIER:
            problems.append(
                f"Brier score {self.brier:.3f} is worse than {MAX_BRIER}, "
                "which is what an agent that always says 0.5 would score"
            )
        return problems

    @property
    def live_eligible(self) -> bool:
        return not self.problems()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent_key,
            "resolved": self.resolved,
            "hit_rate": self.hit_rate,
            "brier": self.brier,
            "live_eligible": self.live_eligible,
            "problems": self.problems(),
        }


class CalibrationBook:
    """Per-agent track records. An unknown agent is not a good one."""

    def __init__(self) -> None:
        self._records: Dict[str, Calibration] = {}

    def record(self, agent: AgentIdentity, confidence: float,
               was_right: bool) -> None:
        record = self._records.setdefault(agent.key, Calibration(agent.key))
        record.record(confidence, was_right)

    def for_agent(self, agent: AgentIdentity) -> Calibration:
        return self._records.get(agent.key, Calibration(agent.key))

    def live_eligible(self, agent: AgentIdentity) -> Tuple[bool, str]:
        record = self.for_agent(agent)
        problems = record.problems()
        if problems:
            return False, "; ".join(problems)
        return True, ""

    def report(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in sorted(self._records.values(),
                                            key=lambda r: r.agent_key)]


# ---------------------------------------------------------------------------
# Arbitration
# ---------------------------------------------------------------------------


class HorizonBucket(Enum):
    """Coarse horizons. Two agents proposing "soon" are proposing the same
    trade, even if one said four minutes and the other said six."""

    INTRADAY = "intraday"       # < 1 day
    SWING = "swing"             # 1-10 days
    POSITION = "position"       # > 10 days

    @staticmethod
    def of(horizon: timedelta) -> "HorizonBucket":
        if horizon < timedelta(days=1):
            return HorizonBucket.INTRADAY
        if horizon <= timedelta(days=10):
            return HorizonBucket.SWING
        return HorizonBucket.POSITION


@dataclass
class ArbitrationPolicy:
    """The rules the arbiter applies. All of them shrink, none enlarge."""

    #: Votes needed before a direction is proposed at all.
    quorum: int = 2
    #: Aggregate confidence needed. Computed as the mean of the winning side.
    min_mean_confidence: float = 0.55
    #: Contradiction within a bucket means no proposal, not the louder side.
    contradiction_is_refusal: bool = True
    #: Only calibrated agents count toward the quorum for a live proposal.
    require_calibration_for_live: bool = True


@dataclass
class ArbitrationOutcome:
    """One instrument-and-horizon, and what the swarm concluded about it."""

    instrument: str
    bucket: HorizonBucket
    proposal: Optional[TradeProposal]
    reasons: List[str] = field(default_factory=list)
    contributing: List[Vote] = field(default_factory=list)
    dissenting: List[Vote] = field(default_factory=list)

    @property
    def proposed(self) -> bool:
        return self.proposal is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.instrument,
            "bucket": self.bucket.value,
            "proposed": self.proposed,
            "proposal_id": self.proposal.proposal_id if self.proposal else None,
            "reasons": list(self.reasons),
            "contributing": [v.to_dict() for v in self.contributing],
            "dissenting": [v.to_dict() for v in self.dissenting],
        }


class SwarmArbiter:
    """Turns votes into at most one proposal per instrument and horizon.

    Deterministic: the same votes in any order produce the same outcome,
    because everything is sorted before it is reduced. That matters more than
    it sounds - a swarm whose result depends on dict ordering cannot be
    reproduced from its own provenance record.
    """

    def __init__(
        self,
        policy: Optional[ArbitrationPolicy] = None,
        calibration: Optional[CalibrationBook] = None,
        universe: Optional[frozenset] = None,
    ) -> None:
        self.policy = policy or ArbitrationPolicy()
        self.calibration = calibration or CalibrationBook()
        self.universe = universe
        self.rejected: List[Dict[str, str]] = []

    # -- the interface ----------------------------------------------------

    def arbitrate(
        self,
        votes: Sequence[Any],
        now: Optional[datetime] = None,
        live: bool = False,
    ) -> List[ArbitrationOutcome]:
        now = now or datetime.now(timezone.utc)
        clean: List[Vote] = []
        for vote in votes:
            try:
                clean.append(validate_vote(vote, universe=self.universe, now=now))
            except VoteRejected as exc:
                agent = getattr(getattr(vote, "agent", None), "key", "unknown")
                self.rejected.append({"agent": agent, "reason": str(exc)})
                logger.warning("Rejected vote from %s: %s", agent, exc)

        grouped: Dict[Tuple[str, HorizonBucket], List[Vote]] = {}
        for vote in clean:
            key = (vote.instrument, HorizonBucket.of(vote.horizon))
            grouped.setdefault(key, []).append(vote)

        outcomes: List[ArbitrationOutcome] = []
        for (instrument, bucket) in sorted(
            grouped, key=lambda k: (k[0], k[1].value)
        ):
            outcomes.append(
                self._resolve(instrument, bucket, grouped[(instrument, bucket)],
                              live=live)
            )
        return outcomes

    # -- one group --------------------------------------------------------

    def _resolve(
        self, instrument: str, bucket: HorizonBucket, group: List[Vote],
        live: bool,
    ) -> ArbitrationOutcome:
        outcome = ArbitrationOutcome(instrument=instrument, bucket=bucket,
                                     proposal=None)

        by_direction: Dict[Direction, List[Vote]] = {}
        for vote in group:
            by_direction.setdefault(vote.direction, []).append(vote)

        acquiring = {Direction.BUY, Direction.SELL} & set(by_direction)
        if len(acquiring) > 1 and self.policy.contradiction_is_refusal:
            outcome.reasons.append(
                "agents proposed both directions; a disagreement routed "
                "independently becomes two positions, so it becomes none"
            )
            outcome.dissenting = sorted(group, key=lambda v: v.vote_id)
            return outcome

        # Deterministic ordering: most votes, then highest mean confidence,
        # then the direction's name, so nothing depends on dict order.
        def rank(item: Tuple[Direction, List[Vote]]) -> Tuple[int, float, str]:
            direction, votes = item
            mean = sum(v.confidence for v in votes) / len(votes)
            return (-len(votes), -mean, direction.value)

        direction, winners = sorted(by_direction.items(), key=rank)[0]
        outcome.contributing = sorted(winners, key=lambda v: v.vote_id)
        outcome.dissenting = sorted(
            [v for v in group if v not in winners], key=lambda v: v.vote_id
        )

        eligible = winners
        if live and self.policy.require_calibration_for_live:
            eligible = [
                v for v in winners if self.calibration.live_eligible(v.agent)[0]
            ]
            uncalibrated = len(winners) - len(eligible)
            if uncalibrated:
                outcome.reasons.append(
                    f"{uncalibrated} vote(s) discounted: the agent has no "
                    "track record, and agreement from an agent nobody has "
                    "scored is not evidence"
                )

        if len(eligible) < self.policy.quorum:
            outcome.reasons.append(
                f"{len(eligible)} eligible vote(s) for {direction.value}, "
                f"below the quorum of {self.policy.quorum}"
            )
            return outcome

        mean_confidence = sum(v.confidence for v in eligible) / len(eligible)
        if mean_confidence < self.policy.min_mean_confidence:
            outcome.reasons.append(
                f"mean confidence {mean_confidence:.2f} is below "
                f"{self.policy.min_mean_confidence}"
            )
            return outcome

        # Size is the *smallest* thing any contributing agent asked for. A
        # swarm agreeing on direction has not agreed on size, and taking the
        # largest would let one aggressive agent size the whole book.
        sizes = [v.desired_notional for v in eligible
                 if v.desired_notional is not None]
        notional = min(sizes) if sizes else None

        if direction is Direction.FLATTEN:
            # Flattening is the one intent that needs no size: the size is
            # whatever is currently held.
            notional, target = None, 0.0
        elif notional is None or notional <= 0:
            outcome.reasons.append(
                "the winning side agreed on a direction but gave no usable "
                "size; a proposal without a size is not a proposal"
            )
            return outcome
        else:
            target = None

        horizon = min(v.horizon for v in eligible)
        proposal = TradeProposal(
            instrument=instrument,
            direction=direction,
            confidence=mean_confidence,
            desired_notional=notional,
            target_exposure=target,
            agent_id="swarm",
            agent_version=self._swarm_version(eligible),
            strategy_id=bucket.value,
            horizon=horizon,
            rationale=sanitize_text(
                "; ".join(f"{v.agent.key}: {v.rationale}" for v in eligible
                          if v.rationale)
            ),
            feature_provenance={
                "votes": [v.vote_id for v in eligible],
                "vote_digests": [v.digest() for v in eligible],
                "dissenting": [v.vote_id for v in outcome.dissenting],
            },
        )
        outcome.proposal = proposal
        outcome.reasons.append(
            f"{len(eligible)} agent(s) agreed on {direction.value} at mean "
            f"confidence {mean_confidence:.2f}"
        )
        return outcome

    @staticmethod
    def _swarm_version(votes: Sequence[Vote]) -> str:
        """A version derived from exactly which agent-versions contributed.

        Two different sets of agents are two different swarms, and the
        promotion evidence for one says nothing about the other.
        """
        payload = "|".join(sorted(v.agent.key for v in votes))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceRecord:
    """Votes, the proposal they produced, and the order that followed.

    Persisted as one row so the chain survives a restart. "Why did the system
    buy this" is a question that gets asked after the fact, by someone who
    was not there, about a process that has since restarted.
    """

    proposal_id: str
    instrument: str
    direction: str
    votes: List[Dict[str, Any]]
    decided_at: datetime
    client_order_id: Optional[str] = None
    risk_decision: str = "not evaluated"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "instrument": self.instrument,
            "direction": self.direction,
            "votes": self.votes,
            "decided_at": self.decided_at.isoformat(),
            "client_order_id": self.client_order_id,
            "risk_decision": self.risk_decision,
        }

    @property
    def complete(self) -> bool:
        """Whether the chain reaches all the way to an order."""
        return bool(self.votes and self.proposal_id and self.client_order_id)


class ProvenanceLog:
    """The chain from votes to orders, queryable both ways."""

    def __init__(self) -> None:
        self.records: Dict[str, ProvenanceRecord] = {}

    def record_decision(self, outcome: ArbitrationOutcome,
                        now: Optional[datetime] = None) -> Optional[ProvenanceRecord]:
        if outcome.proposal is None:
            return None
        record = ProvenanceRecord(
            proposal_id=outcome.proposal.proposal_id,
            instrument=outcome.instrument,
            direction=outcome.proposal.direction.value,
            votes=[v.to_dict() for v in outcome.contributing],
            decided_at=now or datetime.now(timezone.utc),
        )
        self.records[record.proposal_id] = record
        return record

    def attach_order(self, proposal_id: str, client_order_id: str) -> None:
        record = self.records.get(proposal_id)
        if record is None:
            raise KeyError(
                f"no provenance for proposal {proposal_id}; an order with no "
                "recorded decision behind it should not exist"
            )
        record.client_order_id = client_order_id

    def attach_risk_decision(self, proposal_id: str, decision: str) -> None:
        record = self.records.get(proposal_id)
        if record is not None:
            record.risk_decision = decision

    def for_order(self, client_order_id: str) -> Optional[ProvenanceRecord]:
        for record in self.records.values():
            if record.client_order_id == client_order_id:
                return record
        return None

    def orphan_orders(self, client_order_ids: Sequence[str]) -> List[str]:
        """Orders with no decision behind them."""
        known = {r.client_order_id for r in self.records.values()}
        return [oid for oid in client_order_ids if oid not in known]


# ---------------------------------------------------------------------------
# Hard risk is not a vote
# ---------------------------------------------------------------------------


class QuorumOverrodeRisk(AssertionError):
    """A swarm result was allowed past a hard limit."""


def apply_hard_risk(
    outcomes: Sequence[ArbitrationOutcome],
    risk_check: Callable[[str, float], Tuple[bool, str]],
    provenance: Optional[ProvenanceLog] = None,
) -> List[ArbitrationOutcome]:
    """Evaluate hard limits *after* arbitration, where votes cannot reach.

    Ordering is the whole point. If risk were consulted during the vote, a
    sufficiently confident swarm could outweigh it; here the swarm has already
    finished, and its conclusion is an input to a check it cannot influence.
    """
    survived: List[ArbitrationOutcome] = []
    for outcome in outcomes:
        if outcome.proposal is None:
            survived.append(outcome)
            continue
        notional = outcome.proposal.desired_notional or 0.0
        allowed, reason = risk_check(outcome.instrument, notional)
        if provenance is not None:
            provenance.attach_risk_decision(
                outcome.proposal.proposal_id,
                "allowed" if allowed else f"refused: {reason}",
            )
        if not allowed:
            outcome.reasons.append(f"hard risk limit refused: {reason}")
            outcome.proposal = None
        survived.append(outcome)
    return survived
