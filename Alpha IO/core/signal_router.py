# signal_router.py – route signals to appropriate execution path based on strength and asymmetry

from core.precision_trade_planner import map_signal_to_trade
from core.asymmetry_index import AsymmetryIndex

class SignalRouter:
    def __init__(self, asymmetry_threshold=0.6):
        self.ai = AsymmetryIndex()
        self.asymmetry_threshold = asymmetry_threshold

    def route(self, signal_text, confidence, sentiment=0.5, news_mentions=5, gis_factor=0.0, timing="short_term", volatility="medium"):
        # Score how early/strong the signal is
        asym_score = self.ai.compute_asymmetry(signal_text, confidence, sentiment, news_mentions, gis_factor)

        # Determine action type
        if asym_score >= self.asymmetry_threshold and confidence > 0.75:
            trade = map_signal_to_trade(signal_text, confidence, timing, volatility)
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "decision": "trade",
                "execution": trade
            }

        elif asym_score >= 0.4:
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "decision": "watchlist",
                "note": "Track for future opportunity"
            }

        else:
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "decision": "ignore",
                "note": "Low alpha potential"
            }

# Example usage
if __name__ == "__main__":
    router = SignalRouter()
    result = router.route(
        "Cardano smart wallet activity up in Africa",
        confidence=0.81,
        sentiment=0.35,
        news_mentions=2,
        gis_factor=0.5
    )
    print("[ROUTER] Signal Action Plan:", result)