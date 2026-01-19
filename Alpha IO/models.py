"""
Core data models with comprehensive type hints.

These dataclasses provide type-safe representations of all trading entities.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import uuid


class SignalType(Enum):
    """Classification of signal types."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class TradeAction(Enum):
    """Possible trade actions."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradeStrategy(Enum):
    """Available trading strategies."""
    SPOT = "spot"
    FUTURES = "futures"
    OPTIONS = "options"
    SPREAD = "spread"
    DO_NOTHING = "do_nothing"


class DecisionOutcome(Enum):
    """Signal routing decisions."""
    TRADE = "trade"
    WATCHLIST = "watchlist"
    IGNORE = "ignore"


@dataclass
class Signal:
    """Represents a trading signal with full metadata."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    asset: str = ""
    confidence: float = 0.0
    signal_type: SignalType = SignalType.NEUTRAL
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    # Derived scores
    asymmetry_score: float = 0.0
    trust_score: float = 0.0
    resonance_score: float = 0.0

    # Context
    metadata: Dict[str, Any] = field(default_factory=dict)
    similar_signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "text": self.text,
            "asset": self.asset,
            "confidence": self.confidence,
            "signal_type": self.signal_type.value,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "asymmetry_score": self.asymmetry_score,
            "trust_score": self.trust_score,
            "resonance_score": self.resonance_score,
            "metadata": self.metadata,
        }


@dataclass
class Trade:
    """Represents an executed or planned trade."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_id: str = ""
    asset: str = ""
    action: TradeAction = TradeAction.HOLD
    strategy: TradeStrategy = TradeStrategy.SPOT

    # Sizing
    position_size: float = 0.0
    entry_price: float = 0.0
    exit_price: Optional[float] = None

    # Risk parameters
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: float = 1.0

    # Execution
    confidence: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    executed: bool = False
    tx_hash: Optional[str] = None

    # Results
    pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0

    def calculate_net_pnl(self) -> float:
        """Calculate net P&L after fees."""
        self.net_pnl = self.pnl - self.fees
        return self.net_pnl

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "asset": self.asset,
            "action": self.action.value,
            "strategy": self.strategy.value,
            "position_size": self.position_size,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "leverage": self.leverage,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "executed": self.executed,
            "tx_hash": self.tx_hash,
            "pnl": self.pnl,
            "fees": self.fees,
            "net_pnl": self.net_pnl,
        }


@dataclass
class Position:
    """Represents an open position."""
    asset: str
    size: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: float = 1.0
    opened_at: datetime = field(default_factory=datetime.now)

    def update_price(self, price: float) -> float:
        """Update current price and calculate unrealized P&L."""
        self.current_price = price
        self.unrealized_pnl = (price - self.entry_price) * self.size * self.leverage
        return self.unrealized_pnl


@dataclass
class Portfolio:
    """Represents the complete portfolio state."""
    capital: float = 10000.0
    available_capital: float = 10000.0
    positions: Dict[str, Position] = field(default_factory=dict)
    total_pnl: float = 0.0
    trade_count: int = 0
    win_count: int = 0

    @property
    def win_rate(self) -> float:
        """Calculate win rate."""
        return self.win_count / self.trade_count if self.trade_count > 0 else 0.0

    @property
    def total_exposure(self) -> float:
        """Calculate total market exposure."""
        return sum(pos.size * pos.current_price for pos in self.positions.values())

    def update_trade(self, trade: Trade) -> None:
        """Update portfolio after a trade."""
        self.trade_count += 1
        self.total_pnl += trade.net_pnl
        if trade.net_pnl > 0:
            self.win_count += 1


@dataclass
class AgentState:
    """Represents the state of a trading agent."""
    q_table: Dict[float, float] = field(default_factory=dict)
    momentum: List[float] = field(default_factory=list)
    total_trades: int = 0
    total_reward: float = 0.0
    last_action: Optional[TradeAction] = None
    last_confidence: float = 0.0


@dataclass
class VoteResult:
    """Result of multi-agent voting."""
    votes: List[str] = field(default_factory=list)
    execute: bool = False
    confidence: float = 0.0
    memory_context: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class RoutingDecision:
    """Decision from signal router."""
    signal: Signal
    decision: DecisionOutcome = DecisionOutcome.IGNORE
    asymmetry_score: float = 0.0
    execution_plan: Optional[Dict[str, Any]] = None
    note: str = ""
