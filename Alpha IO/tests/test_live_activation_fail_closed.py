"""Explicit live activation authority — ATOS-P0-AUTH-001.

Invariant:

    A caller cannot enter LIVE by passing ``mode="live"`` alone.

Fifteen conditions, all required. The tests below prove each one is
individually load-bearing, and that the four prohibited shortcuts — mode
string, credentials existing, falling back from a failed sandbox, and
environment auto-detection — cannot open the door.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.live_authorization import (  # noqa: E402
    APPROVED_CREDENTIAL_SOURCES,
    REQUIRED_CONDITIONS,
    LiveAuthorizationGate,
    LiveAuthorizationRequest,
    refuse_live,
)

pytestmark = pytest.mark.adversarial

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
ACK = "I UNDERSTAND THIS TRADES REAL MONEY"
FINGERPRINT = "acct-abc123"


def gate():
    return LiveAuthorizationGate(now=NOW)


def full_request(**overrides):
    """A request with all fifteen conditions satisfied."""
    base = dict(
        explicit_live_flag=True,
        human_risk_acknowledgement=ACK,
        environment_designation="production",
        credential_present=True,
        credential_expires_at=NOW + timedelta(days=30),
        credential_source="environment",
        database_healthy=True,
        state_replay_succeeded=True,
        reconciliation_matched=True,
        market_data_healthy=True,
        config_hash="cfg-deadbeef",
        promoted_config_hashes=frozenset({"cfg-deadbeef"}),
        capital_tier_limit=10.0,
        active_risk_trips=[],
        unresolved_order_ids=[],
        expected_account_fingerprint=FINGERPRINT,
        broker_account_fingerprint=FINGERPRINT,
        session_id="sess-1",
        session_id_persisted=True,
    )
    base.update(overrides)
    return LiveAuthorizationRequest(**base)


# ---------------------------------------------------------------------------
# The headline guarantee
# ---------------------------------------------------------------------------

#: Two conditions are phrased negatively - "no active risk trip", "no
#: unresolved order state" - so an empty default genuinely satisfies them.
#: Every condition that requires positive evidence must fail by default.
NEGATIVE_CONDITIONS = frozenset({"no_active_risk_trip", "no_unresolved_order_state"})
POSITIVE_CONDITIONS = frozenset(REQUIRED_CONDITIONS) - NEGATIVE_CONDITIONS


def test_a_default_request_is_refused():
    """Constructing a request and asking nicely must not work."""
    decision = gate().authorize(LiveAuthorizationRequest())

    assert not decision.authorized
    assert POSITIVE_CONDITIONS <= set(decision.failures), (
        "every condition requiring positive evidence must fail by default; "
        f"these did not: {POSITIVE_CONDITIONS - set(decision.failures)}"
    )
    assert set(decision.satisfied) <= NEGATIVE_CONDITIONS, (
        "nothing requiring evidence may be satisfied by an empty request"
    )


def test_mode_live_alone_is_not_authorization():
    """The prohibited shortcut, stated directly."""
    decision = gate().authorize(LiveAuthorizationRequest(explicit_live_flag=True))
    assert not decision.authorized
    assert "human_risk_acknowledgement" in decision.failures


def test_credentials_existing_is_not_authorization():
    """Capability is not permission."""
    decision = gate().authorize(LiveAuthorizationRequest(
        credential_present=True,
        credential_expires_at=NOW + timedelta(days=30),
        credential_source="environment",
    ))
    assert not decision.authorized
    assert "explicit_live_flag" in decision.failures


def test_full_evidence_is_authorized():
    decision = gate().authorize(full_request())
    assert decision.authorized, decision.failures
    assert decision.failures == {}
    assert len(decision.satisfied) == len(REQUIRED_CONDITIONS)
    assert decision.summary() == "LIVE AUTHORIZED"


# ---------------------------------------------------------------------------
# Every condition is individually load-bearing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("condition,override", [
    ("explicit_live_flag", {"explicit_live_flag": False}),
    ("human_risk_acknowledgement", {"human_risk_acknowledgement": None}),
    ("production_environment_designation", {"environment_designation": None}),
    ("valid_unexpired_live_credential", {"credential_present": False}),
    ("approved_credential_source", {"credential_source": None}),
    ("durable_database_healthy", {"database_healthy": False}),
    ("successful_state_replay", {"state_replay_succeeded": False}),
    ("broker_reconciliation_matched", {"reconciliation_matched": False}),
    ("market_data_healthy", {"market_data_healthy": False}),
    ("promotion_evidence_for_config_hash", {"promoted_config_hashes": frozenset()}),
    ("capital_tier_present_and_positive", {"capital_tier_limit": None}),
    ("no_active_risk_trip", {"active_risk_trips": ["daily_loss"]}),
    ("no_unresolved_order_state", {"unresolved_order_ids": ["atos-1"]}),
    ("account_fingerprint_matches", {"broker_account_fingerprint": "other-acct"}),
    ("session_id_persisted", {"session_id_persisted": False}),
])
def test_each_condition_blocks_on_its_own(condition, override):
    decision = gate().authorize(full_request(**override))
    assert not decision.authorized, f"{condition} did not block live activation"
    assert condition in decision.failures
    assert decision.failures[condition], "a refusal must explain itself"


def test_the_condition_list_matches_the_ultraplan_count():
    assert len(REQUIRED_CONDITIONS) == 15
    assert len(set(REQUIRED_CONDITIONS)) == 15


# ---------------------------------------------------------------------------
# The acknowledgement has to be real
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "", "yes", "y", "I understand", "i understand this trades real money",
    "I UNDERSTAND", "I UNDERSTAND THIS TRADES REAL MONEY PLEASE",
])
def test_a_near_miss_acknowledgement_is_refused(phrase):
    decision = gate().authorize(full_request(human_risk_acknowledgement=phrase))
    assert not decision.authorized
    assert "human_risk_acknowledgement" in decision.failures


def test_surrounding_whitespace_is_forgiven():
    decision = gate().authorize(
        full_request(human_risk_acknowledgement=f"  {ACK}\n")
    )
    assert decision.authorized


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_an_expired_credential_is_refused():
    decision = gate().authorize(
        full_request(credential_expires_at=NOW - timedelta(seconds=1))
    )
    assert not decision.authorized
    assert "expired" in decision.failures["valid_unexpired_live_credential"]


def test_a_credential_with_unknown_expiry_is_refused():
    """An unknown expiry cannot be proved unexpired."""
    decision = gate().authorize(full_request(credential_expires_at=None))
    assert not decision.authorized
    assert "no known expiry" in decision.failures["valid_unexpired_live_credential"]


def test_a_repository_file_is_never_an_approved_source():
    """ATOS-P0-SEC-001 is why."""
    for source in ("repository_file", "config_file", "hardcoded", "checked_in"):
        decision = gate().authorize(full_request(credential_source=source))
        assert not decision.authorized
        assert "approved_credential_source" in decision.failures


@pytest.mark.parametrize("source", sorted(APPROVED_CREDENTIAL_SOURCES))
def test_approved_sources_pass(source):
    assert gate().authorize(full_request(credential_source=source)).authorized


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("designation", [None, "", "staging", "dev", "test",
                                         "sandbox", "PRODUCTION", "prod"])
def test_only_an_explicit_production_designation_counts(designation):
    """Auto-detection is prohibited; it can point at production by accident."""
    decision = gate().authorize(full_request(environment_designation=designation))
    assert not decision.authorized
    assert "production_environment_designation" in decision.failures


# ---------------------------------------------------------------------------
# Account identity
# ---------------------------------------------------------------------------

def test_a_missing_expected_fingerprint_blocks():
    decision = gate().authorize(full_request(expected_account_fingerprint=None))
    assert not decision.authorized


def test_a_broker_that_reports_no_fingerprint_blocks():
    decision = gate().authorize(full_request(broker_account_fingerprint=None))
    assert not decision.authorized


def test_trading_an_unrecognised_account_is_refused():
    decision = gate().authorize(
        full_request(broker_account_fingerprint="somebody-elses-account")
    )
    assert not decision.authorized
    assert "unrecognised account" in decision.failures["account_fingerprint_matches"]


# ---------------------------------------------------------------------------
# Capital and promotion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tier", [None, 0.0, -5.0])
def test_a_nonpositive_capital_tier_blocks(tier):
    decision = gate().authorize(full_request(capital_tier_limit=tier))
    assert not decision.authorized
    assert "capital_tier_present_and_positive" in decision.failures


def test_an_unpromoted_config_hash_blocks():
    """A safety-relevant change invalidates prior approval."""
    decision = gate().authorize(full_request(
        config_hash="cfg-changed",
        promoted_config_hashes=frozenset({"cfg-deadbeef"}),
    ))
    assert not decision.authorized
    assert "no promotion evidence" in decision.failures[
        "promotion_evidence_for_config_hash"
    ]


def test_a_config_with_no_hash_blocks():
    decision = gate().authorize(full_request(config_hash=None))
    assert not decision.authorized


# ---------------------------------------------------------------------------
# Outstanding problems
# ---------------------------------------------------------------------------

def test_an_unresolved_order_blocks_and_is_named():
    decision = gate().authorize(
        full_request(unresolved_order_ids=["atos-1", "atos-2"])
    )
    assert not decision.authorized
    detail = decision.failures["no_unresolved_order_state"]
    assert "2 unresolved" in detail
    assert "atos-1" in detail


def test_an_active_risk_trip_blocks_and_is_named():
    decision = gate().authorize(full_request(active_risk_trips=["daily_drawdown"]))
    assert not decision.authorized
    assert "daily_drawdown" in decision.failures["no_active_risk_trip"]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_the_first_failure_follows_the_declared_order():
    """The operator is told the earliest thing to fix, not an arbitrary one."""
    decision = gate().authorize(LiveAuthorizationRequest())
    assert decision.first_failure == REQUIRED_CONDITIONS[0]


def test_decision_serialises_for_audit():
    decision = gate().authorize(full_request(reconciliation_matched=False))
    payload = decision.to_dict()
    assert payload["authorized"] is False
    assert "broker_reconciliation_matched" in payload["failures"]
    assert payload["decided_at"]
    assert len(payload["satisfied"]) == len(REQUIRED_CONDITIONS) - 1


def test_refuse_live_is_a_complete_refusal():
    decision = refuse_live("no authorization subsystem available")
    assert not decision.authorized
    assert set(decision.failures) == set(REQUIRED_CONDITIONS)
    assert decision.satisfied == []


# ---------------------------------------------------------------------------
# No fallback into live
# ---------------------------------------------------------------------------

def test_a_sandbox_failure_cannot_promote_itself_to_live():
    """ATOS-P0-AUTH-001 prohibits live fallback from a failed paper connect.

    There is no code path for it: authorisation is a conjunction over an
    explicit request, so a failure elsewhere cannot *add* satisfied
    conditions. This pins that property.
    """
    sandbox_failed = LiveAuthorizationRequest(
        environment_designation="sandbox",
        credential_present=True,
        credential_source="environment",
        credential_expires_at=NOW + timedelta(days=1),
    )
    decision = gate().authorize(sandbox_failed)
    assert not decision.authorized
    assert "explicit_live_flag" in decision.failures
    assert "production_environment_designation" in decision.failures
