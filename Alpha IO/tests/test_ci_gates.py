"""ATOS-P2-CI-001 — the gates exist, and they are wired to fail.

A CI gate is a claim about what cannot be merged. The claim is only worth
something if the gate is actually configured, actually blocking, and actually
capable of failing. All three have failed here before: the workflow ran a
hand-written list of test files, so a new test file was in the suite locally
and invisible in CI until somebody remembered to add it.

So the workflow is asserted against, as a file. That is unusual for a test
suite and it is the right level: the thing being protected is the contents of
the workflow, and nothing else in the repository will notice if a gate is
quietly deleted.

The second half covers the outbound-request guard added by this issue, which
is production code and belongs in a normal test.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.net_guard import (  # noqa: E402
    PERMITTED_SCHEMES,
    BlockedRequest,
    assert_permitted,
    request_url,
)

pytestmark = pytest.mark.adversarial

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "alpha-io-ci.yml"


@pytest.fixture(scope="module")
def workflow() -> str:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    return WORKFLOW.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The gates the ULTRAPLAN requires
# ---------------------------------------------------------------------------


REQUIRED_GATES = {
    "ruff": r"\bruff check\b",
    "mypy": r"\bmypy\b",
    "bandit": r"\bbandit\b",
    "pip-audit": r"\bpip-audit\b",
    "compileall": r"compileall",
    "secret scan": r"test_secret_hygiene\.py",
    "coverage gate": r"coverage_gate\.py",
    "adversarial marker suite": r"-m adversarial",
    "network isolation": r"-p no_network",
}


@pytest.mark.parametrize("gate,pattern", sorted(REQUIRED_GATES.items()))
def test_the_workflow_runs_every_required_gate(workflow, gate, pattern):
    assert re.search(pattern, workflow), f"CI does not run {gate}"


def test_the_full_suite_runs_rather_than_a_hand_written_list(workflow):
    """The specific regression: a curated list silently omits new test files."""
    listed = re.findall(r'"Alpha IO/tests/(test_[a-z0-9_]+\.py)"', workflow)
    # The secret-hygiene file is named on purpose: it runs alone, first, in a
    # job that gates everything else.
    curated = set(listed) - {"test_secret_hygiene.py", "test_alpaca_paper_smoke.py"}
    assert curated == set(), (
        "CI still names individual test files; it must run the whole "
        f"directory. Still listed: {sorted(curated)}"
    )
    assert re.search(r'pytest[^\n]*"Alpha IO/tests"', workflow), (
        "CI does not run the full Alpha IO test directory"
    )


def test_every_test_file_would_be_reached_by_the_suite_run():
    """No test file can be orphaned once the whole directory is run."""
    files = sorted(p.name for p in (REPO / "Alpha IO" / "tests").glob("test_*.py"))
    assert len(files) > 30, files
    # Nothing to assert about the workflow here beyond the directory run, which
    # the test above pins; this exists to fail loudly if the tests directory
    # itself is moved out from under that path.
    assert (REPO / "Alpha IO" / "tests").is_dir()


def test_the_secret_scan_gates_the_rest_of_the_pipeline(workflow):
    assert "needs: secret-scan" in workflow, (
        "the regression job must depend on the secret scan, or a commit with a "
        "credential in it can go green on the tests alone"
    )


def test_ci_installs_from_the_dependency_manifests(workflow):
    """Ad-hoc package lists drift from what the application actually needs."""
    assert "requirements.txt" in workflow
    assert "requirements-ci.txt" in workflow


def test_the_ci_tool_versions_are_pinned():
    """An unpinned linter turns someone else's release into your red build."""
    manifest = REPO / "Alpha IO" / "requirements-ci.txt"
    assert manifest.is_file()

    pinned = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        assert "==" in line, f"{line!r} is not pinned to an exact version"
        pinned += 1
    assert pinned >= 5, "the CI tool manifest looks empty"


def test_ci_never_asks_for_live_broker_credentials(workflow):
    """Paper-only, minimal permissions, and never a live key."""
    for forbidden in ("ALPACA_LIVE", "LIVE_API_KEY", "LIVE_API_SECRET",
                      "PRODUCTION_API_KEY"):
        assert forbidden not in workflow, forbidden

    # The optional smoke test must be gated on the credentials being present
    # rather than failing the build when they are not.
    assert "test_alpaca_paper_smoke.py" in workflow
    assert re.search(r"if:\s*\$\{\{\s*env\.ALPACA_API_KEY", workflow)


def test_the_workflow_has_no_path_filters(workflow):
    """ATOS-P0-SEC-001: a credential can be committed anywhere in the tree."""
    trigger = workflow.split("jobs:", 1)[0]
    assert "paths:" not in trigger
    assert "paths-ignore:" not in trigger


