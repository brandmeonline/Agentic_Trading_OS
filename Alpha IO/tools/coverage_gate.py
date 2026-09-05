"""Per-module coverage floors for the safety-critical code — ATOS-P2-CI-001.

A single repository-wide coverage percentage is close to useless here. This
tree is 19,000 statements, most of them analytics and demo surfaces; a global
threshold can be met while the order lifecycle is untested, and it moves when
somebody adds a well-tested chart module. What matters is the coverage of the
code that can lose money.

So the floors are per module, and they are *ratchet* floors: each is set at or
just below what the module actually has today, and it may go up but not down.
The point is not to hit a number, it is that a change which removes a test from
the execution engine fails the build instead of quietly lowering an average.

Where a floor is well below where it should be, it says so. A comment naming
the debt is more useful than a floor that pretends the debt is not there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

#: module path -> (floor percent, note)
FLOORS: Dict[str, Tuple[int, str]] = {
    # Order lifecycle and money.
    "core/execution.py": (75, "algo-execution paths (TWAP/VWAP/iceberg) are "
                              "largely untested; ATOS-P3-EXEC-001"),
    "core/order_intent.py": (95, ""),
    "core/money.py": (90, ""),
    "core/exposure.py": (95, ""),

    # Risk.
    "core/risk.py": (78, "the legacy sizing and reporting paths are thin; "
                         "the durable-anchor paths are covered"),
    "core/risk_anchors.py": (95, ""),

    # Broker truth.
    "core/reconciliation.py": (95, ""),
    "core/recurring_reconciliation.py": (90, ""),
    "core/runtime_state.py": (95, ""),
    "core/readiness.py": (95, ""),

    # Authorization and configuration authority.
    "core/live_authorization.py": (95, ""),
    "core/control_plane.py": (95, ""),
    "core/safety_config.py": (90, ""),
    "core/persistence_policy.py": (90, ""),

    # Security-sensitive.
    "core/credentials.py": (60, "the CLI and keyring-adjacent paths are still "
                                "untested; the storage, gating and redaction "
                                "paths are covered by the section 29 pass"),
    "core/net_guard.py": (95, ""),

    # Backtest honesty.
    "core/leakage.py": (90, ""),
    "core/fill_model.py": (90, ""),
    "core/model_governance.py": (90, ""),
    "core/swarm_arbitration.py": (90, ""),
    "core/tuning_governance.py": (90, ""),
    "core/auto_tuner.py": (90, ""),
    "core/venue_rules.py": (90, ""),
    "core/capital_ladder.py": (90, ""),
    "tools/grant_capital_tier.py": (75, "the live broker reconciliation "
                                        "path cannot run without an account"),

    # Operator-facing truth.
    "core/operator_status.py": (95, ""),
    "core/alerting.py": (90, ""),
}


def load(report: Path) -> Dict[str, float]:
    data = json.loads(report.read_text(encoding="utf-8"))
    percentages: Dict[str, float] = {}
    for name, entry in data["files"].items():
        key = name.replace("\\", "/")
        percentages[key] = entry["summary"]["percent_covered"]
    return percentages


def check(percentages: Dict[str, float]) -> List[str]:
    failures: List[str] = []
    for module, (floor, note) in sorted(FLOORS.items()):
        actual = None
        for key, value in percentages.items():
            if key == module or key.endswith("/" + module):
                actual = value
                break
        if actual is None:
            # A module that vanished from the report is a failure, not a pass.
            # It usually means it was renamed and its tests stopped running.
            failures.append(f"{module}: not present in the coverage report")
            continue
        if actual + 1e-9 < floor:
            detail = f" ({note})" if note else ""
            failures.append(
                f"{module}: {actual:.1f}% is below the {floor}% floor{detail}"
            )
    return failures


def main(argv: List[str]) -> int:
    report = Path(argv[1] if len(argv) > 1 else "coverage.json")
    if not report.is_file():
        print(f"coverage report {report} not found", file=sys.stderr)
        return 2

    percentages = load(report)
    failures = check(percentages)

    for module, (floor, _) in sorted(FLOORS.items()):
        actual = next(
            (v for k, v in percentages.items()
             if k == module or k.endswith("/" + module)), None
        )
        shown = f"{actual:5.1f}%" if actual is not None else "absent"
        mark = "FAIL" if any(module in f for f in failures) else "ok  "
        print(f"  {mark} {module:45s} {shown}  floor {floor}%")

    if failures:
        print("\nCoverage floors not met:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nAll safety-module coverage floors met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
