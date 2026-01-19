"""
Agentic Trading OS - Blockchain & DeFi Integration.

Next-generation multi-chain support with:
- Multi-chain wallet management (EVM, Solana, Cosmos)
- DEX aggregation (Uniswap, SushiSwap, 1inch)
- DeFi yield farming dashboard
- NFT portfolio tracking
- Cross-chain bridging
- Smart contract interaction
- Gas optimization
"""

from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum
from pathlib import Path
import threading


class ChainType(Enum):
    """Supported blockchain networks."""
    ETHEREUM = "ethereum"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    BASE = "base"
    AVALANCHE = "avalanche"
    BSC = "bsc"
    SOLANA = "solana"
    COSMOS = "cosmos"
    BITCOIN = "bitcoin"


class ProtocolType(Enum):
    """DeFi protocol types."""
    DEX = "dex"
    LENDING = "lending"
    YIELD = "yield"
    BRIDGE = "bridge"
    NFT = "nft"
    STAKING = "staking"
    DERIVATIVES = "derivatives"


@dataclass
class Token:
    """Token information."""
    symbol: str
    name: str
    address: str
    chain: ChainType
    decimals: int = 18
    logo_url: str = ""
    price_usd: float = 0.0
    market_cap: float = 0.0
    volume_24h: float = 0.0


@dataclass
class WalletBalance:
    """Wallet balance for a token."""
    token: Token
    balance: float
    balance_usd: float
    chain: ChainType
    last_updated: str = ""


@dataclass
class DeFiPosition:
    """DeFi position (lending, staking, LP)."""
    protocol: str
    protocol_type: ProtocolType
    chain: ChainType
    position_type: str  # "supply", "borrow", "stake", "lp"
    tokens: List[Token]
    value_usd: float
    apy: float
    rewards_pending: float = 0.0
    health_factor: float = 0.0  # For lending positions
    impermanent_loss: float = 0.0  # For LP positions
    unlock_time: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NFT:
    """NFT asset."""
    token_id: str
    collection_name: str
    collection_address: str
    chain: ChainType
    name: str = ""
    image_url: str = ""
    floor_price: float = 0.0
    last_sale_price: float = 0.0
    rarity_rank: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SwapQuote:
    """DEX swap quote."""
    from_token: Token
    to_token: Token
    from_amount: float
    to_amount: float
    price_impact: float
    gas_estimate: float
    gas_price_gwei: float
    route: List[str]
    aggregator: str
    expires_at: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BridgeQuote:
    """Cross-chain bridge quote."""
    from_chain: ChainType
    to_chain: ChainType
    token: Token
    amount: float
    fee: float
    estimated_time: int  # seconds
    bridge_protocol: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GasPrice:
    """Gas price information."""
    chain: ChainType
    slow: float
    standard: float
    fast: float
    instant: float
    base_fee: float
    priority_fee: float
    last_updated: str = ""


class ChainRegistry:
    """Registry of supported chains and their configurations."""

    CHAINS = {
        ChainType.ETHEREUM: {
            "name": "Ethereum",
            "chain_id": 1,
            "native_token": "ETH",
            "explorer": "https://etherscan.io",
            "rpc": "https://eth.llamarpc.com",
            "avg_block_time": 12,
        },
        ChainType.POLYGON: {
            "name": "Polygon",
            "chain_id": 137,
            "native_token": "MATIC",
            "explorer": "https://polygonscan.com",
            "rpc": "https://polygon.llamarpc.com",
            "avg_block_time": 2,
        },
        ChainType.ARBITRUM: {
            "name": "Arbitrum One",
            "chain_id": 42161,
            "native_token": "ETH",
            "explorer": "https://arbiscan.io",
            "rpc": "https://arbitrum.llamarpc.com",
            "avg_block_time": 0.3,
        },
        ChainType.OPTIMISM: {
            "name": "Optimism",
            "chain_id": 10,
            "native_token": "ETH",
            "explorer": "https://optimistic.etherscan.io",
            "rpc": "https://optimism.llamarpc.com",
            "avg_block_time": 2,
        },
        ChainType.BASE: {
            "name": "Base",
            "chain_id": 8453,
            "native_token": "ETH",
            "explorer": "https://basescan.org",
            "rpc": "https://base.llamarpc.com",
            "avg_block_time": 2,
        },
        ChainType.AVALANCHE: {
            "name": "Avalanche C-Chain",
            "chain_id": 43114,
            "native_token": "AVAX",
            "explorer": "https://snowtrace.io",
            "rpc": "https://avalanche.llamarpc.com",
            "avg_block_time": 2,
        },
        ChainType.BSC: {
            "name": "BNB Smart Chain",
            "chain_id": 56,
            "native_token": "BNB",
            "explorer": "https://bscscan.com",
            "rpc": "https://bsc.llamarpc.com",
            "avg_block_time": 3,
        },
        ChainType.SOLANA: {
            "name": "Solana",
            "chain_id": 0,  # Not EVM
            "native_token": "SOL",
            "explorer": "https://solscan.io",
            "rpc": "https://api.mainnet-beta.solana.com",
            "avg_block_time": 0.4,
        },
    }

    @classmethod
    def get_chain_info(cls, chain: ChainType) -> Dict[str, Any]:
        """Get chain configuration."""
        return cls.CHAINS.get(chain, {})

    @classmethod
    def get_supported_chains(cls) -> List[ChainType]:
        """Get list of supported chains."""
        return list(cls.CHAINS.keys())


