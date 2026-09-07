"""Explicit live activation authority — ATOS-P0-AUTH-001.

Invariant:

    A caller cannot enter LIVE by passing ``mode="live"`` alone.

Credentials prove capability, not permission. A string in a config file is a
preference, not an authorisation. Neither is evidence that a human decided to
put real money at risk right now, on this build, against this account.

So live activation is a conjunction: every one of the fifteen conditions below
must hold, and each must be *positively* established. A condition that cannot
be evaluated is not satisfied — the whole point is that silence never grants
permission.

The gate is pure and returns a decision listing exactly which conditions
failed, so an operator is told what to fix rather than being left to guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Credential sources that may be used for live trading. A file inside the
#: repository is never one of them, however convenient.
APPROVED_CREDENTIAL_SOURCES = frozenset({
    "environment",
    "secret_manager",
    "keyring",
    "ci_secret",
})

#: The fifteen conditions, in the ULTRAPLAN's order.
REQUIRED_CONDITIONS: tuple = (
    "explicit_live_flag",
    "human_risk_acknowledgement",
    "production_environment_designation",
    "valid_unexpired_live_credential",
    "approved_credential_source",
    "durable_database_healthy",
    "successful_state_replay",
    "broker_reconciliation_matched",
    "market_data_healthy",
    "promotion_evidence_for_config_hash",
    "capital_tier_present_and_positive",
    "no_active_risk_trip",
    "no_unresolved_order_state",
    "account_fingerprint_matches",
    "session_id_persisted",
)


@dataclass
class LiveAuthorizationRequest:
    """Everything the gate is allowed to consider.

    Defaults are all "not established". Constructing this object with no
    arguments and asking for permission must be refused, which is the
    property that makes ``mode="live"`` alone insufficient.
    """

    # 1-3: the human decision
    explicit_live_flag: bool = False
    human_risk_acknowledgement: Optional[str] = None
    environment_designation: Optional[str] = None

    # 4-5: credentials
    credential_expires_at: Optional[datetime] = None
    credential_present: bool = False
    credential_source: Optional[str] = None

    # 6-9: system health
    database_healthy: bool = False
    state_replay_succeeded: bool = False
    reconciliation_matched: bool = False
    market_data_healthy: bool = False

    # 10-11: what is authorised to run, and with how much
    config_hash: Optional[str] = None
    promoted_config_hashes: frozenset = frozenset()
    capital_tier_limit: Optional[float] = None

    # 12-13: outstanding problems
    active_risk_trips: List[str] = field(default_factory=list)
    unresolved_order_ids: List[str] = field(default_factory=list)

    # 14-15: identity and audit
    expected_account_fingerprint: Optional[str] = None
    broker_account_fingerprint: Optional[str] = None
    session_id: Optional[str] = None
    session_id_persisted: bool = False

    #: The exact phrase a human must type. Kept here so the caller cannot
    #: quietly weaken it to something a script would produce by accident.
    required_acknowledgement: str = "I UNDERSTAND THIS TRADES REAL MONEY"


@dataclass
class AuthorizationDecision:
    """Whether live is permitted, and if not, precisely why."""

    authorized: bool
    satisfied: List[str] = field(default_factory=list)
    failures: Dict[str, str] = field(default_factory=dict)
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def first_failure(self) -> Optional[str]:
        for condition in REQUIRED_CONDITIONS:
            if condition in self.failures:
                return condition
        return None

    def summary(self) -> str:
        if self.authorized:
            return "LIVE AUTHORIZED"
        return (
            f"LIVE REFUSED ({len(self.failures)} of {len(REQUIRED_CONDITIONS)} "
            f"conditions unmet); first: {self.first_failure}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "authorized": self.authorized,
            "summary": self.summary(),
            "decided_at": self.decided_at.isoformat(),
            "satisfied": list(self.satisfied),
            "failures": dict(self.failures),
        }


class LiveAuthorizationGate:
    """Evaluates the fifteen conditions. Every one must hold."""

    def __init__(self, now: Optional[datetime] = None) -> None:
        self._now = now

    def _clock(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    def authorize(self, request: LiveAuthorizationRequest) -> AuthorizationDecision:
        failures: Dict[str, str] = {}
        satisfied: List[str] = []

        for condition in REQUIRED_CONDITIONS:
            ok, reason = getattr(self, f"_check_{condition}")(request)
            if ok:
                satisfied.append(condition)
            else:
                failures[condition] = reason

        decision = AuthorizationDecision(
            authorized=not failures,
            satisfied=satisfied,
            failures=failures,
        )
        if decision.authorized:
            logger.warning(
                "LIVE TRADING AUTHORIZED for account %s, session %s, capital tier %s",
                request.broker_account_fingerprint, request.session_id,
                request.capital_tier_limit,
            )
        else:
            logger.info("Live authorization refused: %s", decision.summary())
        return decision

    # -- the fifteen conditions ------------------------------------------

    def _check_explicit_live_flag(self, r):
        if not r.explicit_live_flag:
            return False, (
                "no explicit live flag. A mode string is a preference, not an "
                "instruction to risk money"
            )
        return True, ""

    def _check_human_risk_acknowledgement(self, r):
        if not r.human_risk_acknowledgement:
            return False, "no human risk acknowledgement was given"
        if r.human_risk_acknowledgement.strip() != r.required_acknowledgement:
            return False, (
                "the risk acknowledgement does not match the required phrase "
                "exactly; a near miss is not a confirmation"
            )
        return True, ""

    def _check_production_environment_designation(self, r):
        if not r.environment_designation:
            return False, (
                "no environment designation. Auto-detection is prohibited: it "
                "can point at production by accident"
            )
        if r.environment_designation != "production":
            return False, (
                f"environment is {r.environment_designation!r}, not 'production'"
            )
        return True, ""

    def _check_valid_unexpired_live_credential(self, r):
        if not r.credential_present:
            return False, "no live credential reference is available"
        if r.credential_expires_at is None:
            return False, (
                "the credential has no known expiry; an unknown expiry cannot "
                "be proved unexpired"
            )
        expires = r.credential_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= self._clock():
            return False, f"the live credential expired at {expires.isoformat()}"
        return True, ""

    def _check_approved_credential_source(self, r):
        if not r.credential_source:
            return False, "the credential source is unknown"
        if r.credential_source not in APPROVED_CREDENTIAL_SOURCES:
            return False, (
                f"credential source {r.credential_source!r} is not approved for "
                f"live use (approved: {', '.join(sorted(APPROVED_CREDENTIAL_SOURCES))})"
            )
        return True, ""

    def _check_durable_database_healthy(self, r):
        if not r.database_healthy:
            return False, "the durable database is not healthy"
        return True, ""

    def _check_successful_state_replay(self, r):
        if not r.state_replay_succeeded:
            return False, "local state was not successfully replayed"
        return True, ""

    def _check_broker_reconciliation_matched(self, r):
        if not r.reconciliation_matched:
            return False, "broker reconciliation is not MATCHED"
        return True, ""

    def _check_market_data_healthy(self, r):
        if not r.market_data_healthy:
            return False, "market data is not healthy"
        return True, ""

    def _check_promotion_evidence_for_config_hash(self, r):
        if not r.config_hash:
            return False, "the running config has no hash, so it cannot be promoted"
        if r.config_hash not in r.promoted_config_hashes:
            return False, (
                f"config hash {r.config_hash} has no promotion evidence; a "
                "safety-relevant change invalidates prior approval"
            )
        return True, ""

    def _check_capital_tier_present_and_positive(self, r):
        if r.capital_tier_limit is None:
            return False, (
                "no capital tier is present. initial_capital is a number, not "
                "spend authority"
            )
        if r.capital_tier_limit <= 0:
            return False, f"the capital tier is {r.capital_tier_limit}, which is not positive"
        return True, ""

    def _check_no_active_risk_trip(self, r):
        if r.active_risk_trips:
            return False, f"active risk trips: {', '.join(r.active_risk_trips)}"
        return True, ""

    def _check_no_unresolved_order_state(self, r):
        if r.unresolved_order_ids:
            shown = ", ".join(r.unresolved_order_ids[:5])
            return False, (
                f"{len(r.unresolved_order_ids)} unresolved order(s): {shown}"
            )
        return True, ""

    def _check_account_fingerprint_matches(self, r):
        if not r.expected_account_fingerprint:
            return False, "no expected account fingerprint is configured"
        if not r.broker_account_fingerprint:
            return False, "the broker did not report an account fingerprint"
        if r.expected_account_fingerprint != r.broker_account_fingerprint:
            return False, (
                "the connected broker account is not the expected one; "
                "refusing to trade an unrecognised account"
            )
        return True, ""

    def _check_session_id_persisted(self, r):
        if not r.session_id:
            return False, "no session ID was generated"
        if not r.session_id_persisted:
            return False, (
                "the session ID was not persisted, so this activation would "
                "leave no durable audit record"
            )
        return True, ""


def refuse_live(reason: str) -> AuthorizationDecision:
    """A pre-built refusal, for callers that cannot even assemble a request."""
    return AuthorizationDecision(
        authorized=False,
        failures={condition: reason for condition in REQUIRED_CONDITIONS},
    )
