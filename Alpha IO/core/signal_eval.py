"""
Signal evaluation — does the asymmetry signal actually have an edge?

This module exists because "the signals are more accurate than a human" is a
claim, and claims about trading signals are worthless without measurement. Every
number the system produces so far — asymmetry scores, crowd sentiment, mention
counts — is an *input*. None of it has ever been checked against what prices
subsequently did.

So this measures it: take signals with timestamps, take the prices that followed,
and compute the hit rate, the mean forward return, and whether either beats a
baseline by more than sampling noise explains.

Three design commitments, all of them about not fooling ourselves:

1. **Baselines are mandatory.** A 55% hit rate sounds good and is worthless if
   buy-and-hold over the same windows returned more. Every result is reported
   against always-long, crowd-following, and coin-flip.
2. **Small samples establish nothing.** Below ``MIN_SAMPLES`` the verdict is
   `INSUFFICIENT` regardless of how good the numbers look. Thirty signals with a
   70% hit rate is a coincidence, not an edge.
3. **The verdict is conservative.** Edge is `ESTABLISHED` only when the lower
   bound of the hit-rate confidence interval clears the baseline. A point
   estimate above the baseline is not enough.

Nothing here trades. ``core.pipeline`` reads the verdict to decide whether it is
allowed to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

#: Below this many evaluated signals, no verdict is possible. Chosen so that a
#: 60% hit rate is distinguishable from chance at roughly 95% confidence.
MIN_SAMPLES = 100

#: z for a two-sided 95% interval.
Z_95 = 1.96


class Verdict(Enum):
    """What the evidence supports. Deliberately blunt."""
    INSUFFICIENT = "insufficient"     # not enough samples to say anything
    NO_EDGE = "no_edge"               # measured, and it does not beat baseline
    ESTABLISHED = "established"       # measured, and it does


@dataclass
class SignalObservation:
    """One signal and the market outcome that followed it."""
    timestamp: datetime
    term: str
    score: float
    direction: int                 # +1 long, -1 short, 0 no view
    crowd_sentiment: float
    sources: int
    forward_return: Optional[float] = None   # fractional, e.g. 0.012 = +1.2%

    @property
    def evaluated(self) -> bool:
        return self.forward_return is not None

    @property
    def correct(self) -> Optional[bool]:
        """Did the move go the way the signal pointed?"""
        if self.forward_return is None or self.direction == 0:
            return None
        return (self.forward_return > 0) == (self.direction > 0)

    @property
    def signed_return(self) -> Optional[float]:
        """Return as the signal would have earned it."""
        if self.forward_return is None or self.direction == 0:
            return None
        return self.forward_return * self.direction

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "term": self.term,
            "score": round(self.score, 4),
            "direction": self.direction,
            "crowd_sentiment": round(self.crowd_sentiment, 4),
            "sources": self.sources,
            "forward_return": self.forward_return,
            "correct": self.correct,
        }


@dataclass
class Baseline:
    """A strategy the signal has to beat to be worth anything."""
    name: str
    hit_rate: float
    mean_return: float
    samples: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "hit_rate": round(self.hit_rate, 4),
            "mean_return": round(self.mean_return, 6),
            "samples": self.samples,
        }


@dataclass
class EdgeReport:
    """The verdict, the numbers behind it, and why."""
    verdict: Verdict
    samples: int
    hit_rate: float
    hit_rate_low: float
    hit_rate_high: float
    mean_return: float
    baselines: List[Baseline] = field(default_factory=list)
    reason: str = ""

    @property
    def established(self) -> bool:
        return self.verdict is Verdict.ESTABLISHED

    def best_baseline(self) -> Optional[Baseline]:
        return max(self.baselines, key=lambda b: b.hit_rate, default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "established": self.established,
            "samples": self.samples,
            "hit_rate": round(self.hit_rate, 4),
            "hit_rate_ci95": [round(self.hit_rate_low, 4), round(self.hit_rate_high, 4)],
            "mean_return": round(self.mean_return, 6),
            "baselines": [b.to_dict() for b in self.baselines],
            "reason": self.reason,
        }

    def summary(self) -> str:
        if self.verdict is Verdict.INSUFFICIENT:
            return f"INSUFFICIENT — {self.samples}/{MIN_SAMPLES} samples. {self.reason}"
        beaten = self.best_baseline()
        against = f" vs best baseline {beaten.hit_rate:.1%} ({beaten.name})" if beaten else ""
        return (
            f"{self.verdict.value.upper()} — {self.samples} signals, "
            f"hit rate {self.hit_rate:.1%} "
            f"[{self.hit_rate_low:.1%}, {self.hit_rate_high:.1%}]{against}. {self.reason}"
        )


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation because hit rates live near 0.5
    with modest sample sizes, exactly where the normal approximation is worst and
    can hand back a lower bound below zero.
    """
    if trials <= 0:
        return (0.0, 0.0)
    phat = successes / trials
    denominator = 1 + z * z / trials
    centre = phat + z * z / (2 * trials)
    margin = z * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials))
    return (
        max(0.0, (centre - margin) / denominator),
        min(1.0, (centre + margin) / denominator),
    )


