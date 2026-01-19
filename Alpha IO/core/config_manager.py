"""
Centralized Configuration Manager.

Production-grade configuration management:
- Multi-environment support (dev, staging, production)
- Hot reloading of configurations
- Validation and schema enforcement
- Secrets management
- Configuration versioning
- Override hierarchy
"""

from __future__ import annotations

import json
import os
import hashlib
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable, Type, TypeVar, Union, get_type_hints
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import copy


# =============================================================================
# Configuration Types
# =============================================================================

class Environment(Enum):
    """Deployment environments."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


class ConfigSource(Enum):
    """Configuration sources in priority order."""
    DEFAULT = 0       # Built-in defaults
    FILE = 1          # Configuration files
    ENVIRONMENT = 2   # Environment variables
    OVERRIDE = 3      # Runtime overrides


T = TypeVar('T')


# =============================================================================
# Configuration Schema
# =============================================================================

@dataclass
class ConfigField:
    """Configuration field definition."""
    name: str
    field_type: Type
    default: Any = None
    required: bool = False
    secret: bool = False
    description: str = ""
    validator: Optional[Callable[[Any], bool]] = None
    env_var: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    choices: Optional[List[Any]] = None


class ConfigSchema:
    """Configuration schema definition."""

    def __init__(self, name: str):
        self.name = name
        self.fields: Dict[str, ConfigField] = {}

    def add_field(
        self,
        name: str,
        field_type: Type,
        default: Any = None,
        required: bool = False,
        secret: bool = False,
        description: str = "",
        validator: Optional[Callable[[Any], bool]] = None,
        env_var: Optional[str] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        choices: Optional[List[Any]] = None
    ) -> 'ConfigSchema':
        """Add field to schema."""
        self.fields[name] = ConfigField(
            name=name,
            field_type=field_type,
            default=default,
            required=required,
            secret=secret,
            description=description,
            validator=validator,
            env_var=env_var,
            min_value=min_value,
            max_value=max_value,
            choices=choices,
        )
        return self

    def validate(self, config: Dict[str, Any]) -> List[str]:
        """Validate configuration against schema."""
        errors = []

        for name, field in self.fields.items():
            value = config.get(name)

            # Check required fields
            if field.required and value is None:
                errors.append(f"Required field '{name}' is missing")
                continue

            if value is None:
                continue

            # Type check
            if not isinstance(value, field.field_type):
                try:
                    # Try conversion
                    config[name] = field.field_type(value)
                except (ValueError, TypeError):
                    errors.append(f"Field '{name}' must be type {field.field_type.__name__}")
                    continue

            # Range check
            if field.min_value is not None and value < field.min_value:
                errors.append(f"Field '{name}' must be >= {field.min_value}")

            if field.max_value is not None and value > field.max_value:
                errors.append(f"Field '{name}' must be <= {field.max_value}")

            # Choices check
            if field.choices is not None and value not in field.choices:
                errors.append(f"Field '{name}' must be one of {field.choices}")

            # Custom validator
            if field.validator is not None and not field.validator(value):
                errors.append(f"Field '{name}' failed validation")

        return errors


# =============================================================================
# Pre-defined Trading Schemas
# =============================================================================

def create_trading_schema() -> ConfigSchema:
    """Create trading configuration schema."""
    schema = ConfigSchema("trading")

    # General settings
    schema.add_field("mode", str, default="paper", choices=["live", "paper", "backtest"])
    schema.add_field("initial_capital", float, default=100000.0, min_value=0)
    schema.add_field("base_currency", str, default="USDT")

    # Risk settings
    schema.add_field("max_position_size", float, default=0.20, min_value=0, max_value=1.0)
    schema.add_field("max_total_exposure", float, default=1.0, min_value=0, max_value=2.0)
    schema.add_field("risk_per_trade", float, default=0.02, min_value=0, max_value=0.1)
    schema.add_field("max_drawdown", float, default=0.20, min_value=0, max_value=1.0)
    schema.add_field("stop_loss_pct", float, default=0.05, min_value=0, max_value=1.0)
    schema.add_field("take_profit_pct", float, default=0.15, min_value=0, max_value=1.0)

    # Execution settings
    schema.add_field("slippage_bps", float, default=5.0, min_value=0)
    schema.add_field("commission_bps", float, default=10.0, min_value=0)
    schema.add_field("min_order_size", float, default=10.0, min_value=0)

    # Strategy settings
    schema.add_field("signal_threshold", float, default=0.6, min_value=0, max_value=1.0)
    schema.add_field("rebalance_frequency", str, default="daily", choices=["realtime", "hourly", "daily"])

    return schema


def create_exchange_schema() -> ConfigSchema:
    """Create exchange configuration schema."""
    schema = ConfigSchema("exchange")

    schema.add_field("name", str, required=True)
    schema.add_field("api_key", str, secret=True, env_var="EXCHANGE_API_KEY")
    schema.add_field("api_secret", str, secret=True, env_var="EXCHANGE_API_SECRET")
    schema.add_field("passphrase", str, secret=True, env_var="EXCHANGE_PASSPHRASE")
    schema.add_field("testnet", bool, default=True)
    schema.add_field("rate_limit_per_second", float, default=10.0, min_value=1)
    schema.add_field("rate_limit_per_minute", float, default=1200.0, min_value=60)
    schema.add_field("timeout_seconds", float, default=30.0, min_value=5)
    schema.add_field("max_retries", int, default=3, min_value=0, max_value=10)

    return schema


def create_database_schema() -> ConfigSchema:
    """Create database configuration schema."""
    schema = ConfigSchema("database")

    schema.add_field("type", str, default="sqlite", choices=["sqlite", "postgresql", "memory"])
    schema.add_field("host", str, default="localhost")
    schema.add_field("port", int, default=5432, min_value=1, max_value=65535)
    schema.add_field("database", str, default="trading.db")
    schema.add_field("username", str, env_var="DB_USERNAME")
    schema.add_field("password", str, secret=True, env_var="DB_PASSWORD")
    schema.add_field("pool_size", int, default=5, min_value=1, max_value=50)

    return schema


def create_logging_schema() -> ConfigSchema:
    """Create logging configuration schema."""
    schema = ConfigSchema("logging")

    schema.add_field("level", str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    schema.add_field("log_dir", str, default="logs")
    schema.add_field("max_file_size_mb", int, default=100, min_value=1)
    schema.add_field("max_files", int, default=10, min_value=1)
    schema.add_field("enable_console", bool, default=True)
    schema.add_field("enable_file", bool, default=True)
    schema.add_field("enable_json", bool, default=True)
    schema.add_field("async_logging", bool, default=True)

    return schema


def create_api_schema() -> ConfigSchema:
    """Create API configuration schema."""
    schema = ConfigSchema("api")

    schema.add_field("host", str, default="0.0.0.0")
    schema.add_field("port", int, default=8080, min_value=1, max_value=65535)
    schema.add_field("enable_auth", bool, default=True)
    schema.add_field("jwt_secret", str, secret=True, env_var="JWT_SECRET")
    schema.add_field("jwt_expiry_hours", int, default=24, min_value=1)
    schema.add_field("rate_limit_per_minute", int, default=60, min_value=1)
    schema.add_field("enable_cors", bool, default=True)
    schema.add_field("enable_swagger", bool, default=True)

    return schema


# =============================================================================
# Configuration Store
# =============================================================================

class ConfigStore(ABC):
    """Abstract configuration store."""

    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Load configuration."""
        pass

    @abstractmethod
    def save(self, config: Dict[str, Any]):
        """Save configuration."""
        pass

    @abstractmethod
    def watch(self, callback: Callable[[Dict[str, Any]], None]):
        """Watch for configuration changes."""
        pass


