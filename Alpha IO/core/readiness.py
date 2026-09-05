"""Liveness, readiness and image hygiene — ATOS-P2-DEPLOY-001.

Invariant:

    "The process is answering" and "the system may add new risk" are different
    questions, answered by different probes, and no deployment may use the
    first as an answer to the second.

Today the container has exactly one probe: ``GET /api/v1/health``, which
returns ``{"status": "ok"}`` from a lambda. That is a perfectly good liveness
probe and a catastrophic readiness probe. A process that has lost its broker
connection, failed reconciliation, or come back from a crash with unresolved
order intents answers it cheerfully, so the orchestrator that restarted it
concludes everything is fine and a load balancer keeps sending it work.

The distinction matters in both directions, and the second direction is the
one people get wrong:

* **Readiness must never be wired to the restart policy.** A system that is
  unsafe to trade is usually *more* dangerous restarted than left alone: the
  restart discards in-memory reconciliation state and re-opens the question of
  what the broker holds. So liveness deliberately knows nothing about
  readiness. It answers "is this process wedged", nothing else.

* **Readiness is fail-closed.** A requirement nobody supplied evidence for is
  not satisfied. Not unknown-and-therefore-fine — not satisfied. The whole
  class of deployment accidents this guards against begins with a check that
  was never wired up and therefore never failed.

The third piece is the restart entry state. A process manager will restart a
crashed trading system; that is what process managers do. It must come back
into a state that forces reconciliation, never straight back into the state
that lets it trade.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One thing that must be true before the system may add risk."""

    key: str
    description: str
    #: Requirements that only bind when real capital is involved. Kept small
    #: on purpose: most of these are as necessary in paper, because paper is
    #: where the promotion evidence comes from.
    live_only: bool = False


#: The eleven the ULTRAPLAN names, in the order it names them.
READINESS_REQUIREMENTS: Tuple[Requirement, ...] = (
    Requirement("persistence_healthy",
                "critical writes are durable and the store is reachable"),
    Requirement("broker_auth_healthy",
                "broker credentials authenticate"),
    Requirement("expected_account_confirmed",
                "the broker account is the one this deployment expects"),
    Requirement("reconciliation_fresh_and_matched",
                "a recent reconciliation completed and found agreement"),
    Requirement("data_healthy",
                "market data is live, not stale, invalid or demo"),
    Requirement("no_unresolved_order_intents",
                "no order intent is left without a known outcome"),
    Requirement("no_risk_trip",
                "no risk limit is currently tripped"),
    Requirement("capital_tier_valid",
                "the capital tier is configured and within its limit"),
    Requirement("strategy_promotion_valid",
                "the running strategy and model passed promotion"),
    Requirement("event_loops_healthy",
                "the price, strategy and event threads are alive"),
    Requirement("execution_adapter_healthy",
                "the execution adapter is connected and accepting orders"),
)

REQUIREMENT_KEYS = tuple(r.key for r in READINESS_REQUIREMENTS)


@dataclass(frozen=True)
class Outcome:
    """What was established about one requirement."""

    key: str
    satisfied: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement": self.key,
            "satisfied": self.satisfied,
            "detail": self.detail,
        }


NO_EVIDENCE = "no evidence supplied"


def _coerce(value: Any) -> Tuple[bool, str]:
    """Read one piece of evidence in whatever shape the caller had.

    Accepts ``True``/``False``, ``(bool, detail)``, or a callable returning
    either. Anything else is not evidence — including a truthy string, which
    is the shape a half-finished check tends to return.
    """
    if callable(value):
        try:
            value = value()
        except Exception as exc:  # a check that raised did not pass
            return False, f"check raised {type(exc).__name__}: {exc}"

    if isinstance(value, tuple) and len(value) == 2:
        satisfied, detail = value
        return bool(satisfied), str(detail)

    if isinstance(value, bool):
        return value, "satisfied" if value else "not satisfied"

    return False, f"evidence of type {type(value).__name__} is not a result"


@dataclass
class ReadinessReport:
    """Whether the system may add new risk, and what is stopping it."""

    live: bool
    outcomes: List[Outcome] = field(default_factory=list)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def ready(self) -> bool:
        return not self.blocking()

    def blocking(self) -> List[Outcome]:
        return [o for o in self.outcomes if not o.satisfied]

    @property
    def http_status(self) -> int:
        """503 when not ready, so an orchestrator stops routing to it."""
        return 200 if self.ready else 503

    def summary(self) -> str:
        blocking = self.blocking()
        if not blocking:
            return "ready"
        return "not ready: " + "; ".join(
            f"{o.key} ({o.detail})" for o in blocking
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "live": self.live,
            "summary": self.summary(),
            "checked_at": self.checked_at.isoformat(),
            "requirements": [o.to_dict() for o in self.outcomes],
            "blocking": [o.key for o in self.blocking()],
        }

    def public_dict(self) -> Dict[str, Any]:
        """What an unauthenticated probe may see.

        A readiness endpoint has to be reachable by a process manager, which
        means unauthenticated, which means it must not describe the system's
        internal state to anyone who can reach the port. The count is enough
        for a probe; the reasons live behind the authenticated route.
        """
        return {"ready": self.ready, "blocking_count": len(self.blocking())}


