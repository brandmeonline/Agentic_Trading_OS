"""
Agentic Trading OS - Strategy Marketplace.

Social trading features:
- Strategy sharing and discovery
- Copy trading
- Leaderboards and rankings
- Signal marketplace
"""

from __future__ import annotations

import uuid
import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from enum import Enum
from pathlib import Path
import threading


class StrategyVisibility(Enum):
    """Strategy visibility levels."""
    PRIVATE = "private"
    PUBLIC = "public"
    PREMIUM = "premium"


class StrategyCategory(Enum):
    """Strategy categories."""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    SCALPING = "scalping"
    SWING = "swing"
    AI_ML = "ai_ml"
    ARBITRAGE = "arbitrage"
    OTHER = "other"


@dataclass
class StrategyPerformance:
    """Strategy performance metrics."""
    total_return: float = 0.0
    monthly_return: float = 0.0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    profitable_trades: int = 0
    average_win: float = 0.0
    average_loss: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    last_updated: str = ""


@dataclass
class SharedStrategy:
    """A strategy shared in the marketplace."""
    id: str = ""
    name: str = ""
    description: str = ""
    author_id: str = ""
    author_name: str = ""
    category: StrategyCategory = StrategyCategory.OTHER
    visibility: StrategyVisibility = StrategyVisibility.PRIVATE
    tags: List[str] = field(default_factory=list)

    # Strategy config
    symbols: List[str] = field(default_factory=list)
    timeframe: str = "1d"
    config: Dict[str, Any] = field(default_factory=dict)

    # Performance
    performance: StrategyPerformance = field(default_factory=StrategyPerformance)

    # Social metrics
    followers: int = 0
    copiers: int = 0
    likes: int = 0
    views: int = 0
    rating: float = 0.0
    rating_count: int = 0

    # Monetization
    price: float = 0.0  # 0 = free
    subscription_monthly: float = 0.0

    # Timestamps
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


