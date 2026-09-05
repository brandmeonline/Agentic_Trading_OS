"""Auto-tuner governance — ATOS-P3-TUNE-001.

Invariant:

    Production parameters never change because recent performance improved,
    and no tuner may ever raise a hard risk, capital or leverage ceiling.

``core/auto_tuner.py`` did exactly the thing this issue forbids. Its rule was

    if win_rate > 0.65:
        risk += 0.005

which is: the last few trades went well, so risk more. That is not tuning, it
is a martingale with a spreadsheet, and it is at its most confident precisely
when a run of luck is about to end. The same function raised risk again on
positive average PnL, computed both from the trade log it had just been judged
on, and returned neutral values when the log was missing so it "tuned" on
nothing at all.

The fix is not a better formula. It is that a tuner proposes and something
else decides:

* **A proposal may tighten freely and loosen never.** Tightening on bad news
  is the one direction that is safe to automate, because the failure mode of
  being too careful is opportunity cost. Loosening is the direction that
  loses money, and it is exactly the direction a performance-chasing rule
  wants to go.

* **Ceilings live in the promoted SafetyConfig, not in the tuner.** The tuner
  cannot raise them because it cannot reach them; a proposal is checked
  against them, and a proposal that exceeds one is refused rather than
  clamped, so nobody can mistake a truncated proposal for an accepted one.

* **The evidence must be untouched.** A parameter set fitted on a window and
  evaluated on the same window has been evaluated on nothing. The out-of-
  sample window is recorded on the proposal and checked for overlap with the
  fitting window.

* **Promotion is a lifecycle, not an assignment.** CANDIDATE, SHADOW,
  OOS_VALIDATED, PROMOTED, ROLLED_BACK, RETIRED, with only the transitions
  that make sense, so nothing arrives in production without having been
  somewhere else first.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TuningStage(Enum):
    """Where a parameter set is in its life. Section 25's six, exactly."""

    CANDIDATE = "candidate"
    SHADOW = "shadow"
    OOS_VALIDATED = "oos_validated"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


#: The only legal moves. Written as a whitelist so a new stage has to argue
#: its way in rather than inherit permission from a missing check.
_ALLOWED_TRANSITIONS: Dict[TuningStage, frozenset] = {
    TuningStage.CANDIDATE: frozenset({TuningStage.SHADOW, TuningStage.RETIRED}),
    # No path from SHADOW straight to PROMOTED: shadow shows a parameter set
    # behaves, out-of-sample evidence shows it works, and they are different
    # claims.
    TuningStage.SHADOW: frozenset({TuningStage.OOS_VALIDATED,
                                   TuningStage.RETIRED}),
    TuningStage.OOS_VALIDATED: frozenset({TuningStage.PROMOTED,
                                          TuningStage.RETIRED}),
    TuningStage.PROMOTED: frozenset({TuningStage.ROLLED_BACK,
                                     TuningStage.RETIRED}),
    # A rolled-back set starts again from the beginning. Whatever went wrong
    # in production is not evidence that it is still shadow-clean.
    TuningStage.ROLLED_BACK: frozenset({TuningStage.CANDIDATE,
                                        TuningStage.RETIRED}),
    TuningStage.RETIRED: frozenset(),
}


class IllegalTransition(ValueError):
    """A stage change that skips a step."""


def transition_problems(current: TuningStage, target: TuningStage) -> List[str]:
    if target is current:
        return []
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        return [
            f"{current.value} -> {target.value} is not a legal tuning "
            f"transition; permitted: "
            + ", ".join(sorted(t.value for t in _ALLOWED_TRANSITIONS[current]))
            or "none"
        ]
    return []


# ---------------------------------------------------------------------------
# Parameters and ceilings
# ---------------------------------------------------------------------------


#: Parameters where a *larger* value means more risk, so a tuner increasing
#: one is loosening a limit. Mapped to the SafetyConfig field that caps it.
LOOSENING_UPWARD: Dict[str, str] = {
    "risk_per_trade": "max_risk_per_trade",
    "position_concentration": "max_position_concentration",
    "portfolio_exposure": "max_portfolio_exposure",
    "leverage": "max_leverage",
    "capital_tier": "max_capital_tier",
    "daily_drawdown": "max_daily_drawdown",
    "total_drawdown": "max_total_drawdown",
    "loss_streak": "max_loss_streak",
    "slippage_pct": "max_slippage_pct",
    "quote_age_seconds": "max_quote_age_seconds",
}