class FileConfigStore(ConfigStore):
    """File-based configuration store."""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self._last_mtime = 0.0
        self._callbacks: List[Callable] = []
        self._watch_thread: Optional[threading.Thread] = None
        self._watching = False

    def load(self) -> Dict[str, Any]:
        """Load configuration from file."""
        if not self.filepath.exists():
            return {}

        with open(self.filepath, 'r') as f:
            if self.filepath.suffix == '.json':
                return json.load(f)
            else:
                # Simple key=value format
                config = {}
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = self._parse_value(value.strip())
                return config

    def _parse_value(self, value: str) -> Any:
        """Parse string value to appropriate type."""
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def save(self, config: Dict[str, Any]):
        """Save configuration to file."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(self.filepath, 'w') as f:
            if self.filepath.suffix == '.json':
                json.dump(config, f, indent=2)
            else:
                for key, value in config.items():
                    f.write(f"{key}={value}\n")

    def watch(self, callback: Callable[[Dict[str, Any]], None]):
        """Watch file for changes."""
        self._callbacks.append(callback)

        if not self._watching:
            self._watching = True
            self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
            self._watch_thread.start()

    def _watch_loop(self):
        """Watch loop for file changes."""
        while self._watching:
            try:
                if self.filepath.exists():
                    mtime = self.filepath.stat().st_mtime
                    if mtime > self._last_mtime:
                        self._last_mtime = mtime
                        config = self.load()
                        for callback in self._callbacks:
                            callback(config)
            except Exception:
                pass
            time.sleep(1)

    def stop_watching(self):
        """Stop watching for changes."""
        self._watching = False


class EnvironmentConfigStore(ConfigStore):
    """Environment variable configuration store."""

    def __init__(self, prefix: str = "TRADING_"):
        self.prefix = prefix

    def load(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        config = {}
        for key, value in os.environ.items():
            if key.startswith(self.prefix):
                config_key = key[len(self.prefix):].lower()
                config[config_key] = self._parse_value(value)
        return config

    def _parse_value(self, value: str) -> Any:
        """Parse string value to appropriate type."""
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def save(self, config: Dict[str, Any]):
        """Environment variables are read-only."""
        pass

    def watch(self, callback: Callable[[Dict[str, Any]], None]):
        """Environment variables don't support watching."""
        pass


