"""Model governance for the RL stack — ATOS-P3-ML-001.

Invariant:

    A learned policy may influence real capital only as a named, hashed,
    reproducible artifact that was promoted on evidence, and never as the
    live-updating thing that produced it.

An RL agent is the one component in this system that changes its own
behaviour. Everything else can be reviewed once and trusted until edited; a
policy network edits itself every update step, which makes three ordinary
practices insufficient:

* **Reviewing the code does not review the model.** The weights are the
  behaviour. So the artifact is hashed, and the hash covers the weights, the
  observation schema, the config and the seed - everything that determines
  what it will do.

* **"The agent is doing well" is not evidence.** Doing well on the data it
  learned from is the definition of the failure. Promotion requires
  out-of-sample results across more than one regime, and the registry will not
  serve a model that does not have them.

* **A guard inside the policy is not a guard.** A catastrophic-action check
  implemented as a penalty term is subject to the same optimisation that
  produced the action. The guard here sits outside the network, reads the
  action after the fact, and cannot be trained around.

The last piece is the split. Training, validation and test indices are
allocated once, with a purge between them, and the guard records every index
each phase actually touched. Overlap is not reported as a warning at the end;
it is a violation that fails the run, because the whole value of a test set is
that it was never seen.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation schema
# ---------------------------------------------------------------------------


class SchemaMismatch(ValueError):
    """A model is being fed observations it was not trained on."""


@dataclass(frozen=True)
class ObservationSchema:
    """What the model expects to see, in order, and under what version.

    The ordering matters as much as the membership: a model handed the same
    features in a different order is being handed different features, and
    nothing about the shape of the array will say so. That is the failure this
    exists to catch - ``state_dim: int = 50`` accepts any fifty numbers.
    """

    feature_names: Tuple[str, ...]
    version: str
    #: The extra state the environment appends (position, balance, and so on).
    appended_state: Tuple[str, ...] = ()

    @property
    def width(self) -> int:
        return len(self.feature_names) + len(self.appended_state)

    def fingerprint(self) -> str:
        payload = json.dumps({
            "features": list(self.feature_names),
            "appended": list(self.appended_state),
            "version": self.version,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def compatibility_problems(self, other: "ObservationSchema") -> List[str]:
        """Why observations shaped like ``other`` must not be fed to this."""
        problems: List[str] = []
        if self.fingerprint() == other.fingerprint():
            return problems

        mine, theirs = list(self.feature_names), list(other.feature_names)
        missing = [f for f in mine if f not in theirs]
        extra = [f for f in theirs if f not in mine]
        if missing:
            problems.append("missing feature(s): " + ", ".join(missing))
        if extra:
            problems.append("unexpected feature(s): " + ", ".join(extra))
        if not missing and not extra and mine != theirs:
            problems.append(
                "the same features in a different order, which is a different "
                "input even though the array is the same width"
            )
        if self.appended_state != other.appended_state:
            problems.append(
                f"appended state differs: {self.appended_state} vs "
                f"{other.appended_state}"
            )
        if self.version != other.version and not problems:
            problems.append(
                f"schema version {other.version} is not {self.version}"
            )
        return problems

    def assert_compatible(self, other: "ObservationSchema") -> None:
        problems = self.compatibility_problems(other)
        if problems:
            raise SchemaMismatch("; ".join(problems))


# ---------------------------------------------------------------------------
# Immutable splits
# ---------------------------------------------------------------------------


class SplitViolation(AssertionError):
    """A phase touched data allocated to another phase."""


class Phase(Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


@dataclass(frozen=True)
class DataSplit:
    """Train, validation and test ranges, with a purge between each.

    Frozen, and built by :func:`make_split` rather than assembled by hand, so
    the gaps cannot be closed by a later edit that "just needed a few more
    training bars".
    """

    train: Tuple[int, int]        # [start, end)
    validation: Tuple[int, int]
    test: Tuple[int, int]
    purge: int

    def indices(self, phase: Phase) -> range:
        start, end = getattr(self, phase.value)
        return range(start, end)

    def gaps(self) -> Tuple[int, int]:
        return (
            self.validation[0] - self.train[1],
            self.test[0] - self.validation[1],
        )

    def problems(self) -> List[str]:
        problems: List[str] = []
        for name, (start, end) in (
            ("train", self.train), ("validation", self.validation),
            ("test", self.test),
        ):
            if end <= start:
                problems.append(f"{name} range {start}:{end} is empty")
        for gap, between in zip(self.gaps(), ("train/validation",
                                              "validation/test")):
            if gap < self.purge:
                problems.append(
                    f"only {gap} bar(s) between {between}, and {self.purge} "
                    "are required to keep a feature lookback out of the next "
                    "phase"
                )
        return problems

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train": list(self.train),
            "validation": list(self.validation),
            "test": list(self.test),
            "purge": self.purge,
            "gaps": list(self.gaps()),
        }


def make_split(
    n_bars: int,
    train_frac: float = 0.6,
    validation_frac: float = 0.2,
    purge: int = 50,
) -> DataSplit:
    """Allocate the three ranges once, with the purge taken out of each."""
    if not 0 < train_frac < 1 or not 0 < validation_frac < 1:
        raise ValueError("fractions must be between 0 and 1")
    if train_frac + validation_frac >= 1:
        raise ValueError("train and validation leave nothing for test")

    train_end = int(n_bars * train_frac)
    validation_start = train_end + purge
    validation_end = validation_start + int(n_bars * validation_frac)
    test_start = validation_end + purge

    split = DataSplit(
        train=(0, train_end),
        validation=(validation_start, validation_end),
        test=(test_start, n_bars),
        purge=purge,
    )
    problems = split.problems()
    if problems:
        raise ValueError(
            f"{n_bars} bars cannot be split this way: " + "; ".join(problems)
        )
    return split


class SplitGuard:
    """Records which indices each phase touched, and refuses overlap.

    The check is not "did the code look correct"; it is "which rows were
    actually read". A loader that quietly widened a slice, an off-by-one that
    reached one bar past the boundary, a validation pass that re-used the
    training generator - all produce the same symptom here, and none of them
    is visible in a diff.
    """

    def __init__(self, split: DataSplit) -> None:
        self.split = split
        self.touched: Dict[Phase, Set[int]] = {p: set() for p in Phase}

    def observe(self, phase: Phase, indices: Iterable[int]) -> None:
        self.touched[phase].update(int(i) for i in indices)

    def violations(self) -> List[str]:
        problems: List[str] = []
        for phase in Phase:
            allowed = set(self.split.indices(phase))
            outside = sorted(self.touched[phase] - allowed)
            if outside:
                problems.append(
                    f"{phase.value} read {len(outside)} index/indices outside "
                    f"its range (first: {outside[0]}, last: {outside[-1]})"
                )
        for left in Phase:
            for right in Phase:
                if left.value >= right.value:
                    continue
                shared = self.touched[left] & self.touched[right]
                if shared:
                    problems.append(
                        f"{left.value} and {right.value} both read "
                        f"{len(shared)} index/indices"
                    )
        return problems

    def assert_clean(self) -> None:
        problems = self.violations()
        if problems:
            raise SplitViolation("; ".join(problems))


# ---------------------------------------------------------------------------
# Artifacts and promotion
# ---------------------------------------------------------------------------


def weights_digest(params: Any) -> str:
    """A content hash of a model's parameters.

    Walks whatever nested structure of arrays and numbers it is given, so it
    works for the MLP parameter lists in this repository without importing
    them. Two models with the same digest behave identically; two with
    different digests are different models however they are labelled.
    """
    hasher = hashlib.sha256()

    def absorb(value: Any) -> None:
        if hasattr(value, "tobytes") and hasattr(value, "shape"):
            hasher.update(str(value.shape).encode("utf-8"))
            hasher.update(str(getattr(value, "dtype", "")).encode("utf-8"))
            hasher.update(value.tobytes())
        elif isinstance(value, dict):
            for key in sorted(value, key=str):
                hasher.update(str(key).encode("utf-8"))
                absorb(value[key])
        elif isinstance(value, (list, tuple)):
            for item in value:
                absorb(item)
        else:
            hasher.update(repr(value).encode("utf-8"))

    absorb(params)
    return hasher.hexdigest()[:16]


def config_digest(config: Any) -> str:
    """A stable hash of a configuration object or mapping."""
    if hasattr(config, "__dict__") and not isinstance(config, dict):
        payload = {k: repr(v) for k, v in sorted(vars(config).items())}
    elif isinstance(config, dict):
        payload = {str(k): repr(v) for k, v in sorted(config.items(), key=str)}
    else:
        payload = {"value": repr(config)}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


class ModelStage(Enum):
    """How far a model has got. Only one of these may touch real capital."""

    TRAINING = "training"
    #: Runs alongside production, its proposals recorded and discarded.
    SHADOW = "shadow"
    #: Passed shadow, awaiting promotion evidence.
    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    RETIRED = "retired"


@dataclass(frozen=True)
class ModelArtifact:
    """A specific model, identified by what it will do rather than its name."""

    model_id: str
    algorithm: str
    schema: ObservationSchema
    seed: Optional[int]
    weights: str                      # digest
    config: str                       # digest
    split: Optional[DataSplit] = None
    trained_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def fingerprint(self) -> str:
        """The identity that matters: change any of these and it is a new model."""
        payload = "|".join([
            self.algorithm, self.schema.fingerprint(), str(self.seed),
            self.weights, self.config,
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def reproducible(self) -> bool:
        """Whether this model could be rebuilt.

        An unseeded model cannot be. It may be a perfectly good model; it is
        not an auditable one, and it must not be promoted.
        """
        return self.seed is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "algorithm": self.algorithm,
            "schema_version": self.schema.version,
            "schema_fingerprint": self.schema.fingerprint(),
            "seed": self.seed,
            "weights_digest": self.weights,
            "config_digest": self.config,
            "fingerprint": self.fingerprint(),
            "reproducible": self.reproducible,
            "split": self.split.to_dict() if self.split else None,
            "trained_at": self.trained_at.isoformat(),
        }


@dataclass(frozen=True)
class RegimeResult:
    """Out-of-sample performance in one market regime."""

    regime: str
    sharpe: float
    max_drawdown: float
    trades: int


#: A model promoted on one regime has been shown to work in one regime.
MINIMUM_REGIMES = 3
#: Below this, the result is noise.
MINIMUM_TRADES_PER_REGIME = 20


@dataclass
class PromotionEvidence:
    """What a model must show before it is allowed near real capital."""

    artifact: ModelArtifact
    regime_results: List[RegimeResult] = field(default_factory=list)
    shadow_sessions: int = 0
    approved_by: str = ""
    #: The reward function must charge costs, or the results are fiction.
    costs_modelled: bool = False
    #: Independent guard active during the evaluation.
    guard_active: bool = False

    def problems(self) -> List[str]:
        problems: List[str] = []

        if not self.artifact.reproducible:
            problems.append(
                "the model has no seed, so its training cannot be reproduced "
                "and its results cannot be checked"
            )
        if self.artifact.split is None:
            problems.append("no train/validation/test split is recorded")
        elif self.artifact.split.problems():
            problems.append(
                "the recorded split is invalid: "
                + "; ".join(self.artifact.split.problems())
            )

        usable = [r for r in self.regime_results
                  if r.trades >= MINIMUM_TRADES_PER_REGIME]
        regimes = {r.regime for r in usable}
        if len(regimes) < MINIMUM_REGIMES:
            problems.append(
                f"evaluated in {len(regimes)} regime(s) with at least "
                f"{MINIMUM_TRADES_PER_REGIME} trades; {MINIMUM_REGIMES} are "
                "required, because a model promoted on one regime has been "
                "shown to work in one regime"
            )
        losing = [r.regime for r in usable if r.sharpe <= 0]
        if losing:
            problems.append(
                "negative or zero out-of-sample Sharpe in: "
                + ", ".join(sorted(losing))
            )

        if not self.costs_modelled:
            problems.append(
                "the reward function did not charge transaction costs, so "
                "these results describe a market that does not exist"
            )
        if not self.guard_active:
            problems.append(
                "the catastrophic-action guard was not active during "
                "evaluation, so the results include actions that would be "
                "refused in production"
            )
        if self.shadow_sessions < 1:
            problems.append("the model has not run a shadow session")
        if not self.approved_by:
            problems.append("no human has approved this promotion")

        return problems

    @property
    def sufficient(self) -> bool:
        return not self.problems()


class NotPromoted(PermissionError):
    """A model that may not influence real capital was asked to."""


class ModelRegistry:
    """Which models exist, what stage each is at, and which may serve.

    ``may_serve_live`` is the only question this answers that matters, and it
    defaults to no. A model absent from the registry is not promoted; that is
    the same answer as a model that failed promotion, and deliberately so.
    """

    def __init__(self) -> None:
        self._stages: Dict[str, ModelStage] = {}
        self._artifacts: Dict[str, ModelArtifact] = {}
        self._evidence: Dict[str, PromotionEvidence] = {}
        self.audit: List[Dict[str, Any]] = []

    def register(self, artifact: ModelArtifact,
                 stage: ModelStage = ModelStage.TRAINING) -> None:
        if stage is ModelStage.PROMOTED:
            raise NotPromoted(
                "a model cannot be registered as promoted; promotion requires "
                "evidence, so it goes through promote()"
            )
        self._artifacts[artifact.model_id] = artifact
        self._stages[artifact.model_id] = stage
        self._record(artifact.model_id, "register", stage.value, "")

    def promote(self, evidence: PromotionEvidence) -> None:
        model_id = evidence.artifact.model_id
        problems = evidence.problems()
        if problems:
            self._record(model_id, "promote", "refused", "; ".join(problems))
            raise NotPromoted(
                f"{model_id} may not be promoted: " + "; ".join(problems)
            )
        self._artifacts[model_id] = evidence.artifact
        self._evidence[model_id] = evidence
        self._stages[model_id] = ModelStage.PROMOTED
        self._record(model_id, "promote", "promoted", evidence.approved_by)
        logger.warning("Model %s promoted to live by %s",
                       model_id, evidence.approved_by)

    def retire(self, model_id: str, reason: str) -> None:
        self._stages[model_id] = ModelStage.RETIRED
        self._record(model_id, "retire", "retired", reason)

    def stage(self, model_id: str) -> ModelStage:
        return self._stages.get(model_id, ModelStage.TRAINING)

    def may_serve_live(self, model_id: str) -> Tuple[bool, str]:
        if model_id not in self._stages:
            return False, f"{model_id} is not in the registry"
        stage = self._stages[model_id]
        if stage is not ModelStage.PROMOTED:
            return False, f"{model_id} is {stage.value}, not promoted"
        return True, ""

    def require_live(self, model_id: str) -> ModelArtifact:
        allowed, reason = self.may_serve_live(model_id)
        if not allowed:
            raise NotPromoted(reason)
        return self._artifacts[model_id]

    def _record(self, model_id: str, action: str, outcome: str,
                detail: str) -> None:
        self.audit.append({
            "model_id": model_id,
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })


# ---------------------------------------------------------------------------
# Guards that live outside the network
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionLimits:
    """What any policy is allowed to ask for, whatever it learned."""

    max_position_units: float = 1.0
    max_notional: float = 10_000.0
    #: Turnover as a multiple of equity per session.
    max_turnover: float = 5.0
    permitted_actions: Tuple[int, ...] = (0, 1, 2)


class CatastrophicAction(PermissionError):
    """An action a policy asked for that will not be executed."""


class CatastrophicActionGuard:
    """Refuses actions outside the limits, independently of the policy.

    Outside on purpose. A guard expressed as a penalty term in the reward is
    part of the objective the network is optimising, and a network that finds
    the penalty worth paying will pay it. This one reads the action after the
    network has chosen and does not care why.
    """

    def __init__(self, limits: Optional[ActionLimits] = None) -> None:
        self.limits = limits or ActionLimits()
        self.refusals: List[Dict[str, Any]] = []
        self.turnover = 0.0

    def check(
        self,
        action: int,
        intended_position: float,
        notional: float,
        equity: float,
        risk_tripped: bool = False,
        reducing: bool = False,
    ) -> List[str]:
        problems: List[str] = []

        if action not in self.limits.permitted_actions:
            problems.append(
                f"action {action} is outside the permitted action space "
                f"{self.limits.permitted_actions}"
            )
        if abs(intended_position) > self.limits.max_position_units + 1e-12:
            problems.append(
                f"position {intended_position} exceeds the "
                f"{self.limits.max_position_units} unit limit"
            )
        if abs(notional) > self.limits.max_notional + 1e-12:
            problems.append(
                f"notional {abs(notional):,.2f} exceeds the "
                f"{self.limits.max_notional:,.2f} limit"
            )
        if risk_tripped and not reducing:
            problems.append(
                "a risk limit is tripped, so only risk-reducing actions are "
                "permitted"
            )
        if equity > 0:
            projected = (self.turnover + abs(notional)) / equity
            if projected > self.limits.max_turnover:
                problems.append(
                    f"turnover would reach {projected:.1f}x equity, over the "
                    f"{self.limits.max_turnover}x session limit"
                )
        return problems

    def permit(self, **kwargs: Any) -> None:
        """Raise on a refused action; count the turnover of a permitted one."""
        problems = self.check(**kwargs)
        if problems:
            self.refusals.append({"action": kwargs.get("action"),
                                  "reasons": problems})
            logger.warning("Guard refused action %s: %s",
                           kwargs.get("action"), "; ".join(problems))
            raise CatastrophicAction("; ".join(problems))
        self.turnover += abs(float(kwargs.get("notional", 0.0)))

    def reset_session(self) -> None:
        self.turnover = 0.0


# ---------------------------------------------------------------------------
# Distribution shift
# ---------------------------------------------------------------------------


@dataclass
class ShiftReport:
    """How far the live inputs have drifted from the training inputs."""

    max_z: float
    drifted_features: List[str]
    threshold: float

    @property
    def shifted(self) -> bool:
        return bool(self.drifted_features)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_z": self.max_z,
            "drifted_features": list(self.drifted_features),
            "threshold": self.threshold,
            "shifted": self.shifted,
        }


class DistributionShiftDetector:
    """Compares live observations against the training distribution.

    A policy asked about inputs unlike anything it trained on is extrapolating,
    and its confidence is not evidence about how good the extrapolation is. The
    response is to fall back to shadow - keep producing proposals, stop acting
    on them - rather than to keep trading and hope.
    """

    def __init__(
        self,
        feature_names: Sequence[str],
        means: Sequence[float],
        stds: Sequence[float],
        threshold: float = 4.0,
    ) -> None:
        if not (len(feature_names) == len(means) == len(stds)):
            raise ValueError("feature names, means and stds must line up")
        self.feature_names = list(feature_names)
        self.means = [float(m) for m in means]
        self.stds = [float(s) if abs(float(s)) > 1e-12 else 1.0 for s in stds]
        self.threshold = threshold

    def inspect(self, observation: Sequence[float]) -> ShiftReport:
        if len(observation) != len(self.feature_names):
            raise SchemaMismatch(
                f"observation has {len(observation)} values but the training "
                f"distribution describes {len(self.feature_names)}"
            )
        drifted: List[str] = []
        worst = 0.0
        for name, value, mean, std in zip(
            self.feature_names, observation, self.means, self.stds
        ):
            z = abs((float(value) - mean) / std)
            worst = max(worst, z)
            if z > self.threshold:
                drifted.append(name)
        return ShiftReport(max_z=worst, drifted_features=drifted,
                           threshold=self.threshold)

    def eligibility(self, observation: Sequence[float]) -> Tuple[bool, str]:
        """Whether a proposal from this observation may act, and why not."""
        report = self.inspect(observation)
        if report.shifted:
            return False, (
                "inputs are outside the training distribution ("
                + ", ".join(report.drifted_features)
                + f"; max z={report.max_z:.1f}); falling back to shadow"
            )
        return True, ""


# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RewardCosts:
    """What a trade costs the reward. Defaults charge.

    Units matter here and are easy to get wrong. ``pnl``, ``drawdown`` and
    ``recent_volatility`` are all in the same currency as the account; the
    percentages below apply to traded notional; and the two penalties are
    dimensionless multipliers on the currency amounts. So a drawdown penalty
    of 0.01 charges one percent of the current drawdown on every step - small
    enough not to swamp the PnL signal, persistent enough that sitting in a
    drawdown is not free.
    """

    commission_pct: float = 0.0005
    half_spread_pct: float = 0.0005
    slippage_pct: float = 0.0005
    #: Charged per step on the current drawdown, in account currency.
    drawdown_penalty: float = 0.01
    #: Charged per step on the recent volatility of the reward stream.
    volatility_penalty: float = 0.05

    def round_trip_pct(self) -> float:
        return 2.0 * (self.commission_pct + self.half_spread_pct
                      + self.slippage_pct)

    def free(self) -> bool:
        return self.round_trip_pct() == 0.0


def risk_adjusted_reward(
    pnl: float,
    traded_notional: float,
    drawdown: float,
    recent_volatility: float,
    costs: Optional[RewardCosts] = None,
) -> float:
    """PnL, minus what trading it cost and what carrying it risked.

    A reward of pure PnL teaches a policy that a 40% drawdown on the way to a
    41% return is a good trade, and that turning over the book every bar is
    free. Both are how a backtested agent becomes an unusable one.
    """
    costs = costs or RewardCosts()
    one_way = costs.commission_pct + costs.half_spread_pct + costs.slippage_pct
    transaction_cost = abs(traded_notional) * one_way
    risk_penalty = (
        abs(drawdown) * costs.drawdown_penalty
        + abs(recent_volatility) * costs.volatility_penalty
    )
    return pnl - transaction_cost - risk_penalty
