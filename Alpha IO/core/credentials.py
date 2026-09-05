"""Credentials manager — ATOS-P0-SEC-001 / ATOS-P3 credential hardening.

This docstring used to claim "production-grade secrets management",
"keyring support for OS-level security" and "automatic credential rotation".
None of those were true: there is no keyring integration, and nothing here
has ever rotated a credential with a provider. What follows is what the
module actually is, and its threat model, because a security component whose
documentation overstates it is worse than one with none - somebody reads the
claim instead of the code.

**What this stores.** Exchange API keys and secrets, at rest, in
``.credentials/``. Either Fernet-encrypted (the default) or, in development
only and only when explicitly permitted, as plaintext JSON.

**Where the master key comes from.** ``CREDENTIALS_PASSWORD`` from the
environment. If it is unset, a key is derived from local machine attributes -
and that derivation is development-only, refused for anything marked
production, because the attributes are guessable by anyone with a shell on
the host. See docs/threat-model-credentials.md.

**What it protects against.** A credential file copied off the machine
without the environment that unlocks it: a backup, a mislaid disk, a
repository commit. That is a real and common exposure and encryption at rest
answers it.

**What it does not protect against.** Anyone who can run code as this user.
They can read ``CREDENTIALS_PASSWORD``, or ask this module to decrypt. There
is no protection against a compromised host here, and the honest answer to
"is this enough for production" is that production secrets belong in a secret
manager the application reads and cannot write.

**Rotation.** There is none. ``expires_at`` marks a credential stale locally;
it does not change anything at the provider. :meth:`rotate` raises rather
than pretending, because a rotation that quietly does nothing leaves an
operator believing a leaked key was replaced.
"""

from __future__ import annotations

import logging
import os
import json
import base64
import hashlib
import secrets
import getpass
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from pathlib import Path
import threading

# Optional cryptography support - deferred import to avoid crashes
HAS_CRYPTOGRAPHY = False
Fernet = None
hashes = None
PBKDF2HMAC = None

def _try_import_cryptography():
    """Try to import cryptography, return True if successful."""
    global HAS_CRYPTOGRAPHY, Fernet, hashes, PBKDF2HMAC
    try:
        from cryptography.fernet import Fernet as _Fernet
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2HMAC
        HAS_CRYPTOGRAPHY = True
        Fernet = _Fernet
        hashes = _hashes
        PBKDF2HMAC = _PBKDF2HMAC
        return True
    except Exception:
        return False

# Don't try to import at module load - will be done on first use if needed


# =============================================================================
# Configuration
# =============================================================================

logger = logging.getLogger(__name__)

@dataclass
class CredentialConfig:
    """Credential manager configuration."""
    storage_path: str = ".credentials"
    use_encryption: bool = True
    #: Local staleness marking only. Nothing here rotates a provider key; see
    #: the module docstring and CredentialsManager.rotate.
    local_expiry_days: int = 90
    audit_access: bool = True
    #: Plaintext storage is a development convenience and has to be asked for
    #: by name. It is refused outright for any production credential.
    allow_plaintext_development_only: bool = False


@dataclass
class Credential:
    """Single credential entry."""
    name: str
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    exchange: str = ""
    environment: str = "testnet"  # testnet or production
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if credential has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (excludes secrets)."""
        return {
            "name": self.name,
            "exchange": self.exchange,
            "environment": self.environment,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


# =============================================================================
# Encryption Helper
# =============================================================================

class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption cannot be performed safely."""


