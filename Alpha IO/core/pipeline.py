"""
Signal-to-execution pipeline.

Closes the last open loop: corpus → candidates → router → risk → execution →
ledger. Everything on that path already existed; nothing connected it, so the
system could measure signals and could place orders but never did the one
because of the other.

**Three gates stand between a signal and a live order, and all three must open.**

1. **Mode.** `paper` is the default. `live` must be set explicitly.
2. **Evidence.** A live order additionally requires an `EdgeReport` with verdict
   `ESTABLISHED` — measured hit rate whose 95% lower bound beats the best
   baseline, on at least `MIN_SAMPLES` signals. Without that report the pipeline
   refuses to go live, no matter how the mode is set.
3. **Risk.** Every order passes `RiskManager.check_risk_limits` and is sized by
   `calculate_position_size`. A signal never chooses its own size.

Gate 2 is the one that matters and the one that is easy to skip. A system that
can trade on signals of unmeasured quality is not a trading system; it is a
random number generator with a broker attached. The gate is enforced in code,
not documented as a convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class PipelineMode(Enum):
    """How far a decision is allowed to travel."""
    OBSERVE = "observe"   # score and record only; nothing is ordered
    PAPER = "paper"       # orders go to the ledger, never to a broker
    LIVE = "live"         # orders reach the configured broker adapter


class Blocked(Enum):
    """Why a candidate did not become an order. One reason, always recorded."""
    NOT_ROUTED = "not_routed"                 # router said watchlist or ignore
    OBSERVE_MODE = "observe_mode"
    NO_EDGE_EVIDENCE = "no_edge_evidence"     # gate 2
    RISK_LIMITS = "risk_limits"
    NO_PRICE = "no_price"
    ZERO_SIZE = "zero_size"
    EXECUTION_FAILED = "execution_failed"


@dataclass
class Decision:
    """One candidate's journey, whatever its outcome."""
    term: str
    decision: str                       # router verdict: trade / watchlist / ignore
    score: float
    measured: bool
    sources: int
    ordered: bool = False
    blocked_by: Optional[Blocked] = None
    detail: str = ""
    order_id: Optional[str] = None
    quantity: float = 0.0
    price: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "decision": self.decision,
            "score": round(self.score, 4),
            "measured": self.measured,
            "sources": self.sources,
            "ordered": self.ordered,
            "blocked_by": self.blocked_by.value if self.blocked_by else None,
            "detail": self.detail,
            "order_id": self.order_id,
            "quantity": self.quantity,
            "price": self.price,
        }


@dataclass
class CycleResult:
    """What one pass over the candidates did."""
    ran_at: datetime
    mode: PipelineMode
    considered: int = 0
    ordered: int = 0
    decisions: List[Decision] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "mode": self.mode.value,
            "considered": self.considered,
            "ordered": self.ordered,
            "decisions": [d.to_dict() for d in self.decisions],
        }

    def blocked_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for decision in self.decisions:
            if decision.blocked_by is not None:
                counts[decision.blocked_by.value] = counts.get(decision.blocked_by.value, 0) + 1
        return counts