class ProtocolRegistry:
    """Registry of DeFi protocols."""

    PROTOCOLS = {
        # DEXes
        "uniswap_v3": {
            "name": "Uniswap V3",
            "type": ProtocolType.DEX,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.OPTIMISM, ChainType.BASE],
            "tvl": 4500000000,
        },
        "sushiswap": {
            "name": "SushiSwap",
            "type": ProtocolType.DEX,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.AVALANCHE],
            "tvl": 500000000,
        },
        "curve": {
            "name": "Curve Finance",
            "type": ProtocolType.DEX,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.AVALANCHE],
            "tvl": 2000000000,
        },
        # Lending
        "aave_v3": {
            "name": "Aave V3",
            "type": ProtocolType.LENDING,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.OPTIMISM, ChainType.AVALANCHE],
            "tvl": 10000000000,
        },
        "compound_v3": {
            "name": "Compound V3",
            "type": ProtocolType.LENDING,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM],
            "tvl": 2500000000,
        },
        # Yield
        "yearn": {
            "name": "Yearn Finance",
            "type": ProtocolType.YIELD,
            "chains": [ChainType.ETHEREUM, ChainType.ARBITRUM],
            "tvl": 300000000,
        },
        "convex": {
            "name": "Convex Finance",
            "type": ProtocolType.YIELD,
            "chains": [ChainType.ETHEREUM],
            "tvl": 3000000000,
        },
        # Staking
        "lido": {
            "name": "Lido",
            "type": ProtocolType.STAKING,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.SOLANA],
            "tvl": 15000000000,
        },
        "rocketpool": {
            "name": "Rocket Pool",
            "type": ProtocolType.STAKING,
            "chains": [ChainType.ETHEREUM],
            "tvl": 2000000000,
        },
        # Bridges
        "stargate": {
            "name": "Stargate",
            "type": ProtocolType.BRIDGE,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.OPTIMISM, ChainType.AVALANCHE, ChainType.BSC],
            "tvl": 400000000,
        },
        "across": {
            "name": "Across Protocol",
            "type": ProtocolType.BRIDGE,
            "chains": [ChainType.ETHEREUM, ChainType.POLYGON, ChainType.ARBITRUM, ChainType.OPTIMISM, ChainType.BASE],
            "tvl": 100000000,
        },
    }

    @classmethod
    def get_protocol(cls, protocol_id: str) -> Dict[str, Any]:
        """Get protocol information."""
        return cls.PROTOCOLS.get(protocol_id, {})

    @classmethod
    def get_protocols_by_type(cls, protocol_type: ProtocolType) -> List[str]:
        """Get protocols by type."""
        return [k for k, v in cls.PROTOCOLS.items() if v.get("type") == protocol_type]


