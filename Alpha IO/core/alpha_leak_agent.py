# alpha_leak_agent.py – Simulates access to closed-group alpha leaks and scores them

import random
import hashlib
from datetime import datetime

class AlphaLeakAgent:
    def __init__(self):
        self.leak_sources = ["Telegram_Channel_X", "Discord_Group_Y", "Forum_Alpha_Z"]
        self.memory = {}

    def generate_fake_leaks(self):
        candidates = [
            "New ADA DEX just launched with 3M in liquidity",
            "Insiders saying Polygon getting major Coinbase bump",
            "Rumor: BlackRock launching tokenized asset platform",
            "ETH validator stress may trigger selloff",
            "Solana whale activity shows heavy inflows"
        ]
        leaks = []
        for text in random.sample(candidates, 3):
            signal_hash = hashlib.sha1(text.encode()).hexdigest()
            source = random.choice(self.leak_sources)
            leaks.append({
                "text": text,
                "source": source,
                "hash": signal_hash,
                "timestamp": datetime.now().isoformat(),
                "trust_score": round(random.uniform(0.4, 0.9), 3)
            })
        return leaks

    def score_leak(self, leak_text):
        # Basic score based on novelty and source trust (simulated)
        keywords = ["BlackRock", "whale", "insider", "stress", "DEX"]
        score = sum(1 for k in keywords if k.lower() in leak_text.lower()) / len(keywords)
        return round(score, 2)

    def record_leaks(self, leaks):
        for leak in leaks:
            if leak["hash"] not in self.memory:
                self.memory[leak["hash"]] = {
                    "signal": leak["text"],
                    "source": leak["source"],
                    "score": self.score_leak(leak["text"]),
                    "trust_score": leak["trust_score"],
                    "timestamp": leak["timestamp"]
                }

if __name__ == "__main__":
    agent = AlphaLeakAgent()
    new_leaks = agent.generate_fake_leaks()
    agent.record_leaks(new_leaks)
    for leak in agent.memory.values():
        print("[ALPHA LEAK] Scored:", leak)