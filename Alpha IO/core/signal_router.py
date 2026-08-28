# signal_router.py – route signals to appropriate execution path based on strength and asymmetry

from core.precision_trade_planner import map_signal_to_trade
from core.asymmetry_index import AsymmetryIndex


class SignalRouter:
    """Decides whether a signal is traded, watched, or dropped.

    Pass a corpus (``core.news_feed.NewsCorpus``) and the asymmetry score is
    measured against what the crowd is actually saying rather than against the
    caller's estimate of it. Thresholds are 0.6 to trade and 0.4 to watch, and
    a *measured* signal additionally needs corroboration from more than one
    source before it can trade. This router still terminates at a decision,
    never at a broker.
    """

    #: A measured signal must be corroborated by at least this many distinct
    #: sources before it can route to `trade`. One outlet running a story is one
    #: outlet's opinion; the whole point of tracking source breadth in Phase 5
    #: was to be able to refuse to act on it. Unmeasured signals are unaffected —
    #: they have no source count to judge, and their caller took that risk.
    MIN_SOURCES_TO_TRADE = 2

    def __init__(self, asymmetry_threshold=0.6, corpus=None, analyzer=None,
                 min_sources_to_trade=None):
        self.ai = AsymmetryIndex(corpus=corpus, analyzer=analyzer)
        self.asymmetry_threshold = asymmetry_threshold
        self.corpus = corpus
        self.min_sources_to_trade = (
            self.MIN_SOURCES_TO_TRADE if min_sources_to_trade is None else min_sources_to_trade
        )

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
        clears_score = asym_score >= self.asymmetry_threshold and confidence > 0.75
        corroborated = (
            not measurement.measured
            or measurement.sources >= self.min_sources_to_trade
        )

        if clears_score and corroborated:
            trade = map_signal_to_trade(signal_text, confidence, timing, volatility)
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
                "decision": "trade",
                "execution": trade
            }

        if clears_score and not corroborated:
            # Scored high enough to trade, but only one outlet is carrying it.
            # Demote rather than act.
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
                "decision": "watchlist",
                "note": (
                    f"Cleared the trade threshold on {measurement.sources} source(s); "
                    f"needs {self.min_sources_to_trade} to route."
                )
            }

        if asym_score >= 0.4:
            return {
                "signal": signal_text,
                "asymmetry_score": asym_score,
                "asymmetry": measurement.to_dict(),
                "decision": "watchlist",
                "note": "Track for future opportunity"
            }

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