#: Parameters where a *smaller* value means more risk - a lower confidence
#: threshold trades more, so lowering it loosens.
LOOSENING_DOWNWARD: Tuple[str, ...] = ("min_confidence",)


@dataclass(frozen=True)
class ParameterSet:
    """A candidate set of tunable values.

    Frozen, and hashed, because the artifact is what was evaluated. A
    parameter set that can be edited after its evidence was gathered has no
    evidence.
    """

    values: Mapping[str, float]
    label: str = ""

    def __post_init__(self) -> None:
        for name, value in self.values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} is {value!r}, which is not a number")

    def artifact_hash(self) -> str:
        payload = json.dumps(
            {k: float(v) for k, v in sorted(self.values.items())},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def get(self, name: str, default: Optional[float] = None) -> Optional[float]:
        value = self.values.get(name, default)
        return None if value is None else float(value)

    def differences_from(self, other: "ParameterSet") -> Dict[str, Tuple[Any, Any]]:
        names = set(self.values) | set(other.values)
        return {
            name: (other.values.get(name), self.values.get(name))
            for name in sorted(names)
            if other.values.get(name) != self.values.get(name)
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "values": {k: float(v) for k, v in sorted(self.values.items())},
            "artifact_hash": self.artifact_hash(),
        }


def loosening_problems(
    proposed: ParameterSet,
    current: ParameterSet,
    ceilings: Optional[Any] = None,
) -> List[str]:
    """Every way this proposal takes more risk than the one it replaces.

    Two separate checks, and both matter. The first is relative: has anything
    moved in the direction that risks more? The second is absolute: does
    anything exceed the promoted ceiling? A proposal can pass one and fail the
    other - tightening from an already-illegal value, or loosening slightly
    while still inside a generous cap.
    """
    problems: List[str] = []

    for name in sorted(set(proposed.values) & set(current.values)):
        new, old = proposed.get(name), current.get(name)
        if new is None or old is None:
            continue
        if name in LOOSENING_UPWARD and new > old:
            problems.append(
                f"{name} raised from {old} to {new}; a tuner may tighten a "
                "limit but never loosen one"
            )
        if name in LOOSENING_DOWNWARD and new < old:
            problems.append(
                f"{name} lowered from {old} to {new}, which trades more; a "
                "tuner may tighten but never loosen"
            )

    if ceilings is not None:
        for name, ceiling_field in sorted(LOOSENING_UPWARD.items()):
            value = proposed.get(name)
            ceiling = getattr(ceilings, ceiling_field, None)
            if value is None or ceiling is None:
                continue
            if value > float(ceiling):
                problems.append(
                    f"{name} {value} exceeds the promoted {ceiling_field} of "
                    f"{ceiling}; ceilings live in the safety config and a "
                    "tuner cannot reach them"
                )

    return problems


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """A half-open bar range, so overlap is unambiguous."""

    start: int
    end: int

    def overlaps(self, other: "Window") -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)


#: Shadow sessions before out-of-sample evidence counts for anything.
MINIMUM_SHADOW_SESSIONS = 3
#: Out-of-sample bars below which the result is noise.
MINIMUM_OOS_BARS = 250


