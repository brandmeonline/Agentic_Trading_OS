"""
Sample Data Generator for Testing.

Generates realistic synthetic market data for testing trading strategies
without requiring external data sources.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass


@dataclass
class AssetConfig:
    """Configuration for simulated asset."""
    symbol: str
    initial_price: float
    volatility: float  # Annual volatility
    drift: float  # Annual drift
    volume_mean: float
    volume_std: float


class SampleDataGenerator:
    """
    Generates realistic synthetic market data.

    Uses Geometric Brownian Motion with configurable
    volatility, drift, and volume patterns.
    """

    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            np.random.seed(seed)

        self.assets: Dict[str, AssetConfig] = {}

    def add_asset(
        self,
        symbol: str,
        initial_price: float = 100.0,
        volatility: float = 0.3,
        drift: float = 0.05,
        volume_mean: float = 1000000,
        volume_std: float = 300000
    ) -> None:
        """Add an asset to generate data for."""
        self.assets[symbol] = AssetConfig(
            symbol=symbol,
            initial_price=initial_price,
            volatility=volatility,
            drift=drift,
            volume_mean=volume_mean,
            volume_std=volume_std
        )

    def generate_ohlcv(
        self,
        symbol: str,
        num_bars: int,
        start_date: Optional[datetime] = None,
        timeframe_minutes: int = 1440  # Daily by default
    ) -> List[Dict]:
        """
        Generate OHLCV data for an asset.

        Args:
            symbol: Asset symbol
            num_bars: Number of bars to generate
            start_date: Starting date
            timeframe_minutes: Minutes per bar

        Returns:
            List of OHLCV dicts
        """
        if symbol not in self.assets:
            raise ValueError(f"Unknown asset: {symbol}")

        config = self.assets[symbol]
        bars = []

        current_price = config.initial_price
        current_time = start_date or (datetime.now() - timedelta(days=num_bars))

        # Convert annual params to per-bar
        dt = timeframe_minutes / (252 * 24 * 60)  # Fraction of year
        bar_drift = config.drift * dt
        bar_vol = config.volatility * np.sqrt(dt)

        for _ in range(num_bars):
            # Generate intrabar prices using GBM
            num_ticks = max(10, timeframe_minutes // 5)
            tick_dt = dt / num_ticks
            tick_vol = config.volatility * np.sqrt(tick_dt)

            prices = [current_price]
            for _ in range(num_ticks):
                dW = np.random.normal(0, 1)
                price_change = prices[-1] * (bar_drift / num_ticks + tick_vol * dW)
                prices.append(prices[-1] + price_change)

            # Create OHLCV bar
            open_price = prices[0]
            close_price = prices[-1]
            high_price = max(prices)
            low_price = min(prices)

            # Generate volume with some correlation to price movement
            base_volume = max(0, np.random.normal(config.volume_mean, config.volume_std))
            volatility_boost = abs(close_price - open_price) / open_price
            volume = base_volume * (1 + volatility_boost * 5)

            bars.append({
                "Datetime": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Open": round(open_price, 4),
                "High": round(high_price, 4),
                "Low": round(low_price, 4),
                "Close": round(close_price, 4),
                "Volume": round(volume, 0)
            })

            current_price = close_price
            current_time += timedelta(minutes=timeframe_minutes)

        return bars

    def generate_correlated_prices(
        self,
        symbols: List[str],
        correlation_matrix: np.ndarray,
        num_bars: int,
        start_date: Optional[datetime] = None
    ) -> Dict[str, List[Dict]]:
        """
        Generate correlated price data for multiple assets.

        Args:
            symbols: List of asset symbols
            correlation_matrix: Correlation matrix for assets
            num_bars: Number of bars
            start_date: Starting date

        Returns:
            Dict mapping symbols to OHLCV data
        """
        n_assets = len(symbols)
        if correlation_matrix.shape != (n_assets, n_assets):
            raise ValueError("Correlation matrix size must match number of assets")

        # Generate correlated random numbers using Cholesky decomposition
        L = np.linalg.cholesky(correlation_matrix)
        uncorrelated = np.random.normal(0, 1, (num_bars, n_assets))
        correlated = uncorrelated @ L.T

        all_data = {}
        current_time = start_date or (datetime.now() - timedelta(days=num_bars))

        for i, symbol in enumerate(symbols):
            if symbol not in self.assets:
                self.add_asset(symbol)

            config = self.assets[symbol]
            bars = []
            current_price = config.initial_price

            dt = 1 / 252  # Daily
            bar_drift = config.drift * dt
            bar_vol = config.volatility * np.sqrt(dt)

            for j in range(num_bars):
                # Use correlated random number
                dW = correlated[j, i]

                # Generate OHLC from single random
                open_price = current_price
                daily_return = bar_drift + bar_vol * dW
                close_price = open_price * (1 + daily_return)

                # High and low
                intraday_vol = config.volatility * np.sqrt(dt) * 0.5
                high_price = max(open_price, close_price) * (1 + abs(np.random.normal(0, intraday_vol)))
                low_price = min(open_price, close_price) * (1 - abs(np.random.normal(0, intraday_vol)))

                # Volume
                base_volume = max(0, np.random.normal(config.volume_mean, config.volume_std))
                volume = base_volume * (1 + abs(daily_return) * 5)

                bars.append({
                    "Datetime": (current_time + timedelta(days=j)).strftime("%Y-%m-%d %H:%M:%S"),
                    "Open": round(open_price, 4),
                    "High": round(high_price, 4),
                    "Low": round(low_price, 4),
                    "Close": round(close_price, 4),
                    "Volume": round(volume, 0)
                })

                current_price = close_price

            all_data[symbol] = bars

        return all_data

    def generate_trade_log(
        self,
        num_trades: int,
        assets: List[str],
        start_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Generate sample trade log data.

        Args:
            num_trades: Number of trades to generate
            assets: List of tradeable assets
            start_date: Starting date

        Returns:
            List of trade dicts
        """
        trades = []
        current_time = start_date or (datetime.now() - timedelta(days=30))

        for i in range(num_trades):
            asset = np.random.choice(assets)
            confidence = np.random.uniform(0.55, 0.95)

            # P&L based on confidence with some noise
            expected_pnl = (confidence - 0.5) * 200
            pnl = np.random.normal(expected_pnl, 50)
            reward = pnl / 100

            trades.append({
                "asset": asset,
                "confidence": round(confidence, 2),
                "action_time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "pnl": round(pnl, 2),
                "reward": round(reward, 2)
            })

            current_time += timedelta(hours=np.random.randint(1, 24))

        return trades

    def generate_tweet_metadata(
        self,
        num_tweets: int,
        assets: List[str],
        start_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Generate sample tweet/signal metadata.

        Args:
            num_tweets: Number of tweets to generate
            assets: List of mentioned assets
            start_date: Starting date

        Returns:
            List of tweet metadata dicts
        """
        users = ["crypto_whale", "trader_joe", "alpha_hunter", "market_sage", "defi_master"]
        sentiments = ["bullish", "bearish", "neutral"]

        tweets = []
        current_time = start_date or (datetime.now() - timedelta(days=30))

        for i in range(num_tweets):
            mentioned_assets = list(np.random.choice(assets, size=np.random.randint(1, 3), replace=False))
            sentiment = np.random.choice(sentiments, p=[0.4, 0.3, 0.3])

            if sentiment == "bullish":
                text = f"Very bullish on {', '.join(mentioned_assets)}! 🚀"
            elif sentiment == "bearish":
                text = f"Watch out for {', '.join(mentioned_assets)}, looking weak 📉"
            else:
                text = f"Interesting price action on {', '.join(mentioned_assets)}"

            tweets.append({
                "user": np.random.choice(users),
                "text": text,
                "tickers": str(mentioned_assets),
                "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "sentiment": sentiment,
                "followers": np.random.randint(1000, 100000)
            })

            current_time += timedelta(minutes=np.random.randint(10, 120))

        return tweets

    def save_to_csv(self, data: List[Dict], filepath: str) -> None:
        """Save data to CSV file."""
        if not data:
            return

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)

        print(f"Saved {len(data)} rows to {filepath}")


def generate_sample_dataset(output_dir: str = "data", seed: int = 42) -> None:
    """
    Generate complete sample dataset for testing.

    Creates:
    - Price data for BTC, ETH, ADA
    - Trade log
    - Tweet metadata
    - Signal scores
    """
    generator = SampleDataGenerator(seed=seed)

    # Configure assets
    generator.add_asset("BTC", initial_price=45000, volatility=0.6, drift=0.15, volume_mean=1e9, volume_std=3e8)
    generator.add_asset("ETH", initial_price=2500, volatility=0.7, drift=0.20, volume_mean=5e8, volume_std=2e8)
    generator.add_asset("ADA", initial_price=0.50, volatility=0.9, drift=0.10, volume_mean=1e8, volume_std=5e7)

    # Generate correlated price data
    correlation = np.array([
        [1.0, 0.8, 0.6],
        [0.8, 1.0, 0.7],
        [0.6, 0.7, 1.0]
    ])

    print("Generating price data...")
    price_data = generator.generate_correlated_prices(
        symbols=["BTC", "ETH", "ADA"],
        correlation_matrix=correlation,
        num_bars=365,
        start_date=datetime(2024, 1, 1)
    )

    for symbol, data in price_data.items():
        generator.save_to_csv(data, f"{output_dir}/{symbol}_USD_prices.csv")

    # Generate trade log
    print("Generating trade log...")
    trades = generator.generate_trade_log(
        num_trades=200,
        assets=["BTC", "ETH", "ADA"],
        start_date=datetime(2024, 6, 1)
    )
    generator.save_to_csv(trades, f"{output_dir}/trade_log.csv")

    # Generate tweet metadata
    print("Generating tweet metadata...")
    tweets = generator.generate_tweet_metadata(
        num_tweets=500,
        assets=["BTC", "ETH", "ADA"],
        start_date=datetime(2024, 1, 1)
    )
    generator.save_to_csv(tweets, f"{output_dir}/tweet_metadata.csv")

    # Generate signal scores
    print("Generating signal scores...")
    signals = []
    for tweet in tweets:
        tickers = eval(tweet["tickers"])
        for ticker in tickers:
            price_change = np.random.uniform(-5, 5)
            if tweet["sentiment"] == "bullish":
                price_change += 1
            elif tweet["sentiment"] == "bearish":
                price_change -= 1

            signals.append({
                "user": tweet["user"],
                "tweet": tweet["text"][:50],
                "asset": ticker,
                "timestamp": tweet["timestamp"],
                "price_change_%": round(price_change, 2)
            })

    generator.save_to_csv(signals, f"{output_dir}/tweet_signal_scores.csv")

    print(f"\nSample dataset generated in {output_dir}/")


if __name__ == "__main__":
    import sys

    output_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
    generate_sample_dataset(output_dir)