class MemoryConfigStore(ConfigStore):
    """In-memory configuration store."""

    def __init__(self, initial: Optional[Dict[str, Any]] = None):
        self._config = initial or {}
        self._callbacks: List[Callable] = []

    def load(self) -> Dict[str, Any]:
        return copy.deepcopy(self._config)

    def save(self, config: Dict[str, Any]):
        self._config = copy.deepcopy(config)
        for callback in self._callbacks:
            callback(self._config)

    def watch(self, callback: Callable[[Dict[str, Any]], None]):
        self._callbacks.append(callback)


# =============================================================================
# Configuration Manager
# =============================================================================

class ConfigManager:
    """Central configuration manager."""

    def __init__(
        self,
        environment: Environment = Environment.DEVELOPMENT,
        config_dir: str = "config"
    ):
        self.environment = environment
        self.config_dir = Path(config_dir)

        # Configuration stores by priority
        self._stores: Dict[ConfigSource, ConfigStore] = {}
        self._schemas: Dict[str, ConfigSchema] = {}
        self._config: Dict[str, Dict[str, Any]] = {}
        self._overrides: Dict[str, Dict[str, Any]] = {}

        # Change callbacks
        self._callbacks: List[Callable[[str, Dict[str, Any]], None]] = []
        self._lock = threading.RLock()

        # Version tracking
        self._version = 0
        self._config_hash = ""

        # Setup default stores
        self._setup_stores()

    def _setup_stores(self):
        """Setup configuration stores."""
        # Default in-memory store
        self._stores[ConfigSource.DEFAULT] = MemoryConfigStore()

        # Environment variable store
        self._stores[ConfigSource.ENVIRONMENT] = EnvironmentConfigStore()

        # File stores for each environment
        env_file = self.config_dir / f"{self.environment.value}.json"
        if env_file.exists():
            self._stores[ConfigSource.FILE] = FileConfigStore(str(env_file))
        else:
            # Try default config file
            default_file = self.config_dir / "config.json"
            if default_file.exists():
                self._stores[ConfigSource.FILE] = FileConfigStore(str(default_file))

        # Override store
        self._stores[ConfigSource.OVERRIDE] = MemoryConfigStore()

    def register_schema(self, name: str, schema: ConfigSchema):
        """Register configuration schema."""
        self._schemas[name] = schema

        # Apply defaults from schema
        defaults = {}
        for field_name, field in schema.fields.items():
            if field.default is not None:
                defaults[field_name] = field.default
        self._stores[ConfigSource.DEFAULT].save({name: defaults})

    def load(self):
        """Load all configurations."""
        with self._lock:
            self._config.clear()

            # Load from stores in priority order
            for source in sorted(ConfigSource, key=lambda s: s.value):
                if source not in self._stores:
                    continue

                store_config = self._stores[source].load()
                self._merge_config(store_config)

            # Apply overrides
            for section, overrides in self._overrides.items():
                if section not in self._config:
                    self._config[section] = {}
                self._config[section].update(overrides)

            # Load from environment variables for secrets
            self._load_secrets_from_env()

            # Validate
            self._validate_all()

            # Update version
            self._version += 1
            self._config_hash = self._compute_hash()

    def _merge_config(self, source_config: Dict[str, Any]):
        """Merge source configuration."""
        for section, values in source_config.items():
            if section not in self._config:
                self._config[section] = {}
            if isinstance(values, dict):
                self._config[section].update(values)

    def _load_secrets_from_env(self):
        """Load secrets from environment variables."""
        for section_name, schema in self._schemas.items():
            if section_name not in self._config:
                self._config[section_name] = {}

            for field_name, field in schema.fields.items():
                if field.env_var and field.env_var in os.environ:
                    self._config[section_name][field_name] = os.environ[field.env_var]

    def _validate_all(self):
        """Validate all configurations against schemas."""
        for section_name, schema in self._schemas.items():
            if section_name in self._config:
                errors = schema.validate(self._config[section_name])
                if errors:
                    print(f"Configuration validation errors in '{section_name}':")
                    for error in errors:
                        print(f"  - {error}")

    def _compute_hash(self) -> str:
        """Compute configuration hash."""
        # Exclude secrets from hash
        config_copy = copy.deepcopy(self._config)
        for section_name, schema in self._schemas.items():
            if section_name in config_copy:
                for field_name, field in schema.fields.items():
                    if field.secret and field_name in config_copy[section_name]:
                        config_copy[section_name][field_name] = "***"

        return hashlib.sha256(json.dumps(config_copy, sort_keys=True).encode()).hexdigest()[:16]

    def get(self, section: str, key: Optional[str] = None, default: Any = None) -> Any:
        """Get configuration value."""
        with self._lock:
            if section not in self._config:
                return default

            if key is None:
                return copy.deepcopy(self._config[section])

            return self._config[section].get(key, default)

    def set(self, section: str, key: str, value: Any):
        """Set configuration value (runtime override)."""
        with self._lock:
            if section not in self._overrides:
                self._overrides[section] = {}
            self._overrides[section][key] = value

            if section not in self._config:
                self._config[section] = {}
            self._config[section][key] = value

            # Validate
            if section in self._schemas:
                errors = self._schemas[section].validate(self._config[section])
                if errors:
                    raise ValueError(f"Validation failed: {errors}")

            # Notify callbacks
            self._notify_change(section)

    def set_section(self, section: str, config: Dict[str, Any]):
        """Set entire section."""
        with self._lock:
            self._overrides[section] = config.copy()
            self._config[section] = config.copy()

            if section in self._schemas:
                errors = self._schemas[section].validate(self._config[section])
                if errors:
                    raise ValueError(f"Validation failed: {errors}")

            self._notify_change(section)

    def on_change(self, callback: Callable[[str, Dict[str, Any]], None]):
        """Register change callback."""
        self._callbacks.append(callback)

    def _notify_change(self, section: str):
        """Notify callbacks of configuration change."""
        config = self._config.get(section, {})
        for callback in self._callbacks:
            try:
                callback(section, config)
            except Exception as e:
                print(f"Config change callback error: {e}")

    def save(self, filepath: Optional[str] = None):
        """Save current configuration to file."""
        if filepath is None:
            filepath = str(self.config_dir / f"{self.environment.value}.json")

        # Remove secrets before saving
        config_copy = copy.deepcopy(self._config)
        for section_name, schema in self._schemas.items():
            if section_name in config_copy:
                for field_name, field in schema.fields.items():
                    if field.secret and field_name in config_copy[section_name]:
                        del config_copy[section_name][field_name]

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(config_copy, f, indent=2)

    def export(self, include_secrets: bool = False) -> Dict[str, Any]:
        """Export configuration."""
        config_copy = copy.deepcopy(self._config)

        if not include_secrets:
            for section_name, schema in self._schemas.items():
                if section_name in config_copy:
                    for field_name, field in schema.fields.items():
                        if field.secret and field_name in config_copy[section_name]:
                            config_copy[section_name][field_name] = "***"

        return config_copy

    @property
    def version(self) -> int:
        """Get configuration version."""
        return self._version

    @property
    def config_hash(self) -> str:
        """Get configuration hash."""
        return self._config_hash


