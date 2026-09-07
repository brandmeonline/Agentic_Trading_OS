"""Safety configuration authority — ATOS-P1-CONFIG-001.

Invariant:

    Safety-critical values have one typed, validated source of truth, and any
    change to them invalidates prior live-promotion evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.safety_config import (  # noqa: E402
    ConfigRejected,
    SafetyConfig,
    SafetyConfigAuthority,
)

pytestmark = pytest.mark.adversarial


def live_kwargs(**overrides):
    base = dict(
        mode="live",
        environment_designation="production",
        broker_account_fingerprint="acct-abc",
        allowed_instruments=("AAPL", "SPY"),
        max_capital_tier=10.0,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Unknown fields are errors, not warnings
# ---------------------------------------------------------------------------

def test_a_typo_is_rejected_rather_than_ignored():
    """Silently defaulting is how a limit becomes the one nobody chose."""
    with pytest.raises(ConfigRejected, match="unknown safety configuration"):
        SafetyConfig.from_mapping({"max_positon_concentration": 0.1})


def test_the_rejection_suggests_the_intended_field():
    with pytest.raises(ConfigRejected, match="did you mean"):
        SafetyConfig.from_mapping({"max_positon_concentration": 0.1})


def test_an_entirely_unknown_field_is_still_rejected():
    with pytest.raises(ConfigRejected):
        SafetyConfig.from_mapping({"enable_yolo_mode": True})


def test_a_string_where_a_sequence_is_expected_is_rejected():
    """"AAPL" would silently become ('A','A','P','L')."""
    with pytest.raises(ConfigRejected, match="must be a sequence"):
        SafetyConfig.from_mapping({"allowed_instruments": "AAPL"})


def test_a_non_mapping_is_rejected():
    with pytest.raises(ConfigRejected):
        SafetyConfig.from_mapping([("mode", "paper")])


def test_known_fields_are_accepted():
    config = SafetyConfig.from_mapping({
        "mode": "paper", "max_position_concentration": 0.15,
    })
    assert config.max_position_concentration == 0.15


# ---------------------------------------------------------------------------
# Ranges: a fraction is a fraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "max_risk_per_trade", "max_position_concentration",
    "max_portfolio_exposure", "max_daily_drawdown", "max_total_drawdown",
    "max_slippage_pct", "max_spread_pct",
])
def test_a_percentage_written_as_a_whole_number_is_rejected(name):
    """5.0 is 500%, not 5%."""
    problems = SafetyConfig(**{name: 5.0}).problems()
    assert any(name in p and "fractions" in p for p in problems)


@pytest.mark.parametrize("value", [0.0, -0.1])
def test_a_nonpositive_fraction_is_rejected(value):
    assert SafetyConfig(max_risk_per_trade=value).problems()


def test_a_boolean_is_not_a_fraction():
    assert SafetyConfig(max_risk_per_trade=True).problems()


def test_leverage_below_one_is_rejected():
    assert any(
        "below 1" in p for p in SafetyConfig(max_leverage=0.5).problems()
    )


# ---------------------------------------------------------------------------
# Internal consistency
# ---------------------------------------------------------------------------

def test_one_instrument_cannot_exceed_the_whole_portfolio():
    problems = SafetyConfig(
        max_position_concentration=0.8, max_portfolio_exposure=0.5
    ).problems()
    assert any("whole portfolio" in p for p in problems)


def test_the_daily_limit_must_be_able_to_bind():
    problems = SafetyConfig(
        max_daily_drawdown=0.5, max_total_drawdown=0.2
    ).problems()
    assert any("never bind" in p for p in problems)


def test_leverage_flags_must_agree():
    assert any(
        "disagree" in p
        for p in SafetyConfig(allow_leverage=False, max_leverage=2.0).problems()
    )
    assert any(
        "permits none" in p
        for p in SafetyConfig(allow_leverage=True, max_leverage=1.0).problems()
    )


def test_problems_reports_all_of_them_not_just_the_first():
    problems = SafetyConfig(
        max_risk_per_trade=5.0, max_leverage=0.1, max_loss_streak=0
    ).problems()
    assert len(problems) >= 3


# ---------------------------------------------------------------------------
# Live mode demands more
# ---------------------------------------------------------------------------

def test_a_valid_live_config_passes():
    SafetyConfig(**live_kwargs()).validate()


def test_live_requires_a_production_environment():
    problems = SafetyConfig(
        **live_kwargs(environment_designation="staging")
    ).problems()
    assert any("production" in p for p in problems)


def test_live_requires_an_account_fingerprint():
    problems = SafetyConfig(
        **live_kwargs(broker_account_fingerprint=None)
    ).problems()
    assert any("fingerprint" in p for p in problems)


def test_live_requires_a_positive_capital_tier():
    problems = SafetyConfig(**live_kwargs(max_capital_tier=0.0)).problems()
    assert any("not spend authority" in p for p in problems)


def test_live_requires_an_explicit_instrument_list():
    problems = SafetyConfig(**live_kwargs(allowed_instruments=())).problems()
    assert any("allowed_instruments" in p for p in problems)


@pytest.mark.parametrize("flag", ["allow_options", "allow_futures"])
def test_options_and_futures_are_refused_for_live(flag):
    """The execution layer has no multiplier, expiry or margin handling."""
    problems = SafetyConfig(**live_kwargs(**{flag: True})).problems()
    assert any("not supported for live execution" in p for p in problems)


def test_options_are_permitted_outside_live():
    """Research may explore what live may not execute."""
    SafetyConfig(mode="research", allow_options=True).validate()


# ---------------------------------------------------------------------------
# The hash binds promotion evidence
# ---------------------------------------------------------------------------

def test_identical_configs_hash_the_same():
    assert SafetyConfig().safety_hash() == SafetyConfig().safety_hash()


def test_any_safety_change_changes_the_hash():
    base = SafetyConfig()
    for change in ({"max_risk_per_trade": 0.02},
                   {"allow_shorting": True},
                   {"max_daily_drawdown": 0.06},
                   {"allowed_instruments": ("AAPL",)}):
        assert SafetyConfig(**change).safety_hash() != base.safety_hash(), change


def test_differences_are_reported_old_to_new():
    old = SafetyConfig(max_risk_per_trade=0.01)
    new = SafetyConfig(max_risk_per_trade=0.02)
    diff = new.differences_from(old)
    assert diff["max_risk_per_trade"] == (0.01, 0.02)


def test_a_changed_config_invalidates_promotion():
    old = SafetyConfig()
    new = SafetyConfig(max_position_concentration=0.9,
                       max_portfolio_exposure=0.95)
    assert new.invalidates_promotion_of(old)


# ---------------------------------------------------------------------------
# The authority
# ---------------------------------------------------------------------------

def test_a_fresh_authority_is_not_promoted():
    authority = SafetyConfigAuthority(SafetyConfig())
    assert not authority.is_promoted()


def test_promotion_covers_the_config_it_was_granted_for():
    authority = SafetyConfigAuthority(SafetyConfig())
    authority.promote(approved_by="owner", note="reviewed")
    assert authority.is_promoted()


def test_promotion_must_record_who_approved_it():
    authority = SafetyConfigAuthority(SafetyConfig())
    with pytest.raises(ValueError):
        authority.promote(approved_by="")


def test_changing_the_config_drops_promotion():
    """The headline behaviour."""
    authority = SafetyConfigAuthority(SafetyConfig())
    authority.promote(approved_by="owner")
    assert authority.is_promoted()

    authority.replace(
        SafetyConfig(max_risk_per_trade=0.02), reason="increase per-trade risk"
    )
    assert not authority.is_promoted(), (
        "approval granted for the old limits still applied to the new ones"
    )


def test_an_innocuous_looking_change_still_drops_promotion():
    """"Looks innocuous" is a judgement; the hash is not."""
    authority = SafetyConfigAuthority(SafetyConfig())
    authority.promote(approved_by="owner")
    authority.replace(
        SafetyConfig(max_quote_age_seconds=31.0), reason="feed is a bit slow"
    )
    assert not authority.is_promoted()


def test_reverting_to_a_promoted_config_restores_promotion():
    original = SafetyConfig()
    authority = SafetyConfigAuthority(original)
    authority.promote(approved_by="owner")
    authority.replace(SafetyConfig(max_risk_per_trade=0.02), reason="try more")
    assert not authority.is_promoted()

    authority.replace(original, reason="revert")
    assert authority.is_promoted(), "the reverted config is the approved one"


def test_a_config_change_must_state_a_reason():
    authority = SafetyConfigAuthority(SafetyConfig())
    with pytest.raises(ValueError):
        authority.replace(SafetyConfig(max_risk_per_trade=0.02), reason="")


def test_the_change_log_records_what_moved():
    authority = SafetyConfigAuthority(SafetyConfig())
    authority.promote(approved_by="owner")
    authority.replace(
        SafetyConfig(max_risk_per_trade=0.02), reason="increase risk"
    )
    entry = authority.change_log[-1]
    assert entry["reason"] == "increase risk"
    assert entry["changes"]["max_risk_per_trade"] == {"from": 0.01, "to": 0.02}
    assert entry["still_promoted"] is False


def test_an_unsafe_config_cannot_be_installed():
    with pytest.raises(ConfigRejected):
        SafetyConfigAuthority(SafetyConfig(max_risk_per_trade=5.0))


def test_an_unsafe_replacement_is_refused_and_the_old_one_stands():
    authority = SafetyConfigAuthority(SafetyConfig())
    with pytest.raises(ConfigRejected):
        authority.replace(SafetyConfig(max_risk_per_trade=5.0), reason="oops")
    assert authority.config.max_risk_per_trade == 0.01


def test_the_config_itself_is_immutable():
    """A limit that can be mutated after hashing is a starting point."""
    config = SafetyConfig()
    with pytest.raises(Exception):
        config.max_risk_per_trade = 0.99


def test_report_exposes_the_hash_for_the_authorization_gate():
    authority = SafetyConfigAuthority(SafetyConfig())
    authority.promote(approved_by="owner")
    report = authority.report()
    assert report["promoted"] is True
    assert report["safety_hash"] in report["promoted_hashes"]
    assert report["config"]["safety_hash"] == report["safety_hash"]


def test_the_hash_feeds_the_live_authorization_gate():
    """CONFIG-001 supplies what AUTH-001's promotion condition needs."""
    from core.live_authorization import LiveAuthorizationGate, LiveAuthorizationRequest

    authority = SafetyConfigAuthority(SafetyConfig(**live_kwargs()))
    authority.promote(approved_by="owner")

    decision = LiveAuthorizationGate().authorize(LiveAuthorizationRequest(
        config_hash=authority.config.safety_hash(),
        promoted_config_hashes=authority.promoted_hashes,
    ))
    assert "promotion_evidence_for_config_hash" not in decision.failures

    authority.replace(
        SafetyConfig(**live_kwargs(max_risk_per_trade=0.02)), reason="more risk"
    )
    decision = LiveAuthorizationGate().authorize(LiveAuthorizationRequest(
        config_hash=authority.config.safety_hash(),
        promoted_config_hashes=authority.promoted_hashes,
    ))
    assert "promotion_evidence_for_config_hash" in decision.failures