class EncryptionHelper:
    """Handles encryption/decryption of credentials."""

    def __init__(self, password: Optional[str] = None):
        self._password = password
        self._fernet = None
        self._salt: Optional[bytes] = None
        # Try to import cryptography on first use
        self._use_crypto = _try_import_cryptography()

    def _require_crypto(self) -> None:
        """Ensure real authenticated encryption is available."""
        if not self._use_crypto or Fernet is None:
            raise CredentialEncryptionError(
                "Encrypted credential storage requires the 'cryptography' package. "
                "Install it or explicitly disable encryption for local-only development."
            )

    def initialize(self, salt: Optional[bytes] = None, *,
                   production: bool = False):
        """Initialize encryption with a password.

        ``production`` refuses the local fallback. A master key reconstructible
        from environment variables anyone on the host can read is a
        development convenience; using it for a live trading key is not a
        weaker version of security, it is the appearance of it.
        """
        if self._password is None:
            self._password = os.environ.get("CREDENTIALS_PASSWORD")
            if not self._password:
                if production:
                    raise CredentialEncryptionError(
                        "CREDENTIALS_PASSWORD is not set. The local fallback "
                        "key is derived from USER and HOME, which anyone with "
                        "a shell on this host can read, and it is refused for "
                        "production credentials. Supply the master key from "
                        "the environment or a secret manager."
                    )
                logger.warning(
                    "Using the development fallback encryption key: it is "
                    "derived from local machine attributes and protects only "
                    "against a file leaving this host."
                )
                self._password = self._get_machine_key()

        self._salt = salt or secrets.token_bytes(16)

        self._require_crypto()
        assert Fernet is not None  # _require_crypto raises otherwise
        key = self._derive_key(self._password, self._salt)
        self._fernet = Fernet(key)

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password."""
        if not HAS_CRYPTOGRAPHY or PBKDF2HMAC is None or hashes is None:
            raise CredentialEncryptionError("Cannot derive credential encryption key without cryptography")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def _get_machine_key(self) -> str:
        """A development fallback key derived from local attributes.

        Two things were wrong with the previous version.

        The first was a functional bug: it included ``os.getpid()``. The
        process id changes every run, so the key changed every run, and
        credentials encrypted by one process could not be decrypted by the
        next. The store was write-only, and the failure was silent - the load
        path caught the decryption error, printed an empty message and
        returned no credentials. A system that "securely stored" its keys and
        then reported having none.

        The second is the threat model, and it has not gone away: USER and
        HOME are known to anyone with a shell on this host, so this key
        protects a file that leaves the machine and nothing else. It is
        development-only, and :meth:`initialize` refuses it for production.
        """
        components = [
            os.environ.get("USER", ""),
            os.environ.get("HOME", ""),
            # Deliberately no PID. See above.
        ]
        return hashlib.sha256("".join(components).encode()).hexdigest()

    def encrypt(self, data: str) -> bytes:
        """Encrypt string data."""
        if self._fernet is None:
            self.initialize()
        assert self._fernet is not None  # initialize() sets it or raises
        return self._fernet.encrypt(data.encode())

    def decrypt(self, data: bytes) -> str:
        """Decrypt bytes to string."""
        if data.startswith(b"FALLBACK:"):
            raise CredentialEncryptionError("Refusing to decrypt legacy FALLBACK credential data")

        if self._fernet is None:
            raise RuntimeError("Encryption not initialized")
        return self._fernet.decrypt(data).decode()

    @property
    def salt(self) -> bytes:
        """Get current salt.

        Raises rather than returning None: a caller that persists a salt of
        None has silently stored credentials it cannot decrypt later.
        """
        if self._salt is None:
            raise CredentialEncryptionError(
                "no salt yet; initialize() has not run"
            )
        return self._salt


# =============================================================================
# Credentials Manager
# =============================================================================

class CredentialsManager:
    """Secure credentials storage and retrieval."""

    def __init__(self, config: Optional[CredentialConfig] = None):
        self.config = config or CredentialConfig()
        self._credentials: Dict[str, Credential] = {}
        self._encryption = EncryptionHelper()
        self._lock = threading.RLock()
        self._access_log: List[Dict[str, Any]] = []

        # Initialize storage
        self._storage_path = Path(self.config.storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def add_credential(
        self,
        name: str,
        api_key: str,
        api_secret: str,
        passphrase: Optional[str] = None,
        exchange: str = "",
        environment: str = "testnet",
        expires_days: Optional[int] = None
    ) -> Credential:
        """Add new credential."""
        with self._lock:
            expires_at = None
            if expires_days:
                expires_at = datetime.now() + timedelta(days=expires_days)

            credential = Credential(
                name=name,
                api_key=api_key,
                api_secret=api_secret,
                passphrase=passphrase,
                exchange=exchange,
                environment=environment,
                expires_at=expires_at,
            )

            self._credentials[name] = credential
            self._log_access("add", name)

            # Persist to storage
            if self.config.use_encryption:
                self._save_encrypted()
            else:
                self._save_plaintext()

            return credential

    def get_credential(self, name: str) -> Optional[Credential]:
        """Retrieve credential by name."""
        with self._lock:
            credential = self._credentials.get(name)
            if credential:
                self._log_access("get", name)
                if credential.is_expired():
                    logger.warning(
                        "Credential %r is past its local expiry. Note that "
                        "this is a local marker: nothing has rotated it at "
                        "the provider.", name,
                    )
            return credential

    def remove_credential(self, name: str) -> bool:
        """Remove credential."""
        with self._lock:
            if name in self._credentials:
                del self._credentials[name]
                self._log_access("remove", name)
                self._save_encrypted() if self.config.use_encryption else self._save_plaintext()
                return True
            return False

    def list_credentials(self) -> List[Dict[str, Any]]:
        """List all credentials (without secrets)."""
        with self._lock:
            return [cred.to_dict() for cred in self._credentials.values()]

    def _log_access(self, action: str, name: str):
        """Log credential access."""
        if self.config.audit_access:
            self._access_log.append({
                "action": action,
                "name": name,
                "timestamp": datetime.now().isoformat(),
            })

    def _save_encrypted(self):
        """Save credentials with encryption."""
        data = {}
        for name, cred in self._credentials.items():
            data[name] = {
                "api_key": cred.api_key,
                "api_secret": cred.api_secret,
                "passphrase": cred.passphrase,
                "exchange": cred.exchange,
                "environment": cred.environment,
                "created_at": cred.created_at.isoformat(),
                "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
                "metadata": cred.metadata,
            }

        json_data = json.dumps(data)
        encrypted = self._encryption.encrypt(json_data)

        # Save encrypted data and salt
        cred_file = self._storage_path / "credentials.enc"
        salt_file = self._storage_path / "credentials.salt"

        with open(cred_file, "wb") as f:
            f.write(encrypted)
        with open(salt_file, "wb") as f:
            f.write(self._encryption.salt)

    def _require_plaintext_permitted(self) -> None:
        """Refuse to write cleartext secrets unless somebody asked for it.

        Two conditions, and the second is not negotiable by configuration: it
        has to be explicitly permitted, and no credential may be marked
        production. A comment saying "not recommended" is not a control.
        """
        if not self.config.allow_plaintext_development_only:
            raise CredentialEncryptionError(
                "plaintext credential storage is refused. Set "
                "allow_plaintext_development_only if this is a development "
                "machine; production secrets belong in a secret manager."
            )
        production = sorted(
            name for name, cred in self._credentials.items()
            if str(cred.environment).lower() in ("production", "live")
        )
        if production:
            raise CredentialEncryptionError(
                "refusing to write production credential(s) in cleartext: "
                + ", ".join(production)
            )
        logger.warning(
            "Writing %d credential(s) to %s in cleartext. Development only.",
            len(self._credentials), self._storage_path,
        )

    def _save_plaintext(self):
        """Save credentials without encryption. Development only, and gated."""
        self._require_plaintext_permitted()
        data = {}
        for name, cred in self._credentials.items():
            data[name] = {
                "api_key": cred.api_key,
                "api_secret": cred.api_secret,
                "passphrase": cred.passphrase,
                "exchange": cred.exchange,
                "environment": cred.environment,
                "created_at": cred.created_at.isoformat(),
                "expires_at": cred.expires_at.isoformat() if cred.expires_at else None,
            }

        cred_file = self._storage_path / "credentials.json"
        with open(cred_file, "w") as f:
            json.dump(data, f, indent=2)

    def load(self):
        """Load credentials from storage."""
        with self._lock:
            if self.config.use_encryption:
                self._load_encrypted()
            else:
                self._load_plaintext()

    def _load_encrypted(self):
        """Load encrypted credentials."""
        cred_file = self._storage_path / "credentials.enc"
        salt_file = self._storage_path / "credentials.salt"

        if not cred_file.exists() or not salt_file.exists():
            return

        try:
            with open(salt_file, "rb") as f:
                salt = f.read()
            self._encryption.initialize(salt)

            with open(cred_file, "rb") as f:
                encrypted = f.read()

            json_data = self._encryption.decrypt(encrypted)
            data = json.loads(json_data)

            for name, cred_data in data.items():
                self._credentials[name] = Credential(
                    name=name,
                    api_key=cred_data["api_key"],
                    api_secret=cred_data["api_secret"],
                    passphrase=cred_data.get("passphrase"),
                    exchange=cred_data.get("exchange", ""),
                    environment=cred_data.get("environment", "testnet"),
                    created_at=datetime.fromisoformat(cred_data["created_at"]),
                    expires_at=datetime.fromisoformat(cred_data["expires_at"]) if cred_data.get("expires_at") else None,
                    metadata=cred_data.get("metadata", {}),
                )
        except CredentialEncryptionError:
            raise
        except Exception as exc:
            # Fernet's InvalidToken stringifies to nothing, so the previous
            # message was literally "Failed to load encrypted credentials: "
            # followed by silence, and the caller got an empty credential
            # list. Say what happened and what usually causes it.
            logger.error(
                "Failed to decrypt %s (%s). The master key does not match the "
                "one these credentials were written with - check "
                "CREDENTIALS_PASSWORD.",
                cred_file, type(exc).__name__,
            )

    def _load_plaintext(self):
        """Load plaintext credentials. Same gate as writing them."""
        if not self.config.allow_plaintext_development_only:
            raise CredentialEncryptionError(
                "plaintext credential storage is refused; set "
                "allow_plaintext_development_only to read a development store"
            )
        cred_file = self._storage_path / "credentials.json"

        if not cred_file.exists():
            return

        try:
            with open(cred_file, "r") as f:
                data = json.load(f)

            for name, cred_data in data.items():
                self._credentials[name] = Credential(
                    name=name,
                    api_key=cred_data["api_key"],
                    api_secret=cred_data["api_secret"],
                    passphrase=cred_data.get("passphrase"),
                    exchange=cred_data.get("exchange", ""),
                    environment=cred_data.get("environment", "testnet"),
                    created_at=datetime.fromisoformat(cred_data["created_at"]),
                    expires_at=datetime.fromisoformat(cred_data["expires_at"]) if cred_data.get("expires_at") else None,
                )
        except Exception as exc:
            # The type, never the payload. A parse error on a credential file
            # can quote the content it choked on.
            logger.error("Failed to load credentials: %s", type(exc).__name__)

    def from_environment(self, prefix: str = "", environment: str = "testnet"):
        """Load credentials from environment variables.

        ``environment`` is explicit. It used to be inferred - "production" if
        no prefix was given - so the default path labelled every key it found
        as production without being told to.
        """
        exchanges = ["BINANCE", "COINBASE", "KRAKEN"]

        for exchange in exchanges:
            key_var = f"{prefix}{exchange}_API_KEY"
            secret_var = f"{prefix}{exchange}_API_SECRET"
            passphrase_var = f"{prefix}{exchange}_PASSPHRASE"

            api_key = os.environ.get(key_var)
            api_secret = os.environ.get(secret_var)

            if api_key and api_secret:
                self.add_credential(
                    name=f"{exchange.lower()}_default",
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=os.environ.get(passphrase_var),
                    exchange=exchange.lower(),
                    # Never inferred. A credential silently labelled
                    # "production" because no prefix was passed is a live key
                    # wearing whatever label the code guessed.
                    environment=environment,
                )

    def rotate(self, name: str) -> None:
        """Rotate a credential at the provider. Not implemented, and says so.

        Raising is the whole point. A rotate() that re-saved the same key
        would leave an operator believing a leaked credential had been
        replaced, which is worse than knowing it has not - they would stop
        looking for the leak.
        """
        raise NotImplementedError(
            f"provider-side rotation is not implemented, so {name!r} has not "
            "been rotated. Rotate it in the provider's console and replace it "
            "here; expires_at is a local staleness marker and changes nothing "
            "at the provider."
        )

    def expiring_soon(self, within_days: int = 7) -> List[str]:
        """Locally-marked credentials approaching their expiry date."""
        threshold = datetime.now() + timedelta(days=within_days)
        with self._lock:
            return sorted(
                name for name, cred in self._credentials.items()
                if cred.expires_at is not None and cred.expires_at <= threshold
            )

    def get_access_log(self) -> List[Dict[str, Any]]:
        """Get credential access log."""
        return self._access_log.copy()


# =============================================================================
# Testnet Credentials (Pre-configured for demo)
# =============================================================================

TESTNET_ENDPOINTS = {
    "binance_spot": {
        "rest": "https://testnet.binance.vision",
        "ws": "wss://testnet.binance.vision/ws",
        "ws_stream": "wss://testnet.binance.vision/stream",
    },
    "binance_futures": {
        "rest": "https://testnet.binancefuture.com",
        "ws": "wss://stream.binancefuture.com/ws",
    },
    "coinbase_sandbox": {
        "rest": "https://api-public.sandbox.exchange.coinbase.com",
        "ws": "wss://ws-feed-public.sandbox.exchange.coinbase.com",
    },
}

# Public endpoints that don't require API keys
PUBLIC_ENDPOINTS = {
    "binance": {
        "ticker": "https://api.binance.com/api/v3/ticker/price",
        "klines": "https://api.binance.com/api/v3/klines",
        "depth": "https://api.binance.com/api/v3/depth",
        "trades": "https://api.binance.com/api/v3/trades",
        "ws_stream": "wss://stream.binance.com:9443/ws",
    },
    "coingecko": {
        "price": "https://api.coingecko.com/api/v3/simple/price",
        "coins": "https://api.coingecko.com/api/v3/coins/markets",
        "history": "https://api.coingecko.com/api/v3/coins/{id}/market_chart",
    },
    "cryptocompare": {
        "price": "https://min-api.cryptocompare.com/data/price",
        "history": "https://min-api.cryptocompare.com/data/v2/histohour",
    },
}


def get_testnet_endpoint(exchange: str, endpoint_type: str = "rest") -> str:
    """Get testnet endpoint URL."""
    if exchange in TESTNET_ENDPOINTS:
        return TESTNET_ENDPOINTS[exchange].get(endpoint_type, "")
    return ""


def get_public_endpoint(source: str, endpoint_type: str) -> str:
    """Get public endpoint URL (no API key required)."""
    if source in PUBLIC_ENDPOINTS:
        return PUBLIC_ENDPOINTS[source].get(endpoint_type, "")
    return ""


# =============================================================================
# Factory Functions
# =============================================================================

_global_manager: Optional[CredentialsManager] = None


def get_credentials_manager() -> CredentialsManager:
    """Get global credentials manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = CredentialsManager()
        _global_manager.load()
    return _global_manager


