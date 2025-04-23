import pandas as pd
import numpy as np

class TradingAgent:
    def __init__(self, confidence_bins=None):
        if confidence_bins is None:
            confidence_bins = [0.5, 0.6, 0.7, 0.8, 0.9]
        self.confidence_bins = confidence_bins
        self.q_table = {}
        self.momentum = []

    def _bin_confidence(self, confidence):
        for b in reversed(self.confidence_bins):
            if confidence >= b:
                return b
        return 0.5

    def update(self, confidence, reward):
        bin_key = self._bin_confidence(confidence)
        self.q_table.setdefault(bin_key, 0)
        self.q_table[bin_key] = 0.9 * self.q_table[bin_key] + 0.1 * reward
        self.momentum.append(reward)
        if len(self.momentum) > 5:
            self.momentum.pop(0)

    def decide(self, confidence):
        bin_key = self._bin_confidence(confidence)
        expected_reward = self.q_table.get(bin_key, 0)
        streak_bonus = 0.05 if sum(r > 0 for r in self.momentum[-3:]) >= 2 else 0
        return expected_reward + streak_bonus > 0

    def summary(self):
        return {
            "Q-Table": self.q_table,
            "Momentum": self.momentum
        }

if __name__ == "__main__":
    try:
        trades = pd.read_csv("data/trade_log.csv")
        agent = TradingAgent()
        for _, row in trades.iterrows():
            agent.update(row["confidence"], row["reward"])
        print("Agent Summary:", agent.summary())
    except FileNotFoundError:
        print("No trade log found. Run backtest to generate signals.")