class TradingPipeline:
    """Turns measured signals into risk-sized orders, under three gates.

    Collaborators are injected rather than constructed so that the gates can be
    tested without a broker, a corpus, or a clock.
    """

    def __init__(
        self,
        corpus: Any,
        router: Any,
        risk_manager: Any,
        execution_engine: Any,
        mode: PipelineMode = PipelineMode.PAPER,
        edge_report: Optional[Any] = None,
        price_lookup: Optional[Callable[[str], Optional[float]]] = None,
        confidence: float = 0.85,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_orders_per_cycle: int = 3,
    ) -> None:
        self.corpus = corpus
        self.router = router
        self.risk_manager = risk_manager
        self.execution_engine = execution_engine
        self.mode = mode
        self.edge_report = edge_report
        self.price_lookup = price_lookup
        self.confidence = confidence
        self._clock = clock
        self.max_orders_per_cycle = max_orders_per_cycle
        self.last_result: Optional[CycleResult] = None

    # -- gates -------------------------------------------------------------

    @property
    def edge_established(self) -> bool:
        """Whether measurement supports trading on this signal at all."""
        return bool(self.edge_report is not None and getattr(self.edge_report, "established", False))

    def effective_mode(self) -> PipelineMode:
        """The mode actually in force after the evidence gate.

        Asking for LIVE without an established edge does not raise — it
        downgrades to PAPER and says so in every decision. Failing loudly at
        startup would be worse: an operator who set the flag would simply
        remove the check.
        """
        if self.mode is PipelineMode.LIVE and not self.edge_established:
            return PipelineMode.PAPER
        return self.mode

    def gate_status(self) -> Dict[str, Any]:
        """Why the pipeline will or will not place a live order."""
        report = self.edge_report
        return {
            "requested_mode": self.mode.value,
            "effective_mode": self.effective_mode().value,
            "edge_established": self.edge_established,
            "edge": report.to_dict() if hasattr(report, "to_dict") else None,
            "downgraded": self.mode is PipelineMode.LIVE and not self.edge_established,
        }

    # -- the cycle ---------------------------------------------------------

    def run_cycle(self, terms: Optional[List[str]] = None, now: Optional[datetime] = None) -> CycleResult:
        """Score candidates, route them, and order what survives every gate."""
        now = now or self._clock()
        mode = self.effective_mode()
        result = CycleResult(ran_at=now, mode=mode)

        for term in (terms if terms is not None else self._candidate_terms(now)):
            decision = self._consider(term, mode, result)
            result.decisions.append(decision)
            result.considered += 1
            if decision.ordered:
                result.ordered += 1
                if result.ordered >= self.max_orders_per_cycle:
                    break

        self.last_result = result
        return result

    def _candidate_terms(self, now: datetime) -> List[str]:
        """Terms the corpus has seen, most-mentioned first."""
        counts: Dict[str, int] = {}
        for item in self.corpus.recent(now):
            for entity in item.entities:
                counts[entity] = counts.get(entity, 0) + 1
        return [term for term, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def _consider(self, term: str, mode: PipelineMode, result: CycleResult) -> Decision:
        routed = self.router.route(f"{term} signal", confidence=self.confidence, term=term)
        measurement = routed.get("asymmetry", {})
        decision = Decision(
            term=term,
            decision=routed.get("decision", "ignore"),
            score=float(routed.get("asymmetry_score", 0.0)),
            measured=bool(measurement.get("measured")),
            sources=int(measurement.get("sources", 0)),
        )

        if decision.decision != "trade":
            decision.blocked_by = Blocked.NOT_ROUTED
            decision.detail = routed.get("note", "")
            return decision

        if mode is PipelineMode.OBSERVE:
            decision.blocked_by = Blocked.OBSERVE_MODE
            decision.detail = "Pipeline is in observe mode; nothing is ordered."
            return decision

        if self.mode is PipelineMode.LIVE and not self.edge_established:
            decision.blocked_by = Blocked.NO_EDGE_EVIDENCE
            decision.detail = (
                "Live trading requires an established edge report. "
                "Routing to paper instead."
            )
            # Fall through: paper execution still proceeds, the reason is recorded.

        price = self.price_lookup(term) if self.price_lookup else None
        if price is None or price <= 0:
            decision.blocked_by = Blocked.NO_PRICE
            decision.detail = f"No usable price for {term}."
            return decision
        decision.price = price

        if not self.risk_manager.check_risk_limits(term):
            decision.blocked_by = Blocked.RISK_LIMITS
            decision.detail = "Risk limits refused this asset."
            return decision

        quantity = self._size(term, price)
        if quantity <= 0:
            decision.blocked_by = Blocked.ZERO_SIZE
            decision.detail = "Risk sizing returned zero."
            return decision
        decision.quantity = quantity

        return self._execute(decision, term, quantity, price)

    def _size(self, term: str, price: float) -> float:
        """Ask the risk manager for a size. The signal never picks its own.

        ``calculate_position_size`` returns **capital units** (notional), not a
        share count, so it is converted here. Getting this wrong in the other
        direction would size every order by a factor of the share price.
        """
        try:
            notional = self.risk_manager.calculate_position_size(
                term, self.confidence, entry_price=price
            )
        except Exception:  # noqa: BLE001 - a sizing failure means no order, not a crash
            return 0.0

        try:
            notional = float(notional)
        except (TypeError, ValueError):
            return 0.0

        if notional <= 0 or price <= 0:
            return 0.0
        return notional / price

    def _execute(self, decision: Decision, term: str, quantity: float, price: float) -> Decision:
        try:
            from core.execution import OrderSide

            self.execution_engine.set_price(term, price)
            order = self.execution_engine.create_order(
                asset=term,
                side=OrderSide.BUY,
                quantity=quantity,
                strategy="asymmetry-pipeline",
            )
            self.execution_engine.submit_order(order)
        except Exception as exc:  # noqa: BLE001 - one bad order must not stop the cycle
            decision.blocked_by = Blocked.EXECUTION_FAILED
            decision.detail = f"{type(exc).__name__}: {exc}"
            return decision

        decision.ordered = True
        decision.order_id = getattr(order, "order_id", None)
        if not decision.detail:
            decision.detail = "Ordered."
        return decision

    def stats(self) -> Dict[str, Any]:
        return {
            "gates": self.gate_status(),
            "last_cycle": self.last_result.to_dict() if self.last_result else None,
            "blocked": self.last_result.blocked_counts() if self.last_result else {},
        }
