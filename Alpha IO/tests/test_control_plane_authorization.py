"""Control-plane authorization — ATOS-P2-API-001.

Invariant:

    Every mutation of trading state is authenticated, authorized, rate
    limited and audited, in every mode.

The regression that motivates the first section: the orchestrator set
``enable_auth = (mode == LIVE)``, so in paper mode - the default - the entire
REST control plane was unauthenticated, while binding every interface.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.control_plane import (  # noqa: E402
    DANGEROUS_ACTIONS,
    ControlPlaneDenied,
    ControlPlaneGuard,
    ControlPlanePolicy,
    Permission,
)

pytestmark = pytest.mark.adversarial


def guard(**policy_kwargs):
    return ControlPlaneGuard(ControlPlanePolicy(**policy_kwargs))


# ---------------------------------------------------------------------------
# Authentication is unconditional for mutations
# ---------------------------------------------------------------------------

def test_an_anonymous_mutation_is_denied():
    entry = guard().authorize("place_order", actor=None, mutating=True)
    assert not entry.allowed
    assert "authentication is required" in entry.reason


def test_an_anonymous_mutation_is_denied_even_with_anonymous_reads_on():
    """Opening telemetry must not open the order endpoint."""
    entry = guard(allow_anonymous_reads=True).authorize(
        "place_order", actor=None, mutating=True
    )
    assert not entry.allowed


def test_an_anonymous_read_is_denied_by_default():
    entry = guard().authorize("get_positions", actor=None, mutating=False)
    assert not entry.allowed


def test_an_anonymous_read_can_be_opened_deliberately():
    entry = guard(allow_anonymous_reads=True).authorize(
        "get_positions", actor=None, mutating=False
    )
    assert entry.allowed


def test_the_orchestrator_no_longer_ties_auth_to_mode():
    """The regression, pinned at its source."""
    source = (Path(__file__).resolve().parents[1] / "core" / "orchestrator.py")
    text = source.read_text(encoding="utf-8")
    assert "enable_auth=self.config.mode == TradingMode.LIVE" not in text, (
        "paper mode would run an unauthenticated control plane again"
    )
    assert "enable_auth=True" in text


def test_the_rest_api_does_not_bind_every_interface_by_default():
    source = (Path(__file__).resolve().parents[1] / "core" / "rest_api.py")
    text = source.read_text(encoding="utf-8")
    assert 'host: str = "0.0.0.0"' not in text
    assert 'host: str = "127.0.0.1"' in text


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_reading_does_not_grant_trading():
    entry = guard().authorize(
        "place_order", actor="alice", permissions=[Permission.READ.value],
        mutating=True,
    )
    assert not entry.allowed
    assert "'trade' permission" in entry.reason


def test_trade_permission_permits_an_ordinary_order():
    entry = guard().authorize(
        "place_order", actor="alice", permissions=[Permission.TRADE.value],
        mutating=True,
    )
    assert entry.allowed


@pytest.mark.parametrize("action", sorted(DANGEROUS_ACTIONS))
def test_trade_permission_does_not_grant_a_dangerous_action(action):
    """Reading a position and flattening the book are not the same request."""
    entry = guard().authorize(
        action, actor="alice", permissions=[Permission.TRADE.value],
        mutating=True, confirmation=action,
    )
    assert not entry.allowed
    assert "'elevated' permission" in entry.reason


@pytest.mark.parametrize("action", sorted(DANGEROUS_ACTIONS))
def test_a_dangerous_action_needs_elevated_and_confirmation(action):
    granted = [Permission.TRADE.value, Permission.ELEVATED.value]

    without = guard().authorize(action, actor="alice", permissions=granted,
                                mutating=True)
    assert not without.allowed
    assert "confirmation" in without.reason

    wrong = guard().authorize(action, actor="alice", permissions=granted,
                              mutating=True, confirmation="yes")
    assert not wrong.allowed

    right = guard().authorize(action, actor="alice", permissions=granted,
                              mutating=True, confirmation=action)
    assert right.allowed


def test_admin_can_perform_dangerous_actions_with_confirmation():
    entry = guard().authorize(
        "flatten_all", actor="root", permissions=[Permission.ADMIN.value],
        mutating=True, confirmation="flatten_all",
    )
    assert entry.allowed


def test_confirmation_can_be_disabled_for_automation():
    entry = guard(require_confirmation_for_dangerous=False).authorize(
        "clear_freeze", actor="ops", permissions=[Permission.ELEVATED.value],
        mutating=True,
    )
    assert entry.allowed


# ---------------------------------------------------------------------------
# Network restriction
# ---------------------------------------------------------------------------

def test_a_source_outside_the_allowed_network_is_denied():
    g = guard(allowed_source_networks=("10.0.0.0/8",))
    assert not g.authorize("place_order", actor="alice",
                           permissions=["trade"], source_ip="203.0.113.5").allowed


def test_a_source_inside_the_allowed_network_is_permitted():
    g = guard(allowed_source_networks=("10.0.0.0/8",))
    assert g.authorize("place_order", actor="alice",
                       permissions=["trade"], source_ip="10.1.2.3").allowed


def test_an_unparseable_source_is_denied():
    g = guard(allowed_source_networks=("10.0.0.0/8",))
    assert not g.authorize("place_order", actor="alice",
                           permissions=["trade"], source_ip="not-an-ip").allowed


# ---------------------------------------------------------------------------
# Bind safety
# ---------------------------------------------------------------------------

def test_binding_every_interface_is_flagged():
    problems = ControlPlanePolicy(bind_host="0.0.0.0").binding_problems()
    assert problems
    assert "every interface" in problems[0]


def test_loopback_is_clean():
    assert ControlPlanePolicy(bind_host="127.0.0.1").binding_problems() == []


def test_a_private_address_is_acceptable():
    assert ControlPlanePolicy(bind_host="10.0.0.5").binding_problems() == []


def test_declaring_external_protection_clears_the_warning():
    policy = ControlPlanePolicy(bind_host="0.0.0.0",
                                external_network_protection=True)
    assert policy.binding_problems() == []


def test_a_public_address_is_flagged():
    # A globally routable address. Note 203.0.113.x would not do: Python
    # classifies the documentation ranges as private, so it is not a public
    # address as far as ipaddress is concerned.
    assert ControlPlanePolicy(bind_host="8.8.8.8").binding_problems()


def test_a_hostname_cannot_be_classified_so_is_treated_as_exposed():
    assert ControlPlanePolicy(bind_host="api.example.com").binding_problems()


def test_anonymous_reads_on_a_public_bind_are_flagged_separately():
    problems = ControlPlanePolicy(
        bind_host="0.0.0.0", allow_anonymous_reads=True
    ).binding_problems()
    assert any("publishes account telemetry" in p for p in problems)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_every_decision_is_audited_including_denials():
    g = guard()
    g.authorize("place_order", actor=None, mutating=True)
    g.authorize("place_order", actor="alice", permissions=["trade"],
                mutating=True)

    assert len(g.audit_log) == 2
    assert len(g.denials()) == 1


def test_the_audit_entry_records_actor_action_and_outcome():
    g = guard()
    g.authorize("cancel_order", actor="alice", permissions=["trade"],
                mutating=True, source_ip="10.0.0.1")
    entry = g.audit_log[-1].to_dict()
    assert entry["actor"] == "alice"
    assert entry["action"] == "cancel_order"
    assert entry["allowed"] is True
    assert entry["source_ip"] == "10.0.0.1"
    assert entry["at"]


def test_the_audit_log_carries_no_token():
    """The actor is an identifier, never a credential."""
    g = guard()
    g.authorize("place_order", actor="alice", permissions=["trade"],
                mutating=True)
    assert "Bearer" not in str(g.report())
    assert "token" not in str(g.report()).lower()


def test_dangerous_actions_are_separately_enumerable():
    g = guard()
    g.authorize("place_order", actor="a", permissions=["trade"], mutating=True)
    g.authorize("flatten_all", actor="a", permissions=["elevated"],
                mutating=True, confirmation="flatten_all")

    taken = g.dangerous_actions_taken()
    assert [e.action for e in taken] == ["flatten_all"]


def test_require_raises_on_denial():
    with pytest.raises(ControlPlaneDenied):
        guard().require("place_order", actor=None, mutating=True)


def test_require_returns_the_entry_on_success():
    entry = guard().require("place_order", actor="alice",
                            permissions=["trade"], mutating=True)
    assert entry.allowed


def test_the_report_surfaces_binding_problems():
    g = guard(bind_host="0.0.0.0")
    report = g.report()
    assert report["binding_problems"]
    assert report["bind_host"] == "0.0.0.0"
