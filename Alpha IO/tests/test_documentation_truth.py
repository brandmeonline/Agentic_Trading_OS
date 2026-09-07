"""Documentation truth pass — ULTRAPLAN sections 28 and 29.

Section 28 asks for a search-and-correct over the repository's claims. Doing
that once is worth little: the banners came back the last time somebody
tidied them, and a claim in a docstring costs nothing to write. So the pass
is a test.

The scan is deliberately blunt. Every match is either fixed or listed here
with the reason it is not a claim - a disclaimer that quotes the phrase it is
withdrawing, a competitor's description in a market assessment - and the list
is short enough to read. A phrase nobody can justify has nowhere to go except
out of the tree.

The credential half asserts the properties section 29 requires, against the
module rather than against its docstring, because the docstring was the
problem.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.credentials import (  # noqa: E402
    CredentialConfig,
    CredentialEncryptionError,
    CredentialsManager,
    EncryptionHelper,
)

pytestmark = pytest.mark.adversarial

REPO = Path(__file__).resolve().parents[2]

#: Phrases that assert a level of assurance this system has not demonstrated.
BANNED_CLAIMS = (
    # "not production ready" is the accurate statement and is allowed; the
    # bare claim is not.
    r"(?<!not )production[- ]ready",
    r"production[- ]grade",
    r"enterprise[- ]grade",
    r"bank[- ]grade",
    r"military[- ]grade",
    r"automatic (credential )?rotation",
    r"outperform institutional",
)

#: Files where a banned phrase appears because it is being withdrawn or
#: attributed, with the reason. Anything not here has to not say it.
CLAIM_EXEMPTIONS = {
    # The remediation specification quotes the claims it is asking us to fix.
    "docs/ULTRAPLAN_PRODUCTION_HARDENING.md":
        "the spec that requires this pass; it quotes the phrases to search for",
    # The threat model and the credential module both quote the old claim in
    # the act of retracting it.
    "docs/threat-model-credentials.md":
        "quotes the retracted claim while retracting it",
    "Alpha IO/core/credentials.py":
        "quotes the retracted claim in the docstring that replaces it",
    # This file lists the phrases in order to search for them.
    "Alpha IO/tests/test_documentation_truth.py":
        "contains the search patterns themselves",
    # A market assessment describing other people's products.
    "Alpha IO/docs/READINESS_ASSESSMENT.md":
        "describes competitors, and states our own status as not ready",
}

SCANNED_SUFFIXES = (".py", ".md", ".html", ".yml", ".yaml", ".txt")
SKIPPED_DIRECTORIES = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules",
    "htmlcov",
}


def _scan() -> dict:
    """Every banned phrase in the tree, by repository-relative path."""
    pattern = re.compile("|".join(BANNED_CLAIMS), re.IGNORECASE)
    found: dict = {}
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIPPED_DIRECTORIES for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        matches = [m.group(0) for m in pattern.finditer(text)]
        if matches:
            found[str(path.relative_to(REPO))] = sorted(set(matches))
    return found


# ---------------------------------------------------------------------------
# The claims
# ---------------------------------------------------------------------------


def test_no_unexplained_assurance_claims_remain():
    unexplained = {
        path: matches for path, matches in _scan().items()
        if path not in CLAIM_EXEMPTIONS
    }
    assert unexplained == {}, (
        "these files assert a level of assurance the system has not "
        "demonstrated; fix the claim or add it to CLAIM_EXEMPTIONS with a "
        "reason: " + repr(unexplained)
    )


def test_the_scan_can_actually_find_something():
    """Otherwise the test above passes because the pattern is broken."""
    pattern = re.compile("|".join(BANNED_CLAIMS), re.IGNORECASE)
    for sample in ("This is production-ready.", "Enterprise-Grade monitoring",
                   "automatic credential rotation", "we outperform "
                   "institutional desks"):
        assert pattern.search(sample), sample


def test_every_exemption_is_still_needed():
    """An exemption for a file that no longer says it is stale, and a stale
    exemption is how the next real claim slips through."""
    found = _scan()
    unused = sorted(set(CLAIM_EXEMPTIONS) - set(found))
    assert unused == [], f"exemptions no longer needed: {unused}"


def test_every_exemption_gives_a_reason():
    for path, reason in CLAIM_EXEMPTIONS.items():
        assert len(reason) > 20, path


def test_the_readme_says_plainly_that_it_is_not_production_ready():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "not production ready" in readme.lower()
    assert "/api/ready" in readme


def test_the_derivative_claim_is_gone_from_both_readmes():
    for name in ("README.md", "Alpha IO/docs/README.md"):
        lines = [line.strip()
                 for line in (REPO / name).read_text(encoding="utf-8").splitlines()]
        assert "Execution via futures, options, spreads" not in lines, name
        assert "- Execution via futures, options, spreads" not in lines, name


def test_the_login_page_no_longer_carries_a_readiness_banner():
    page = (REPO / "Alpha IO" / "web" / "templates" / "login.html").read_text(
        encoding="utf-8"
    )
    assert "Production-Ready" not in page


def test_the_exchange_connector_docstring_admits_it_is_simulated():
    """It claimed production-grade connectivity while returning mock data."""
    source = (REPO / "Alpha IO" / "core" / "exchange_connectors.py").read_text(
        encoding="utf-8"
    )
    header = source[:source.index('"""', 5)]
    assert "simulated" in header.lower()
    assert "_mock_request" in header


