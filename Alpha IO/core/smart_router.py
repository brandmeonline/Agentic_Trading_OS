"""
Smart Order Routing Engine.

Advanced order routing and execution optimization:
- Multi-venue liquidity aggregation
- Transaction cost analysis (TCA)
- Optimal execution algorithms
- Market impact modeling
- Best execution compliance
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime
import time
import threading
from collections import deque


# =============================================================================
# Configuration and Enums
# =============================================================================

class VenueType(Enum):
    """Trading venue types."""
    EXCHANGE = "exchange"
    DARK_POOL = "dark_pool"
    ATS = "ats"
    MARKET_MAKER = "market_maker"
    INTERNAL = "internal"


class ExecutionAlgo(Enum):
    """Execution algorithm types."""
    MARKET = "market"
    LIMIT = "limit"
    TWAP = "twap"
    VWAP = "vwap"
    POV = "pov"  # Percentage of volume
    IS = "is"  # Implementation shortfall
    ADAPTIVE = "adaptive"
    ICEBERG = "iceberg"
    SNIPER = "sniper"
    DARK = "dark"


class OrderUrgency(Enum):
    """Order urgency levels."""
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class RoutingConfig:
    """Routing configuration."""
    max_slippage_bps: float = 50.0
    max_market_impact_bps: float = 100.0
    min_fill_rate: float = 0.8
    max_venues: int = 5
    enable_dark_pools: bool = True
    enable_internalization: bool = True
    smart_routing: bool = True
    adaptive_algo: bool = True


@dataclass
class VenueConfig:
    """Individual venue configuration."""
    name: str
    venue_type: VenueType
    fee_bps: float  # Maker/taker fees
    rebate_bps: float = 0.0  # Maker rebate
    min_order_size: float = 0.0
    max_order_size: float = float('inf')
    latency_ms: float = 10.0
    reliability: float = 0.99
    enabled: bool = True


# =============================================================================
# Market Data Models
# =============================================================================

@dataclass
class VenueLiquidity:
    """Liquidity available at a venue."""
    venue: str
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    timestamp: float = field(default_factory=time.time)
    spread_bps: float = field(init=False)

    def __post_init__(self):
        mid = (self.bid_price + self.ask_price) / 2
        self.spread_bps = ((self.ask_price - self.bid_price) / mid) * 10000 if mid > 0 else 0


@dataclass
class RouteDecision:
    """Routing decision for an order."""
    venue: str
    quantity: float
    price: float
    algorithm: ExecutionAlgo
    urgency: OrderUrgency
    expected_cost_bps: float
    expected_fill_time: float
    confidence: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionReport:
    """Execution report for completed order."""
    order_id: str
    symbol: str
    side: str
    requested_qty: float
    filled_qty: float
    avg_price: float
    arrival_price: float
    benchmark_price: float
    venues_used: List[str]
    start_time: float
    end_time: float
    fills: List[Dict]

    @property
    def fill_rate(self) -> float:
        return self.filled_qty / self.requested_qty if self.requested_qty > 0 else 0

    @property
    def slippage_bps(self) -> float:
        if self.arrival_price == 0:
            return 0
        return ((self.avg_price - self.arrival_price) / self.arrival_price) * 10000

    @property
    def implementation_shortfall_bps(self) -> float:
        if self.benchmark_price == 0:
            return 0
        return ((self.avg_price - self.benchmark_price) / self.benchmark_price) * 10000


# =============================================================================
# Transaction Cost Model
# =============================================================================

class TransactionCostModel:
    """
    Transaction cost analysis and prediction.

    Models:
    - Spread costs
    - Market impact
    - Timing costs
    - Opportunity costs
    """

    def __init__(self):
        # Impact model parameters (calibrated from historical data)
        self.temp_impact_coef = 0.1  # Temporary impact coefficient
        self.perm_impact_coef = 0.05  # Permanent impact coefficient
        self.decay_rate = 0.5  # Impact decay rate

    def estimate_spread_cost(
        self,
        liquidity: VenueLiquidity,
        side: str,
        quantity: float
    ) -> float:
        """Estimate spread crossing cost in bps."""
        return liquidity.spread_bps / 2  # Half spread

    def estimate_market_impact(
        self,
        symbol: str,
        side: str,
        quantity: float,
        adv: float,  # Average daily volume
        volatility: float,
        urgency: OrderUrgency
    ) -> Tuple[float, float]:
        """
        Estimate market impact using Almgren-Chriss model.

        Returns:
            Tuple of (temporary_impact_bps, permanent_impact_bps)
        """
        # Participation rate
        participation = quantity / adv if adv > 0 else 0

        # Urgency multiplier
        urgency_mult = {
            OrderUrgency.LOW: 0.5,
            OrderUrgency.MEDIUM: 1.0,
            OrderUrgency.HIGH: 2.0,
            OrderUrgency.CRITICAL: 4.0
        }.get(urgency, 1.0)

        # Temporary impact (market order impact)
        temp_impact = self.temp_impact_coef * volatility * np.sqrt(participation) * 10000

        # Permanent impact
        perm_impact = self.perm_impact_coef * participation * volatility * 10000

        # Apply urgency scaling
        temp_impact *= urgency_mult

        return temp_impact, perm_impact

    def estimate_timing_cost(
        self,
        volatility: float,
        execution_time: float,  # in hours
        urgency: OrderUrgency
    ) -> float:
        """Estimate timing/delay cost in bps."""
        # Cost of delayed execution due to volatility
        # Higher urgency = less tolerance for timing cost
        urgency_mult = {
            OrderUrgency.LOW: 0.25,
            OrderUrgency.MEDIUM: 0.5,
            OrderUrgency.HIGH: 1.0,
            OrderUrgency.CRITICAL: 2.0
        }.get(urgency, 0.5)

        return volatility * np.sqrt(execution_time / 6.5) * 10000 * urgency_mult

    def estimate_total_cost(
        self,
        liquidity: VenueLiquidity,
        side: str,
        quantity: float,
        adv: float,
        volatility: float,
        urgency: OrderUrgency,
        execution_time: float = 0.1
    ) -> Dict[str, float]:
        """Estimate total transaction cost."""
        spread_cost = self.estimate_spread_cost(liquidity, side, quantity)
        temp_impact, perm_impact = self.estimate_market_impact(
            liquidity.venue, side, quantity, adv, volatility, urgency
        )
        timing_cost = self.estimate_timing_cost(volatility, execution_time, urgency)

        return {
            "spread_cost_bps": spread_cost,
            "temp_impact_bps": temp_impact,
            "perm_impact_bps": perm_impact,
            "timing_cost_bps": timing_cost,
            "total_cost_bps": spread_cost + temp_impact + perm_impact + timing_cost
        }


# =============================================================================
# Venue Analyzer
# =============================================================================

class VenueAnalyzer:
    """
    Analyzes venue characteristics and performance.

    Tracks:
    - Fill rates
    - Execution quality
    - Latency
    - Cost efficiency
    """

    def __init__(self):
        self.venue_stats: Dict[str, Dict] = {}
        self._history: Dict[str, deque] = {}

    def record_execution(
        self,
        venue: str,
        order_id: str,
        requested_qty: float,
        filled_qty: float,
        slippage_bps: float,
        latency_ms: float
    ):
        """Record execution statistics."""
        if venue not in self.venue_stats:
            self.venue_stats[venue] = {
                "total_orders": 0,
                "total_requested": 0.0,
                "total_filled": 0.0,
                "total_slippage_bps": 0.0,
                "total_latency_ms": 0.0,
                "successful_orders": 0
            }
            self._history[venue] = deque(maxlen=1000)

        stats = self.venue_stats[venue]
        stats["total_orders"] += 1
        stats["total_requested"] += requested_qty
        stats["total_filled"] += filled_qty
        stats["total_slippage_bps"] += slippage_bps
        stats["total_latency_ms"] += latency_ms

        if filled_qty >= requested_qty * 0.95:
            stats["successful_orders"] += 1

        self._history[venue].append({
            "timestamp": time.time(),
            "order_id": order_id,
            "requested_qty": requested_qty,
            "filled_qty": filled_qty,
            "slippage_bps": slippage_bps,
            "latency_ms": latency_ms
        })

    def get_venue_score(self, venue: str) -> float:
        """
        Calculate venue quality score (0-1).

        Considers:
        - Fill rate
        - Slippage
        - Latency
        - Reliability
        """
        if venue not in self.venue_stats:
            return 0.5  # Default score for unknown venues

        stats = self.venue_stats[venue]
        if stats["total_orders"] == 0:
            return 0.5

        # Fill rate component (0-1)
        fill_rate = stats["total_filled"] / stats["total_requested"] if stats["total_requested"] > 0 else 0
        fill_score = min(1.0, fill_rate)

        # Slippage component (lower is better)
        avg_slippage = stats["total_slippage_bps"] / stats["total_orders"]
        slippage_score = max(0, 1 - avg_slippage / 100)  # 100 bps = 0 score

        # Latency component (lower is better)
        avg_latency = stats["total_latency_ms"] / stats["total_orders"]
        latency_score = max(0, 1 - avg_latency / 100)  # 100ms = 0 score

        # Reliability component
        reliability = stats["successful_orders"] / stats["total_orders"]

        # Weighted combination
        score = (
            0.3 * fill_score +
            0.3 * slippage_score +
            0.2 * latency_score +
            0.2 * reliability
        )

        return score

    def get_best_venues(self, n: int = 5) -> List[Tuple[str, float]]:
        """Get top N venues by score."""
        scores = [(venue, self.get_venue_score(venue)) for venue in self.venue_stats]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n]

    def get_venue_statistics(self, venue: str) -> Dict[str, float]:
        """Get detailed venue statistics."""
        if venue not in self.venue_stats:
            return {}

        stats = self.venue_stats[venue]
        n = stats["total_orders"]

        if n == 0:
            return {}

        return {
            "total_orders": n,
            "fill_rate": stats["total_filled"] / stats["total_requested"] if stats["total_requested"] > 0 else 0,
            "avg_slippage_bps": stats["total_slippage_bps"] / n,
            "avg_latency_ms": stats["total_latency_ms"] / n,
            "success_rate": stats["successful_orders"] / n,
            "quality_score": self.get_venue_score(venue)
        }


# =============================================================================
# Liquidity Aggregator
# =============================================================================

class LiquidityAggregator:
    """
    Aggregates liquidity across multiple venues.

    Provides:
    - Consolidated order book
    - Best bid/offer
    - Depth analysis
    - Liquidity scoring
    """

    def __init__(self, config: RoutingConfig):
        self.config = config
        self._liquidity: Dict[str, Dict[str, VenueLiquidity]] = {}  # symbol -> venue -> liquidity
        self._lock = threading.Lock()

    def update_liquidity(self, symbol: str, liquidity: VenueLiquidity):
        """Update liquidity for a venue."""
        with self._lock:
            if symbol not in self._liquidity:
                self._liquidity[symbol] = {}
            self._liquidity[symbol][liquidity.venue] = liquidity

    def get_consolidated_book(self, symbol: str) -> Dict[str, List[Tuple[float, float, str]]]:
        """
        Get consolidated order book across venues.

        Returns:
            Dict with 'bids' and 'asks', each a list of (price, size, venue)
        """
        with self._lock:
            if symbol not in self._liquidity:
                return {"bids": [], "asks": []}

            bids = []
            asks = []

            for venue, liq in self._liquidity[symbol].items():
                # Check freshness
                if time.time() - liq.timestamp > 5:  # 5 second staleness
                    continue

                bids.append((liq.bid_price, liq.bid_size, venue))
                asks.append((liq.ask_price, liq.ask_size, venue))

            # Sort: bids descending, asks ascending
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])

            return {"bids": bids, "asks": asks}

    def get_nbbo(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        """Get National Best Bid/Offer."""
        book = self.get_consolidated_book(symbol)

        best_bid = book["bids"][0][0] if book["bids"] else None
        best_ask = book["asks"][0][0] if book["asks"] else None

        return best_bid, best_ask

    def get_available_liquidity(
        self,
        symbol: str,
        side: str,
        max_slippage_bps: float = 50.0
    ) -> List[Tuple[str, float, float]]:
        """
        Get available liquidity within slippage tolerance.

        Returns:
            List of (venue, quantity, price) tuples
        """
        book = self.get_consolidated_book(symbol)

        if side == "buy":
            levels = book["asks"]
            best_price = levels[0][0] if levels else None
        else:
            levels = book["bids"]
            best_price = levels[0][0] if levels else None

        if not best_price:
            return []

        result = []
        for price, size, venue in levels:
            slippage = abs(price - best_price) / best_price * 10000
            if slippage <= max_slippage_bps:
                result.append((venue, size, price))

        return result

    def estimate_fill_price(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> Tuple[float, float]:
        """
        Estimate average fill price for a quantity.

        Returns:
            Tuple of (avg_price, filled_quantity)
        """
        book = self.get_consolidated_book(symbol)

        if side == "buy":
            levels = book["asks"]
        else:
            levels = book["bids"]

        if not levels:
            return 0.0, 0.0

        remaining = quantity
        total_cost = 0.0
        total_filled = 0.0

        for price, size, _ in levels:
            fill = min(remaining, size)
            total_cost += fill * price
            total_filled += fill
            remaining -= fill

            if remaining <= 0:
                break

        avg_price = total_cost / total_filled if total_filled > 0 else 0.0
        return avg_price, total_filled


# =============================================================================
# Execution Optimizer
# =============================================================================

class ExecutionOptimizer:
    """
    Optimizes execution strategy based on order characteristics.

    Considers:
    - Order size relative to liquidity
    - Urgency
    - Market conditions
    - Historical venue performance
    """

    def __init__(
        self,
        config: RoutingConfig,
        cost_model: TransactionCostModel,
        venue_analyzer: VenueAnalyzer
    ):
        self.config = config
        self.cost_model = cost_model
        self.venue_analyzer = venue_analyzer

    def select_algorithm(
        self,
        symbol: str,
        side: str,
        quantity: float,
        urgency: OrderUrgency,
        adv: float,
        volatility: float
    ) -> ExecutionAlgo:
        """Select optimal execution algorithm."""
        # Participation rate
        participation = quantity / adv if adv > 0 else 0

        # Small orders: Market or limit
        if participation < 0.001:
            return ExecutionAlgo.MARKET if urgency.value >= OrderUrgency.HIGH.value else ExecutionAlgo.LIMIT

        # Medium orders: TWAP/VWAP
        if participation < 0.01:
            if urgency.value >= OrderUrgency.HIGH.value:
                return ExecutionAlgo.TWAP
            return ExecutionAlgo.VWAP

        # Large orders: Implementation shortfall or adaptive
        if participation < 0.05:
            if self.config.adaptive_algo:
                return ExecutionAlgo.ADAPTIVE
            return ExecutionAlgo.IS

        # Very large orders: Careful execution
        if urgency.value >= OrderUrgency.HIGH.value:
            return ExecutionAlgo.POV
        return ExecutionAlgo.ICEBERG

    def optimize_route(
        self,
        symbol: str,
        side: str,
        quantity: float,
        urgency: OrderUrgency,
        available_liquidity: List[Tuple[str, float, float]],
        venue_configs: Dict[str, VenueConfig],
        adv: float,
        volatility: float
    ) -> List[RouteDecision]:
        """
        Optimize order routing across venues.

        Returns list of routing decisions.
        """
        if not available_liquidity:
            return []

        # Select algorithm
        algorithm = self.select_algorithm(symbol, side, quantity, urgency, adv, volatility)

        # Score each venue
        venue_scores = []
        for venue, liq_size, price in available_liquidity:
            config = venue_configs.get(venue, VenueConfig(venue, VenueType.EXCHANGE, 10.0))

            if not config.enabled:
                continue

            # Base score from historical performance
            perf_score = self.venue_analyzer.get_venue_score(venue)

            # Cost score (lower cost = higher score)
            fee_score = 1 - config.fee_bps / 100

            # Liquidity score
            liq_score = min(1.0, liq_size / quantity)

            # Latency score
            latency_score = 1 - config.latency_ms / 100

            # Combined score
            total_score = (
                0.3 * perf_score +
                0.25 * fee_score +
                0.25 * liq_score +
                0.2 * latency_score
            )

            venue_scores.append((venue, liq_size, price, total_score, config))

        # Sort by score
        venue_scores.sort(key=lambda x: x[3], reverse=True)

        # Allocate quantity across top venues
        decisions = []
        remaining = quantity
        used_venues = 0

        for venue, liq_size, price, score, config in venue_scores:
            if remaining <= 0:
                break

            if used_venues >= self.config.max_venues:
                break

            # Allocate based on liquidity and score
            allocation = min(
                remaining,
                liq_size,
                config.max_order_size
            )

            if allocation < config.min_order_size:
                continue

            # Estimate cost
            costs = self.cost_model.estimate_total_cost(
                VenueLiquidity(venue, price * 0.999, liq_size, price * 1.001, liq_size),
                side, allocation, adv, volatility, urgency
            )

            decisions.append(RouteDecision(
                venue=venue,
                quantity=allocation,
                price=price,
                algorithm=algorithm,
                urgency=urgency,
                expected_cost_bps=costs["total_cost_bps"],
                expected_fill_time=config.latency_ms / 1000,
                confidence=score,
                metadata={
                    "fee_bps": config.fee_bps,
                    "cost_breakdown": costs
                }
            ))

            remaining -= allocation
            used_venues += 1

        return decisions


# =============================================================================
# Smart Order Router
# =============================================================================

class SmartOrderRouter:
    """
    Main smart order routing engine.

    Orchestrates:
    - Liquidity aggregation
    - Venue analysis
    - Cost optimization
    - Order execution
    """

    def __init__(self, config: Optional[RoutingConfig] = None):
        self.config = config or RoutingConfig()

        self.cost_model = TransactionCostModel()
        self.venue_analyzer = VenueAnalyzer()
        self.liquidity_aggregator = LiquidityAggregator(self.config)
        self.optimizer = ExecutionOptimizer(
            self.config, self.cost_model, self.venue_analyzer
        )

        self.venue_configs: Dict[str, VenueConfig] = {}
        self._order_counter = 0
        self._lock = threading.Lock()

    def add_venue(self, config: VenueConfig):
        """Add a trading venue."""
        self.venue_configs[config.name] = config

    def update_liquidity(self, symbol: str, venue: str, liquidity: Dict):
        """Update liquidity from a venue."""
        liq = VenueLiquidity(
            venue=venue,
            bid_price=liquidity.get("bid_price", 0),
            bid_size=liquidity.get("bid_size", 0),
            ask_price=liquidity.get("ask_price", 0),
            ask_size=liquidity.get("ask_size", 0),
            timestamp=liquidity.get("timestamp", time.time())
        )
        self.liquidity_aggregator.update_liquidity(symbol, liq)

    def route_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        urgency: OrderUrgency = OrderUrgency.MEDIUM,
        adv: Optional[float] = None,
        volatility: Optional[float] = None,
        max_cost_bps: Optional[float] = None
    ) -> Tuple[List[RouteDecision], Dict]:
        """
        Route an order across venues.

        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            quantity: Order quantity
            urgency: Order urgency
            adv: Average daily volume (optional)
            volatility: Current volatility (optional)
            max_cost_bps: Maximum acceptable cost (optional)

        Returns:
            Tuple of (route_decisions, routing_info)
        """
        with self._lock:
            self._order_counter += 1
            order_id = f"SOR_{self._order_counter}"

        # Get available liquidity
        available = self.liquidity_aggregator.get_available_liquidity(
            symbol, side, self.config.max_slippage_bps
        )

        if not available:
            return [], {"error": "No liquidity available", "order_id": order_id}

        # Default parameters if not provided
        adv = adv or quantity * 100  # Assume 1% of ADV
        volatility = volatility or 0.02  # Default 2% daily vol

        # Optimize routing
        decisions = self.optimizer.optimize_route(
            symbol=symbol,
            side=side,
            quantity=quantity,
            urgency=urgency,
            available_liquidity=available,
            venue_configs=self.venue_configs,
            adv=adv,
            volatility=volatility
        )

        # Filter by max cost if specified
        if max_cost_bps is not None:
            decisions = [d for d in decisions if d.expected_cost_bps <= max_cost_bps]

        # Calculate summary
        total_quantity = sum(d.quantity for d in decisions)
        avg_cost = (
            sum(d.quantity * d.expected_cost_bps for d in decisions) / total_quantity
            if total_quantity > 0 else 0
        )

        routing_info = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "requested_quantity": quantity,
            "routed_quantity": total_quantity,
            "fill_rate": total_quantity / quantity if quantity > 0 else 0,
            "num_venues": len(decisions),
            "expected_cost_bps": avg_cost,
            "venues": [d.venue for d in decisions]
        }

        return decisions, routing_info

    def execute_routed_order(
        self,
        decisions: List[RouteDecision],
        routing_info: Dict
    ) -> ExecutionReport:
        """
        Execute a routed order (simulated).

        In production, this would send orders to actual venues.
        """
        order_id = routing_info.get("order_id", "unknown")
        symbol = routing_info.get("symbol", "unknown")
        side = routing_info.get("side", "buy")
        requested_qty = routing_info.get("requested_quantity", 0)

        start_time = time.time()
        fills = []
        total_filled = 0.0
        total_cost = 0.0

        # Get arrival price
        best_bid, best_ask = self.liquidity_aggregator.get_nbbo(symbol)
        arrival_price = best_ask if side == "buy" else best_bid
        arrival_price = arrival_price or 100.0

        for decision in decisions:
            # Simulate fill with some randomness
            fill_rate = min(1.0, np.random.beta(5, 1))  # Usually fills well
            filled_qty = decision.quantity * fill_rate

            # Simulate price with slippage
            slippage = np.random.exponential(decision.expected_cost_bps / 2) / 10000
            if side == "buy":
                fill_price = decision.price * (1 + slippage)
            else:
                fill_price = decision.price * (1 - slippage)

            fills.append({
                "venue": decision.venue,
                "quantity": filled_qty,
                "price": fill_price,
                "timestamp": time.time(),
                "algorithm": decision.algorithm.value
            })

            total_filled += filled_qty
            total_cost += filled_qty * fill_price

            # Record for venue analysis
            actual_slippage = (fill_price - decision.price) / decision.price * 10000
            self.venue_analyzer.record_execution(
                venue=decision.venue,
                order_id=order_id,
                requested_qty=decision.quantity,
                filled_qty=filled_qty,
                slippage_bps=abs(actual_slippage),
                latency_ms=np.random.exponential(10)
            )

        avg_price = total_cost / total_filled if total_filled > 0 else 0

        return ExecutionReport(
            order_id=order_id,
            symbol=symbol,
            side=side,
            requested_qty=requested_qty,
            filled_qty=total_filled,
            avg_price=avg_price,
            arrival_price=arrival_price,
            benchmark_price=arrival_price,
            venues_used=[d.venue for d in decisions],
            start_time=start_time,
            end_time=time.time(),
            fills=fills
        )

    def get_pre_trade_analysis(
        self,
        symbol: str,
        side: str,
        quantity: float,
        urgency: OrderUrgency = OrderUrgency.MEDIUM
    ) -> Dict:
        """
        Get pre-trade cost analysis.

        Useful for order preview before execution.
        """
        # Route without executing
        decisions, routing_info = self.route_order(
            symbol, side, quantity, urgency
        )

        if not decisions:
            return {
                "error": "Cannot route order",
                "liquidity_available": False
            }

        # Get market data
        best_bid, best_ask = self.liquidity_aggregator.get_nbbo(symbol)
        mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else None

        # Aggregate cost estimates
        total_spread_cost = sum(
            d.metadata.get("cost_breakdown", {}).get("spread_cost_bps", 0) * d.quantity
            for d in decisions
        )
        total_impact_cost = sum(
            d.metadata.get("cost_breakdown", {}).get("temp_impact_bps", 0) * d.quantity
            for d in decisions
        )
        total_qty = sum(d.quantity for d in decisions)

        return {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "routable_quantity": total_qty,
            "mid_price": mid_price,
            "expected_avg_price": sum(d.quantity * d.price for d in decisions) / total_qty if total_qty > 0 else None,
            "spread_cost_bps": total_spread_cost / total_qty if total_qty > 0 else 0,
            "market_impact_bps": total_impact_cost / total_qty if total_qty > 0 else 0,
            "total_cost_bps": routing_info.get("expected_cost_bps", 0),
            "recommended_algo": decisions[0].algorithm.value if decisions else None,
            "venues": [d.venue for d in decisions],
            "confidence": sum(d.confidence for d in decisions) / len(decisions) if decisions else 0
        }

    def get_post_trade_analysis(self, report: ExecutionReport) -> Dict:
        """
        Get post-trade execution analysis.

        Provides TCA metrics for executed orders.
        """
        return {
            "order_id": report.order_id,
            "symbol": report.symbol,
            "side": report.side,
            "fill_rate": report.fill_rate,
            "slippage_bps": report.slippage_bps,
            "implementation_shortfall_bps": report.implementation_shortfall_bps,
            "execution_time_ms": (report.end_time - report.start_time) * 1000,
            "venues_used": report.venues_used,
            "num_fills": len(report.fills),
            "avg_fill_size": report.filled_qty / len(report.fills) if report.fills else 0,
            "price_improvement": (report.arrival_price - report.avg_price) / report.arrival_price * 10000 if report.side == "buy" else (report.avg_price - report.arrival_price) / report.arrival_price * 10000
        }

    def get_venue_rankings(self) -> List[Dict]:
        """Get current venue quality rankings."""
        rankings = []

        for venue_name, config in self.venue_configs.items():
            stats = self.venue_analyzer.get_venue_statistics(venue_name)
            rankings.append({
                "venue": venue_name,
                "type": config.venue_type.value,
                "quality_score": stats.get("quality_score", 0.5),
                "fill_rate": stats.get("fill_rate", 0),
                "avg_slippage_bps": stats.get("avg_slippage_bps", 0),
                "fee_bps": config.fee_bps,
                "total_orders": stats.get("total_orders", 0)
            })

        rankings.sort(key=lambda x: x["quality_score"], reverse=True)
        return rankings


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create smart router
    config = RoutingConfig(
        max_slippage_bps=30.0,
        max_venues=3,
        enable_dark_pools=True
    )
    router = SmartOrderRouter(config)

    # Add venues
    venues = [
        VenueConfig("binance", VenueType.EXCHANGE, fee_bps=10.0, latency_ms=5),
        VenueConfig("coinbase", VenueType.EXCHANGE, fee_bps=15.0, latency_ms=10),
        VenueConfig("kraken", VenueType.EXCHANGE, fee_bps=12.0, latency_ms=15),
        VenueConfig("dark_pool_1", VenueType.DARK_POOL, fee_bps=5.0, latency_ms=20),
    ]

    for venue in venues:
        router.add_venue(venue)

    # Simulate liquidity updates
    for venue in venues:
        base_price = 45000 + np.random.randn() * 10
        router.update_liquidity("BTC/USDT", venue.name, {
            "bid_price": base_price - 5,
            "bid_size": np.random.exponential(10),
            "ask_price": base_price + 5,
            "ask_size": np.random.exponential(10)
        })

    # Pre-trade analysis
    print("Pre-Trade Analysis:")
    pre_trade = router.get_pre_trade_analysis(
        symbol="BTC/USDT",
        side="buy",
        quantity=5.0,
        urgency=OrderUrgency.MEDIUM
    )
    for key, value in pre_trade.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        else:
            print(f"  {key}: {value}")

    # Route and execute order
    print("\nRouting Order:")
    decisions, routing_info = router.route_order(
        symbol="BTC/USDT",
        side="buy",
        quantity=5.0,
        urgency=OrderUrgency.MEDIUM,
        adv=1000.0,
        volatility=0.03
    )

    print(f"  Order ID: {routing_info['order_id']}")
    print(f"  Venues: {routing_info['venues']}")
    print(f"  Expected Cost: {routing_info['expected_cost_bps']:.2f} bps")

    # Execute
    print("\nExecuting Order:")
    report = router.execute_routed_order(decisions, routing_info)
    print(f"  Fill Rate: {report.fill_rate:.2%}")
    print(f"  Avg Price: {report.avg_price:.2f}")
    print(f"  Slippage: {report.slippage_bps:.2f} bps")

    # Post-trade analysis
    print("\nPost-Trade Analysis:")
    post_trade = router.get_post_trade_analysis(report)
    for key, value in post_trade.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        else:
            print(f"  {key}: {value}")

    # Venue rankings
    print("\nVenue Rankings:")
    rankings = router.get_venue_rankings()
    for rank in rankings:
        print(f"  {rank['venue']}: score={rank['quality_score']:.3f}, fill_rate={rank['fill_rate']:.2%}")