@dataclass
class TuningEvidence:
    """What a parameter set must show before it may reach production."""

    proposed: ParameterSet
    current: ParameterSet
    fit_window: Optional[Window] = None
    oos_window: Optional[Window] = None
    oos_sharpe: Optional[float] = None
    incumbent_oos_sharpe: Optional[float] = None
    shadow_sessions: int = 0
    approved_by: str = ""
    ceilings: Optional[Any] = None

    def problems(self) -> List[str]:
        problems = loosening_problems(self.proposed, self.current, self.ceilings)

        if self.fit_window is None or self.oos_window is None:
            problems.append(
                "no fitting and evaluation windows recorded, so there is no "
                "way to tell whether the evidence is out of sample"
            )
        else:
            if self.fit_window.overlaps(self.oos_window):
                problems.append(
                    f"the evaluation window {self.oos_window.start}:"
                    f"{self.oos_window.end} overlaps the fitting window "
                    f"{self.fit_window.start}:{self.fit_window.end}; a "
                    "parameter set evaluated on the data it was fitted to has "
                    "been evaluated on nothing"
                )
            if self.oos_window.start < self.fit_window.end:
                problems.append(
                    "the evaluation window starts before the fitting window "
                    "ends; evidence must come from after the fit, not around it"
                )
            if self.oos_window.length < MINIMUM_OOS_BARS:
                problems.append(
                    f"{self.oos_window.length} out-of-sample bar(s); "
                    f"{MINIMUM_OOS_BARS} are needed before the result is "
                    "distinguishable from noise"
                )

        if self.oos_sharpe is None:
            problems.append("no out-of-sample result recorded")
        elif self.incumbent_oos_sharpe is None:
            problems.append(
                "no incumbent result to compare against; 'better' is a "
                "comparison and needs both halves"
            )
        elif self.oos_sharpe <= self.incumbent_oos_sharpe:
            problems.append(
                f"out-of-sample Sharpe {self.oos_sharpe:.2f} does not beat the "
                f"incumbent's {self.incumbent_oos_sharpe:.2f}; the default is "
                "to keep the champion"
            )

        if self.shadow_sessions < MINIMUM_SHADOW_SESSIONS:
            problems.append(
                f"{self.shadow_sessions} shadow session(s); "
                f"{MINIMUM_SHADOW_SESSIONS} are required"
            )
        if not self.approved_by:
            problems.append("no human has approved this promotion")

        return problems

    @property
    def sufficient(self) -> bool:
        return not self.problems()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposed": self.proposed.to_dict(),
            "current": self.current.to_dict(),
            "fit_window": [self.fit_window.start, self.fit_window.end]
            if self.fit_window else None,
            "oos_window": [self.oos_window.start, self.oos_window.end]
            if self.oos_window else None,
            "oos_sharpe": self.oos_sharpe,
            "incumbent_oos_sharpe": self.incumbent_oos_sharpe,
            "shadow_sessions": self.shadow_sessions,
            "approved_by": self.approved_by,
            "problems": self.problems(),
        }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


class TuningRefused(PermissionError):
    """A tuning change that will not be applied."""


#: A change to any of these invalidates the running strategy's promotion,
#: because the strategy was approved with them at their previous values.
MATERIAL_PARAMETERS = frozenset({
    "risk_per_trade", "min_confidence", "position_concentration",
    "portfolio_exposure", "leverage", "capital_tier", "daily_drawdown",
    "total_drawdown", "loss_streak", "execution_algo",
})


def invalidates_strategy_promotion(
    proposed: ParameterSet, current: ParameterSet
) -> List[str]:
    """Which material parameters moved.

    Any of them means the running strategy's promotion no longer describes
    what is running, and it has to be re-approved. This is deliberately not
    limited to loosening: a strategy approved at 1% risk per trade was not
    approved at 0.2%, and a much tighter limit changes its behaviour just as
    surely.
    """
    return [
        name for name in sorted(MATERIAL_PARAMETERS)
        if proposed.get(name) != current.get(name)
        and (name in proposed.values or name in current.values)
    ]