class MultiChainWallet:
    """Multi-chain wallet manager."""

    def __init__(self):
        self.addresses: Dict[ChainType, str] = {}
        self.balances: Dict[str, WalletBalance] = {}
        self.defi_positions: List[DeFiPosition] = []
        self.nfts: List[NFT] = []
        self._lock = threading.Lock()

    def add_wallet(self, chain: ChainType, address: str):
        """Add wallet address for a chain."""
        with self._lock:
            self.addresses[chain] = address

    def get_total_value(self) -> float:
        """Get total portfolio value across all chains."""
        total = sum(b.balance_usd for b in self.balances.values())
        total += sum(p.value_usd for p in self.defi_positions)
        total += sum(n.floor_price for n in self.nfts)
        return total

    def get_chain_breakdown(self) -> Dict[str, float]:
        """Get value breakdown by chain."""
        breakdown = {}
        for balance in self.balances.values():
            chain_name = balance.chain.value
            breakdown[chain_name] = breakdown.get(chain_name, 0) + balance.balance_usd
        for position in self.defi_positions:
            chain_name = position.chain.value
            breakdown[chain_name] = breakdown.get(chain_name, 0) + position.value_usd
        return breakdown

    def get_protocol_breakdown(self) -> Dict[str, float]:
        """Get value breakdown by protocol."""
        breakdown = {}
        for position in self.defi_positions:
            breakdown[position.protocol] = breakdown.get(position.protocol, 0) + position.value_usd
        return breakdown

    def get_asset_breakdown(self) -> Dict[str, float]:
        """Get value breakdown by asset type."""
        return {
            "tokens": sum(b.balance_usd for b in self.balances.values()),
            "defi": sum(p.value_usd for p in self.defi_positions),
            "nfts": sum(n.floor_price for n in self.nfts)
        }


class DEXAggregator:
    """DEX aggregator for best swap rates."""

    AGGREGATORS = ["1inch", "paraswap", "0x", "kyber"]

    def __init__(self):
        self._cache: Dict[str, SwapQuote] = {}
        self._lock = threading.Lock()

    def get_quote(
        self,
        from_token: Token,
        to_token: Token,
        amount: float,
        slippage: float = 0.5
    ) -> List[SwapQuote]:
        """Get swap quotes from multiple aggregators."""
        quotes = []

        for aggregator in self.AGGREGATORS:
            quote = self._get_aggregator_quote(aggregator, from_token, to_token, amount, slippage)
            if quote:
                quotes.append(quote)

        # Sort by output amount (best rate first)
        quotes.sort(key=lambda q: q.to_amount, reverse=True)
        return quotes

    def _get_aggregator_quote(
        self,
        aggregator: str,
        from_token: Token,
        to_token: Token,
        amount: float,
        slippage: float
    ) -> Optional[SwapQuote]:
        """Get quote from specific aggregator."""
        # Simulated quote generation
        import random

        # Simulate different rates from aggregators
        base_rate = to_token.price_usd / from_token.price_usd if from_token.price_usd > 0 else 1
        rate_variation = random.uniform(0.995, 1.005)
        to_amount = amount * base_rate * rate_variation

        price_impact = random.uniform(0.01, 0.5)
        gas_estimate = random.uniform(100000, 300000)
        gas_price = random.uniform(20, 50)

        return SwapQuote(
            from_token=from_token,
            to_token=to_token,
            from_amount=amount,
            to_amount=to_amount,
            price_impact=price_impact,
            gas_estimate=gas_estimate,
            gas_price_gwei=gas_price,
            route=[from_token.symbol, "WETH", to_token.symbol] if from_token.symbol != to_token.symbol else [from_token.symbol],
            aggregator=aggregator,
            expires_at=(datetime.now() + timedelta(minutes=2)).isoformat(),
            data={"slippage": slippage}
        )

    def get_best_route(
        self,
        from_token: Token,
        to_token: Token,
        amount: float
    ) -> Optional[SwapQuote]:
        """Get the best swap route."""
        quotes = self.get_quote(from_token, to_token, amount)
        return quotes[0] if quotes else None


class BridgeAggregator:
    """Cross-chain bridge aggregator."""

    BRIDGES = ["stargate", "across", "hop", "synapse", "celer"]

    def get_bridge_quote(
        self,
        from_chain: ChainType,
        to_chain: ChainType,
        token: Token,
        amount: float
    ) -> List[BridgeQuote]:
        """Get bridge quotes for cross-chain transfer."""
        quotes = []

        for bridge in self.BRIDGES:
            quote = self._get_bridge_quote(bridge, from_chain, to_chain, token, amount)
            if quote:
                quotes.append(quote)

        # Sort by fee (lowest first)
        quotes.sort(key=lambda q: q.fee)
        return quotes

    def _get_bridge_quote(
        self,
        bridge: str,
        from_chain: ChainType,
        to_chain: ChainType,
        token: Token,
        amount: float
    ) -> Optional[BridgeQuote]:
        """Get quote from specific bridge."""
        import random

        # Simulate bridge fees and times
        fee_pct = random.uniform(0.1, 0.5)
        fee = amount * fee_pct / 100
        time_seconds = random.randint(60, 600)

        return BridgeQuote(
            from_chain=from_chain,
            to_chain=to_chain,
            token=token,
            amount=amount,
            fee=fee,
            estimated_time=time_seconds,
            bridge_protocol=bridge,
            data={"fee_pct": fee_pct}
        )