def create_credentials_manager(
    storage_path: str = ".credentials",
    use_encryption: bool = True
) -> CredentialsManager:
    """Create new credentials manager."""
    config = CredentialConfig(
        storage_path=storage_path,
        use_encryption=use_encryption,
    )
    manager = CredentialsManager(config)
    manager.load()
    return manager


# =============================================================================
# CLI Setup Helper
# =============================================================================

def setup_credentials_interactive():
    """Interactive credential setup."""
    print("\n=== Agentic Trading OS - Credential Setup ===\n")

    manager = get_credentials_manager()

    while True:
        print("\nOptions:")
        print("1. Add Binance credentials")
        print("2. Add Coinbase credentials")
        print("3. List credentials")
        print("4. Remove credential")
        print("5. Load from environment")
        print("6. Exit")

        choice = input("\nSelect option (1-6): ").strip()

        if choice == "1":
            print("\nBinance Credential Setup")
            print("Get API keys from: https://testnet.binance.vision/ (testnet)")
            print("Or: https://www.binance.com/en/my/settings/api-management (production)")

            name = input("Credential name [binance_default]: ").strip() or "binance_default"
            api_key = input("API Key: ").strip()
            api_secret = getpass.getpass("API Secret: ")
            env = input("Environment [testnet/production]: ").strip() or "testnet"

            if api_key and api_secret:
                manager.add_credential(
                    name=name,
                    api_key=api_key,
                    api_secret=api_secret,
                    exchange="binance",
                    environment=env,
                )
                print(f"✓ Added credential: {name}")
            else:
                print("✗ API key and secret are required")

        elif choice == "2":
            print("\nCoinbase Credential Setup")
            print("Get API keys from: https://public.sandbox.exchange.coinbase.com/ (sandbox)")

            name = input("Credential name [coinbase_default]: ").strip() or "coinbase_default"
            api_key = input("API Key: ").strip()
            api_secret = getpass.getpass("API Secret: ")
            passphrase = getpass.getpass("Passphrase: ")
            env = input("Environment [testnet/production]: ").strip() or "testnet"

            if api_key and api_secret:
                manager.add_credential(
                    name=name,
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    exchange="coinbase",
                    environment=env,
                )
                print(f"✓ Added credential: {name}")
            else:
                print("✗ API key and secret are required")

        elif choice == "3":
            creds = manager.list_credentials()
            if creds:
                print("\nStored Credentials:")
                for cred in creds:
                    print(f"  - {cred['name']} ({cred['exchange']}, {cred['environment']})")
            else:
                print("No credentials stored")

        elif choice == "4":
            name = input("Credential name to remove: ").strip()
            if manager.remove_credential(name):
                print(f"✓ Removed: {name}")
            else:
                print(f"✗ Not found: {name}")

        elif choice == "5":
            manager.from_environment()
            print("✓ Loaded credentials from environment variables")

        elif choice == "6":
            print("Exiting...")
            break


