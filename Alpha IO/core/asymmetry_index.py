# asymmetry_index.py – calculate how early and unique a signal is compared to crowd trends

import numpy as np
import hashlib
from datetime import datetime

class AsymmetryIndex:
    def __init__(self):
        self.signal_hashes = {}
        self.trend_sentiment_db = {}  # Simulated crowd trend sentiment archive

    def _hash_signal(self, text):
        return hashlib.sha256(text.encode()).hexdigest()

    def record_signal(self, signal_text, confidence, timestamp=None):
        # Hash the signal to track duplicates
        key = self._hash_signal(signal_text)
        timestamp = timestamp or datetime.now().isoformat()
        self.signal_hashes[key] = {"confidence": confidence, "timestamp": timestamp}

    def compute_asymmetry(self, signal_text, confidence, crowd_sentiment=0.5, news_count=10, gis_factor=0.0):
        """
        Scores how early and contrarian a signal is.
        Parameters:
            signal_text: str – the signal content
            confidence: float – agent confidence score
            crowd_sentiment: float – average social/media sentiment (0-1)
            news_count: int – number of media articles mentioning similar terms
            gis_factor: float – optional boost for regional divergence in interest/activity (0-1)
        Returns:
            float – asymmetry score (higher = more alpha potential)
        """
        signal_hash = self._hash_signal(signal_text)

        # Lower crowd sentiment + few mentions + high confidence = high asymmetry
        rarity = max(1 - crowd_sentiment, 0.01)
        novelty = max(1 / (news_count + 1), 0.01)
        alignment = confidence

        # GIS factor adds boost for regional patterns others are ignoring
        score = alignment * rarity * novelty * (1 + gis_factor)

        return round(score, 4)

# Example usage
if __name__ == "__main__":
    ai = AsymmetryIndex()
    text = "Bullish activity on ADA rising in LATAM"
    confidence = 0.82
    sentiment = 0.3  # crowd is uncertain
    mentions = 4     # not widely talked about
    gis_spike = 0.4  # regional interest high

    ai.record_signal(text, confidence)
    score = ai.compute_asymmetry(text, confidence, sentiment, mentions, gis_spike)
    print("[ASYMMETRY SCORE]", score)