class YieldOptimizer:
    """DeFi yield optimization engine."""

    def __init__(self):
        self._opportunities: List[Dict] = []
        self._lock = threading.Lock()

    def find_opportunities(
        self,
        token: str,
        amount: float,
        chains: List[ChainType] = None,
        min_apy: float = 0.0,
        max_risk: str = "high"
    ) -> List[Dict]:
        """Find yield opportunities for a token."""
        if chains is None:
            chains = list(ChainType)

        # Simulated yield opportunities
        opportunities = [
            {
                "protocol": "Aave V3",
                "chain": ChainType.ETHEREUM,
                "strategy": "lending",
                "base_apy": 3.5,
                "reward_apy": 1.2,
                "total_apy": 4.7,
                "tvl": 1500000000,
                "risk": "low",
                "token": token,
            },
            {
                "protocol": "Compound V3",
                "chain": ChainType.ETHEREUM,
                "strategy": "lending",
                "base_apy": 3.2,
                "reward_apy": 0.8,
                "total_apy": 4.0,
                "tvl": 800000000,
                "risk": "low",
                "token": token,
            },
            {
                "protocol": "Curve",
                "chain": ChainType.ETHEREUM,
                "strategy": "liquidity",
                "base_apy": 2.1,
                "reward_apy": 8.5,
                "total_apy": 10.6,
                "tvl": 500000000,
                "risk": "medium",
                "token": token,
            },
            {
                "protocol": "Convex",
                "chain": ChainType.ETHEREUM,
                "strategy": "staking",
                "base_apy": 5.0,
                "reward_apy": 12.0,
                "total_apy": 17.0,
                "tvl": 200000000,
                "risk": "medium",
                "token": token,
            },
            {
                "protocol": "Yearn",
                "chain": ChainType.ETHEREUM,
                "strategy": "vault",
                "base_apy": 8.5,
                "reward_apy": 0.0,
                "total_apy": 8.5,
                "tvl": 150000000,
                "risk": "medium",
                "token": token,
            },
        ]

        # Filter by criteria
        risk_levels = {"low": 1, "medium": 2, "high": 3}
        max_risk_level = risk_levels.get(max_risk, 3)

        filtered = [
            o for o in opportunities
            if o["chain"] in chains
            and o["total_apy"] >= min_apy
            and risk_levels.get(o["risk"], 3) <= max_risk_level
        ]

        # Sort by APY
        filtered.sort(key=lambda x: x["total_apy"], reverse=True)

        return filtered

    def optimize_portfolio(
        self,
        assets: Dict[str, float],
        risk_tolerance: str = "medium"
    ) -> Dict:
        """Optimize portfolio allocation across DeFi protocols."""
        total_value = sum(assets.values())
        recommendations = []

        for token, amount in assets.items():
            opportunities = self.find_opportunities(token, amount, max_risk=risk_tolerance)
            if opportunities:
                best = opportunities[0]
                recommendations.append({
                    "token": token,
                    "amount": amount,
                    "current_yield": 0,
                    "recommended_protocol": best["protocol"],
                    "recommended_chain": best["chain"].value,
                    "expected_apy": best["total_apy"],
                    "expected_annual_return": amount * best["total_apy"] / 100
                })

        total_expected_return = sum(r["expected_annual_return"] for r in recommendations)
        avg_apy = total_expected_return / total_value * 100 if total_value > 0 else 0

        return {
            "total_value": total_value,
            "average_apy": round(avg_apy, 2),
            "expected_annual_return": round(total_expected_return, 2),
            "recommendations": recommendations
        }


