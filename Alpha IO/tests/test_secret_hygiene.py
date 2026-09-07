"""Secret hygiene guards — ATOS-P0-SEC-001.

INV-ATOS-014: no real secret appears in tracked repository content.

These tests are deliberately structural rather than clever. The strongest
guarantee comes from the shape of the repository (which credential-bearing
files are allowed to be tracked at all), not from pattern matching. The
entropy heuristics are a second net for secrets pasted into source; CI runs a
dedicated secret scanner as the third.

Regression origin: a real Alpaca paper key/secret pair was committed at
``Alpha IO/config/alpaca_credentials.json``. The secret was 44 alphanumeric
characters, so any guard here must catch bare high-entropy alphanumeric
values, not only vendor-prefixed tokens.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Files whose *purpose* is to carry credential-shaped content. Only ".example."
# variants of these may be tracked, and only with placeholder values.
CREDENTIAL_FILE_RE = re.compile(r"(credentials|secrets)[^/]*\.json$", re.IGNORECASE)

# Keys whose values are treated as secret-bearing.
SECRET_KEY_RE = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|client[_-]?secret"
    r"|password|passwd|passphrase|token|bearer|private[_-]?key)\b"
)

# High-confidence vendor token shapes. A hit is a failure with no exceptions.
VENDOR_TOKEN_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "json_web_token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "alpaca_key": re.compile(r"\bPK[A-Z0-9]{16,}\b"),
    "alpaca_broker_key": re.compile(r"\bCK[A-Z0-9]{16,}\b"),
}

# Wording that marks a value as an intentional non-secret.
PLACEHOLDER_RE = re.compile(
    r"(?i)(replace[_-]?me|your[_-]|placeholder|example|sample|dummy|fake|not[_-]a[_-]real"
    r"|xxx+|<[^>]+>|\.\.\.|change[_-]?me|paste[_-]|todo|redacted|\*{3,})"
)

# Text extensions worth scanning. Binary and data files are skipped.
SCANNABLE_SUFFIXES = {
    ".py", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".sh", ".bash",
    ".env", ".md", ".txt", ".html", ".js", ".css", ".sql", ".xml", "",
}

# Files that must contain token-shaped strings to do their job. Each is a test
# whose subject is secret handling, so realistic shapes are the fixture: a
# redaction test that only ever sees obviously-fake input proves nothing about
# the real thing. The exemption is narrow and every entry is a test file.
SCAN_EXEMPT_PATHS = {
    # Quotes token shapes while describing what to forbid.
    "docs/ULTRAPLAN_PRODUCTION_HARDENING.md",
    # This file's own detector fixtures.
    "Alpha IO/tests/test_secret_hygiene.py",
    # ATOS-P2-OPS-001: asserts that realistic vendor tokens are scrubbed from
    # alert payloads. The tokens below are invented, but they are invented to
    # look real, which is the point.
    "Alpha IO/tests/test_alerting_escalation.py",
}


def tracked_files() -> list[str]:
    """Every path git currently tracks."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def read_text(path: str) -> str | None:
    """Read a tracked text file, or None if it isn't worth scanning."""
    full = REPO_ROOT / path
    if full.suffix.lower() not in SCANNABLE_SUFFIXES:
        return None
    try:
        if full.stat().st_size > 2_000_000:
            return None
        return full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def shannon_entropy(value: str) -> float:
    """Bits of entropy per character."""
    if not value:
        return 0.0
    counts = {ch: value.count(ch) for ch in set(value)}
    length = len(value)
    return -sum(
        (n / length) * math.log2(n / length) for n in counts.values()
    )


def looks_like_real_secret(value: str) -> bool:
    """True when a string has the shape of a live credential.

    Real keys are long, unbroken, mixed letters and digits, and high entropy.
    Test fixtures in this repo read like ``terminal-test-password`` — separated
    English words — so requiring an unbroken alphanumeric run keeps them out
    while still catching the 26- and 44-character Alpaca values that were
    actually committed.
    """
    if len(value) < 20:
        return False
    if PLACEHOLDER_RE.search(value):
        return False
    if not value.isalnum():
        return False
    has_alpha = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    if not (has_alpha and has_digit):
        return False
    return shannon_entropy(value) >= 3.2


def redact(value: str) -> str:
    """Never let a failure message print the secret it found."""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-3:]} (len={len(value)})"


