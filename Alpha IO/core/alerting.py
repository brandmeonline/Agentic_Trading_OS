"""External alerting and escalation — ATOS-P2-OPS-001.

Invariant:

    Conditions that require a human reach a human, and no alert ever carries
    a credential.

Logging to stdout is adequate while somebody is watching the terminal. It is
not adequate for capital running unattended, where the failure mode is that
the system froze correctly at 02:00 and nobody found out until morning — by
which point the freeze protected the capital but the opportunity cost is
real, and worse, an operator-action condition sat unattended for six hours.

Two design points do most of the work here:

* **Redaction happens on the way out, not at the call site.** Every alert
  payload passes through a scrubber before any channel sees it. Relying on
  each caller to remember not to include the API key is relying on every
  future caller too, and alerting code is exactly where someone dumps the
  whole config to make debugging easier.

* **A channel that fails must not take the system with it.** An alert is a
  notification about a problem; if the notification path is itself broken,
  that is a second problem, not a reason to crash the first one's handler.
  Delivery failures are counted and logged, never raised.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    #: Trading has stopped and will not resume without a person.
    OPERATOR_ACTION_REQUIRED = "operator_action_required"


class AlertKind(Enum):
    """The conditions the ULTRAPLAN requires to escalate."""

    LIVE_ACTIVATED = "live_activated"
    LIVE_DEACTIVATED = "live_deactivated"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    UNKNOWN_ORDER = "unknown_order"
    TIMEOUT_AFTER_ACCEPT = "timeout_after_accept"
    PERSISTENCE_FAILURE = "persistence_failure"
    RISK_LIMIT_TRIP = "risk_limit_trip"
    DRAWDOWN_TRIP = "drawdown_trip"
    STALE_OR_INVALID_FEED = "stale_or_invalid_feed"
    BROKER_AUTH_FAILURE = "broker_auth_failure"
    CAPITAL_BREACH = "capital_breach"
    REPEATED_ORDER_REJECTIONS = "repeated_order_rejections"
    RECOVERY_REQUIRED = "recovery_required"


#: Conditions that always need a person, whatever the caller thinks.
ALWAYS_OPERATOR_ACTION = frozenset({
    AlertKind.RECOVERY_REQUIRED,
    AlertKind.UNKNOWN_ORDER,
    AlertKind.TIMEOUT_AFTER_ACCEPT,
    AlertKind.CAPITAL_BREACH,
    AlertKind.RECONCILIATION_MISMATCH,
})


#: Keys whose values never leave the process, however they are spelled.
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|secret|password|passwd|passphrase|token"
    r"|bearer|authorization|auth|signature|private[_-]?key|credential|cookie"
    r"|session[_-]?id)"
)

#: Secret-shaped values that might appear inside otherwise innocent strings.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bPK[A-Z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"),
)

REDACTED = "[REDACTED]"


def redact(value: Any, _depth: int = 0) -> Any:
    """Strip anything credential-shaped from an alert payload.

    Recurses through dicts and sequences. A key that looks secret has its
    value replaced wholesale; a string that merely *contains* something
    secret-shaped has that part replaced, so an error message stays readable
    without carrying the token that caused it.
    """
    if _depth > 12:
        return REDACTED

    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key):
                cleaned[key] = REDACTED
            else:
                cleaned[key] = redact(item, _depth + 1)
        return cleaned

    if isinstance(value, (list, tuple, set)):
        cleaned_items = [redact(item, _depth + 1) for item in value]
        return type(value)(cleaned_items) if not isinstance(value, set) else set(cleaned_items)

    if isinstance(value, str):
        text = value
        for pattern in _SECRET_VALUE_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    return value


@dataclass
class Alert:
    """One condition worth waking someone for."""

    kind: AlertKind
    severity: AlertSeverity
    summary: str
    detail: Dict[str, Any] = field(default_factory=dict)
    raised_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def needs_operator(self) -> bool:
        return self.severity is AlertSeverity.OPERATOR_ACTION_REQUIRED

    def to_dict(self) -> Dict[str, Any]:
        """The wire form. Redaction happens here so no channel can skip it."""
        return {
            "kind": self.kind.value,
            "severity": self.severity.value,
            "summary": redact(self.summary),
            "detail": redact(self.detail),
            "needs_operator": self.needs_operator,
            "raised_at": self.raised_at.isoformat(),
        }


class AlertChannel:
    """Somewhere an alert can go. Subclasses implement ``deliver``."""

    name = "base"

    def deliver(self, payload: Dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError


class LoggingAlertChannel(AlertChannel):
    """The floor: structured logs. Not sufficient alone, but never absent."""

    name = "logging"

    def deliver(self, payload: Dict[str, Any]) -> None:
        level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "critical": logging.ERROR,
            "operator_action_required": logging.CRITICAL,
        }.get(payload["severity"], logging.WARNING)
        logger.log(
            level, "ALERT [%s] %s | %s",
            payload["kind"], payload["summary"], payload["detail"],
        )


class CallbackAlertChannel(AlertChannel):
    """Adapts any callable into a channel, for webhooks, email or paging."""

    def __init__(self, name: str, send: Callable[[Dict[str, Any]], None]) -> None:
        self.name = name
        self._send = send

    def deliver(self, payload: Dict[str, Any]) -> None:
        self._send(payload)


class AlertManager:
    """Routes alerts to channels, redacting on the way and never raising."""

    def __init__(
        self,
        channels: Optional[List[AlertChannel]] = None,
        min_severity: AlertSeverity = AlertSeverity.WARNING,
    ) -> None:
        # The logging channel is always present. An alerting system whose
        # only channel is misconfigured should still leave a trace.
        self.channels: List[AlertChannel] = list(channels or [])
        if not any(isinstance(c, LoggingAlertChannel) for c in self.channels):
            self.channels.append(LoggingAlertChannel())

        self.min_severity = min_severity
        self.history: List[Alert] = []
        self.delivery_failures: List[Dict[str, str]] = []

    _SEVERITY_RANK = {
        AlertSeverity.INFO: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.CRITICAL: 2,
        AlertSeverity.OPERATOR_ACTION_REQUIRED: 3,
    }

    def raise_alert(
        self,
        kind: AlertKind,
        summary: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        **detail: Any,
    ) -> Alert:
        """Raise an alert and attempt delivery to every channel."""
        # Some conditions are operator-action regardless of how the caller
        # graded them: a caller who under-rates an unknown order does not get
        # to downgrade it.
        if kind in ALWAYS_OPERATOR_ACTION:
            severity = AlertSeverity.OPERATOR_ACTION_REQUIRED

        alert = Alert(kind=kind, severity=severity, summary=summary,
                      detail=dict(detail))
        self.history.append(alert)

        if self._SEVERITY_RANK[severity] < self._SEVERITY_RANK[self.min_severity]:
            return alert

        payload = alert.to_dict()
        for channel in self.channels:
            try:
                channel.deliver(payload)
            except Exception as exc:
                # A broken notification path is a second problem, not a
                # reason to crash the handler for the first one.
                self.delivery_failures.append({
                    "channel": channel.name,
                    "kind": kind.value,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                logger.error(
                    "Alert channel %s failed to deliver a %s alert: %s",
                    channel.name, kind.value, type(exc).__name__,
                )
        return alert

    # -- convenience for the conditions the ULTRAPLAN names ---------------

    def live_activated(self, account: str, capital_tier: float) -> Alert:
        return self.raise_alert(
            AlertKind.LIVE_ACTIVATED,
            f"Live trading activated on {account} with a {capital_tier} tier",
            AlertSeverity.CRITICAL,
            account=account, capital_tier=capital_tier,
        )

    def reconciliation_mismatch(self, summary: str, **detail: Any) -> Alert:
        return self.raise_alert(
            AlertKind.RECONCILIATION_MISMATCH,
            f"Broker reconciliation mismatch: {summary}", **detail,
        )

    def unknown_order(self, client_order_id: str, reason: str) -> Alert:
        return self.raise_alert(
            AlertKind.UNKNOWN_ORDER,
            f"Order {client_order_id} is in an unknown state: {reason}",
            client_order_id=client_order_id, reason=reason,
        )

    def persistence_failure(self, write_kind: str, detail: str) -> Alert:
        return self.raise_alert(
            AlertKind.PERSISTENCE_FAILURE,
            f"Critical write failed ({write_kind}): {detail}",
            AlertSeverity.CRITICAL,
            write_kind=write_kind,
        )

    def risk_trip(self, name: str, reason: str) -> Alert:
        return self.raise_alert(
            AlertKind.RISK_LIMIT_TRIP,
            f"Risk trip {name}: {reason}",
            AlertSeverity.CRITICAL,
            trip=name, reason=reason,
        )

    def recovery_required(self, reason: str) -> Alert:
        return self.raise_alert(
            AlertKind.RECOVERY_REQUIRED,
            f"Operator action required: {reason}", reason=reason,
        )

    # -- reporting --------------------------------------------------------

    def outstanding_operator_actions(self) -> List[Alert]:
        return [a for a in self.history if a.needs_operator]

    def report(self) -> Dict[str, Any]:
        return {
            "channels": [c.name for c in self.channels],
            "total_alerts": len(self.history),
            "operator_actions": len(self.outstanding_operator_actions()),
            "delivery_failures": list(self.delivery_failures),
            "recent": [a.to_dict() for a in self.history[-10:]],
        }
