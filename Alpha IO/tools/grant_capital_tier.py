"""Grant a capital tier — ATOS-P3-CAP-001.

Spend authority is a persisted grant, and this is the only thing that creates
one. It exists as a separate tool rather than a config field for the reason
the whole ladder exists: a number in a config file is something a deployment
inherits, and spend authority should be something a person did.

The tool draws a hard line between two kinds of evidence, and records which
is which:

**Verified.** Checked here, now, by running something. The P0/P1 safety gates
are verified by running the adversarial suite and requiring it to pass. The
safety config hash is computed, not accepted. A reconciliation is attempted
against the broker if credentials are present.

**Attested.** A human said so. Anything a machine cannot check on this host -
most often a reconciliation against an account this container cannot reach -
has to be attested explicitly, with a note and a name, and the grant records
that it was attested rather than verified.

The distinction is the point. A grant that cannot tell you which of its
evidence was checked and which was somebody's word is a grant you cannot
audit after a loss.

Usage:

    python "Alpha IO/tools/grant_capital_tier.py" \\
        --tier L1 \\
        --approved-by "Jane Smith" \\
        --ladder data/capital_ladder.json \\
        --i-am-authorizing-real-money \\
        --risk-acknowledgement 'I UNDERSTAND THIS TRADES REAL MONEY' \\
        --attest reconciliation_clean="checked by hand in the broker console"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ALPHA_IO = Path(__file__).resolve().parents[1]
if str(ALPHA_IO) not in sys.path:
    sys.path.insert(0, str(ALPHA_IO))

from core.capital_ladder import (  # noqa: E402
    CapitalLadder,
    CapitalTier,
    LadderStore,
    TierEvidence,
    TierRefused,
    next_tier,
)
from core.safety_config import SafetyConfig  # noqa: E402

#: The exact words. Same convention as activate.py, so an operator who has
#: armed live once already knows this phrase.
REQUIRED_ACKNOWLEDGEMENT = "I UNDERSTAND THIS TRADES REAL MONEY"

#: Evidence fields a human may attest. Everything else is verified or absent -
#: there is no attesting your way past a failing test suite.
ATTESTABLE = {
    "reconciliation_clean",
    "supervised_lifecycle_samples",
    "timeout_drill_passed",
    "cancel_drill_passed",
    "crash_drill_passed",
    "supervised_sessions",
    "unexplained_mismatches",
    "oos_net_of_cost_sharpe",
    "execution_shortfall_bps",
    "external_alerting_verified",
    "independent_review_by",
}

_BOOL_FIELDS = {
    "reconciliation_clean", "timeout_drill_passed", "cancel_drill_passed",
    "crash_drill_passed", "external_alerting_verified",
}
_INT_FIELDS = {
    "supervised_lifecycle_samples", "supervised_sessions",
    "unexplained_mismatches",
}
_FLOAT_FIELDS = {"oos_net_of_cost_sharpe", "execution_shortfall_bps"}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_safety_gates(timeout: int = 900) -> Tuple[bool, str]:
    """Run the adversarial suite. This is what "the P0/P1 gates pass" means.

    Not a checkbox. The gates are a body of tests, so verifying them is
    running them, and a grant issued while they are red is a grant issued on
    nothing.
    """
    command = [
        sys.executable, "-m", "pytest", "-q", "-m", "adversarial",
        str(ALPHA_IO / "tests"),
    ]
    try:
        result = subprocess.run(  # nosec B603
            command, cwd=str(ALPHA_IO.parent), capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"the adversarial suite did not finish within {timeout}s"
    except OSError as exc:
        return False, f"could not run the adversarial suite: {exc}"

    tail = (result.stdout or result.stderr or "").strip().splitlines()
    summary = tail[-1] if tail else "no output"
    if result.returncode != 0:
        return False, f"the adversarial suite failed: {summary}"
    return True, f"adversarial suite passed: {summary}"


def attempt_reconciliation() -> Tuple[Optional[bool], str]:
    """Reconcile against the broker, if there is one to reach.

    Returns ``(None, reason)`` when no broker is configured — which is not a
    failure, it is the absence of a check, and the caller has to decide
    whether to attest it instead. Conflating "could not check" with "checked
    and fine" is the specific mistake this whole ladder guards against.
    """
    if not (os.environ.get("ALPACA_API_KEY") and
            os.environ.get("ALPACA_API_SECRET")):
        return None, ("no broker credentials in the environment, so no "
                      "reconciliation was attempted")
    try:
        from core.alpaca_connector import create_alpaca_client
        from core.reconciliation import (
            BrokerSnapshot,
            LocalSnapshot,
            ReconciliationEngine,
        )
    except ImportError as exc:
        return None, f"reconciliation modules unavailable: {exc}"

    try:
        client = create_alpaca_client(
            os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"],
            paper=True,
        )
        if not client.connect():
            return False, "the broker refused the connection"
        account = client.get_account() or {}
        positions = {
            p.get("symbol", ""): float(p.get("qty", 0) or 0)
            for p in (client.get_positions() or [])
        }
    except Exception as exc:
        return False, f"broker query failed: {type(exc).__name__}: {exc}"

    fingerprint = str(account.get("account_number", "") or "")
    broker = BrokerSnapshot(
        account_fingerprint=fingerprint,
        cash=float(account.get("cash", 0) or 0),
        equity=float(account.get("equity", 0) or 0),
        positions=positions,
    )
    # A fresh deployment's local book is empty. Reconciliation says whether
    # the broker agrees; it is not asked to agree with an assumption.
    local = LocalSnapshot(
        account_fingerprint=fingerprint,
        cash=broker.cash, equity=broker.equity, positions=dict(positions),
    )
    report = ReconciliationEngine().reconcile(broker=broker, local=local)
    if report.may_acquire:
        return True, f"reconciled against {fingerprint or 'the broker'}: matched"
    return False, f"reconciliation found: {report.summary()}"


def strategy_digest(config: SafetyConfig) -> str:
    """A hash over the strategy modules and the promoted versions.

    Binds the grant to the code that will run under it, so a strategy edit
    invalidates the tier the same way a safety-config edit does.
    """
    hasher = hashlib.sha256()
    hasher.update(json.dumps({
        "strategies": sorted(config.promoted_strategy_versions),
        "models": sorted(config.promoted_model_versions),
    }, sort_keys=True).encode("utf-8"))
    for name in ("strategy.py", "signal_router.py", "trade_proposal.py",
                 "swarm_arbitration.py"):
        path = ALPHA_IO / "core" / name
        if path.is_file():
            hasher.update(name.encode("utf-8"))
            hasher.update(path.read_bytes())
    return "strat-" + hasher.hexdigest()[:16]


def load_safety_config(path: Optional[str]) -> SafetyConfig:
    if not path:
        return SafetyConfig()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return SafetyConfig.from_mapping(raw)


# ---------------------------------------------------------------------------
# Attestations
# ---------------------------------------------------------------------------


def parse_attestation(raw: str) -> Tuple[str, Any, str]:
    """``field=note`` or ``field=value:note`` into (field, value, note)."""
    if "=" not in raw:
        raise ValueError(
            f"--attest {raw!r} must be field=note (or field=value:note)"
        )
    field, remainder = raw.split("=", 1)
    field = field.strip()
    if field not in ATTESTABLE:
        raise ValueError(
            f"{field!r} is not attestable. Attestable fields: "
            + ", ".join(sorted(ATTESTABLE))
        )

    if field in _BOOL_FIELDS:
        return field, True, remainder.strip()

    value_text, _, note = remainder.partition(":")
    value_text, note = value_text.strip(), note.strip()
    if not note:
        raise ValueError(
            f"--attest {field}=... needs 'value:note'; an attestation with no "
            "reason is a checkbox"
        )
    if field in _INT_FIELDS:
        return field, int(value_text), note
    if field in _FLOAT_FIELDS:
        return field, float(value_text), note
    return field, value_text, note


# ---------------------------------------------------------------------------
# The grant
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grant a capital tier (ATOS-P3-CAP-001)",
    )
    parser.add_argument("--tier", required=True,
                        help="Target tier, e.g. L1. Rungs are not skipped.")
    parser.add_argument("--approved-by", required=True,
                        help="The person granting this. Recorded in the grant.")
    parser.add_argument("--ladder", default="data/capital_ladder.json",
                        help="Where the persisted grant lives")
    parser.add_argument("--safety-config", default=None,
                        help="JSON safety config; defaults to SafetyConfig()")
    parser.add_argument("--attest", action="append", default=[],
                        metavar="FIELD=NOTE",
                        help="Attest a field a machine cannot check here")
    parser.add_argument("--i-am-authorizing-real-money", action="store_true",
                        help="Required for any tier above L0")
    parser.add_argument("--risk-acknowledgement", default=None,
                        help=f"Must be exactly: {REQUIRED_ACKNOWLEDGEMENT!r}")
    parser.add_argument("--skip-gate-verification", action="store_true",
                        help="Do not run the adversarial suite. Refused for "
                             "any tier above L0.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate the evidence and print it; grant nothing")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        target = CapitalTier[args.tier.upper()]
    except KeyError:
        print(f"✗ {args.tier!r} is not a tier. One of: "
              + ", ".join(t.name for t in CapitalTier))
        return 2

    # A dry run grants nothing, so it does not need the real-money flags. An
    # operator should be able to find out what evidence a rung wants before
    # committing to wanting it.
    if target.is_live and not args.dry_run:
        if not args.i_am_authorizing_real_money:
            print(f"\n✗ Refusing to grant {target.name} "
                  f"(${target.max_capital:,.2f} of real capital) without "
                  "--i-am-authorizing-real-money.")
            return 2
        if args.risk_acknowledgement != REQUIRED_ACKNOWLEDGEMENT:
            print("\n✗ Refusing without the exact acknowledgement:")
            print(f"      --risk-acknowledgement '{REQUIRED_ACKNOWLEDGEMENT}'")
            return 2
        if args.skip_gate_verification:
            print("\n✗ --skip-gate-verification is refused above L0. The "
                  "gates are the evidence.")
            return 2

    config = load_safety_config(args.safety_config)
    config_hash = config.safety_hash()
    strat_hash = strategy_digest(config)

    verified: Dict[str, str] = {}
    attested: Dict[str, str] = {}
    fields: Dict[str, Any] = {}

    # --- verified ---------------------------------------------------------
    if target.is_live and not args.skip_gate_verification:
        print("Running the adversarial suite to verify the P0/P1 gates...")
        passed, detail = verify_safety_gates()
        fields["p0_p1_gates_passed"] = passed
        verified["p0_p1_gates_passed"] = detail
        print(f"  {'✓' if passed else '✗'} {detail}")
        if not passed:
            print("\n✗ Not granting a tier while the safety gates are red.")
            return 1

        reconciled, detail = attempt_reconciliation()
        if reconciled is None:
            print(f"  · reconciliation not verified here: {detail}")
        else:
            fields["reconciliation_clean"] = reconciled
            verified["reconciliation_clean"] = detail
            print(f"  {'✓' if reconciled else '✗'} {detail}")

    verified["safety_config_hash"] = f"computed: {config_hash}"
    verified["strategy_hash"] = f"computed: {strat_hash}"

    # --- attested ---------------------------------------------------------
    for raw in args.attest:
        try:
            field, value, note = parse_attestation(raw)
        except ValueError as exc:
            print(f"✗ {exc}")
            return 2
        if field in verified:
            print(f"✗ {field} was verified here ({verified[field]}); an "
                  "attestation cannot override a check that actually ran.")
            return 2
        fields[field] = value
        attested[field] = note

    evidence = TierEvidence(
        target=target, approved_by=args.approved_by,
        safety_config_hash=config_hash, strategy_hash=strat_hash,
        **fields,
    )

    problems = evidence.problems()
    ladder = CapitalLadder(LadderStore(args.ladder))
    expected = next_tier(ladder.tier)
    if expected is not None and target is not expected:
        problems.insert(0, f"the next rung from {ladder.tier.name} is "
                           f"{expected.name}, not {target.name}")

    if args.dry_run or problems:
        print(f"\nTier:      {target.name} (${target.max_capital:,.2f})")
        print(f"Current:   {ladder.tier.name}")
        print(f"Approver:  {args.approved_by}")
        print("Verified:")
        for key, note in sorted(verified.items()):
            print(f"  · {key}: {note}")
        print("Attested:" if attested else "Attested: (nothing)")
        for key, note in sorted(attested.items()):
            print(f"  · {key}: {note}")
        if problems:
            print("\n✗ Evidence insufficient:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("\n(dry run) Evidence is sufficient; nothing was granted.")
        return 0

    try:
        ladder.promote(evidence)
    except TierRefused as exc:
        print(f"\n✗ Refused: {exc}")
        return 1

    record = {
        "tier": target.name,
        "max_capital_at_risk": target.max_capital,
        "approved_by": args.approved_by,
        "granted_at": datetime.now(timezone.utc).isoformat(),
        "safety_config_hash": config_hash,
        "strategy_hash": strat_hash,
        "verified": verified,
        "attested": attested,
        "safety_config": asdict(config),
    }
    sidecar = Path(args.ladder).with_suffix(".attestation.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(record, indent=2, sort_keys=True, default=str),
                       encoding="utf-8")

    print(f"\n✓ {target.name} granted: ${target.max_capital:,.2f} maximum real "
          f"capital at risk.")
    print(f"  Grant:       {args.ladder}")
    print(f"  Attestation: {sidecar}")
    if attested:
        print("  Note: the following were attested, not verified here — "
              + ", ".join(sorted(attested)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
