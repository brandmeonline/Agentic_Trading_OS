# asymmetry_index.py – calculate how early and unique a signal is compared to crowd trends

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


# =============================================================================
# Novelty models
# =============================================================================
#
# Novelty is the term that punishes coverage, and its shape decides the whole
# score range. The original shape was hyperbolic, 1/(n+1), which decays so fast
# that any term with two or more mentions could not clear SignalRouter's 0.4
# watchlist threshold at any confidence — the index was blind to the early,
# thinly-covered signals it exists to find. See docs/ULTRA_PLAN.md Phase 2.1.
#
# Every shape is kept so the choice stays evidence-based
# (tools/asymmetry_calibration.py) rather than asserted. The default is now
# LogNovelty; see DEFAULT_NOVELTY below for why, and pass LEGACY_NOVELTY to
# reproduce the old scores.


class NoveltyModel:
    """Maps a mention count to a novelty multiplier in (0, 1]."""

    name = "novelty"

    def __call__(self, news_count: int) -> float:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


class HyperbolicNovelty(NoveltyModel):
    """``k / (k + n)``. With k=1 this is the original ``1 / (n + 1)``.

    Larger k decays more slowly. k is the mention count at which novelty has
    fallen to one half.
    """

    name = "hyperbolic"

    def __init__(self, half_life: float = 1.0):
        if half_life <= 0:
            raise ValueError("half_life must be > 0")
        self.half_life = float(half_life)

    def __call__(self, news_count: int) -> float:
        return self.half_life / (self.half_life + max(0, news_count))

    def describe(self) -> str:
        return f"hyperbolic(half_life={self.half_life:g})"


class LogNovelty(NoveltyModel):
    """``1 / (1 + log1p(n) / scale)``.

    Decays far more slowly than hyperbolic, so a term with real coverage keeps
    a usable score and the router's existing thresholds stay reachable.
    """

    name = "log"

    def __init__(self, scale: float = 1.0):
        if scale <= 0:
            raise ValueError("scale must be > 0")
        self.scale = float(scale)

    def __call__(self, news_count: int) -> float:
        return 1.0 / (1.0 + math.log1p(max(0, news_count)) / self.scale)

    def describe(self) -> str:
        return f"log(scale={self.scale:g})"


#: The original shape, kept for reproducing pre-2026-08 scores.
LEGACY_NOVELTY = HyperbolicNovelty(half_life=1.0)

#: The default, chosen 2026-08-28 from tools/asymmetry_calibration.py.
#:
#: Rationale, in the order it mattered:
#:
#: 1. Under the legacy hyperbolic shape a term with two or more mentions could
#:    not reach the router's 0.4 watchlist threshold at any confidence. The
#:    index exists to catch early, uncrowded signals, and it was blind to
#:    exactly those: one to three mentions with a divergent crowd scored
#:    `ignore`.
#: 2. Slower shapes fix that, but `hyperbolic-8` routes a *single* bearish
#:    headline straight to `trade` (0.6271 at 0.85 confidence). Auto-routing on
#:    one uncorroborated story is the failure mode this system was reviewed
#:    against; rejected outright.
#: 3. `log-2` reaches watchlist at up to 19 mentions. Nineteen mentions is a
#:    crowded story, not an early one, and putting those on the watchlist
#:    dilutes it into a news feed.
#: 4. `log` reaches watchlist at up to 3 mentions and never reaches `trade`
#:    from a thin story. Logarithmic decay is also the principled shape for an
#:    attention variable; the hyperbolic form was algebraic convenience.
#:
#: Thresholds were deliberately left at 0.6/0.4. Changing shape and thresholds
#: together would make the effect unattributable.
DEFAULT_NOVELTY = LogNovelty(scale=1.0)

#: Named shapes for the calibration tool and for configuration.
NOVELTY_MODELS: Dict[str, NoveltyModel] = {
    "hyperbolic": LEGACY_NOVELTY,
    "hyperbolic-3": HyperbolicNovelty(half_life=3.0),
    "hyperbolic-8": HyperbolicNovelty(half_life=8.0),
    "log": LogNovelty(scale=1.0),
    "log-2": LogNovelty(scale=2.0),
}


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

    def __init__(
        self,
        corpus: Optional[Any] = None,
        analyzer: Optional[Callable[[str], float]] = None,
        novelty_model: Optional[NoveltyModel] = None,
    ):
        self.signal_hashes = {}
        self.trend_sentiment_db = {}
        self.corpus = corpus
        self.analyzer = analyzer
        # None means the shipped default (LogNovelty). Pass LEGACY_NOVELTY to
        # reproduce pre-2026-08 scores exactly.
        self.novelty_model = novelty_model or DEFAULT_NOVELTY

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
        novelty = max(self.novelty_model(news_count), 0.01)
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
