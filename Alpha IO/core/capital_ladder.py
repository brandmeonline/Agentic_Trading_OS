"""The capital promotion ladder — ATOS-P3-CAP-001.

Invariant:

    Spend authority is a persisted tier that was granted by a named owner
    against evidence. ``initial_capital`` is a number in a config file and
    grants nothing.

This is the difference between a system that could lose $10 and one that
could lose whatever was typed into a field. Today's orchestrator takes
``initial_capital: float = 100000.0`` from a dataclass default and treats it
as how much there is to trade. Nobody authorised that; it is the value
somebody picked while writing the class.

Four properties do the work:

* **Tiers cannot be skipped.** L0 to L7, one step at a time. Skipping is how
  a system that has never placed a live order ends up at the $1,000 rung,
  and the ladder exists precisely because each rung's evidence is about the
  rung below it having held.

* **Promotion needs a named owner.** Not a flag, not an environment variable
  - a person's identifier, recorded next to what they approved. Automated
  promotion of spend authority is the thing this issue exists to prevent.

* **A breach freezes, and may demote.** Exceeding the tier's ceiling is not
  a warning. It means the controls that were supposed to keep exposure under
  it did not, and the response is to stop rather than to raise the ceiling to
  fit - which is the direction a system under pressure wants to move.

* **The tier is bound to a configuration hash.** A tier was granted to a
  specific set of safety limits and a specific strategy. Change either and
  the evidence describes something that is no longer running, so the tier
  needs re-approval before it authorises anything.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


class CapitalTier(Enum):
    """The eight rungs from section 27, in order."""

    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5
    L6 = 6
    L7 = 7

    @property
    def max_capital(self) -> float:
        return _TIER_CAPITAL[self]

    @property
    def evidence_required(self) -> str:
        return _TIER_EVIDENCE[self]

    @property
    def is_live(self) -> bool:
        return self.max_capital > 0


_TIER_CAPITAL: Dict[CapitalTier, float] = {
    CapitalTier.L0: 0.0,
    CapitalTier.L1: 10.0,
    CapitalTier.L2: 25.0,
    CapitalTier.L3: 50.0,
    CapitalTier.L4: 100.0,
    CapitalTier.L5: 250.0,
    CapitalTier.L6: 500.0,
    CapitalTier.L7: 1000.0,
}

_TIER_EVIDENCE: Dict[CapitalTier, str] = {
    CapitalTier.L0: "research, backtest and paper only",
    CapitalTier.L1: "all P0 and P1 safety gates, plus broker reconciliation",
    CapitalTier.L2: "a clean supervised lifecycle sample",
    CapitalTier.L3: "timeout, cancel and crash drills pass",
    CapitalTier.L4: "an extended supervised period with zero unexplained "
                    "reconciliation mismatches",
    CapitalTier.L5: "positive out-of-sample net-of-cost strategy evidence",
    CapitalTier.L6: "stable execution shortfall and external alerting",
    CapitalTier.L7: "independent review and operational maturity",
}


def next_tier(tier: CapitalTier) -> Optional[CapitalTier]:
    return CapitalTier(tier.value + 1) if tier.value < CapitalTier.L7.value else None


class TierRefused(PermissionError):
    """A change to spend authority that will not be applied."""


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass
class TierEvidence:
    """What a promotion to one specific tier must show.

    The per-tier requirements are checked against the tier being promoted
    *to*, so promoting to L3 asks about drills and promoting to L5 asks about
    out-of-sample results. A single "approved: true" would let the same
    evidence justify every rung.
    """

    target: CapitalTier
    approved_by: str = ""
    safety_config_hash: str = ""
    strategy_hash: str = ""

    #: L1
    p0_p1_gates_passed: bool = False
    reconciliation_clean: bool = False
    #: L2
    supervised_lifecycle_samples: int = 0
    #: L3
    timeout_drill_passed: bool = False
    cancel_drill_passed: bool = False
    crash_drill_passed: bool = False
    #: L4
    supervised_sessions: int = 0
    unexplained_mismatches: int = 0
    #: L5
    oos_net_of_cost_sharpe: Optional[float] = None
    #: L6
    execution_shortfall_bps: Optional[float] = None
    external_alerting_verified: bool = False
    #: L7
    independent_review_by: str = ""

    def problems(self) -> List[str]:
        problems: List[str] = []

        if not self.approved_by:
            problems.append(
                "no owner has authorised this promotion; spend authority is "
                "granted by a person, not by a config value"
            )
        if not self.safety_config_hash:
            problems.append(
                "no safety config hash recorded, so the tier would not be "
                "bound to the limits it was granted against"
            )
        if not self.strategy_hash:
            problems.append("no strategy hash recorded")

        target = self.target
        if target.value >= CapitalTier.L1.value:
            if not self.p0_p1_gates_passed:
                problems.append("the P0 and P1 safety gates have not passed")
            if not self.reconciliation_clean:
                problems.append("broker reconciliation is not clean")
        if target.value >= CapitalTier.L2.value:
            if self.supervised_lifecycle_samples < 1:
                problems.append(
                    "no clean supervised order lifecycle has been observed"
                )
        if target.value >= CapitalTier.L3.value:
            missing = [
                name for name, passed in (
                    ("timeout", self.timeout_drill_passed),
                    ("cancel", self.cancel_drill_passed),
                    ("crash", self.crash_drill_passed),
                ) if not passed
            ]
            if missing:
                problems.append(
                    "drills not passed: " + ", ".join(missing)
                )
        if target.value >= CapitalTier.L4.value:
            if self.supervised_sessions < 10:
                problems.append(
                    f"{self.supervised_sessions} supervised session(s); an "
                    "extended period means at least 10"
                )
            if self.unexplained_mismatches:
                problems.append(
                    f"{self.unexplained_mismatches} unexplained reconciliation "
                    "mismatch(es); the requirement is zero"
                )
        if target.value >= CapitalTier.L5.value:
            if self.oos_net_of_cost_sharpe is None:
                problems.append("no out-of-sample net-of-cost result")
            elif self.oos_net_of_cost_sharpe <= 0:
                problems.append(
                    f"out-of-sample net-of-cost Sharpe "
                    f"{self.oos_net_of_cost_sharpe:.2f} is not positive"
                )
        if target.value >= CapitalTier.L6.value:
            if self.execution_shortfall_bps is None:
                problems.append("execution shortfall has not been measured")
            if not self.external_alerting_verified:
                problems.append(
                    "external alerting has not been verified end to end"
                )
        if target.value >= CapitalTier.L7.value:
            if not self.independent_review_by:
                problems.append("no independent review")
            elif self.independent_review_by == self.approved_by:
                problems.append(
                    "the independent review is by the same person who "
                    "approved the promotion, which is not independent"
                )

        return problems

    @property
    def sufficient(self) -> bool:
        return not self.problems()


# ---------------------------------------------------------------------------
# Durable state
# ---------------------------------------------------------------------------


@dataclass
class LadderState:
    """The persisted grant. This, not a config field, is spend authority."""

    tier: CapitalTier = CapitalTier.L0
    approved_by: str = ""
    approved_at: Optional[datetime] = None
    safety_config_hash: str = ""
    strategy_hash: str = ""
    frozen: bool = False
    freeze_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.name,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "safety_config_hash": self.safety_config_hash,
            "strategy_hash": self.strategy_hash,
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LadderState":
        approved_at = raw.get("approved_at")
        return cls(
            tier=CapitalTier[raw.get("tier", "L0")],
            approved_by=raw.get("approved_by", ""),
            approved_at=datetime.fromisoformat(approved_at) if approved_at else None,
            safety_config_hash=raw.get("safety_config_hash", ""),
            strategy_hash=raw.get("strategy_hash", ""),
            frozen=bool(raw.get("frozen", False)),
            freeze_reason=raw.get("freeze_reason", ""),
        )


class LadderStore:
    """Reads and writes the grant, atomically.

    A partially written grant is worse than none: a truncated file that parses
    as L0 is safe, and one that parses as L7 is not, so the write is a
    temp-file-and-rename and a file that will not parse reads as L0 with a
    loud complaint rather than as whatever survived.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> LadderState:
        if not self.path.is_file():
            return LadderState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return LadderState.from_dict(raw)
        except Exception as exc:
            logger.error(
                "Capital ladder state at %s is unreadable (%s); treating it "
                "as L0. Spend authority is not something to guess at.",
                self.path, exc,
            )
            return LadderState()

    def save(self, state: LadderState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".ladder-", suffix=".json"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(state.to_dict(), stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise


# ---------------------------------------------------------------------------
# The ladder itself
# ---------------------------------------------------------------------------


class CapitalLadder:
    """Holds the current grant and the rules for changing it."""

    def __init__(
        self,
        store: Optional[LadderStore] = None,
        state: Optional[LadderState] = None,
    ) -> None:
        self.store = store
        self.state = state or (store.load() if store else LadderState())
        self.audit: List[Dict[str, Any]] = []

    # -- what is authorised ------------------------------------------------

    @property
    def tier(self) -> CapitalTier:
        return self.state.tier

    @property
    def max_capital_at_risk(self) -> float:
        """Zero while frozen. A freeze is not a smaller allowance."""
        if self.state.frozen:
            return 0.0
        return self.state.tier.max_capital

    def authority_problems(
        self,
        safety_config_hash: str = "",
        strategy_hash: str = "",
    ) -> List[str]:
        """Reasons the recorded grant does not authorise anything right now."""
        problems: List[str] = []
        if self.state.frozen:
            problems.append(
                f"capital is frozen: {self.state.freeze_reason or 'no reason recorded'}"
            )
        if self.state.tier is CapitalTier.L0:
            problems.append("tier L0 authorises no real capital")
            return problems
        if not self.state.approved_by:
            problems.append("the recorded tier has no approver")
        if safety_config_hash and self.state.safety_config_hash != safety_config_hash:
            problems.append(
                "the safety config has changed since this tier was granted; "
                "the evidence describes limits that are no longer running"
            )
        if strategy_hash and self.state.strategy_hash != strategy_hash:
            problems.append(
                "the strategy has changed since this tier was granted"
            )
        return problems

    def may_risk(
        self,
        amount: float,
        safety_config_hash: str = "",
        strategy_hash: str = "",
    ) -> Tuple[bool, str]:
        """Whether this much real capital is authorised."""
        problems = self.authority_problems(safety_config_hash, strategy_hash)
        if problems:
            return False, "; ".join(problems)
        if amount > self.max_capital_at_risk + 1e-9:
            return False, (
                f"{amount:,.2f} exceeds the {self.state.tier.name} ceiling of "
                f"{self.max_capital_at_risk:,.2f}"
            )
        return True, ""

    # -- changing it -------------------------------------------------------

    def promote(self, evidence: TierEvidence) -> CapitalTier:
        """One rung, with evidence, by a named owner."""
        target = evidence.target
        expected = next_tier(self.state.tier)

        problems: List[str] = []
        if self.state.frozen:
            problems.append(
                "capital is frozen; clear the freeze before promoting, and "
                "clearing it is its own decision"
            )
        if expected is None:
            problems.append(f"{self.state.tier.name} is the top of the ladder")
        elif target is not expected:
            problems.append(
                f"cannot promote from {self.state.tier.name} to {target.name}; "
                f"the next rung is {expected.name} and rungs are not skipped, "
                "because each one's evidence is about the one below it holding"
            )
        problems.extend(evidence.problems())

        if problems:
            self._record("promote", target.name, "refused", "; ".join(problems))
            raise TierRefused("; ".join(problems))

        self.state = LadderState(
            tier=target,
            approved_by=evidence.approved_by,
            approved_at=datetime.now(timezone.utc),
            safety_config_hash=evidence.safety_config_hash,
            strategy_hash=evidence.strategy_hash,
        )
        self._persist()
        self._record("promote", target.name, "granted", evidence.approved_by)
        logger.warning(
            "Capital tier promoted to %s (%.2f) by %s",
            target.name, target.max_capital, evidence.approved_by,
        )
        return target

    def demote(self, reason: str, to: Optional[CapitalTier] = None) -> CapitalTier:
        """Down is always allowed, and may skip rungs.

        The asymmetry is deliberate. Going up needs evidence about the rung
        below; going down needs only a reason, because the failure mode of
        demoting too far is opportunity cost.
        """
        if not reason:
            raise TierRefused("a demotion must state its reason")
        target = to if to is not None else CapitalTier(
            max(0, self.state.tier.value - 1)
        )
        if target.value > self.state.tier.value:
            raise TierRefused(
                f"{target.name} is above the current {self.state.tier.name}; "
                "that is a promotion and needs evidence"
            )
        self.state = LadderState(
            tier=target,
            approved_by=self.state.approved_by,
            approved_at=self.state.approved_at,
            safety_config_hash=self.state.safety_config_hash,
            strategy_hash=self.state.strategy_hash,
            frozen=self.state.frozen,
            freeze_reason=self.state.freeze_reason,
        )
        self._persist()
        self._record("demote", target.name, "demoted", reason)
        logger.warning("Capital tier demoted to %s: %s", target.name, reason)
        return target

    def record_breach(self, amount: float, reason: str = "") -> None:
        """A breach freezes, and demotes.

        Exceeding the ceiling means the controls that were supposed to keep
        exposure under it did not. Raising the ceiling to fit is the direction
        a system under pressure wants to move, so the ladder moves the other
        way and stops.
        """
        detail = (
            f"exposure {amount:,.2f} exceeded the {self.state.tier.name} "
            f"ceiling of {self.state.tier.max_capital:,.2f}"
            + (f": {reason}" if reason else "")
        )
        previous = self.state.tier
        self.state.frozen = True
        self.state.freeze_reason = detail
        self._persist()
        self._record("breach", previous.name, "frozen", detail)
        logger.error("Capital breach: %s", detail)

        if previous is not CapitalTier.L0:
            self.demote(f"capital breach at {previous.name}")

    def clear_freeze(self, cleared_by: str, note: str = "") -> None:
        if not cleared_by:
            raise TierRefused("clearing a freeze needs a name")
        self.state.frozen = False
        self.state.freeze_reason = ""
        self._persist()
        self._record("clear_freeze", self.state.tier.name, "cleared",
                     f"{cleared_by}: {note}")

    def rebind(self, safety_config_hash: str, strategy_hash: str,
               approved_by: str) -> None:
        """Re-approve the tier against a changed configuration."""
        if not approved_by:
            raise TierRefused("rebinding a tier needs an approver")
        self.state.safety_config_hash = safety_config_hash
        self.state.strategy_hash = strategy_hash
        self.state.approved_by = approved_by
        self._persist()
        self._record("rebind", self.state.tier.name, "rebound", approved_by)

    # -- plumbing ----------------------------------------------------------

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save(self.state)

    def _record(self, action: str, tier: str, outcome: str,
                detail: str) -> None:
        self.audit.append({
            "action": action,
            "tier": tier,
            "outcome": outcome,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def report(self) -> Dict[str, Any]:
        return {
            "tier": self.state.tier.name,
            "max_capital_at_risk": self.max_capital_at_risk,
            "evidence_required": self.state.tier.evidence_required,
            "frozen": self.state.frozen,
            "freeze_reason": self.state.freeze_reason,
            "approved_by": self.state.approved_by,
            "authority_problems": self.authority_problems(),
            "audit": list(self.audit[-20:]),
        }


def spend_authority(
    ladder: Optional[CapitalLadder], initial_capital: float
) -> Tuple[float, str]:
    """How much may actually be risked, given a config's initial_capital.

    The answer is never ``initial_capital``. With no ladder there is no grant
    and the answer is zero; with one, it is the tier's ceiling, which the
    config number can lower but never raise.
    """
    if ladder is None:
        return 0.0, (
            "no capital ladder is configured; initial_capital is a number in "
            "a config file and grants no spend authority"
        )
    ceiling = ladder.max_capital_at_risk
    problems = ladder.authority_problems()
    if problems:
        return 0.0, "; ".join(problems)
    allowed = min(float(initial_capital), ceiling)
    if allowed < float(initial_capital):
        return allowed, (
            f"initial_capital {initial_capital:,.2f} is above the "
            f"{ladder.tier.name} ceiling of {ceiling:,.2f}; the ceiling wins"
        )
    return allowed, ""