def evaluate_readiness(
    evidence: Mapping[str, Any],
    live: bool = False,
    requirements: Sequence[Requirement] = READINESS_REQUIREMENTS,
) -> ReadinessReport:
    """Judge readiness from whatever evidence the caller could establish.

    Evidence is looked up by requirement key. A key that is absent is
    *unsatisfied*, and says so — this is the whole safety property, and it is
    why the function iterates the requirement list rather than the evidence.
    """
    outcomes: List[Outcome] = []
    for requirement in requirements:
        if requirement.live_only and not live:
            outcomes.append(Outcome(
                requirement.key, True, "not required outside live"
            ))
            continue
        if requirement.key not in evidence:
            outcomes.append(Outcome(requirement.key, False, NO_EVIDENCE))
            continue
        satisfied, detail = _coerce(evidence[requirement.key])
        outcomes.append(Outcome(requirement.key, satisfied, detail))

    unknown = set(evidence) - {r.key for r in requirements}
    if unknown:
        # Not fatal, but worth surfacing: evidence for a requirement that does
        # not exist is usually a typo in a key, which silently means the real
        # requirement got NO_EVIDENCE.
        logger.warning(
            "Readiness evidence supplied for unrecognised requirement(s): %s",
            ", ".join(sorted(unknown)),
        )

    return ReadinessReport(live=live, outcomes=outcomes)


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------


def liveness(
    started_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Answer "is this process wedged", and nothing else.

    Deliberately incapable of consulting readiness. A trading system that is
    unsafe to trade must not be restarted for it: the restart throws away
    whatever it had established about the broker's book and makes the
    situation worse. Liveness exists to catch a hung process, so that is all
    it reports.
    """
    now = now or datetime.now(timezone.utc)
    uptime = None
    if started_at is not None:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        uptime = (now - started_at).total_seconds()
    return {
        "alive": True,
        "uptime_seconds": uptime,
        "at": now.isoformat(),
        "note": "liveness only; this says nothing about whether the system "
                "may trade. See the readiness probe.",
    }


# ---------------------------------------------------------------------------
# Restart entry
# ---------------------------------------------------------------------------

#: A restarted process may come back into any of these. The list is a
#: whitelist rather than a blacklist of one, because a future state should
#: have to argue its way in rather than inherit permission.
PERMITTED_RESTART_STATES = frozenset({
    "backtest",
    "research",
    "paper",
    "paper_live_data",
    "live_reconciling",
    "recovery_required",
    "frozen",
    "halted",
})

#: Coming back straight into these would mean resuming trading on the strength
#: of state that did not survive the crash.
FORBIDDEN_RESTART_STATES = frozenset({"live_active", "live_armed"})


class UnsafeRestart(RuntimeError):
    """A restart that would resume trading without reconciling."""


def check_restart_state(state_value: str) -> None:
    """Raise unless a restarted process came back somewhere safe."""
    value = str(state_value).lower()
    if value in FORBIDDEN_RESTART_STATES:
        raise UnsafeRestart(
            f"a restarted process entered {value.upper()}; it must reconcile "
            "before it may add risk again"
        )
    if value not in PERMITTED_RESTART_STATES:
        raise UnsafeRestart(
            f"unrecognised restart state {value!r}; a state that has not been "
            "reviewed is not permitted to be a restart entry point"
        )


# ---------------------------------------------------------------------------
# Image hygiene: secrets injected, not baked
# ---------------------------------------------------------------------------

#: Paths that hold, or have held, credentials in this repository.
SECRET_BEARING_PATHS = (
    ".credentials",
    ".env",
    "config/",
    "secrets",
    "*.pem",
    "*.key",
)

_SECRET_NAME = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|token|passphrase|private[_-]?key"
    r"|credential)"
)

#: Assignments whose value is obviously a placeholder rather than a secret.
_PLACEHOLDER = re.compile(
    r"^(|\$\{[^}]*\}|changeme|change_me|placeholder|your[_-].*|xxx+|<.*>)$",
    re.IGNORECASE,
)


def _is_placeholder(value: str) -> bool:
    value = value.strip().strip('"').strip("'")
    return bool(_PLACEHOLDER.match(value))


def dockerfile_problems(text: str, dockerignore: str = "") -> List[str]:
    """Reasons this image would carry secrets it should be handed at runtime."""
    problems: List[str] = []
    ignored = {
        line.strip().rstrip("/")
        for line in dockerignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        upper = line.upper()
        if upper.startswith("COPY ") or upper.startswith("ADD "):
            for path in SECRET_BEARING_PATHS:
                bare = path.rstrip("/")
                if bare in line and bare not in ignored:
                    problems.append(
                        f"line {number}: copies {path} into the image; runtime "
                        "secrets must be injected, not baked"
                    )

        if upper.startswith("ENV ") or upper.startswith("ARG "):
            body = line.split(None, 1)[1] if " " in line else ""
            for assignment in re.finditer(r"([A-Za-z0-9_]+)\s*=\s*(\S*)", body):
                name, value = assignment.group(1), assignment.group(2)
                if _SECRET_NAME.search(name) and not _is_placeholder(value):
                    problems.append(
                        f"line {number}: {name} is given a value in the image"
                    )

    for path in SECRET_BEARING_PATHS:
        if path.rstrip("/") not in ignored:
            problems.append(
                f".dockerignore does not exclude {path}; a stray file there "
                "would be copied by any broad COPY"
            )

    return problems


def compose_problems(text: str) -> List[str]:
    """Reasons this compose file supplies a secret instead of requiring one."""
    problems: List[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        match = re.match(r"-?\s*([A-Za-z0-9_]+)\s*[:=]\s*(.+)$", line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if not _SECRET_NAME.search(name):
            continue

        # ${VAR:-default} supplies `default` whenever VAR is unset, which is
        # exactly the accident: the deployment comes up with a known password
        # instead of refusing to start.
        default = re.match(r"^\$\{[^:}]+:-(.*)\}$", value)
        if default and default.group(1).strip():
            problems.append(
                f"line {number}: {name} falls back to a baked-in default; use "
                "${VAR:?set this} so a missing secret fails the deployment"
            )
        elif not value.startswith("$") and not _is_placeholder(value):
            problems.append(f"line {number}: {name} is set to a literal value")

    return problems