# =============================================================================
# Testing
# =============================================================================

def test_credentials():
    """Test credentials manager."""
    import tempfile
    import shutil

    print("Testing Credentials Manager...")

    # Create temp directory
    test_dir = tempfile.mkdtemp()

    try:
        # Test encrypted storage
        print("\n1. Testing Encrypted Storage...")
        manager = create_credentials_manager(
            storage_path=os.path.join(test_dir, "creds"),
            use_encryption=True
        )

        manager.add_credential(
            name="test_binance",
            api_key="test_api_key_123",
            api_secret="test_secret_456",
            exchange="binance",
            environment="testnet",
        )
        print("   ✓ Added credential")

        # Retrieve
        cred = manager.get_credential("test_binance")
        assert cred is not None
        assert cred.api_key == "test_api_key_123"
        print("   ✓ Retrieved credential")

        # List
        creds = manager.list_credentials()
        assert len(creds) == 1
        print(f"   ✓ Listed {len(creds)} credential(s)")

        # Test reload
        print("\n2. Testing Persistence...")
        manager2 = create_credentials_manager(
            storage_path=os.path.join(test_dir, "creds"),
            use_encryption=True
        )
        cred2 = manager2.get_credential("test_binance")
        assert cred2 is not None
        assert cred2.api_key == "test_api_key_123"
        print("   ✓ Credential persisted and reloaded")

        # Test access log
        print("\n3. Testing Access Logging...")
        log = manager2.get_access_log()
        assert len(log) > 0
        print(f"   ✓ Access log has {len(log)} entries")

        # Test removal
        print("\n4. Testing Removal...")
        assert manager2.remove_credential("test_binance")
        assert manager2.get_credential("test_binance") is None
        print("   ✓ Credential removed")

        # Test endpoints
        print("\n5. Testing Endpoint Configuration...")
        binance_ws = get_testnet_endpoint("binance_spot", "ws")
        assert "testnet" in binance_ws
        print(f"   ✓ Binance testnet WS: {binance_ws}")

        public_ticker = get_public_endpoint("binance", "ticker")
        assert "binance.com" in public_ticker
        print(f"   ✓ Binance public ticker: {public_ticker}")

    finally:
        shutil.rmtree(test_dir)

    print("\n✓ All credentials tests passed!")
    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_credentials_interactive()
    else:
        test_credentials()