class TuningRegistry:
    """Tracks parameter sets through the lifecycle and holds the champion.

    Only one set is PROMOTED at a time, and promoting a new one rolls the old
    one back rather than dropping it, so the previous champion is always a
    named artifact somebody can return to.
    """

    def __init__(self, champion: ParameterSet,
                 ceilings: Optional[Any] = None) -> None:
        self._champion = champion
        self.ceilings = ceilings
        self._stages: Dict[str, TuningStage] = {
            champion.artifact_hash(): TuningStage.PROMOTED
        }
        self._sets: Dict[str, ParameterSet] = {
            champion.artifact_hash(): champion
        }
        self.audit: List[Dict[str, Any]] = []
        self.strategy_promotion_valid = True

    # -- state ------------------------------------------------------------

    @property
    def champion(self) -> ParameterSet:
        return self._champion

    def stage(self, parameters: ParameterSet) -> TuningStage:
        return self._stages.get(parameters.artifact_hash(),
                                TuningStage.CANDIDATE)

    # -- lifecycle ---------------------------------------------------------

    def register_candidate(self, parameters: ParameterSet) -> None:
        digest = parameters.artifact_hash()
        self._sets[digest] = parameters
        self._stages.setdefault(digest, TuningStage.CANDIDATE)
        self._record(digest, "register", TuningStage.CANDIDATE.value, "")

    def advance(self, parameters: ParameterSet, target: TuningStage,
                reason: str = "") -> None:
        """Move one step. Promotion is not done here; it needs evidence."""
        if target is TuningStage.PROMOTED:
            raise TuningRefused(
                "promotion requires evidence, so it goes through promote()"
            )
        digest = parameters.artifact_hash()
        current = self.stage(parameters)
        problems = transition_problems(current, target)
        if problems:
            self._record(digest, "advance", "refused", "; ".join(problems))
            raise IllegalTransition("; ".join(problems))
        self._sets[digest] = parameters
        self._stages[digest] = target
        self._record(digest, "advance", target.value, reason)

    def promote(self, evidence: TuningEvidence) -> ParameterSet:
        """Replace the champion, if the evidence supports it."""
        proposed = evidence.proposed
        digest = proposed.artifact_hash()

        if evidence.ceilings is None:
            evidence.ceilings = self.ceilings

        problems = list(evidence.problems())
        current_stage = self.stage(proposed)
        problems.extend(
            transition_problems(current_stage, TuningStage.PROMOTED)
        )
        if evidence.current.artifact_hash() != self._champion.artifact_hash():
            problems.append(
                "the evidence compares against a set that is not the current "
                "champion, so it does not say whether this is an improvement "
                "on what is running"
            )

        if problems:
            self._record(digest, "promote", "refused", "; ".join(problems))
            raise TuningRefused("; ".join(problems))

        previous = self._champion
        self._stages[previous.artifact_hash()] = TuningStage.ROLLED_BACK
        self._sets[digest] = proposed
        self._stages[digest] = TuningStage.PROMOTED
        self._champion = proposed

        material = invalidates_strategy_promotion(proposed, previous)
        if material:
            self.strategy_promotion_valid = False
            logger.warning(
                "Tuning changed %s; the running strategy's promotion is no "
                "longer valid and must be re-approved",
                ", ".join(material),
            )

        self._record(digest, "promote", "promoted",
                     f"approved by {evidence.approved_by}; material: "
                     + (", ".join(material) or "none"))
        return proposed

    def roll_back(self, to: ParameterSet, reason: str) -> ParameterSet:
        """Return to a previously promoted set.

        The target must be one the registry has seen. Rolling back to a set
        nobody evaluated is not a rollback, it is an untested change made in
        a hurry, which is the worst moment for one.
        """
        digest = to.artifact_hash()
        if digest not in self._sets:
            raise TuningRefused(
                f"parameter set {digest} is unknown to the registry; a "
                "rollback target must be something that was promoted before"
            )
        self._stages[self._champion.artifact_hash()] = TuningStage.ROLLED_BACK
        self._champion = self._sets[digest]
        self._stages[digest] = TuningStage.PROMOTED
        self.strategy_promotion_valid = False
        self._record(digest, "roll_back", "promoted", reason)
        return self._champion

    def revalidate_strategy_promotion(self, approved_by: str) -> None:
        if not approved_by:
            raise TuningRefused("re-approval needs a name")
        self.strategy_promotion_valid = True
        self._record(self._champion.artifact_hash(), "revalidate",
                     "approved", approved_by)

    # -- reporting ---------------------------------------------------------

    def _record(self, digest: str, action: str, outcome: str,
                detail: str) -> None:
        self.audit.append({
            "artifact_hash": digest,
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def report(self) -> Dict[str, Any]:
        return {
            "champion": self._champion.to_dict(),
            "strategy_promotion_valid": self.strategy_promotion_valid,
            "stages": {h: s.value for h, s in sorted(self._stages.items())},
            "audit": list(self.audit[-20:]),
        }


# ---------------------------------------------------------------------------
# What a tuner is allowed to produce
# ---------------------------------------------------------------------------


@dataclass
class TuningSuggestion:
    """A tuner's output: a proposal and its reasoning, never an assignment."""

    parameters: ParameterSet
    reasons: List[str] = field(default_factory=list)
    #: Set when the tuner declined to change anything, which is the common
    #: and correct outcome.
    unchanged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameters": self.parameters.to_dict(),
            "reasons": list(self.reasons),
            "unchanged": self.unchanged,
        }
