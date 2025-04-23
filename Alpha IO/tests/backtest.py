import pandas as pd
from core.agent import TradingAgent
from core.risk import RiskManager

# Load signal scores
signals = pd.read_csv("data/tweet_signal_scores.csv")

# Initialize agent and risk manager
agent = TradingAgent()
risk = RiskManager()

# Parameters
TRADE_SIZE_USD = 1000
CONFIDENCE_THRESHOLD = 0.7
FEE_RATE = 0.003

def confidence_model(change_pct):
    return min(1.0, max(0.5, abs(change_pct) / 10))

trade_log = []
for _, row in signals.iterrows():
    change = row["price_change_%"]
    confidence = confidence_model(change)
    asset = row["asset"]

    if not agent.decide(confidence):
        continue

    if not risk.check_risk_limits(asset):
        continue

    size = risk.get_position_size(asset, confidence)
    gross_return = size * (change / 100)
    fee = size * FEE_RATE
    net_pnl = gross_return - fee
    reward = net_pnl

    risk.update_after_trade(asset, net_pnl)
    agent.update(confidence, reward)

    trade_log.append({
        "asset": asset,
        "confidence": round(confidence, 2),
        "action_time": row["timestamp"],
        "pnl": round(net_pnl, 2),
        "reward": round(reward, 2)
    })

df_trades = pd.DataFrame(trade_log)
df_trades.to_csv("data/trade_log.csv", index=False)
print("Backtest complete. Saved to trade_log.csv.")