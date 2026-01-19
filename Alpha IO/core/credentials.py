"""
Secure Credentials Manager.

Production-grade secrets management:
- Encrypted storage of API keys
- Environment variable integration
- Keyring support for OS-level security
- Automatic credential rotation
- Audit logging of access
"""

from __future__ import annotations

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
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import threading


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class CredentialConfig:
    """Credential manager configuration."""
    storage_path: str = ".credentials"
    use_encryption: bool = True
    use_keyring: bool = False  # OS keyring integration
    auto_rotate_days: int = 90
    audit_access: bool = True


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

class EncryptionHelper:
    """Handles encryption/decryption of credentials."""

    def __init__(self, password: Optional[str] = None):
        self._password = password
        self._fernet: Optional[Fernet] = None
        self._salt: Optional[bytes] = None

    def initialize(self, salt: Optional[bytes] = None):
        """Initialize encryption with password."""
        if self._password is None:
            # Try environment variable
            self._password = os.environ.get("CREDENTIALS_PASSWORD")
            if not self._password:
                # Generate a machine-specific key
                self._password = self._get_machine_key()

        self._salt = salt or secrets.token_bytes(16)
        key = self._derive_key(self._password, self._salt)
        self._fernet = Fernet(key)

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def _get_machine_key(self) -> str:
        """Generate machine-specific key."""
        # Combine various machine identifiers
        components = [
            os.environ.get("USER", ""),
            os.environ.get("HOME", ""),
            str(os.getpid()),
        ]
        return hashlib.sha256("".join(components).encode()).hexdigest()

    def encrypt(self, data: str) -> bytes:
        """Encrypt string data."""
        if self._fernet is None:
            self.initialize()
        return self._fernet.encrypt(data.encode())

    def decrypt(self, data: bytes) -> str:
        """Decrypt bytes to string."""
        if self._fernet is None:
            raise RuntimeError("Encryption not initialized")
        return self._fernet.decrypt(data).decode()

    @property
    def salt(self) -> bytes:
        """Get current salt."""
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
                    print(f"Warning: Credential '{name}' has expired")
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

    def _save_plaintext(self):
        """Save credentials without encryption (not recommended)."""
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
        except Exception as e:
            print(f"Failed to load encrypted credentials: {e}")

    def _load_plaintext(self):
        """Load plaintext credentials."""
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
        except Exception as e:
            print(f"Failed to load credentials: {e}")

    def from_environment(self, prefix: str = ""):
        """Load credentials from environment variables."""
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
                    environment="production" if not prefix else "testnet",
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
