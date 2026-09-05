"""External alerting and escalation — ATOS-P2-OPS-001.

Invariant:

    Conditions that require a human reach a human, and no alert ever carries
    a credential.

The redaction tests carry the most weight. Alerting is exactly where somebody
dumps the whole config object to make debugging easier, and an alert channel
is by definition a path out of the process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.alerting import (  # noqa: E402
    ALWAYS_OPERATOR_ACTION,
    REDACTED,
    AlertKind,
    AlertManager,
    AlertSeverity,
    CallbackAlertChannel,
    LoggingAlertChannel,
    redact,
)

pytestmark = pytest.mark.adversarial


def collecting_manager(**kwargs):
    received = []
    manager = AlertManager(
        channels=[CallbackAlertChannel("collector", received.append)], **kwargs
    )
    return manager, received


# ---------------------------------------------------------------------------
# Redaction: nothing secret leaves the process
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "api_key", "api-key", "API_KEY", "apiKey", "api_secret", "secret",
    "password", "passwd", "passphrase", "token", "bearer", "authorization",
    "auth", "signature", "private_key", "credential", "cookie", "session_id",
])
def test_a_secret_named_key_is_replaced_wholesale(key):
    assert redact({key: "hunter2-the-real-value"})[key] == REDACTED


def test_redaction_recurses_into_nested_structures():
    payload = {
        "config": {"broker": {"api_key": "PKREALKEY123456789", "url": "x"}},
        "orders": [{"token": "abc"}, {"symbol": "AAPL"}],
    }
    cleaned = redact(payload)
    assert cleaned["config"]["broker"]["api_key"] == REDACTED
    assert cleaned["config"]["broker"]["url"] == "x", "innocent keys survive"
    assert cleaned["orders"][0]["token"] == REDACTED
    assert cleaned["orders"][1]["symbol"] == "AAPL"


@pytest.mark.parametrize("secret", [
    "PKABCDEFGHIJKLMNOP12",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_abcdefghijklmnopqrstuvwxyz012345",
    "xoxb-123456789012-abcdefghijkl",
    "sk-abcdefghijklmnopqrstuvwxyz0123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop",
])
def test_a_secret_shaped_value_is_scrubbed_from_free_text(secret):
    """An error message must not carry the token that caused it."""
    message = f"request failed for credential {secret} at 12:00"
    cleaned = redact(message)
    assert secret not in cleaned
    assert REDACTED in cleaned
    assert "request failed" in cleaned, "the message stays readable"


def test_an_authorization_header_is_scrubbed():
    cleaned = redact("Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    assert "abcdefghijklmnopqrstuvwxyz" not in cleaned


def test_a_private_key_block_is_scrubbed():
    cleaned = redact("-----BEGIN RSA PRIVATE KEY-----\nMIIabc")
    assert "BEGIN RSA PRIVATE KEY" not in cleaned


def test_redaction_survives_a_deeply_nested_payload():
    payload: dict = {"a": {}}
    node = payload["a"]
    for _ in range(30):
        node["next"] = {}
        node = node["next"]
    node["api_key"] = "deep-secret"
    redact(payload)  # must not recurse forever


def test_the_alert_payload_is_redacted_not_the_caller():
    """Redaction happens on the way out, so no caller can forget."""
    manager, received = collecting_manager()
    manager.raise_alert(
        AlertKind.BROKER_AUTH_FAILURE,
        "auth failed",
        AlertSeverity.CRITICAL,
        api_key="PKREALKEY1234567890",
        config={"api_secret": "also-real"},
    )
    payload = received[0]
    assert payload["detail"]["api_key"] == REDACTED
    assert payload["detail"]["config"]["api_secret"] == REDACTED
    assert "PKREALKEY" not in str(payload)


def test_a_secret_in_the_summary_is_also_redacted():
    manager, received = collecting_manager()
    manager.raise_alert(
        AlertKind.BROKER_AUTH_FAILURE,
        "rejected key PKABCDEFGHIJKLMNOP12",
        AlertSeverity.CRITICAL,
    )
    assert "PKABCDEFGHIJKLMNOP12" not in received[0]["summary"]


# ---------------------------------------------------------------------------
# The conditions the ULTRAPLAN names
# ---------------------------------------------------------------------------

def test_every_required_condition_has_an_alert_kind():
    names = {k.value for k in AlertKind}
    required = {
        "live_activated", "live_deactivated", "reconciliation_mismatch",
        "unknown_order", "timeout_after_accept", "persistence_failure",
        "risk_limit_trip", "drawdown_trip", "stale_or_invalid_feed",
        "broker_auth_failure", "capital_breach", "repeated_order_rejections",
        "recovery_required",
    }
    assert required <= names, f"missing: {required - names}"


@pytest.mark.parametrize("kind", sorted(ALWAYS_OPERATOR_ACTION, key=lambda k: k.value))
def test_some_conditions_are_operator_action_whatever_the_caller_says(kind):
    """A caller who under-rates an unknown order does not get to downgrade it."""
    manager, received = collecting_manager()
    manager.raise_alert(kind, "something happened", AlertSeverity.INFO)
    assert received[0]["severity"] == "operator_action_required"
    assert received[0]["needs_operator"] is True


def test_the_convenience_helpers_produce_the_right_severity():
    manager, received = collecting_manager()
    manager.unknown_order("atos-1", "timeout")
    manager.recovery_required("unresolved intent")
    manager.risk_trip("daily_drawdown", "limit reached")
    manager.persistence_failure("order_intent", "disk full")

    severities = [p["severity"] for p in received]
    assert severities[0] == "operator_action_required"
    assert severities[1] == "operator_action_required"
    assert severities[2] == "critical"
    assert severities[3] == "critical"


def test_outstanding_operator_actions_are_enumerable():
    manager, _ = collecting_manager()
    manager.raise_alert(AlertKind.STALE_OR_INVALID_FEED, "stale")
    manager.unknown_order("atos-1", "timeout")
    manager.recovery_required("db down")

    outstanding = manager.outstanding_operator_actions()
    assert len(outstanding) == 2
    assert all(a.needs_operator for a in outstanding)


# ---------------------------------------------------------------------------
# Delivery is best-effort and never fatal
# ---------------------------------------------------------------------------

def test_a_failing_channel_does_not_raise():
    """A broken notification path is a second problem, not a crash."""
    def explode(_payload):
        raise ConnectionError("pager service unreachable")

    manager = AlertManager(channels=[CallbackAlertChannel("pager", explode)])
    manager.recovery_required("something bad")  # must not raise

    assert len(manager.delivery_failures) == 1
    assert manager.delivery_failures[0]["channel"] == "pager"
    assert "ConnectionError" in manager.delivery_failures[0]["error"]


def test_one_failing_channel_does_not_stop_the_others():
    delivered = []

    def explode(_payload):
        raise RuntimeError("down")

    manager = AlertManager(channels=[
        CallbackAlertChannel("broken", explode),
        CallbackAlertChannel("working", delivered.append),
    ])
    manager.recovery_required("problem")
    assert len(delivered) == 1


def test_a_logging_channel_is_always_present():
    """An alerting system whose only channel is misconfigured still traces."""
    manager = AlertManager(channels=[])
    assert any(isinstance(c, LoggingAlertChannel) for c in manager.channels)


def test_a_supplied_logging_channel_is_not_duplicated():
    manager = AlertManager(channels=[LoggingAlertChannel()])
    assert sum(isinstance(c, LoggingAlertChannel) for c in manager.channels) == 1


# ---------------------------------------------------------------------------
# Severity filtering
# ---------------------------------------------------------------------------

def test_below_threshold_alerts_are_recorded_but_not_delivered():
    manager, received = collecting_manager(min_severity=AlertSeverity.CRITICAL)
    manager.raise_alert(AlertKind.LIVE_DEACTIVATED, "stopped", AlertSeverity.INFO)

    assert received == [], "an INFO alert reached a CRITICAL-only channel"
    assert len(manager.history) == 1, "it must still be recorded"


def test_operator_action_always_passes_any_threshold():
    manager, received = collecting_manager(
        min_severity=AlertSeverity.OPERATOR_ACTION_REQUIRED
    )
    manager.recovery_required("db down")
    assert len(received) == 1


def test_history_records_everything_regardless_of_delivery():
    manager, _ = collecting_manager(min_severity=AlertSeverity.CRITICAL)
    for _ in range(5):
        manager.raise_alert(AlertKind.LIVE_DEACTIVATED, "x", AlertSeverity.INFO)
    assert len(manager.history) == 5


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_the_report_is_itself_redacted():
    manager, _ = collecting_manager()
    manager.raise_alert(
        AlertKind.BROKER_AUTH_FAILURE, "failed", AlertSeverity.CRITICAL,
        api_secret="very-secret-value",
    )
    report = manager.report()
    assert "very-secret-value" not in str(report)
    assert report["recent"][0]["detail"]["api_secret"] == REDACTED


def test_the_report_counts_operator_actions_and_failures():
    def explode(_payload):
        raise RuntimeError("down")

    manager = AlertManager(channels=[CallbackAlertChannel("broken", explode)])
    manager.recovery_required("a")
    manager.unknown_order("atos-1", "b")

    report = manager.report()
    assert report["total_alerts"] == 2
    assert report["operator_actions"] == 2
    assert len(report["delivery_failures"]) == 2
    assert "logging" in report["channels"]
