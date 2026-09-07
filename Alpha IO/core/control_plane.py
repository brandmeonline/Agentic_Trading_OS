"""Control-plane authorization and audit — ATOS-P2-API-001.

Invariant:

    Every mutation of trading state is authenticated, authorized, rate
    limited and audited — in every mode, not only when real money is at
    stake.

The tempting shortcut is to relax authentication in paper mode on the grounds
that nothing is at risk. Three things are wrong with that:

* Paper mode is where promotion evidence comes from. An unauthenticated party
  who can place paper orders can corrupt the evidence used to justify going
  live, which is a slower path to the same loss.

* The setting is decided once, at construction, from a mode that can change.
  A system that binds an auth policy to a mode has to get every subsequent
  mode transition right, forever.

* An API bound to all interfaces with no auth is reachable by anything on the
  network, and "it is only paper" is a claim about intent, not about who can
  connect.

So authentication is unconditional for anything that mutates. Read-only
telemetry can be opened deliberately, by an explicit setting that says so.

Dangerous actions get a tier of their own. Reading a position and flattening
the book are not the same kind of request, and a token that can do the first
should not automatically do the second.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Permission(Enum):
    """What a caller is allowed to do."""

    READ = "read"
    #: Place and cancel ordinary orders.
    TRADE = "trade"
    #: Arm live trading, flatten the book, clear a freeze. Separate because
    #: these change what the system is permitted to do, not just what it is
    #: currently doing.
    ELEVATED = "elevated"
    #: Change configuration and promote.
    ADMIN = "admin"


#: Actions that require ELEVATED and an explicit confirmation token.
DANGEROUS_ACTIONS = frozenset({
    "live_arm",
    "live_disarm",
    "flatten_all",
    "clear_freeze",
    "clear_risk_trip",
    "promote_capital_tier",
    "override_reconciliation",
})


class ControlPlaneDenied(PermissionError):
    """A control-plane request that must not proceed."""


@dataclass
class AuditEntry:
    """Who did what, when, and what happened.

    Carries no secrets: the actor is an identifier, never a token.
    """

    actor: str
    action: str
    allowed: bool
    reason: str = ""
    source_ip: Optional[str] = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "actor": self.actor,
            "action": self.action,
            "allowed": self.allowed,
            "reason": self.reason,
            "source_ip": self.source_ip,
            "at": self.at.isoformat(),
        }


@dataclass
class ControlPlanePolicy:
    """How the control plane decides who may do what."""

    #: Authentication for mutating routes. Not configurable to False: the
    #: field exists to be read, not to be turned off.
    require_auth_for_mutations: bool = True
    #: Read-only telemetry can be opened deliberately.
    allow_anonymous_reads: bool = False
    #: Dangerous actions need a matching confirmation string.
    require_confirmation_for_dangerous: bool = True
    #: Addresses permitted to reach the control plane. Empty means any, which
    #: is only safe behind an external network control.
    allowed_source_networks: tuple = ()
    #: Bind address. Loopback by default; widening is a deliberate act.
    bind_host: str = "127.0.0.1"
    #: Set when an external control (VPN, mTLS proxy, Cloudflare Access) is
    #: known to be in front. Only then may the plane bind publicly.
    external_network_protection: bool = False

    def binding_problems(self) -> List[str]:
        """Reasons this bind configuration is unsafe."""
        problems = []
        try:
            address = ipaddress.ip_address(self.bind_host)
            # is_global is the precise question: reachable from the public
            # internet. is_private is broader than it sounds - it covers
            # loopback and the documentation ranges too - so using its
            # negation would misclassify both.
            public = address.is_global
            unspecified = address.is_unspecified   # 0.0.0.0 or ::
        except ValueError:
            # A hostname. Cannot be classified, so treat it as exposed.
            public, unspecified = True, False

        if unspecified and not self.external_network_protection:
            problems.append(
                f"binding {self.bind_host} exposes the control plane on every "
                "interface with no declared external protection; bind loopback "
                "or set external_network_protection once a VPN, mTLS proxy or "
                "access gateway is actually in front"
            )
        elif public and not self.external_network_protection:
            problems.append(
                f"binding the public address {self.bind_host} with no declared "
                "external protection"
            )
        if self.allow_anonymous_reads and (unspecified or public):
            problems.append(
                "anonymous reads are enabled on a non-loopback bind, which "
                "publishes account telemetry"
            )
        return problems


class ControlPlaneGuard:
    """Authorizes control-plane requests and records every decision."""

    def __init__(self, policy: Optional[ControlPlanePolicy] = None) -> None:
        self.policy = policy or ControlPlanePolicy()
        self.audit_log: List[AuditEntry] = []

    # -- decisions -------------------------------------------------------

    def authorize(
        self,
        action: str,
        actor: Optional[str],
        permissions: Optional[List[str]] = None,
        mutating: bool = True,
        confirmation: Optional[str] = None,
        source_ip: Optional[str] = None,
    ) -> AuditEntry:
        """Decide, record, and return the decision.

        Always returns an entry rather than raising, so the caller can shape
        its own response — but the entry is appended to the audit log either
        way, because a denied request is exactly what an audit log is for.
        """
        granted = set(permissions or [])
        entry = self._decide(
            action, actor, granted, mutating, confirmation, source_ip
        )
        self.audit_log.append(entry)
        if not entry.allowed:
            logger.warning(
                "Control plane denied %s for %s: %s",
                action, actor or "anonymous", entry.reason,
            )
        elif action in DANGEROUS_ACTIONS:
            logger.warning(
                "Control plane permitted dangerous action %s for %s",
                action, actor,
            )
        return entry

    def require(self, *args: Any, **kwargs: Any) -> AuditEntry:
        """Like :meth:`authorize`, but raises on denial."""
        entry = self.authorize(*args, **kwargs)
        if not entry.allowed:
            raise ControlPlaneDenied(entry.reason)
        return entry

    def _decide(
        self,
        action: str,
        actor: Optional[str],
        granted: set,
        mutating: bool,
        confirmation: Optional[str],
        source_ip: Optional[str],
    ) -> AuditEntry:
        def deny(reason: str) -> AuditEntry:
            return AuditEntry(
                actor=actor or "anonymous", action=action, allowed=False,
                reason=reason, source_ip=source_ip,
            )

        if source_ip and self.policy.allowed_source_networks:
            if not self._source_allowed(source_ip):
                return deny(f"source address {source_ip} is not permitted")

        if not actor:
            if mutating:
                # Unconditional. Not relaxed by mode, not relaxed in paper.
                return deny(
                    "authentication is required for any request that mutates "
                    "trading state"
                )
            if not self.policy.allow_anonymous_reads:
                return deny("authentication is required")

        if mutating and Permission.TRADE.value not in granted \
                and Permission.ADMIN.value not in granted \
                and Permission.ELEVATED.value not in granted:
            return deny(f"{action} requires the 'trade' permission")

        if action in DANGEROUS_ACTIONS:
            if Permission.ELEVATED.value not in granted \
                    and Permission.ADMIN.value not in granted:
                return deny(
                    f"{action} is a dangerous action and requires the "
                    "'elevated' permission; 'trade' is not sufficient"
                )
            if self.policy.require_confirmation_for_dangerous:
                if confirmation != action:
                    return deny(
                        f"{action} requires an explicit confirmation equal to "
                        f"the action name; got {confirmation!r}"
                    )

        return AuditEntry(
            actor=actor or "anonymous", action=action, allowed=True,
            reason="authorized", source_ip=source_ip,
        )

    def _source_allowed(self, source_ip: str) -> bool:
        try:
            address = ipaddress.ip_address(source_ip)
        except ValueError:
            return False
        for network in self.policy.allowed_source_networks:
            try:
                if address in ipaddress.ip_network(network, strict=False):
                    return True
            except ValueError:
                continue
        return False

    # -- reporting -------------------------------------------------------

    def denials(self) -> List[AuditEntry]:
        return [e for e in self.audit_log if not e.allowed]

    def dangerous_actions_taken(self) -> List[AuditEntry]:
        return [
            e for e in self.audit_log
            if e.allowed and e.action in DANGEROUS_ACTIONS
        ]

    def report(self) -> Dict[str, Any]:
        return {
            "bind_host": self.policy.bind_host,
            "binding_problems": self.policy.binding_problems(),
            "anonymous_reads": self.policy.allow_anonymous_reads,
            "total_requests": len(self.audit_log),
            "denied": len(self.denials()),
            "dangerous_actions": len(self.dangerous_actions_taken()),
            "recent": [e.to_dict() for e in self.audit_log[-20:]],
        }