def iter_json_strings(node, path=""):
    """Yield (json_path, key, string_value) for every string in a document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from iter_json_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_json_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, path.rsplit(".", 1)[-1], node


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------

def test_no_real_credential_file_is_tracked():
    """Only .example. credential files may be tracked."""
    offenders = [
        path for path in tracked_files()
        if CREDENTIAL_FILE_RE.search(path) and ".example." not in path
    ]
    assert not offenders, (
        "Credential files are tracked in git. Revoke the exposed keys at the "
        f"provider, then remove the files: {offenders}"
    )


def test_real_credential_paths_are_gitignored():
    """The conventional credential paths must be ignored, not merely absent."""
    must_be_ignored = [
        "Alpha IO/config/alpaca_credentials.json",
        "Alpha IO/config/binance_credentials.json",
        "Alpha IO/config/master.key",
        ".env.production",
    ]
    # --stdin -z, because repository paths contain spaces ("Alpha IO/...")
    # and git only accepts -z alongside --stdin.
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        input="\0".join(must_be_ignored),
    )
    ignored = {p for p in result.stdout.split("\0") if p}
    missing = [path for path in must_be_ignored if path not in ignored]
    assert not missing, f".gitignore does not cover credential paths: {missing}"


def test_example_credential_file_is_tracked_and_usable():
    """The example must survive the ignore rules and document its schema."""
    example = REPO_ROOT / "Alpha IO/config/alpaca_credentials.example.json"
    assert example.exists(), "alpaca_credentials.example.json is missing"
    assert str(example.relative_to(REPO_ROOT)) in tracked_files(), (
        "The example credential file is not tracked — a .gitignore rule is "
        "swallowing it, so contributors have no schema to copy."
    )
    payload = json.loads(example.read_text(encoding="utf-8"))
    assert "alpaca_paper" in payload, "example lost the alpaca_paper section"
    for field in ("api_key", "api_secret"):
        assert field in payload["alpaca_paper"], f"example lost {field}"


@pytest.mark.parametrize(
    "path",
    [p for p in tracked_files() if CREDENTIAL_FILE_RE.search(p)],
)
def test_tracked_credential_files_hold_only_placeholders(path):
    """Every value in a tracked credential file must be an obvious non-secret."""
    payload = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
    for json_path, key, value in iter_json_strings(payload):
        if not SECRET_KEY_RE.search(key):
            continue
        assert not looks_like_real_secret(value), (
            f"{path}{json_path} looks like a live credential: {redact(value)}"
        )
        assert PLACEHOLDER_RE.search(value), (
            f"{path}{json_path} must use obvious placeholder wording "
            f"(e.g. REPLACE_ME_...), got {redact(value)}"
        )


# ---------------------------------------------------------------------------
# Content scanning
# ---------------------------------------------------------------------------

def test_no_vendor_tokens_in_tracked_files():
    """No recognisable vendor credential appears anywhere in the tree."""
    findings = []
    for path in tracked_files():
        if path in SCAN_EXEMPT_PATHS:
            continue
        text = read_text(path)
        if text is None:
            continue
        for name, pattern in VENDOR_TOKEN_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                findings.append(f"{path}:{line} [{name}] {redact(match.group(0))}")
    assert not findings, "Vendor credentials found in tracked files:\n" + "\n".join(findings)


def test_no_high_entropy_secrets_assigned_in_source():
    """No high-entropy literal is assigned to a secret-named field."""
    assignment = re.compile(
        r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|client[_-]?secret"
        r"|password|passwd|passphrase|token|bearer)\b"
        r"\s*[:=]\s*[\"']([^\"']+)[\"']"
    )
    findings = []
    for path in tracked_files():
        if path in SCAN_EXEMPT_PATHS:
            continue
        text = read_text(path)
        if text is None:
            continue
        for match in assignment.finditer(text):
            value = match.group(2)
            if looks_like_real_secret(value):
                line = text[: match.start()].count("\n") + 1
                findings.append(f"{path}:{line} [{match.group(1)}] {redact(value)}")
    assert not findings, (
        "High-entropy values assigned to secret fields:\n" + "\n".join(findings)
    )


# ---------------------------------------------------------------------------
# The heuristic itself must stay honest
# ---------------------------------------------------------------------------

def test_detector_catches_the_shape_that_was_actually_committed():
    """Guard the regression: bare alphanumeric Alpaca-style key and secret."""
    assert looks_like_real_secret("PKA1B2C3D4E5F6G7H8I9")
    assert looks_like_real_secret("aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7dE9f")
    assert looks_like_real_secret("k7Ht2Qm9Rz4Xb1Nw6Ly8Pv3Cd5Jf0Gs")


def test_detector_does_not_flag_repository_test_fixtures():
    """Existing fixtures read as words, not secrets, and must stay passing."""
    for benign in (
        "terminal-test-password",
        "terminal-test-secret",
        "test_api_key_123",
        "test_secret_456",
        "REPLACE_ME_NOT_A_REAL_ALPACA_SECRET",
        "your-api-key-here",
        "",
    ):
        assert not looks_like_real_secret(benign), f"false positive on {benign!r}"