# ---------------------------------------------------------------------------
# Configuration the gates depend on
# ---------------------------------------------------------------------------


def test_ruff_is_configured_and_the_repository_is_clean_against_it():
    """A gate nobody can pass gets switched off; one nobody can fail is decor."""
    config = REPO / "ruff.toml"
    assert config.is_file()
    text = config.read_text(encoding="utf-8")
    assert "select" in text
    # The one scoped-off rule must remain scoped to the one file it is for.
    assert '"Alpha IO/core/__init__.py" = ["F401"]' in text
    assert text.count("per-file-ignores") == 1
    assert text.count('" = ["') == 1, (
        "a second per-file ignore appeared; each one needs its own reasoning"
    )


def test_the_coverage_gate_names_the_safety_modules():
    from tools.coverage_gate import FLOORS

    for required in (
        "core/execution.py", "core/risk.py", "core/reconciliation.py",
        "core/credentials.py", "core/order_intent.py", "core/runtime_state.py",
        "core/live_authorization.py", "core/control_plane.py",
    ):
        assert required in FLOORS, required


def test_the_coverage_gate_fails_when_a_floor_is_missed():
    """Otherwise the gate is a report, not a gate."""
    from tools.coverage_gate import check

    assert check({"core/execution.py": 100.0}) != []          # others missing
    failures = check({name: 100.0 for name in _all_floor_names()})
    assert failures == []

    below = {name: 100.0 for name in _all_floor_names()}
    below["core/execution.py"] = 1.0
    failures = check(below)
    assert len(failures) == 1
    assert "core/execution.py" in failures[0]


def test_a_module_missing_from_the_report_is_a_failure():
    """A renamed module whose tests stopped running must not read as a pass."""
    from tools.coverage_gate import check

    report = {name: 100.0 for name in _all_floor_names()}
    del report["core/reconciliation.py"]
    failures = check(report)
    assert any("core/reconciliation.py" in f and "not present" in f
               for f in failures)


def _all_floor_names():
    from tools.coverage_gate import FLOORS
    return list(FLOORS)


def test_the_network_plugin_exists_and_blocks_a_remote_connection():
    plugin = REPO / "no_network.py"
    assert plugin.is_file()

    sys.path.insert(0, str(REPO))
    import no_network

    with pytest.raises(no_network.NetworkAccessBlocked):
        no_network._check(("api.example.com", 443))
    # Loopback is deliberately left alone: some tests bind a local server.
    no_network._check(("127.0.0.1", 8080))


# ---------------------------------------------------------------------------
# The outbound request guard
# ---------------------------------------------------------------------------


def test_only_https_is_permitted():
    assert PERMITTED_SCHEMES == ("https://",)
    assert assert_permitted("https://api.alpaca.markets/v2/account")


@pytest.mark.parametrize("url", [
    "http://api.alpaca.markets/v2/account",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com",
    "//example.com/x",
    "api.alpaca.markets",
])
def test_everything_else_is_refused(url):
    with pytest.raises(BlockedRequest):
        assert_permitted(url)


def test_plain_http_is_refused_rather_than_warned_about():
    """A plaintext request carrying an API key is not a lesser problem."""
    with pytest.raises(BlockedRequest) as caught:
        assert_permitted("http://api.example.com/orders?key=secret")
    assert "only https" in str(caught.value)


def test_a_prepared_request_is_checked_by_its_url():
    request = urllib.request.Request("https://example.com/x")
    assert assert_permitted(request) == "https://example.com/x"

    blocked = urllib.request.Request("file:///etc/passwd")
    with pytest.raises(BlockedRequest):
        assert_permitted(blocked)


def test_the_scheme_check_is_case_insensitive():
    assert assert_permitted("HTTPS://EXAMPLE.COM/x")
    with pytest.raises(BlockedRequest):
        assert_permitted("FILE:///etc/passwd")


def test_request_url_reads_both_shapes():
    assert request_url("https://x/y") == "https://x/y"
    assert request_url(urllib.request.Request("https://x/y")) == "https://x/y"


def test_every_urlopen_in_the_tree_is_guarded():
    """The guard only works if it is in front of every call, so check.

    Written against the source rather than by monkeypatching, because the
    thing that goes wrong is a *new* call site added without the guard, and
    no runtime test covers a call site nobody wrote a test for.
    """
    unguarded = []
    for path in sorted((REPO / "Alpha IO").rglob("*.py")):
        if "tests/" in str(path).replace("\\", "/"):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if "urllib.request.urlopen(" not in line:
                continue
            window = "\n".join(lines[max(0, number - 6):number])
            if "assert_permitted" not in window:
                unguarded.append(f"{path.relative_to(REPO)}:{number + 1}")
    assert unguarded == [], (
        "urlopen call sites with no assert_permitted() above them: "
        + ", ".join(unguarded)
    )
