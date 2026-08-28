# asymmetry_index.py – calculate how early and unique a signal is compared to crowd trends

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional


@dataclass
class MeasuredAsymmetry:
    """An asymmetry score plus where its inputs came from.

    ``measured`` is the whole point: a score computed from a corpus is evidence,
    one computed from default arguments is a guess. Callers that route on this
    need to be able to tell them apart.
    """
    score: float
    term: Optional[str]
    measured: bool
    crowd_sentiment: float
    news_count: int
    sources: int = 0

    def to_dict(self):
        return {
            "score": self.score,
            "term": self.term,
            "measured": self.measured,
            "crowd_sentiment": round(self.crowd_sentiment, 4),
            "news_count": self.news_count,
            "sources": self.sources,
        }


class AsymmetryIndex:
    """Scores how early and contrarian a signal is.

    Attach a corpus (``core.news_feed.NewsCorpus``, or anything exposing
    ``crowding(term)``) and ``compute_measured`` reads crowd sentiment and
    mention count from the rolling window instead of taking them on trust from
    the caller. Without one, behaviour is unchanged.
    """

    def __init__(self, corpus: Optional[Any] = None, analyzer: Optional[Callable[[str], float]] = None):
        self.signal_hashes = {}
        self.trend_sentiment_db = {}
        self.corpus = corpus
        self.analyzer = analyzer

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
        # Lower crowd sentiment + few mentions + high confidence = high asymmetry
        rarity = max(1 - crowd_sentiment, 0.01)
        novelty = max(1 / (news_count + 1), 0.01)
        alignment = confidence

        # GIS factor adds boost for regional patterns others are ignoring
        score = alignment * rarity * novelty * (1 + gis_factor)

        return round(score, 4)

    def compute_measured(
        self,
        signal_text,
        confidence,
        term: Optional[str] = None,
        gis_factor: float = 0.0,
        crowd_sentiment: float = 0.5,
        news_count: int = 10,
    ) -> MeasuredAsymmetry:
        """Score a signal against the corpus rather than against arguments.

        ``term`` is what gets looked up. When omitted it is inferred from the
        signal text. If nothing can be measured — no corpus, or no term to look
        up — the supplied defaults are used and ``measured`` is False, so the
        caller can weight the result accordingly.
        """
        resolved = term or self._infer_term(signal_text)
        measured = False
        sources = 0

        if self.corpus is not None and resolved:
            stats = self.corpus.crowding(resolved, analyzer=self.analyzer)
            crowd_sentiment = stats.crowd_sentiment
            news_count = stats.mentions
            sources = stats.sources
            measured = True

        return MeasuredAsymmetry(
            score=self.compute_asymmetry(
                signal_text, confidence, crowd_sentiment, news_count, gis_factor
            ),
            term=resolved,
            measured=measured,
            crowd_sentiment=crowd_sentiment,
            news_count=news_count,
            sources=sources,
        )

    @staticmethod
    def _infer_term(signal_text: str) -> Optional[str]:
        """Pick the term to measure crowding on: first ticker, else nothing.

        Returning None rather than guessing keeps an unmeasurable signal
        honestly labelled instead of silently scored against an unrelated term.
        """
        try:
            from core.news_feed import extract_tickers
        except ImportError:
            return None
        tickers: List[str] = extract_tickers(signal_text, limit=1)
        return tickers[0] if tickers else None


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