# =============================================================================
# Configuration Dataclasses
# =============================================================================

@dataclass
class TradingConfig:
    """Trading configuration dataclass."""
    mode: str = "paper"
    initial_capital: float = 100000.0
    base_currency: str = "USDT"
    max_position_size: float = 0.20
    max_total_exposure: float = 1.0
    risk_per_trade: float = 0.02
    max_drawdown: float = 0.20
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    slippage_bps: float = 5.0
    commission_bps: float = 10.0
    min_order_size: float = 10.0
    signal_threshold: float = 0.6
    rebalance_frequency: str = "daily"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TradingConfig':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExchangeConfig:
    """Exchange configuration dataclass."""
    name: str = ""
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    testnet: bool = True
    rate_limit_per_second: float = 10.0
    rate_limit_per_minute: float = 1200.0
    timeout_seconds: float = 30.0
    max_retries: int = 3

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExchangeConfig':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DatabaseConfig:
    """Database configuration dataclass."""
    type: str = "sqlite"
    host: str = "localhost"
    port: int = 5432
    database: str = "trading.db"
    username: str = ""
    password: str = ""
    pool_size: int = 5

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DatabaseConfig':
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# =============================================================================
# Factory Functions
# =============================================================================

def create_config_manager(
    environment: str = "development",
    config_dir: str = "config"
) -> ConfigManager:
    """Create and initialize configuration manager."""
    env = Environment(environment)
    manager = ConfigManager(environment=env, config_dir=config_dir)

    # Register default schemas
    manager.register_schema("trading", create_trading_schema())
    manager.register_schema("exchange", create_exchange_schema())
    manager.register_schema("database", create_database_schema())
    manager.register_schema("logging", create_logging_schema())
    manager.register_schema("api", create_api_schema())

    # Load configurations
    manager.load()

    return manager


