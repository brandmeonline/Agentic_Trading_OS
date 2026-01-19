"""
Centralized configuration management for the trading system.

All configurable parameters are defined here with sensible defaults.
Override via environment variables or .env file.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TradingConfig:
    """Core trading parameters."""
    confidence_threshold: float = 0.7
    min_confidence: float = 0.5
    confidence_bins: List[float] = field(default_factory=lambda: [0.5, 0.6, 0.7, 0.8, 0.9])

    # Q-learning parameters
    learning_rate: float = 0.1
    discount_factor: float = 0.9
    momentum_window: int = 5


@dataclass
class RiskConfig:
    """Risk management parameters."""
    initial_capital: float = 10000.0
    max_risk_per_trade: float = 0.015  # 1.5% per trade
    max_drawdown: float = 0.06  # 6% daily drawdown limit
    max_position_concentration: float = 0.25  # 25% max in single asset
    max_loss_streak: int = 3
    fee_rate: float = 0.003  # 0.3% trading fee


@dataclass
class SignalConfig:
    """Signal processing parameters."""
    embedding_dim: int = 1536
    similarity_threshold: float = 0.7
    asymmetry_threshold: float = 0.6
    max_similar_signals: int = 5
    cluster_count: int = 3


@dataclass
class ExecutionConfig:
    """Trade execution parameters."""
    simulation_mode: bool = True
    gas_price_gwei: int = 30
    gas_limit: int = 300000
    slippage_tolerance: float = 0.01  # 1%
    deadline_seconds: int = 600


@dataclass
class APIConfig:
    """External API configuration."""
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    openai_model: str = "gpt-4"
    embedding_model: str = "text-embedding-3-small"
    infura_url: Optional[str] = field(default_factory=lambda: os.getenv("WEB3_INFURA_URL"))
    wallet_address: Optional[str] = field(default_factory=lambda: os.getenv("WALLET_PUBLIC_ADDRESS"))
    wallet_private_key: Optional[str] = field(default_factory=lambda: os.getenv("WALLET_PRIVATE_KEY"))


@dataclass
class SystemConfig:
    """System-wide configuration."""
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    api: APIConfig = field(default_factory=APIConfig)

    # Paths
    data_dir: str = "data"
    log_dir: str = "logs"
    trade_log_file: str = "data/trade_log.csv"

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


# Global configuration instance
config = SystemConfig()


def get_config() -> SystemConfig:
    """Get the global configuration instance."""
    return config


def update_config(**kwargs) -> SystemConfig:
    """Update configuration parameters dynamically."""
    global config
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config