@dataclass
class Trader:
    """Trader profile for leaderboards."""
    id: str = ""
    username: str = ""
    display_name: str = ""
    bio: str = ""

    # Performance
    total_return: float = 0.0
    monthly_return: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0

    # Social
    followers: int = 0
    following: int = 0
    strategies_shared: int = 0

    # Rankings
    rank_overall: int = 0
    rank_monthly: int = 0
    rank_category: Dict[str, int] = field(default_factory=dict)

    # Badges
    badges: List[str] = field(default_factory=list)

    # Copy trading
    copiers: int = 0
    copy_trading_enabled: bool = False
    min_copy_amount: float = 1000.0

    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class CopyTradeSettings:
    """Copy trading settings."""
    id: str = ""
    copier_id: str = ""
    leader_id: str = ""
    strategy_id: str = ""  # Optional - copy all trades or specific strategy

    # Copy settings
    enabled: bool = True
    allocation_percent: float = 10.0  # % of portfolio to allocate
    max_position_size: float = 5000.0
    copy_ratio: float = 1.0  # 1.0 = same size as leader

    # Risk settings
    max_daily_loss: float = 500.0
    stop_loss_percent: float = 5.0
    max_open_positions: int = 10

    # Filters
    copy_symbols: List[str] = field(default_factory=list)  # Empty = all
    excluded_symbols: List[str] = field(default_factory=list)
    min_trade_size: float = 0.0

    # Stats
    trades_copied: int = 0
    total_pnl: float = 0.0

    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class Signal:
    """Trading signal in the marketplace."""
    id: str = ""
    strategy_id: str = ""
    author_id: str = ""

    # Signal details
    symbol: str = ""
    action: str = ""  # BUY, SELL, HOLD
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0  # 0-100

    # Analysis
    analysis: str = ""
    indicators_used: List[str] = field(default_factory=list)

    # Outcome
    outcome: str = ""  # pending, win, loss, expired
    exit_price: float = 0.0
    pnl_percent: float = 0.0

    created_at: str = ""
    expires_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class StrategyMarketplace:
    """Strategy marketplace manager."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or Path(__file__).parent.parent / "data" / "marketplace"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.strategies: Dict[str, SharedStrategy] = {}
        self.traders: Dict[str, Trader] = {}
        self.copy_settings: Dict[str, CopyTradeSettings] = {}
        self.signals: Dict[str, Signal] = {}

        self._lock = threading.Lock()
        self._load_data()

    def _load_data(self):
        """Load marketplace data from files."""
        strategies_file = self.data_dir / "strategies.json"
        if strategies_file.exists():
            try:
                with open(strategies_file) as f:
                    data = json.load(f)
                    for s in data:
                        s["category"] = StrategyCategory(s.get("category", "other"))
                        s["visibility"] = StrategyVisibility(s.get("visibility", "private"))
                        s["performance"] = StrategyPerformance(**s.get("performance", {}))
                        self.strategies[s["id"]] = SharedStrategy(**s)
            except Exception:
                pass

        traders_file = self.data_dir / "traders.json"
        if traders_file.exists():
            try:
                with open(traders_file) as f:
                    data = json.load(f)
                    for t in data:
                        self.traders[t["id"]] = Trader(**t)
            except Exception:
                pass

    def _save_data(self):
        """Save marketplace data to files."""
        # Save strategies
        strategies_file = self.data_dir / "strategies.json"
        strategies_data = []
        for s in self.strategies.values():
            d = asdict(s)
            d["category"] = s.category.value
            d["visibility"] = s.visibility.value
            strategies_data.append(d)

        with open(strategies_file, "w") as f:
            json.dump(strategies_data, f, indent=2)

        # Save traders
        traders_file = self.data_dir / "traders.json"
        with open(traders_file, "w") as f:
            json.dump([asdict(t) for t in self.traders.values()], f, indent=2)

    # =========================================================================
    # Strategy Management
    # =========================================================================

    def create_strategy(
        self,
        name: str,
        description: str,
        author_id: str,
        author_name: str,
        category: StrategyCategory = StrategyCategory.OTHER,
        visibility: StrategyVisibility = StrategyVisibility.PRIVATE,
        symbols: List[str] = None,
        tags: List[str] = None,
        config: Dict = None,
        price: float = 0.0
    ) -> SharedStrategy:
        """Create a new strategy."""
        strategy = SharedStrategy(
            name=name,
            description=description,
            author_id=author_id,
            author_name=author_name,
            category=category,
            visibility=visibility,
            symbols=symbols or [],
            tags=tags or [],
            config=config or {},
            price=price
        )

        with self._lock:
            self.strategies[strategy.id] = strategy
            self._save_data()

        return strategy

    def update_strategy(self, strategy_id: str, updates: Dict) -> Optional[SharedStrategy]:
        """Update a strategy."""
        with self._lock:
            if strategy_id not in self.strategies:
                return None

            strategy = self.strategies[strategy_id]

            for key, value in updates.items():
                if hasattr(strategy, key):
                    if key == "category":
                        value = StrategyCategory(value)
                    elif key == "visibility":
                        value = StrategyVisibility(value)
                    setattr(strategy, key, value)

            strategy.updated_at = datetime.now().isoformat()
            self._save_data()

            return strategy

    def delete_strategy(self, strategy_id: str) -> bool:
        """Delete a strategy."""
        with self._lock:
            if strategy_id in self.strategies:
                del self.strategies[strategy_id]
                self._save_data()
                return True
            return False

    def get_strategy(self, strategy_id: str) -> Optional[SharedStrategy]:
        """Get a strategy by ID."""
        strategy = self.strategies.get(strategy_id)
        if strategy:
            with self._lock:
                strategy.views += 1
                self._save_data()
        return strategy

    def list_strategies(
        self,
        category: Optional[StrategyCategory] = None,
        visibility: Optional[StrategyVisibility] = None,
        author_id: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "followers",
        limit: int = 50,
        offset: int = 0
    ) -> List[SharedStrategy]:
        """List strategies with filters."""
        results = list(self.strategies.values())

        # Filter by visibility (always include public unless specified)
        if visibility:
            results = [s for s in results if s.visibility == visibility]
        else:
            results = [s for s in results if s.visibility != StrategyVisibility.PRIVATE]

        # Filter by category
        if category:
            results = [s for s in results if s.category == category]

        # Filter by author
        if author_id:
            results = [s for s in results if s.author_id == author_id]

        # Search
        if search:
            search_lower = search.lower()
            results = [
                s for s in results
                if search_lower in s.name.lower() or
                   search_lower in s.description.lower() or
                   any(search_lower in tag.lower() for tag in s.tags)
            ]

        # Sort
        sort_key = {
            "followers": lambda s: s.followers,
            "copiers": lambda s: s.copiers,
            "rating": lambda s: s.rating,
            "return": lambda s: s.performance.total_return,
            "newest": lambda s: s.created_at,
            "views": lambda s: s.views
        }.get(sort_by, lambda s: s.followers)

        results.sort(key=sort_key, reverse=True)

        return results[offset:offset + limit]

    def like_strategy(self, strategy_id: str) -> bool:
        """Like a strategy."""
        with self._lock:
            if strategy_id in self.strategies:
                self.strategies[strategy_id].likes += 1
                self._save_data()
                return True
            return False

    def rate_strategy(self, strategy_id: str, rating: float) -> bool:
        """Rate a strategy (1-5)."""
        if not 1 <= rating <= 5:
            return False

        with self._lock:
            if strategy_id in self.strategies:
                s = self.strategies[strategy_id]
                # Update weighted average
                total = s.rating * s.rating_count + rating
                s.rating_count += 1
                s.rating = total / s.rating_count
                self._save_data()
                return True
            return False

    def follow_strategy(self, strategy_id: str, user_id: str) -> bool:
        """Follow a strategy."""
        with self._lock:
            if strategy_id in self.strategies:
                self.strategies[strategy_id].followers += 1
                self._save_data()
                return True
            return False

    def update_performance(
        self,
        strategy_id: str,
        total_return: float,
        monthly_return: float,
        win_rate: float,
        sharpe_ratio: float,
        max_drawdown: float,
        total_trades: int,
        profitable_trades: int
    ):
        """Update strategy performance metrics."""
        with self._lock:
            if strategy_id in self.strategies:
                s = self.strategies[strategy_id]
                s.performance.total_return = total_return
                s.performance.monthly_return = monthly_return
                s.performance.win_rate = win_rate
                s.performance.sharpe_ratio = sharpe_ratio
                s.performance.max_drawdown = max_drawdown
                s.performance.total_trades = total_trades
                s.performance.profitable_trades = profitable_trades
                s.performance.last_updated = datetime.now().isoformat()
                self._save_data()

    # =========================================================================
    # Trader Management
    # =========================================================================

    def create_trader(
        self,
        user_id: str,
        username: str,
        display_name: str = "",
        bio: str = ""
    ) -> Trader:
        """Create a trader profile."""
        trader = Trader(
            id=user_id,
            username=username,
            display_name=display_name or username,
            bio=bio
        )

        with self._lock:
            self.traders[trader.id] = trader
            self._save_data()

        return trader

    def get_trader(self, trader_id: str) -> Optional[Trader]:
        """Get a trader by ID."""
        return self.traders.get(trader_id)

    def update_trader_stats(
        self,
        trader_id: str,
        total_return: float,
        monthly_return: float,
        win_rate: float,
        total_trades: int,
        sharpe_ratio: float = 0.0,
        max_drawdown: float = 0.0
    ):
        """Update trader statistics."""
        with self._lock:
            if trader_id in self.traders:
                t = self.traders[trader_id]
                t.total_return = total_return
                t.monthly_return = monthly_return
                t.win_rate = win_rate
                t.total_trades = total_trades
                t.sharpe_ratio = sharpe_ratio
                t.max_drawdown = max_drawdown
                self._save_data()

    def get_leaderboard(
        self,
        sort_by: str = "return",
        timeframe: str = "all",
        limit: int = 100
    ) -> List[Trader]:
        """Get trader leaderboard."""
        traders = list(self.traders.values())

        sort_key = {
            "return": lambda t: t.total_return,
            "monthly": lambda t: t.monthly_return,
            "win_rate": lambda t: t.win_rate,
            "sharpe": lambda t: t.sharpe_ratio,
            "followers": lambda t: t.followers,
            "copiers": lambda t: t.copiers
        }.get(sort_by, lambda t: t.total_return)

        traders.sort(key=sort_key, reverse=True)

        # Update rankings
        for i, trader in enumerate(traders[:limit], 1):
            trader.rank_overall = i

        return traders[:limit]

    def follow_trader(self, follower_id: str, leader_id: str) -> bool:
        """Follow a trader."""
        with self._lock:
            if leader_id in self.traders and follower_id in self.traders:
                self.traders[leader_id].followers += 1
                self.traders[follower_id].following += 1
                self._save_data()
                return True
            return False

    # =========================================================================
    # Copy Trading
    # =========================================================================

    def setup_copy_trading(
        self,
        copier_id: str,
        leader_id: str,
        allocation_percent: float = 10.0,
        max_position_size: float = 5000.0,
        copy_ratio: float = 1.0,
        strategy_id: str = ""
    ) -> CopyTradeSettings:
        """Set up copy trading."""
        settings = CopyTradeSettings(
            copier_id=copier_id,
            leader_id=leader_id,
            strategy_id=strategy_id,
            allocation_percent=allocation_percent,
            max_position_size=max_position_size,
            copy_ratio=copy_ratio
        )

        with self._lock:
            self.copy_settings[settings.id] = settings

            if leader_id in self.traders:
                self.traders[leader_id].copiers += 1

            self._save_data()

        return settings

    def update_copy_settings(self, settings_id: str, updates: Dict) -> Optional[CopyTradeSettings]:
        """Update copy trading settings."""
        with self._lock:
            if settings_id in self.copy_settings:
                settings = self.copy_settings[settings_id]
                for key, value in updates.items():
                    if hasattr(settings, key):
                        setattr(settings, key, value)
                return settings
            return None

    def disable_copy_trading(self, settings_id: str) -> bool:
        """Disable copy trading."""
        with self._lock:
            if settings_id in self.copy_settings:
                settings = self.copy_settings[settings_id]
                settings.enabled = False

                if settings.leader_id in self.traders:
                    self.traders[settings.leader_id].copiers -= 1

                return True
            return False

    def get_copy_settings(self, copier_id: str) -> List[CopyTradeSettings]:
        """Get copy trading settings for a user."""
        return [
            s for s in self.copy_settings.values()
            if s.copier_id == copier_id
        ]

    def process_copy_trade(
        self,
        leader_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> List[Dict]:
        """Process a trade for all copiers."""
        copied_trades = []

        # Find all active copy settings for this leader
        for settings in self.copy_settings.values():
            if not settings.enabled or settings.leader_id != leader_id:
                continue

            # Check symbol filters
            if settings.copy_symbols and symbol not in settings.copy_symbols:
                continue
            if symbol in settings.excluded_symbols:
                continue

            # Calculate copy size
            copy_qty = quantity * settings.copy_ratio
            copy_value = copy_qty * price

            # Apply limits
            if copy_value > settings.max_position_size:
                copy_qty = settings.max_position_size / price

            if copy_qty > 0:
                copied_trades.append({
                    "copier_id": settings.copier_id,
                    "symbol": symbol,
                    "side": side,
                    "quantity": copy_qty,
                    "price": price,
                    "settings_id": settings.id
                })

                settings.trades_copied += 1

        return copied_trades

    # =========================================================================
    # Signals
    # =========================================================================

    def create_signal(
        self,
        author_id: str,
        symbol: str,
        action: str,
        entry_price: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        confidence: float = 75.0,
        analysis: str = "",
        indicators: List[str] = None,
        strategy_id: str = "",
        expires_hours: int = 24
    ) -> Signal:
        """Create a trading signal."""
        signal = Signal(
            author_id=author_id,
            strategy_id=strategy_id,
            symbol=symbol,
            action=action,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            analysis=analysis,
            indicators_used=indicators or [],
            outcome="pending",
            expires_at=(datetime.now() + timedelta(hours=expires_hours)).isoformat()
        )

        with self._lock:
            self.signals[signal.id] = signal

        return signal

    def get_signals(
        self,
        author_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        symbol: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50
    ) -> List[Signal]:
        """Get trading signals."""
        signals = list(self.signals.values())

        if author_id:
            signals = [s for s in signals if s.author_id == author_id]

        if strategy_id:
            signals = [s for s in signals if s.strategy_id == strategy_id]

        if symbol:
            signals = [s for s in signals if s.symbol == symbol]

        if active_only:
            now = datetime.now().isoformat()
            signals = [
                s for s in signals
                if s.outcome == "pending" and s.expires_at > now
            ]

        signals.sort(key=lambda s: s.created_at, reverse=True)
        return signals[:limit]

    def close_signal(self, signal_id: str, exit_price: float) -> Optional[Signal]:
        """Close a signal with outcome."""
        with self._lock:
            if signal_id not in self.signals:
                return None

            signal = self.signals[signal_id]
            signal.exit_price = exit_price

            # Calculate P&L
            if signal.action == "BUY":
                pnl = ((exit_price - signal.entry_price) / signal.entry_price) * 100
            else:
                pnl = ((signal.entry_price - exit_price) / signal.entry_price) * 100

            signal.pnl_percent = pnl
            signal.outcome = "win" if pnl > 0 else "loss"

            return signal

    # =========================================================================
    # Featured / Discovery
    # =========================================================================

    def get_featured_strategies(self, limit: int = 6) -> List[SharedStrategy]:
        """Get featured strategies for homepage."""
        public = [
            s for s in self.strategies.values()
            if s.visibility == StrategyVisibility.PUBLIC
        ]

        # Score by combination of metrics
        def score(s):
            return (
                s.followers * 2 +
                s.copiers * 3 +
                s.rating * 10 +
                s.performance.total_return * 0.5 +
                s.performance.sharpe_ratio * 5
            )

        public.sort(key=score, reverse=True)
        return public[:limit]

    def get_top_performers(self, limit: int = 10) -> List[SharedStrategy]:
        """Get top performing strategies."""
        public = [
            s for s in self.strategies.values()
            if s.visibility == StrategyVisibility.PUBLIC
        ]
        public.sort(key=lambda s: s.performance.total_return, reverse=True)
        return public[:limit]

    def get_trending(self, limit: int = 10) -> List[SharedStrategy]:
        """Get trending strategies (most views recently)."""
        public = [
            s for s in self.strategies.values()
            if s.visibility == StrategyVisibility.PUBLIC
        ]
        public.sort(key=lambda s: s.views, reverse=True)
        return public[:limit]


# =============================================================================
# Singleton Factory
# =============================================================================

_marketplace: Optional[StrategyMarketplace] = None


def get_marketplace() -> StrategyMarketplace:
    """Get or create the marketplace singleton."""
    global _marketplace
    if _marketplace is None:
        _marketplace = StrategyMarketplace()
    return _marketplace
