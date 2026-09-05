"""Safety-critical configuration authority — ATOS-P1-CONFIG-001.

Invariant:

    Safety-critical values have one typed, validated source of truth, and any
    change to them invalidates prior live-promotion evidence.

The repository has several configuration surfaces: ``config.py``, a config
manager, orchestrator config, credential environments and runtime arguments.
Each is reasonable alone. Together they mean the answer to "what is the
maximum position concentration right now?" depends on which object you ask,
and that a value can be changed in one place while a promotion decision made
against another place still looks valid.

This module is deliberately narrow. It does not try to own every setting -
log levels and dashboard refresh intervals belong where they are. It owns the
values that decide how much money can be lost, and it does three things with
them:

* **Rejects unknown fields.** A typo like ``max_positon_concentration`` would
  otherwise be silently ignored, leaving the default in force while the
  operator believes they changed it. Silently accepting a misspelling is how
  a limit ends up being the one nobody chose.

* **Validates ranges.** A concentration limit of 5.0 is not 5%; it is 500%,
  and it should not be reachable by forgetting to divide.

* **Hashes the whole set.** Promotion evidence is bound to a hash. Change any
  safety-relevant value and the hash changes, so evidence gathered under the
  old configuration no longer applies to the new one.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, fields
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


class ConfigRejected(ValueError):
    """Configuration that cannot be accepted as safe."""


@dataclass(frozen=True)
class SafetyConfig:
    """The values that decide how much can be lost.

    Frozen: a safety limit that can be mutated after the promotion hash was
    computed is not a limit, it is a starting point.
    """

    # Mode and identity
    mode: str = "paper"                       # paper | live | backtest | research
    environment_designation: str = "development"
    broker_account_fingerprint: Optional[str] = None

    # What may be traded
    allowed_instruments: Tuple[str, ...] = ()
    allowed_asset_classes: Tuple[str, ...] = ("equity",)

    # How much
    max_capital_tier: float = 0.0
    max_risk_per_trade: float = 0.01
    max_position_concentration: float = 0.20
    max_portfolio_exposure: float = 0.50

    # What kinds of risk
    allow_shorting: bool = False
    allow_leverage: bool = False
    max_leverage: float = 1.0
    allow_options: bool = False
    allow_futures: bool = False

    # Execution policy
    execution_algo: str = "immediate"
    max_slippage_pct: float = 0.01
    order_timeout_seconds: int = 300

    # Stop and drawdown policy
    max_daily_drawdown: float = 0.05
    max_total_drawdown: float = 0.20
    max_loss_streak: int = 5

    # Market data health
    max_quote_age_seconds: float = 30.0
    max_spread_pct: float = 0.05
    max_clock_skew_seconds: float = 5.0

    # What is approved to run
    promoted_strategy_versions: Tuple[str, ...] = ()
    promoted_model_versions: Tuple[str, ...] = ()

    _VALID_MODES = ("paper", "live", "backtest", "research")
    _VALID_ENVIRONMENTS = ("development", "staging", "production")
    _VALID_ALGOS = ("immediate", "twap", "vwap", "iceberg", "smart")

    # -- construction ----------------------------------------------------

    @classmethod
    def field_names(cls) -> FrozenSet[str]:
        return frozenset(f.name for f in fields(cls))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SafetyConfig":
        """Build from a mapping, refusing anything unrecognised.

        An unknown key is an error, not a warning. The most likely cause is a
        typo in a key the operator believes they set, and continuing with the
        default silently substitutes a limit nobody chose.
        """
        if not isinstance(raw, Mapping):
            raise ConfigRejected(
                f"expected a mapping, got {type(raw).__name__}"
            )

        known = cls.field_names()
        unknown = sorted(set(raw) - known)
        if unknown:
            suggestions = []
            for key in unknown:
                near = _closest(key, known)
                suggestions.append(f"{key!r}" + (f" (did you mean {near!r}?)" if near else ""))
            raise ConfigRejected(
                "unknown safety configuration field(s): " + ", ".join(suggestions)
            )

        coerced: Dict[str, Any] = {}
        for key, value in raw.items():
            if key in {"allowed_instruments", "allowed_asset_classes",
                       "promoted_strategy_versions", "promoted_model_versions"}:
                if isinstance(value, str):
                    raise ConfigRejected(
                        f"{key} must be a sequence, not a single string; "
                        f"got {value!r}"
                    )
                coerced[key] = tuple(value)
            else:
                coerced[key] = value

        config = cls(**coerced)
        config.validate()
        return config

    # -- validation ------------------------------------------------------

    def validate(self) -> None:
        """Raise if any value is outside a safe range."""
        problems = self.problems()
        if problems:
            raise ConfigRejected(
                "unsafe safety configuration: " + "; ".join(problems)
            )

    def problems(self) -> List[str]:
        """Every reason this configuration is unsafe, not just the first."""
        found: List[str] = []

        if self.mode not in self._VALID_MODES:
            found.append(
                f"mode {self.mode!r} is not one of {self._VALID_MODES}"
            )
        if self.environment_designation not in self._VALID_ENVIRONMENTS:
            found.append(
                f"environment_designation {self.environment_designation!r} is "
                f"not one of {self._VALID_ENVIRONMENTS}"
            )
        if self.execution_algo not in self._VALID_ALGOS:
            found.append(
                f"execution_algo {self.execution_algo!r} is not one of "
                f"{self._VALID_ALGOS}"
            )

        # Fractions are fractions. 5.0 is not 5%.
        for name in ("max_risk_per_trade", "max_position_concentration",
                     "max_portfolio_exposure", "max_daily_drawdown",
                     "max_total_drawdown", "max_slippage_pct", "max_spread_pct"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                found.append(f"{name} must be a number, got {value!r}")
                continue
            if not 0 < value <= 1:
                found.append(
                    f"{name} is {value}, which is outside (0, 1]; these are "
                    "fractions, so 5% is 0.05 and not 5"
                )

        if self.max_capital_tier < 0:
            found.append(f"max_capital_tier {self.max_capital_tier} is negative")
        if self.max_leverage < 1:
            found.append(
                f"max_leverage {self.max_leverage} is below 1; unlevered is 1"
            )
        if self.max_loss_streak < 1:
            found.append(f"max_loss_streak {self.max_loss_streak} must be at least 1")
        if self.order_timeout_seconds <= 0:
            found.append("order_timeout_seconds must be positive")
        for name in ("max_quote_age_seconds", "max_clock_skew_seconds"):
            if getattr(self, name) <= 0:
                found.append(f"{name} must be positive")

        # Internal consistency.
        if self.max_position_concentration > self.max_portfolio_exposure:
            found.append(
                f"max_position_concentration {self.max_position_concentration} "
                f"exceeds max_portfolio_exposure {self.max_portfolio_exposure}; "
                "one instrument would be allowed more than the whole portfolio"
            )
        if self.max_risk_per_trade > self.max_position_concentration:
            found.append(
                f"max_risk_per_trade {self.max_risk_per_trade} exceeds "
                f"max_position_concentration {self.max_position_concentration}"
            )
        if self.max_daily_drawdown > self.max_total_drawdown:
            found.append(
                f"max_daily_drawdown {self.max_daily_drawdown} exceeds "
                f"max_total_drawdown {self.max_total_drawdown}; the daily limit "
                "could never bind"
            )
        if self.allow_leverage and self.max_leverage <= 1:
            found.append(
                "allow_leverage is set but max_leverage is 1, which permits none"
            )
        if not self.allow_leverage and self.max_leverage > 1:
            found.append(
                f"max_leverage {self.max_leverage} is set but allow_leverage is "
                "false; the two disagree"
            )

        # Live-specific requirements.
        if self.mode == "live":
            if self.environment_designation != "production":
                found.append(
                    "live mode requires environment_designation='production'"
                )
            if not self.broker_account_fingerprint:
                found.append(
                    "live mode requires a broker_account_fingerprint so the "
                    "system can refuse an unrecognised account"
                )
            if self.max_capital_tier <= 0:
                found.append(
                    "live mode requires a positive max_capital_tier; "
                    "initial capital is not spend authority"
                )
            if not self.allowed_instruments:
                found.append(
                    "live mode requires an explicit allowed_instruments list"
                )

        # Products the execution layer does not safely support.
        # ATOS-P3-EXEC-001 says these must be disabled from live until
        # contract multipliers, expiries, margin and assignment are handled.
        if self.mode == "live" and (self.allow_options or self.allow_futures):
            found.append(
                "options and futures are not supported for live execution "
                "(no contract multiplier, expiry, margin or assignment "
                "handling); see ATOS-P3-EXEC-001"
            )

        return found

    # -- identity --------------------------------------------------------

    def safety_hash(self) -> str:
        """A stable hash over every safety-critical value.

        Promotion evidence is bound to this. Change any field here and the
        hash changes, so approval gathered under the old configuration stops
        applying - which is the point.
        """
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return "cfg-" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    def differences_from(self, other: "SafetyConfig") -> Dict[str, Tuple[Any, Any]]:
        """Which safety-critical values changed, old to new."""
        mine, theirs = asdict(self), asdict(other)
        return {
            key: (theirs[key], mine[key])
            for key in mine
            if mine[key] != theirs[key]
        }

    def invalidates_promotion_of(self, other: "SafetyConfig") -> bool:
        """Whether moving from ``other`` to this config voids prior approval."""
        return self.safety_hash() != other.safety_hash()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["safety_hash"] = self.safety_hash()
        return payload


@dataclass
class PromotionEvidence:
    """A record that a specific configuration was approved for live use."""

    safety_hash: str
    approved_by: str
    approved_at: str
    note: str = ""

    def covers(self, config: SafetyConfig) -> bool:
        return self.safety_hash == config.safety_hash()


class SafetyConfigAuthority:
    """Holds the current configuration and the evidence approving it."""

    def __init__(
        self,
        config: SafetyConfig,
        evidence: Optional[List[PromotionEvidence]] = None,
    ) -> None:
        config.validate()
        self._config = config
        self._evidence: List[PromotionEvidence] = list(evidence or [])
        self.change_log: List[Dict[str, Any]] = []

    @property
    def config(self) -> SafetyConfig:
        return self._config

    @property
    def promoted_hashes(self) -> FrozenSet[str]:
        return frozenset(e.safety_hash for e in self._evidence)

    def is_promoted(self) -> bool:
        """Whether the *current* configuration has approval."""
        return any(e.covers(self._config) for e in self._evidence)

    def promote(self, approved_by: str, note: str = "") -> PromotionEvidence:
        """Record approval of the configuration as it stands right now."""
        if not approved_by:
            raise ValueError("promotion must record who approved it")
        from datetime import datetime, timezone

        evidence = PromotionEvidence(
            safety_hash=self._config.safety_hash(),
            approved_by=approved_by,
            approved_at=datetime.now(timezone.utc).isoformat(),
            note=note,
        )
        self._evidence.append(evidence)
        logger.warning(
            "Safety configuration %s promoted by %s", evidence.safety_hash,
            approved_by,
        )
        return evidence

    def replace(self, new_config: SafetyConfig, reason: str) -> None:
        """Swap in a new configuration, recording what changed.

        Promotion is not carried across. If the new hash has no evidence of
        its own, the system is unpromoted until someone approves it - even if
        the change looks innocuous, because "looks innocuous" is a judgement
        and the hash is not.
        """
        if not reason:
            raise ValueError("a configuration change must state its reason")
        new_config.validate()
        changes = new_config.differences_from(self._config)
        previous_hash = self._config.safety_hash()
        self._config = new_config

        self.change_log.append({
            "from_hash": previous_hash,
            "to_hash": new_config.safety_hash(),
            "reason": reason,
            "changes": {k: {"from": v[0], "to": v[1]} for k, v in changes.items()},
            "still_promoted": self.is_promoted(),
        })
        if changes and not self.is_promoted():
            logger.warning(
                "Safety configuration changed (%s); prior promotion evidence no "
                "longer applies. Changed: %s",
                ", ".join(sorted(changes)), previous_hash,
            )

    def report(self) -> Dict[str, Any]:
        return {
            "safety_hash": self._config.safety_hash(),
            "promoted": self.is_promoted(),
            "promoted_hashes": sorted(self.promoted_hashes),
            "config": self._config.to_dict(),
            "changes": list(self.change_log),
        }


def _closest(word: str, candidates: FrozenSet[str]) -> Optional[str]:
    """Nearest known field name, for a helpful typo message."""
    import difflib

    matches = difflib.get_close_matches(word, sorted(candidates), n=1, cutoff=0.7)
    return matches[0] if matches else None
