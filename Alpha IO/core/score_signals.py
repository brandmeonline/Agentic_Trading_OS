"""Correlate social-post timing with subsequent price moves.

The ticker column arrives as text in a CSV. It used to be passed to ``eval()``,
which executes arbitrary Python from a data file — a remote code execution path
in anything that ingests third-party post metadata. It is now parsed as data.
"""

import ast
import json
import os

import pandas as pd

TWEET_METADATA = "data/tweet_metadata.csv"
PRICE_FILES = {
    "BTC": "data/BTC_USD_prices.csv",
    "ETH": "data/ETH_USD_prices.csv",
    "ADA": "data/ADA_USD_prices.csv",
}
OUTPUT = "data/tweet_signal_scores.csv"


class MalformedTickerField(ValueError):
    """Raised when a ticker cell is neither a JSON nor a Python list literal."""


def parse_tickers(raw):
    """Parse a ticker cell into a list of symbols, without executing it.

    Accepts a JSON array, a Python list literal, or a comma-separated string.
    Anything else — an expression, a call, a name — is rejected.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item).strip().upper() for item in raw if str(item).strip()]
    if not isinstance(raw, str):
        raise MalformedTickerField(f"unsupported ticker field type: {type(raw).__name__}")

    text = raw.strip()
    if not text:
        return []

    for loader in (json.loads, ast.literal_eval):
        try:
            value = loader(text)
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, (list, tuple)):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [value.strip().upper()] if value.strip() else []
        raise MalformedTickerField(f"ticker field is not a list: {text!r}")

    if "," in text or text.replace("$", "").isalnum():
        return [part.strip().lstrip("$").upper() for part in text.split(",") if part.strip()]

    raise MalformedTickerField(f"could not parse ticker field as data: {text!r}")


def compute_price_delta(prices, post_time):
    """Percent change from the last close before a post to the first close after."""
    prices = prices.copy()
    prices["Datetime"] = pd.to_datetime(prices["Datetime"])
    prices = prices.sort_values("Datetime").reset_index(drop=True)
    post_dt = pd.to_datetime(post_time)

    after = prices[prices["Datetime"] > post_dt]
    if after.empty:
        return 0.0

    position = prices["Datetime"].searchsorted(post_dt)
    if position == 0:
        return 0.0

    recent_close = prices.iloc[position - 1]["Close"]
    if not recent_close:
        return 0.0

    future_close = after.iloc[0]["Close"]
    return round((future_close - recent_close) / recent_close * 100, 2)


def _require(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. score_signals requires post metadata and per-asset "
            f"price history; see docs/ULTRA_PLAN.md for the ingestion layer that "
            f"produces them."
        )
    return path


def score_signals(metadata_path=TWEET_METADATA, price_files=None, output=OUTPUT):
    """Score each post by the price move that followed it."""
    price_files = price_files or PRICE_FILES

    posts = pd.read_csv(_require(metadata_path))
    asset_map = {symbol: pd.read_csv(_require(path)) for symbol, path in price_files.items()}

    scores = []
    for _, row in posts.iterrows():
        for ticker in parse_tickers(row.get("tickers")):
            if ticker not in asset_map:
                continue
            scores.append({
                "user": row.get("user"),
                "tweet": row.get("text"),
                "asset": ticker,
                "timestamp": row.get("timestamp"),
                "price_change_%": compute_price_delta(asset_map[ticker], row.get("timestamp")),
            })

    frame = pd.DataFrame(scores)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"Signal scoring complete. {len(frame)} rows written to {output}.")
    return frame


if __name__ == "__main__":
    score_signals()
