"""The capital-tier grant tool — ATOS-P3-CAP-001.

The tool is the only thing that creates spend authority, so the properties
that matter are its refusals and its honesty about where its evidence came
from. A grant that cannot say which of its evidence was checked and which was
somebody's word is a grant nobody can audit after a loss.

The adversarial suite is not actually run in these tests — that takes a
minute and is verified for real when the tool runs. It is stubbed, and one
test asserts the tool refuses when the stub reports failure, which is the
behaviour that matters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.capital_ladder import CapitalLadder, CapitalTier, LadderStore  # noqa: E402
from tools import grant_capital_tier as grant  # noqa: E402

pytestmark = pytest.mark.adversarial

ACK = grant.REQUIRED_ACKNOWLEDGEMENT

#: Captured before the autouse fixture below replaces it.
REAL_ATTEMPT_RECONCILIATION = grant.attempt_reconciliation


@pytest.fixture(autouse=True)
def fast_gates(monkeypatch):
    """Stub the suite run. One test below overrides this with a failure."""
    monkeypatch.setattr(grant, "verify_safety_gates",
                        lambda timeout=900: (True, "stubbed: 1165 passed"))
    monkeypatch.setattr(grant, "attempt_reconciliation",
                        lambda: (None, "no broker credentials"))


def _argv(ladder, tier="L1", extra=None):
    argv = [
        "--tier", tier, "--approved-by", "Owner", "--ladder", str(ladder),
        "--i-am-authorizing-real-money", "--risk-acknowledgement", ACK,
        "--attest", "reconciliation_clean=owner reviewed the console",
    ]
    return argv + (extra or [])


# ---------------------------------------------------------------------------
# The happy path, so the refusals below are not vacuous
# ---------------------------------------------------------------------------


def test_a_well_evidenced_l1_grant_succeeds(tmp_path, capsys):
    ladder_path = tmp_path / "ladder.json"

    assert grant.main(_argv(ladder_path)) == 0

    persisted = CapitalLadder(LadderStore(str(ladder_path)))
    assert persisted.tier is CapitalTier.L1
    assert persisted.max_capital_at_risk == 10.0
    assert persisted.state.approved_by == "Owner"
    assert "$10.00" in capsys.readouterr().out


def test_the_grant_records_what_was_verified_and_what_was_attested(tmp_path):
    """The distinction is the whole audit trail."""
    ladder_path = tmp_path / "ladder.json"
    grant.main(_argv(ladder_path))

    record = json.loads(
        (tmp_path / "ladder.attestation.json").read_text(encoding="utf-8")
    )

    assert "p0_p1_gates_passed" in record["verified"]
    assert "reconciliation_clean" in record["attested"]
    assert "reconciliation_clean" not in record["verified"]
    assert record["attested"]["reconciliation_clean"] == \
        "owner reviewed the console"
    assert record["approved_by"] == "Owner"
    assert record["safety_config_hash"].startswith("cfg-")
    assert record["strategy_hash"].startswith("strat-")


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_live_tier_needs_the_real_money_flag(tmp_path):
    argv = ["--tier", "L1", "--approved-by", "Owner",
            "--ladder", str(tmp_path / "l.json"),
            "--risk-acknowledgement", ACK]
    assert grant.main(argv) == 2
    assert not (tmp_path / "l.json").exists()


def test_a_live_tier_needs_the_exact_acknowledgement(tmp_path):
    argv = ["--tier", "L1", "--approved-by", "Owner",
            "--ladder", str(tmp_path / "l.json"),
            "--i-am-authorizing-real-money",
            "--risk-acknowledgement", "i understand this trades real money"]
    assert grant.main(argv) == 2


def test_gate_verification_cannot_be_skipped_above_l0(tmp_path):
    """The gates are the evidence; skipping them empties the grant."""
    assert grant.main(_argv(tmp_path / "l.json",
                            extra=["--skip-gate-verification"])) == 2


def test_a_red_safety_suite_stops_the_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(grant, "verify_safety_gates",
                        lambda timeout=900: (False, "3 failed"))
    ladder_path = tmp_path / "l.json"

    assert grant.main(_argv(ladder_path)) == 1
    assert not ladder_path.exists()


def test_rungs_cannot_be_skipped(tmp_path, capsys):
    assert grant.main(_argv(tmp_path / "l.json", tier="L4")) == 1
    assert "the next rung from L0 is L1" in capsys.readouterr().out


def test_an_unknown_tier_is_refused(tmp_path):
    assert grant.main(_argv(tmp_path / "l.json", tier="L9")) == 2


def test_missing_evidence_refuses_and_grants_nothing(tmp_path, capsys):
    """L1 without the reconciliation attestation has no reconciliation."""
    argv = ["--tier", "L1", "--approved-by", "Owner",
            "--ladder", str(tmp_path / "l.json"),
            "--i-am-authorizing-real-money", "--risk-acknowledgement", ACK]

    assert grant.main(argv) == 1
    assert "reconciliation" in capsys.readouterr().out
    assert not (tmp_path / "l.json").exists()


# ---------------------------------------------------------------------------
# Attestation discipline
# ---------------------------------------------------------------------------


def test_an_attestation_cannot_override_a_check_that_actually_ran(
    tmp_path, monkeypatch, capsys
):
    """If the tool reconciled against a real broker and it failed, saying
    otherwise is not an attestation, it is a contradiction."""
    monkeypatch.setattr(grant, "attempt_reconciliation",
                        lambda: (False, "position mismatch on AAPL"))

    assert grant.main(_argv(tmp_path / "l.json")) == 2
    assert "cannot override a check that actually ran" in capsys.readouterr().out


def test_a_verified_reconciliation_is_recorded_as_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(grant, "attempt_reconciliation",
                        lambda: (True, "reconciled against 1234: matched"))
    ladder_path = tmp_path / "ladder.json"

    argv = ["--tier", "L1", "--approved-by", "Owner",
            "--ladder", str(ladder_path), "--i-am-authorizing-real-money",
            "--risk-acknowledgement", ACK]
    assert grant.main(argv) == 0

    record = json.loads(
        (tmp_path / "ladder.attestation.json").read_text(encoding="utf-8")
    )
    assert "reconciliation_clean" in record["verified"]
    assert record["attested"] == {}


def test_only_attestable_fields_may_be_attested(tmp_path, capsys):
    assert grant.main(_argv(tmp_path / "l.json",
                            extra=["--attest", "p0_p1_gates_passed=trust me"])) == 2
    assert "not attestable" in capsys.readouterr().out


def test_an_attestation_without_a_reason_is_refused():
    with pytest.raises(ValueError, match="checkbox"):
        grant.parse_attestation("supervised_sessions=20")


def test_attestation_parsing_handles_each_field_type():
    assert grant.parse_attestation("reconciliation_clean=note") == \
        ("reconciliation_clean", True, "note")
    assert grant.parse_attestation("supervised_sessions=20:ran them") == \
        ("supervised_sessions", 20, "ran them")
    assert grant.parse_attestation("oos_net_of_cost_sharpe=1.4:backtested") == \
        ("oos_net_of_cost_sharpe", 1.4, "backtested")
    assert grant.parse_attestation("independent_review_by=Sam:external") == \
        ("independent_review_by", "Sam", "external")


def test_a_malformed_attestation_is_refused():
    with pytest.raises(ValueError, match="field=note"):
        grant.parse_attestation("no equals sign here")


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def test_a_dry_run_grants_nothing(tmp_path, capsys):
    ladder_path = tmp_path / "ladder.json"
    assert grant.main(_argv(ladder_path, extra=["--dry-run"])) == 0

    assert not ladder_path.exists()
    assert "nothing was granted" in capsys.readouterr().out


def test_a_dry_run_does_not_need_the_arming_flags(tmp_path):
    """An operator should be able to find out what a rung wants before
    committing to wanting it."""
    argv = ["--tier", "L1", "--approved-by", "Owner",
            "--ladder", str(tmp_path / "l.json"), "--dry-run",
            "--attest", "reconciliation_clean=checked"]
    assert grant.main(argv) == 0


# ---------------------------------------------------------------------------
# Hashes bind the grant to what will run
# ---------------------------------------------------------------------------


def test_the_config_hash_is_computed_not_accepted(tmp_path):
    from core.safety_config import SafetyConfig

    ladder_path = tmp_path / "ladder.json"
    grant.main(_argv(ladder_path))
    record = json.loads(
        (tmp_path / "ladder.attestation.json").read_text(encoding="utf-8")
    )
    assert record["safety_config_hash"] == SafetyConfig().safety_hash()


def test_a_different_safety_config_produces_a_different_grant(tmp_path):
    from core.safety_config import SafetyConfig

    config_path = tmp_path / "safety.json"
    config_path.write_text(
        json.dumps({"mode": "paper", "max_risk_per_trade": 0.004}),
        encoding="utf-8",
    )
    grant.main(_argv(tmp_path / "ladder.json",
                     extra=["--safety-config", str(config_path)]))

    record = json.loads(
        (tmp_path / "ladder.attestation.json").read_text(encoding="utf-8")
    )
    assert record["safety_config_hash"] != SafetyConfig().safety_hash()
    assert record["safety_config"]["max_risk_per_trade"] == 0.004


def test_the_strategy_hash_covers_the_strategy_modules():
    from core.safety_config import SafetyConfig

    base = grant.strategy_digest(SafetyConfig())
    changed = grant.strategy_digest(
        SafetyConfig(promoted_strategy_versions=("momentum-v2",))
    )
    assert base.startswith("strat-")
    assert base != changed


def test_reconciliation_is_not_attempted_without_credentials(monkeypatch):
    """Absence of a check is not a passing check: it returns None, which the
    caller must resolve by attesting rather than by assuming."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    # The autouse fixture stubs the module attribute, so call the real one.
    result, reason = REAL_ATTEMPT_RECONCILIATION()

    assert result is None
    assert "credentials" in reason


def test_a_partial_credential_pair_still_counts_as_none(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "only-the-key")
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    result, _ = REAL_ATTEMPT_RECONCILIATION()
    assert result is None
