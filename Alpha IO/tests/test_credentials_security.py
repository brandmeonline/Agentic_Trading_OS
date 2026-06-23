import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.credentials as credentials_module
from core.credentials import (
    CredentialConfig,
    CredentialEncryptionError,
    CredentialsManager,
    EncryptionHelper,
)


def test_encryption_requires_real_crypto_backend(monkeypatch):
    monkeypatch.setattr(credentials_module, "_try_import_cryptography", lambda: False)

    helper = EncryptionHelper(password="test-password")

    with pytest.raises(CredentialEncryptionError):
        helper.encrypt("secret payload")


def test_legacy_fallback_payload_is_refused():
    helper = EncryptionHelper(password="test-password")

    with pytest.raises(CredentialEncryptionError):
        helper.decrypt(b"FALLBACK:c2VjcmV0")


def test_credentials_manager_does_not_write_fallback_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials_module, "_try_import_cryptography", lambda: False)

    manager = CredentialsManager(CredentialConfig(
        storage_path=str(tmp_path),
        use_encryption=True,
    ))

    with pytest.raises(CredentialEncryptionError):
        manager.add_credential("alpaca", "key", "secret", exchange="alpaca")

    credential_file = tmp_path / "credentials.enc"
    assert not credential_file.exists()
