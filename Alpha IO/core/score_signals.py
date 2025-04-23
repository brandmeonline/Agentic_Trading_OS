import pandas as pd
from datetime import datetime

def compute_price_delta(prices, tweet_time):
    prices["Datetime"] = pd.to_datetime(prices["Datetime"])
    tweet_dt = pd.to_datetime(tweet_time)
    after = prices[prices["Datetime"] > tweet_dt]
    if after.empty:
        return 0.0
    future_close = after.iloc[0]["Close"]
    recent_close = prices.iloc[prices["Datetime"].searchsorted(tweet_dt) - 1]["Close"]
    return round((future_close - recent_close) / recent_close * 100, 2)

def score_signals():
    tweets = pd.read_csv("data/tweet_metadata.csv")
    btc = pd.read_csv("data/BTC_USD_prices.csv")
    eth = pd.read_csv("data/ETH_USD_prices.csv")
    ada = pd.read_csv("data/ADA_USD_prices.csv")
    asset_map = {"BTC": btc, "ETH": eth, "ADA": ada}

    scores = []
    for _, row in tweets.iterrows():
        tickers = eval(row["tickers"]) if isinstance(row["tickers"], str) else row["tickers"]
        for ticker in tickers:
            if ticker in asset_map:
                delta = compute_price_delta(asset_map[ticker], row["timestamp"])
                scores.append({
                    "user": row["user"],
                    "tweet": row["text"],
                    "asset": ticker,
                    "timestamp": row["timestamp"],
                    "price_change_%": delta
                })

    df_scores = pd.DataFrame(scores)
    df_scores.to_csv("data/tweet_signal_scores.csv", index=False)
    print("Signal scoring complete. Output saved to tweet_signal_scores.csv.")

if __name__ == "__main__":
    score_signals()