# ---------------------------------------------------------------------------
# Section 29: the credential manager
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_PASSWORD", raising=False)
    return str(tmp_path / "creds")


def test_a_credential_survives_a_change_of_process_id(store, monkeypatch):
    """The master key included os.getpid(), so it changed every run and the
    store was write-only. The failure was silent: an empty credential list."""
    monkeypatch.setattr(os, "getpid", lambda: 1111)
    first = CredentialsManager(CredentialConfig(storage_path=store))
    first.add_credential("alpaca", "KEYVALUE", "SECRETVALUE")

    monkeypatch.setattr(os, "getpid", lambda: 2222)
    second = CredentialsManager(CredentialConfig(storage_path=store))
    second.load()

    assert [c["name"] for c in second.list_credentials()] == ["alpaca"]
    assert second.get_credential("alpaca").api_secret == "SECRETVALUE"


def test_the_derived_key_does_not_depend_on_the_process(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_PASSWORD", raising=False)
    helper = EncryptionHelper()

    monkeypatch.setattr(os, "getpid", lambda: 4242)
    first = helper._get_machine_key()
    monkeypatch.setattr(os, "getpid", lambda: 9999)
    assert helper._get_machine_key() == first


def test_the_derived_key_is_refused_for_production(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_PASSWORD", raising=False)
    with pytest.raises(CredentialEncryptionError, match="CREDENTIALS_PASSWORD"):
        EncryptionHelper().initialize(production=True)


def test_an_externally_supplied_key_is_accepted_for_production(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_PASSWORD", "supplied-by-a-secret-manager")
    EncryptionHelper().initialize(production=True)      # must not raise


def test_plaintext_storage_is_refused_by_default(store):
    manager = CredentialsManager(
        CredentialConfig(storage_path=store, use_encryption=False)
    )
    with pytest.raises(CredentialEncryptionError, match="refused"):
        manager.add_credential("dev", "KEY", "SECRET")


def test_plaintext_storage_works_when_explicitly_permitted(store):
    manager = CredentialsManager(CredentialConfig(
        storage_path=store, use_encryption=False,
        allow_plaintext_development_only=True,
    ))
    manager.add_credential("dev", "KEY", "SECRET", environment="testnet")
    assert (Path(store) / "credentials.json").is_file()


def test_plaintext_storage_is_refused_for_a_production_credential(store):
    """Explicitly permitted is not the same as permitted for anything."""
    manager = CredentialsManager(CredentialConfig(
        storage_path=store, use_encryption=False,
        allow_plaintext_development_only=True,
    ))
    with pytest.raises(CredentialEncryptionError, match="production"):
        manager.add_credential("live", "KEY", "SECRET", environment="production")


def test_reading_a_plaintext_store_is_gated_too(store):
    permitted = CredentialsManager(CredentialConfig(
        storage_path=store, use_encryption=False,
        allow_plaintext_development_only=True,
    ))
    permitted.add_credential("dev", "KEY", "SECRET")

    refused = CredentialsManager(
        CredentialConfig(storage_path=store, use_encryption=False)
    )
    with pytest.raises(CredentialEncryptionError):
        refused.load()


def test_rotation_raises_rather_than_pretending(store):
    """A rotate() that re-saved the same key would leave an operator believing
    a leaked credential had been replaced, and they would stop looking."""
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.add_credential("alpaca", "KEY", "SECRET")

    with pytest.raises(NotImplementedError) as caught:
        manager.rotate("alpaca")
    assert "provider's console" in str(caught.value)


def test_local_expiry_is_reported_as_local(store):
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.add_credential("soon", "KEY", "SECRET", expires_days=3)
    manager.add_credential("later", "KEY", "SECRET", expires_days=90)

    assert manager.expiring_soon(within_days=7) == ["soon"]


def test_the_environment_of_a_loaded_credential_is_never_guessed(store, monkeypatch):
    """It used to be labelled "production" whenever no prefix was passed."""
    monkeypatch.setenv("BINANCE_API_KEY", "KEY")
    monkeypatch.setenv("BINANCE_API_SECRET", "SECRET")

    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.from_environment()

    listed = manager.list_credentials()
    assert listed and all(c["environment"] == "testnet" for c in listed)

    manager.from_environment(environment="production")
    assert any(c["environment"] == "production"
               for c in manager.list_credentials())


def test_the_listing_exposes_metadata_only(store):
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.add_credential("alpaca", "PKVERYSECRETKEY", "andthesecret")

    rendered = repr(manager.list_credentials())
    assert "PKVERYSECRETKEY" not in rendered
    assert "andthesecret" not in rendered
    assert "alpaca" in rendered


def test_the_access_log_records_actions_not_values(store):
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.add_credential("alpaca", "PKVERYSECRETKEY", "andthesecret")
    manager.get_credential("alpaca")

    rendered = repr(manager.get_access_log())
    assert "PKVERYSECRETKEY" not in rendered
    assert "andthesecret" not in rendered
    assert "add" in rendered and "get" in rendered


def test_a_failed_decryption_says_what_happened_without_the_payload(
    store, monkeypatch, caplog
):
    """The old message was "Failed to load encrypted credentials: " and
    nothing - InvalidToken stringifies to an empty string."""
    monkeypatch.setenv("CREDENTIALS_PASSWORD", "the-right-one")
    CredentialsManager(
        CredentialConfig(storage_path=store)
    ).add_credential("alpaca", "KEY", "SECRET")

    monkeypatch.setenv("CREDENTIALS_PASSWORD", "the-wrong-one")
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    manager.load()

    assert manager.list_credentials() == []
    assert "CREDENTIALS_PASSWORD" in caplog.text
    assert "SECRET" not in caplog.text


def test_the_credential_paths_are_gitignored():
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".credentials", "*.salt"):
        assert pattern in ignore, pattern


def test_the_threat_model_document_exists_and_states_the_limits():
    document = (REPO / "docs" / "threat-model-credentials.md").read_text(
        encoding="utf-8"
    )
    for required in ("Not defended", "There is none", "secret manager",
                     "development fallback"):
        assert required in document, required


def test_a_temp_directory_is_not_left_behind_by_the_fixtures():
    """Housekeeping: these tests write credential files, so make sure the
    system temp directory is not accumulating them."""
    stray = [p for p in Path(tempfile.gettempdir()).glob(".ladder-*")]
    assert stray == []


def test_expiry_is_a_local_marker_with_no_provider_effect(store):
    manager = CredentialsManager(CredentialConfig(storage_path=store))
    credential = manager.add_credential("alpaca", "KEY", "SECRET",
                                        expires_days=-1)
    assert credential.is_expired()
    # Still returned: local expiry does not revoke anything, and pretending
    # otherwise would hide a live key from the operator who needs to revoke it.
    assert manager.get_credential("alpaca") is not None
    assert credential.expires_at < datetime.now() + timedelta(seconds=1)
