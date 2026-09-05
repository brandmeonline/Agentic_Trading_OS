"""Runtime state and fail-closed live startup — ATOS-P0-REC-001.

Invariant:

    A restart cannot manufacture a clean or flat portfolio from missing state.

A process that starts up with empty in-memory books and begins trading is
asserting that the broker holds nothing. It has no evidence for that. The
position it opened yesterday, the order still working overnight, the fill that
landed while it was down — none of that is in the fresh process, and none of it
stops being real.

So live startup is a gauntlet, not a constructor. Fifteen ordered checks, each
of which must produce evidence. The first one that cannot leaves the system in
FROZEN or RECOVERY_REQUIRED, where it can observe but not acquire. Reaching
LIVE_ACTIVE requires every single one to pass.

Paper mode runs the same machine with the broker-dependent checks skipped, so
the code path that reaches "running" is the same code path in both modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RuntimeState(Enum):
    """Where the system is, and therefore what it is allowed to do."""

    # Not touching real money at all.
    BACKTEST = "backtest"
    RESEARCH = "research"
    PAPER = "paper"
    PAPER_LIVE_DATA = "paper_live_data"

    # On the way to real money.
    LIVE_ARMED = "live_armed"            # authorised, not yet reconciled
    LIVE_RECONCILING = "live_reconciling"  # comparing local against broker
    LIVE_ACTIVE = "live_active"          # reconciled; may acquire

    # Something is wrong.
    FROZEN = "frozen"                    # may reduce risk, may not add it
    RECOVERY_REQUIRED = "recovery_required"  # operator must intervene
    HALTED = "halted"                    # stopped deliberately


#: States in which the system may open or increase a position.
STATES_PERMITTING_ACQUISITION = frozenset({
    RuntimeState.BACKTEST,
    RuntimeState.RESEARCH,
    RuntimeState.PAPER,
    RuntimeState.PAPER_LIVE_DATA,
    RuntimeState.LIVE_ACTIVE,
})

#: States that touch real capital.
LIVE_STATES = frozenset({
    RuntimeState.LIVE_ARMED,
    RuntimeState.LIVE_RECONCILING,
    RuntimeState.LIVE_ACTIVE,
})

#: States an operator must clear by hand.
BLOCKED_STATES = frozenset({
    RuntimeState.FROZEN,
    RuntimeState.RECOVERY_REQUIRED,
    RuntimeState.HALTED,
})


class StartupAborted(RuntimeError):
    """Raised when live startup cannot safely continue."""


@dataclass
class CheckResult:
    """The outcome of one startup check."""

    name: str
    passed: bool
    detail: str = ""
    #: Where to land if this check fails. Missing evidence about broker state
    #: needs an operator; a merely unhealthy dependency can freeze and retry.
    failure_state: RuntimeState = RuntimeState.FROZEN
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "failure_state": self.failure_state.value,
            "at": self.at,
        }


@dataclass
class StartupCheck:
    """One step of the live startup sequence.

    ``run`` returns ``(passed, detail)``. Raising is treated as failure, since
    an exception during a safety check is not evidence of safety.
    """

    name: str
    run: Callable[[], Tuple[bool, str]]
    failure_state: RuntimeState = RuntimeState.FROZEN
    #: Paper mode skips checks that only make sense against a real broker.
    live_only: bool = False
    #: Checks that protect real capital are still *run* in paper mode, and
    #: their result is reported, but a failure does not block startup. The
    #: ULTRAPLAN's fifteen steps are the live sequence; applying all of them
    #: to paper would stop the evidence runs that promotion depends on, while
    #: protecting no capital.
    advisory_in_paper: bool = False


class RuntimeStateMachine:
    """Holds the runtime state and refuses illegal moves.

    Every transition is recorded with a reason. Nothing may reach LIVE_ACTIVE
    except through LIVE_RECONCILING, and nothing leaves a blocked state
    without an explicit operator action.
    """

    _ALLOWED: Dict[RuntimeState, frozenset] = {
        RuntimeState.BACKTEST: frozenset({RuntimeState.HALTED, RuntimeState.RESEARCH}),
        RuntimeState.RESEARCH: frozenset({RuntimeState.HALTED, RuntimeState.BACKTEST}),
        RuntimeState.PAPER: frozenset({
            RuntimeState.PAPER_LIVE_DATA, RuntimeState.LIVE_ARMED,
            RuntimeState.FROZEN, RuntimeState.HALTED,
            RuntimeState.RECOVERY_REQUIRED,
        }),
        RuntimeState.PAPER_LIVE_DATA: frozenset({
            RuntimeState.PAPER, RuntimeState.LIVE_ARMED, RuntimeState.FROZEN,
            RuntimeState.HALTED, RuntimeState.RECOVERY_REQUIRED,
        }),
        RuntimeState.LIVE_ARMED: frozenset({
            RuntimeState.LIVE_RECONCILING, RuntimeState.FROZEN,
            RuntimeState.RECOVERY_REQUIRED, RuntimeState.HALTED,
        }),
        # The only door into LIVE_ACTIVE.
        RuntimeState.LIVE_RECONCILING: frozenset({
            RuntimeState.LIVE_ACTIVE, RuntimeState.FROZEN,
            RuntimeState.RECOVERY_REQUIRED, RuntimeState.HALTED,
        }),
        RuntimeState.LIVE_ACTIVE: frozenset({
            RuntimeState.FROZEN, RuntimeState.RECOVERY_REQUIRED,
            RuntimeState.LIVE_RECONCILING, RuntimeState.HALTED,
        }),
        # Leaving a blocked state means re-reconciling, never resuming.
        RuntimeState.FROZEN: frozenset({
            RuntimeState.LIVE_RECONCILING, RuntimeState.RECOVERY_REQUIRED,
            RuntimeState.HALTED, RuntimeState.PAPER,
        }),
        RuntimeState.RECOVERY_REQUIRED: frozenset({
            RuntimeState.LIVE_RECONCILING, RuntimeState.HALTED,
            RuntimeState.PAPER,
        }),
        RuntimeState.HALTED: frozenset({
            RuntimeState.PAPER, RuntimeState.RESEARCH, RuntimeState.BACKTEST,
            RuntimeState.LIVE_ARMED,
        }),
    }

    def __init__(self, initial: RuntimeState = RuntimeState.PAPER) -> None:
        self._state = initial
        self.history: List[Dict[str, Any]] = []
        self._record(None, initial, "initial state")

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def may_acquire(self) -> bool:
        """Whether the system may open or increase a position right now."""
        return self._state in STATES_PERMITTING_ACQUISITION

    @property
    def is_live(self) -> bool:
        return self._state in LIVE_STATES

    @property
    def is_blocked(self) -> bool:
        return self._state in BLOCKED_STATES

    def can_transition_to(self, new_state: RuntimeState) -> bool:
        return new_state in self._ALLOWED.get(self._state, frozenset())

    def transition_to(self, new_state: RuntimeState, reason: str) -> None:
        if not reason:
            raise ValueError("every runtime state change must state its reason")
        if new_state is self._state:
            self._record(self._state, new_state, f"no-op: {reason}")
            return
        if not self.can_transition_to(new_state):
            self._record(self._state, new_state, f"REFUSED: {reason}")
            raise StartupAborted(
                f"{self._state.name} -> {new_state.name} is not a legal runtime "
                f"transition ({reason})"
            )
        previous = self._state
        self._state = new_state
        self._record(previous, new_state, reason)
        logger.info("Runtime state %s -> %s (%s)", previous.name, new_state.name, reason)

    def freeze(self, reason: str) -> None:
        """Stop adding risk. Reducing risk stays permitted."""
        if self._state is RuntimeState.FROZEN:
            self._record(self._state, self._state, f"already frozen: {reason}")
            return
        self.transition_to(RuntimeState.FROZEN, reason)

    def require_recovery(self, reason: str) -> None:
        """Escalate to an operator. Nothing automatic clears this."""
        if self._state is RuntimeState.RECOVERY_REQUIRED:
            self._record(self._state, self._state, f"already in recovery: {reason}")
            return
        self.transition_to(RuntimeState.RECOVERY_REQUIRED, reason)

    def _record(
        self,
        previous: Optional[RuntimeState],
        new_state: RuntimeState,
        reason: str,
    ) -> None:
        self.history.append({
            "from": previous.value if previous else None,
            "to": new_state.value,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        })


@dataclass
class StartupReport:
    """What happened during a startup attempt, and where it ended."""

    final_state: RuntimeState
    checks: List[CheckResult] = field(default_factory=list)
    aborted_at: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.final_state is RuntimeState.LIVE_ACTIVE

    @property
    def failures(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_state": self.final_state.value,
            "succeeded": self.succeeded,
            "aborted_at": self.aborted_at,
            "checks": [c.to_dict() for c in self.checks],
        }


#: The ULTRAPLAN's ordered live startup sequence. The order is load-bearing:
#: authorisation is checked before the broker is touched, unresolved intents
#: are enumerated before the broker is asked about them, and reconciliation
#: happens before any risk anchor is trusted.
LIVE_STARTUP_STEPS: Tuple[str, ...] = (
    "load_validated_config",
    "verify_live_authorization",
    "initialize_durable_storage",
    "replay_local_state",
    "enumerate_unresolved_intents",
    "authenticate_broker",
    "fetch_account",
    "fetch_positions",
    "fetch_open_orders",
    "fetch_recent_fills",
    "reconcile_local_against_broker",
    "restore_risk_anchors",
    "validate_market_data_health",
    "validate_capital_tier",
)


class LiveStartupSequence:
    """Runs the startup gauntlet and lands the machine somewhere honest.

    Checks run in order and stop at the first failure. There is no partial
    success: either every check produced evidence, or the system is blocked.
    """

    def __init__(
        self,
        machine: RuntimeStateMachine,
        checks: List[StartupCheck],
        live: bool = True,
    ) -> None:
        self.machine = machine
        self.checks = checks
        self.live = live
        self._validate_coverage()

    def _validate_coverage(self) -> None:
        """Refuse a sequence that silently omits a required step.

        A startup sequence missing a step would pass while never having asked
        the question, which is the failure mode this whole issue exists to
        prevent.
        """
        if not self.live:
            return
        provided = {check.name for check in self.checks}
        missing = [step for step in LIVE_STARTUP_STEPS if step not in provided]
        if missing:
            raise StartupAborted(
                "live startup sequence is missing required checks: "
                + ", ".join(missing)
            )

    def run(self) -> StartupReport:
        report = StartupReport(final_state=self.machine.state)

        if self.live:
            self.machine.transition_to(
                RuntimeState.LIVE_ARMED, "live startup requested"
            )
            self.machine.transition_to(
                RuntimeState.LIVE_RECONCILING, "running live startup checks"
            )

        for check in self.checks:
            if check.live_only and not self.live:
                continue

            try:
                passed, detail = check.run()
            except Exception as exc:
                logger.exception("Startup check %s raised", check.name)
                passed, detail = False, (
                    f"check raised {type(exc).__name__}: {exc}"
                )

            result = CheckResult(
                name=check.name,
                passed=bool(passed),
                detail=detail,
                failure_state=check.failure_state,
            )
            report.checks.append(result)

            if not result.passed and check.advisory_in_paper and not self.live:
                logger.warning(
                    "Paper startup: %s reports %s. Not blocking - no real "
                    "capital is at risk - but this check must pass before live.",
                    check.name, detail,
                )
                continue

            if not result.passed:
                report.aborted_at = check.name
                reason = f"startup check {check.name!r} failed: {detail}"
                if check.failure_state is RuntimeState.RECOVERY_REQUIRED:
                    self.machine.require_recovery(reason)
                else:
                    self.machine.freeze(reason)
                report.final_state = self.machine.state
                logger.error(
                    "Live startup aborted at %s -> %s",
                    check.name, self.machine.state.name,
                )
                return report

        if self.live:
            self.machine.transition_to(
                RuntimeState.LIVE_ACTIVE,
                "every startup check produced evidence",
            )
        report.final_state = self.machine.state
        return report


def build_live_startup_checks(
    *,
    config_loader: Callable[[], Tuple[bool, str]],
    authorization: Callable[[], Tuple[bool, str]],
    storage: Callable[[], Tuple[bool, str]],
    replay: Callable[[], Tuple[bool, str]],
    unresolved_intents: Callable[[], Tuple[bool, str]],
    broker_auth: Callable[[], Tuple[bool, str]],
    account: Callable[[], Tuple[bool, str]],
    positions: Callable[[], Tuple[bool, str]],
    open_orders: Callable[[], Tuple[bool, str]],
    recent_fills: Callable[[], Tuple[bool, str]],
    reconciliation: Callable[[], Tuple[bool, str]],
    risk_anchors: Callable[[], Tuple[bool, str]],
    market_data: Callable[[], Tuple[bool, str]],
    capital_tier: Callable[[], Tuple[bool, str]],
) -> List[StartupCheck]:
    """Assemble the fourteen evidence checks in their required order.

    Failure states are not uniform. A missing config or an unhealthy feed can
    freeze and be retried. Anything implying we may have lost track of real
    broker state - an unresolved intent, a reconciliation mismatch - needs a
    human, so it lands in RECOVERY_REQUIRED.
    """
    recovery = RuntimeState.RECOVERY_REQUIRED
    return [
        StartupCheck("load_validated_config", config_loader),
        StartupCheck("verify_live_authorization", authorization, live_only=True),
        StartupCheck("initialize_durable_storage", storage, recovery,
                     advisory_in_paper=True),
        StartupCheck("replay_local_state", replay, recovery,
                     advisory_in_paper=True),
        StartupCheck("enumerate_unresolved_intents", unresolved_intents, recovery,
                     advisory_in_paper=True),
        StartupCheck("authenticate_broker", broker_auth, live_only=True),
        StartupCheck("fetch_account", account, live_only=True),
        StartupCheck("fetch_positions", positions, recovery, live_only=True),
        StartupCheck("fetch_open_orders", open_orders, recovery, live_only=True),
        StartupCheck("fetch_recent_fills", recent_fills, recovery, live_only=True),
        StartupCheck("reconcile_local_against_broker", reconciliation, recovery,
                     live_only=True),
        StartupCheck("restore_risk_anchors", risk_anchors, recovery,
                     advisory_in_paper=True),
        # Live trading on an unhealthy feed is how you buy a stale price.
        # Paper trading on one just produces weaker evidence.
        StartupCheck("validate_market_data_health", market_data,
                     advisory_in_paper=True),
        StartupCheck("validate_capital_tier", capital_tier, live_only=True),
    ]
