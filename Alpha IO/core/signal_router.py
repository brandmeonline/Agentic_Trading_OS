# signal_router.py – route signals to appropriate execution path based on strength and asymmetry

from core.precision_trade_planner import map_signal_to_trade
from core.asymmetry_index import AsymmetryIndex


class SignalRouter:
    """Decides whether a signal is traded, watched, or dropped.

    Pass a corpus (``core.news_feed.NewsCorpus``) and the asymmetry score is
    measured against what the crowd is actually saying rather than against the
    caller's estimate of it. The routing thresholds are unchanged either way —
    this router still terminates at a decision, never at a broker.
    """

    def __init__(self, asymmetry_threshold=0.6, corpus=None, analyzer=None):
        self.ai = AsymmetryIndex(corpus=corpus, analyzer=analyzer)
        self.asymmetry_threshold = asymmetry_threshold
        self.corpus = corpus

    def route(self, signal_text, confidence, sentiment=0.5, news_mentions=5, gis_factor=0.0,
              timing="short_term", volatility="medium", term=None):
        # Score how early/strong the signal is. With a corpus attached the crowd
        # figures come from measurement; without one they come from the caller.
        measurement = self.ai.compute_measured(
            signal_text,
            confidence,
            term=term,
            gis_factor=gis_factor,
            crowd_sentiment=sentiment,
            news_count=news_mentions,
        )
        asym_score = measurement.score

        # Determine action type
        if asym_score >= self.asymmetry_threshold and confidence > 0.75:
            trade = map_signal_to_trade(signal_text, confidence, timing, volatility)
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
                "decision": "trade",
                "execution": trade
            }

        elif asym_score >= 0.4:
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
                "decision": "watchlist",
                "note": "Track for future opportunity"
            }

        else:
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
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