class GasTracker:
    """Gas price tracker across chains."""

    def __init__(self):
        self._gas_prices: Dict[ChainType, GasPrice] = {}
        self._lock = threading.Lock()

    def get_gas_price(self, chain: ChainType) -> GasPrice:
        """Get current gas prices for a chain."""
        import random

        # Simulated gas prices
        if chain == ChainType.ETHEREUM:
            base = random.uniform(20, 50)
        elif chain in [ChainType.POLYGON, ChainType.BSC]:
            base = random.uniform(30, 100)
        elif chain in [ChainType.ARBITRUM, ChainType.OPTIMISM, ChainType.BASE]:
            base = random.uniform(0.1, 0.5)
        else:
            base = random.uniform(5, 20)

        return GasPrice(
            chain=chain,
            slow=base * 0.8,
            standard=base,
            fast=base * 1.2,
            instant=base * 1.5,
            base_fee=base * 0.9,
            priority_fee=base * 0.1,
            last_updated=datetime.now().isoformat()
        )

    def get_optimal_time(self, chain: ChainType) -> Dict:
        """Get optimal time to transact based on gas patterns."""
        # Simulated optimal times (in production, use historical data)
        return {
            "current_gwei": self.get_gas_price(chain).standard,
            "optimal_time": "02:00 UTC",
            "expected_savings": "15-25%",
            "recommendation": "Gas prices typically lowest during early UTC hours"
        }


class BlockchainManager:
    """Main blockchain manager class."""

    def __init__(self):
        self.wallet = MultiChainWallet()
        self.dex = DEXAggregator()
        self.bridge = BridgeAggregator()
        self.yield_optimizer = YieldOptimizer()
        self.gas_tracker = GasTracker()

        # Sample data
        self._initialize_sample_data()

    def _initialize_sample_data(self):
        """Initialize with sample data for demo."""
        # Sample balances
        eth_token = Token(
            symbol="ETH",
            name="Ethereum",
            address="0x0000000000000000000000000000000000000000",
            chain=ChainType.ETHEREUM,
            price_usd=2000.0,
            market_cap=240000000000
        )

        self.wallet.balances["ETH-ethereum"] = WalletBalance(
            token=eth_token,
            balance=5.5,
            balance_usd=11000.0,
            chain=ChainType.ETHEREUM,
            last_updated=datetime.now().isoformat()
        )

        # Sample DeFi position
        self.wallet.defi_positions.append(DeFiPosition(
            protocol="Aave V3",
            protocol_type=ProtocolType.LENDING,
            chain=ChainType.ETHEREUM,
            position_type="supply",
            tokens=[eth_token],
            value_usd=5000.0,
            apy=4.5,
            health_factor=2.5
        ))

    def get_portfolio_summary(self) -> Dict:
        """Get comprehensive portfolio summary."""
        return {
            "total_value": self.wallet.get_total_value(),
            "chain_breakdown": self.wallet.get_chain_breakdown(),
            "protocol_breakdown": self.wallet.get_protocol_breakdown(),
            "asset_breakdown": self.wallet.get_asset_breakdown(),
            "defi_positions": len(self.wallet.defi_positions),
            "nfts": len(self.wallet.nfts),
            "chains_active": len(self.wallet.addresses)
        }

    def get_supported_chains(self) -> List[Dict]:
        """Get list of supported chains with info."""
        return [
            {
                "chain": chain.value,
                **ChainRegistry.get_chain_info(chain)
            }
            for chain in ChainRegistry.get_supported_chains()
        ]

    def get_defi_overview(self) -> Dict:
        """Get DeFi protocol overview."""
        protocols_by_type = {}
        for protocol_type in ProtocolType:
            protocols = ProtocolRegistry.get_protocols_by_type(protocol_type)
            protocols_by_type[protocol_type.value] = [
                {
                    "id": p,
                    **ProtocolRegistry.get_protocol(p)
                }
                for p in protocols
            ]

        return {
            "protocols_by_type": protocols_by_type,
            "total_protocols": len(ProtocolRegistry.PROTOCOLS),
            "user_positions": len(self.wallet.defi_positions),
            "total_defi_value": sum(p.value_usd for p in self.wallet.defi_positions)
        }


# =============================================================================
# Singleton Factory
# =============================================================================

_blockchain_manager: Optional[BlockchainManager] = None


def get_blockchain_manager() -> BlockchainManager:
    """Get or create the blockchain manager singleton."""
    global _blockchain_manager
    if _blockchain_manager is None:
        _blockchain_manager = BlockchainManager()
    return _blockchain_manager