class SignalEvaluator:
    """Attaches outcomes to signals and judges whether they beat baselines.

    ``price_lookup(term, at)`` must return a price or None. Anything satisfying
    that shape works: a live client, a CSV, a test fixture.
    """

    def __init__(
        self,
        price_lookup: Callable[[str, datetime], Optional[float]],
        horizon: timedelta = timedelta(days=1),
        min_samples: int = MIN_SAMPLES,
    ) -> None:
        self.price_lookup = price_lookup
        self.horizon = horizon
        self.min_samples = min_samples

    def attach_outcomes(self, observations: Sequence[SignalObservation]) -> List[SignalObservation]:
        """Fill in ``forward_return`` where prices are available.

        Signals whose horizon has not elapsed, or whose prices are missing, are
        left unevaluated rather than assumed flat — treating an unknown outcome
        as a zero return silently biases the hit rate toward the baseline.
        """
        evaluated: List[SignalObservation] = []
        for obs in observations:
            entry = self.price_lookup(obs.term, obs.timestamp)
            exit_price = self.price_lookup(obs.term, obs.timestamp + self.horizon)
            if entry is None or exit_price is None or entry == 0:
                evaluated.append(obs)
                continue
            obs.forward_return = (exit_price - entry) / entry
            evaluated.append(obs)
        return evaluated

    def evaluate(self, observations: Sequence[SignalObservation]) -> EdgeReport:
        """Judge the signal. Returns a verdict, never a bare number."""
        usable = [o for o in observations if o.evaluated and o.direction != 0]
        n = len(usable)

        if n < self.min_samples:
            return EdgeReport(
                verdict=Verdict.INSUFFICIENT,
                samples=n,
                hit_rate=0.0, hit_rate_low=0.0, hit_rate_high=0.0,
                mean_return=0.0,
                reason=(
                    "Not enough evaluated signals to distinguish skill from noise. "
                    "No trading decision may rely on this."
                ),
            )

        hits = sum(1 for o in usable if o.correct)
        hit_rate = hits / n
        low, high = wilson_interval(hits, n)
        mean_return = sum(o.signed_return for o in usable) / n

        baselines = self._baselines(usable)
        best = max(baselines, key=lambda b: b.hit_rate, default=None)
        bar = best.hit_rate if best else 0.5

        if low > bar:
            verdict = Verdict.ESTABLISHED
            reason = (
                f"Lower bound of the 95% interval ({low:.1%}) clears the best "
                f"baseline ({bar:.1%})."
            )
        else:
            verdict = Verdict.NO_EDGE
            reason = (
                f"Lower bound ({low:.1%}) does not clear the best baseline "
                f"({bar:.1%}). The point estimate may look better; sampling noise "
                f"explains it."
            )

        return EdgeReport(
            verdict=verdict,
            samples=n,
            hit_rate=hit_rate,
            hit_rate_low=low,
            hit_rate_high=high,
            mean_return=mean_return,
            baselines=baselines,
            reason=reason,
        )

    @staticmethod
    def _baselines(usable: Sequence[SignalObservation]) -> List[Baseline]:
        """Strategies the signal must beat, computed on the same observations."""
        n = len(usable)

        # Always long: the market drifts up; any signal must beat simply being in.
        long_hits = sum(1 for o in usable if o.forward_return > 0)
        long_returns = [o.forward_return for o in usable]

        # Follow the crowd: buy what the tape is bullish on. This is the
        # "what a human reading the news would do" comparator, and it is the one
        # the asymmetry thesis explicitly bets against.
        crowd_dir = [1 if o.crowd_sentiment >= 0.5 else -1 for o in usable]
        crowd_hits = sum(
            1 for o, d in zip(usable, crowd_dir) if (o.forward_return > 0) == (d > 0)
        )
        crowd_returns = [o.forward_return * d for o, d in zip(usable, crowd_dir)]

        # Coin flip, made deterministic by alternating, so the report is
        # reproducible rather than reseeded on every run.
        flip_dir = [1 if i % 2 == 0 else -1 for i in range(n)]
        flip_hits = sum(
            1 for o, d in zip(usable, flip_dir) if (o.forward_return > 0) == (d > 0)
        )
        flip_returns = [o.forward_return * d for o, d in zip(usable, flip_dir)]

        return [
            Baseline("always-long", long_hits / n, sum(long_returns) / n, n),
            Baseline("follow-the-crowd", crowd_hits / n, sum(crowd_returns) / n, n),
            Baseline("coin-flip", flip_hits / n, sum(flip_returns) / n, n),
        ]


def observation_from_measurement(
    measurement: Any,
    timestamp: datetime,
    direction: Optional[int] = None,
) -> SignalObservation:
    """Build an observation from a ``MeasuredAsymmetry``.

    Direction defaults to *against* the crowd, which is the index's actual
    thesis: a bearish tape on an uncrowded name is a long candidate, not a
    short one.
    """
    if direction is None:
        direction = -1 if measurement.crowd_sentiment >= 0.5 else 1
    return SignalObservation(
        timestamp=timestamp,
        term=measurement.term or "",
        score=measurement.score,
        direction=direction,
        crowd_sentiment=measurement.crowd_sentiment,
        sources=measurement.sources,
    )