# =============================================================================
# Global Instance
# =============================================================================

_global_config: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """Get global configuration manager."""
    global _global_config
    if _global_config is None:
        _global_config = create_config_manager()
    return _global_config


def set_config(config: ConfigManager):
    """Set global configuration manager."""
    global _global_config
    _global_config = config


# =============================================================================
# Testing
# =============================================================================

def test_config_manager():
    """Test configuration manager."""
    import tempfile
    import shutil

    print("Testing Configuration Manager...")

    # Create temp directory
    test_dir = tempfile.mkdtemp()

    try:
        # Create test config file
        config_dir = os.path.join(test_dir, "config")
        os.makedirs(config_dir)

        test_config = {
            "trading": {
                "mode": "paper",
                "initial_capital": 50000.0,
                "max_position_size": 0.15,
            },
            "exchange": {
                "name": "binance",
                "testnet": True,
            }
        }

        with open(os.path.join(config_dir, "development.json"), "w") as f:
            json.dump(test_config, f)

        # Test basic functionality
        print("\n1. Testing Configuration Manager Creation...")
        manager = create_config_manager(config_dir=config_dir)
        print(f"   Environment: {manager.environment.value}")
        print(f"   Version: {manager.version}")

        # Test get
        print("\n2. Testing Get Configuration...")
        trading = manager.get("trading")
        assert trading["mode"] == "paper"
        assert trading["initial_capital"] == 50000.0
        print(f"   Trading mode: {trading['mode']}")
        print(f"   Initial capital: ${trading['initial_capital']:.2f}")

        # Test defaults
        print("\n3. Testing Default Values...")
        risk = manager.get("trading", "risk_per_trade")
        assert risk == 0.02
        print(f"   Risk per trade (default): {risk}")

        # Test set
        print("\n4. Testing Set Configuration...")
        manager.set("trading", "initial_capital", 75000.0)
        assert manager.get("trading", "initial_capital") == 75000.0
        print(f"   Updated capital: ${manager.get('trading', 'initial_capital'):.2f}")

        # Test validation
        print("\n5. Testing Validation...")
        try:
            manager.set("trading", "max_position_size", 2.0)  # Should fail
            print("   ERROR: Should have raised validation error")
        except ValueError:
            print("   Validation correctly rejected invalid value")

        # Test change callback
        print("\n6. Testing Change Callbacks...")
        changes = []
        manager.on_change(lambda section, config: changes.append((section, config)))
        manager.set("trading", "slippage_bps", 10.0)
        assert len(changes) == 1
        print(f"   Change callback triggered: {changes[0][0]}")

        # Test export
        print("\n7. Testing Export...")
        exported = manager.export(include_secrets=False)
        assert "trading" in exported
        print(f"   Exported sections: {list(exported.keys())}")

        # Test save
        print("\n8. Testing Save...")
        save_path = os.path.join(test_dir, "saved_config.json")
        manager.save(save_path)
        assert os.path.exists(save_path)
        print(f"   Saved to: {save_path}")

        # Test dataclass conversion
        print("\n9. Testing Dataclass Conversion...")
        trading_config = TradingConfig.from_dict(manager.get("trading"))
        assert trading_config.mode == "paper"
        print(f"   TradingConfig.mode: {trading_config.mode}")

        # Test schema validation
        print("\n10. Testing Schema Validation...")
        schema = create_trading_schema()
        errors = schema.validate({
            "mode": "invalid_mode",
            "initial_capital": -1000,
        })
        assert len(errors) > 0
        print(f"   Validation errors found: {len(errors)}")

        # Test environment variable loading
        print("\n11. Testing Environment Variables...")
        os.environ["TRADING_DEBUG"] = "true"
        env_store = EnvironmentConfigStore()
        env_config = env_store.load()
        assert env_config.get("debug") == True
        del os.environ["TRADING_DEBUG"]
        print("   Environment variable loading works!")

        # Test file watching (brief)
        print("\n12. Testing File Watching...")
        file_store = FileConfigStore(os.path.join(config_dir, "development.json"))
        watch_triggered = []
        file_store.watch(lambda c: watch_triggered.append(c))
        time.sleep(0.5)
        file_store.stop_watching()
        print("   File watching initialized!")

    finally:
        shutil.rmtree(test_dir)

    print("\n✓ All configuration manager tests passed!")
    return True


if __name__ == "__main__":
    test_config_manager()
