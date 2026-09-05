"""Deterministic leak traps for backtests — ATOS-P3-BT-001.

Invariant:

    Nothing a backtest computes for bar *t* may depend on data from bar *t+1*
    or later, and no fill may happen at a price the decision could not have
    been made before.

Lookahead is the failure mode that flatters. It does not crash, it does not
warn, and it produces exactly the result the author was hoping for, which is
why it survives review. The usual defence — reading the code carefully — is
the one defence that has already failed by the time the bug exists.

So the defence here is mechanical, and rests on one property:

    **Prefix invariance.** A causal transform, given only the first *k* bars,
    must produce for those *k* bars exactly what it produces when given the
    whole series. If truncating the future changes the past, the past was
    reading the future.

That single check catches full-series normalisation, centred rolling windows,
``fillna(method="bfill")``, a scaler fitted before the split, an indicator
that peeks one bar ahead, and the classic "signal on today's close, fill at
today's close" — without knowing anything about the transform.

The rest of this module is the machinery that makes the property usable:
purged and embargoed splits, so a train window's lookback cannot reach into
the test window; a scaler that can only be fitted once; and a canary series
whose future genuinely carries no information, so a strategy that profits on
it has necessarily cheated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

Series = Sequence[float]
Transform = Callable[[Sequence[float]], Sequence[float]]


class LeakageDetected(AssertionError):
    """A computation for bar t depended on data from after bar t."""


# ---------------------------------------------------------------------------
# Prefix invariance
# ---------------------------------------------------------------------------


def _as_list(values: Any) -> List[Any]:
    try:
        return list(values)
    except TypeError:  # pragma: no cover - a transform returning a scalar
        return [values]


def _differs(left: Any, right: Any, tolerance: float) -> bool:
    if left is None or right is None:
        return left is not right
    try:
        return abs(float(left) - float(right)) > tolerance
    except (TypeError, ValueError):
        return left != right


def causality_violations(
    transform: Transform,
    series: Series,
    prefixes: Optional[Sequence[int]] = None,
    tolerance: float = 1e-9,
    warmup: int = 0,
) -> List[str]:
    """Every position where truncating the future changed the past.

    ``warmup`` skips the leading positions a transform is entitled to leave
    undefined; it does not skip anything else, because a transform that
    settles down after a while has still leaked for the bars before that.
    """
    full = _as_list(transform(list(series)))
    n = len(series)
    if prefixes is None:
        # A spread of cut points rather than every one: the failures this
        # catches are systematic, and checking n prefixes of an n-bar series
        # is quadratic for no extra discrimination.
        prefixes = sorted({
            max(warmup + 1, n // 8), n // 4, n // 2, (3 * n) // 4, n - 1,
        })

    violations: List[str] = []
    for cut in prefixes:
        if cut <= warmup or cut >= n:
            continue
        truncated = _as_list(transform(list(series[:cut])))
        for index in range(warmup, min(cut, len(truncated), len(full))):
            if _differs(truncated[index], full[index], tolerance):
                violations.append(
                    f"position {index} is {truncated[index]!r} when the series "
                    f"ends at {cut} but {full[index]!r} when it does not; "
                    "the value depends on data that had not happened yet"
                )
                break  # one report per cut is enough to identify the fault
    return violations


def assert_causal(
    transform: Transform,
    series: Series,
    tolerance: float = 1e-9,
    warmup: int = 0,
    name: str = "transform",
) -> None:
    """Raise unless the transform is prefix invariant."""
    violations = causality_violations(
        transform, series, tolerance=tolerance, warmup=warmup
    )
    if violations:
        raise LeakageDetected(
            f"{name} reads the future:\n  " + "\n  ".join(violations[:5])
        )


# ---------------------------------------------------------------------------
# Purged, embargoed splits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One train/test window, with the gap between them made explicit."""

    train_start: int
    train_end: int      # exclusive
    test_start: int
    test_end: int       # exclusive

    @property
    def gap(self) -> int:
        return self.test_start - self.train_end

    @property
    def train_length(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_length(self) -> int:
        return self.test_end - self.test_start

    def to_dict(self) -> Dict[str, int]:
        return {
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "gap": self.gap,
        }


def required_gap(feature_lookback: int, label_horizon: int, embargo: int) -> int:
    """How many bars must separate train from test.

    Three separate reasons, and systems usually account for one:

    * the last training label looks *forward* ``label_horizon`` bars, so those
      bars are already inside the training set's information;
    * the first test feature looks *back* ``feature_lookback`` bars, so those
      bars are already inside the test set's information;
    * an embargo on top, because serial correlation does not stop dead at the
      boundary.
    """
    return max(0, feature_lookback) + max(0, label_horizon) + max(0, embargo)


def purged_windows(
    n_bars: int,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
    feature_lookback: int = 0,
    label_horizon: int = 0,
    embargo: int = 0,
) -> List[Window]:
    """Walk-forward windows with a real gap between train and test.

    Anchored to the *test* window and working backwards, so the gap is carved
    out of the training data rather than silently overlapping it. A window
    whose training set does not survive that subtraction is dropped, which is
    the honest outcome: there was not enough history to test that period
    without cheating.
    """
    gap = required_gap(feature_lookback, label_horizon, embargo)
    # `step or test_size` would turn an explicit 0 into a default, which is a
    # caller error silently repaired into an infinite loop's opposite.
    step = test_size if step is None else step
    if train_size <= 0 or test_size <= 0 or step <= 0:
        raise ValueError("train_size, test_size and step must all be positive")

    windows: List[Window] = []
    test_start = train_size + gap
    while test_start + test_size <= n_bars:
        train_end = test_start - gap
        train_start = max(0, train_end - train_size)
        if train_end - train_start >= train_size:
            windows.append(Window(train_start, train_end,
                                  test_start, test_start + test_size))
        test_start += step
    return windows


def overlap_problems(windows: Sequence[Window], minimum_gap: int) -> List[str]:
    """Reasons a set of windows would leak."""
    problems: List[str] = []
    for index, window in enumerate(windows):
        if window.train_end > window.test_start:
            problems.append(
                f"window {index}: training runs to {window.train_end} but the "
                f"test starts at {window.test_start}; they overlap"
            )
        elif window.gap < minimum_gap:
            problems.append(
                f"window {index}: only {window.gap} bar(s) between train and "
                f"test, and {minimum_gap} are needed to cover the feature "
                "lookback, the label horizon and the embargo"
            )
    return problems


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


class TrainOnlyScaler:
    """Standardises features using statistics from the training set alone.

    Fitting a scaler on the whole dataset is the most common leak in this
    class of code, and the most forgivable: it is one line, it looks like
    preprocessing rather than modelling, and the leak it introduces is small
    per-sample and decisive in aggregate. The mean of the full series is a
    fact about the future.

    So this refuses to be fitted twice, and refuses to transform before it has
    been fitted. Both refusals exist because the mistake is an ordering
    mistake, and an ordering mistake is only catchable by an object that knows
    what order it was used in.
    """

    def __init__(self) -> None:
        self.mean: Optional[float] = None
        self.std: Optional[float] = None
        self._fitted = False

    def fit(self, train: Series) -> "TrainOnlyScaler":
        if self._fitted:
            raise LeakageDetected(
                "this scaler has already been fitted; refitting it on more "
                "data means the earlier transform used statistics that "
                "later changed"
            )
        values = [float(v) for v in train]
        if not values:
            raise ValueError("cannot fit a scaler on an empty training set")
        self.mean = sum(values) / len(values)
        variance = sum((v - self.mean) ** 2 for v in values) / len(values)
        self.std = variance ** 0.5
        self._fitted = True
        return self

    def transform(self, values: Series) -> List[float]:
        if not self._fitted:
            raise LeakageDetected(
                "transform() before fit(): a scaler that derives its "
                "statistics from the data it is scaling has seen the test set"
            )
        scale = self.std or 1.0
        assert self.mean is not None
        return [(float(v) - self.mean) / scale for v in values]

    def fit_transform_train(self, train: Series) -> List[float]:
        return self.fit(train).transform(train)


def full_series_normalisation_leak(
    series: Series, train_end: int, tolerance: float = 1e-9
) -> bool:
    """Whether standardising over the whole series differs from train-only.

    Returns True when the two disagree, which is the normal case and the
    point: if a pipeline's normalised values match the full-series version,
    the pipeline normalised over the full series.
    """
    train = list(series[:train_end])
    if not train or train_end >= len(series):
        return False
    scaler = TrainOnlyScaler().fit(train)
    train_only = scaler.transform(series)

    values = [float(v) for v in series]
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 or 1.0
    full = [(v - mean) / std for v in values]

    return any(
        abs(a - b) > tolerance for a, b in zip(train_only[train_end:], full[train_end:])
    )


# ---------------------------------------------------------------------------
# The canary series
# ---------------------------------------------------------------------------


def unpredictable_series(
    n_bars: int, seed: int = 20260101, start: float = 100.0,
    volatility: float = 0.01,
) -> List[float]:
    """A price path whose future carries no information about itself.

    Deterministic from the seed, so a leak trap built on it is reproducible.
    Any strategy that makes money on this after costs did not find an edge:
    there is none to find.
    """
    import random

    rng = random.Random(seed)
    prices = [start]
    for _ in range(n_bars - 1):
        prices.append(max(0.01, prices[-1] * (1.0 + rng.gauss(0.0, volatility))))
    return prices


@dataclass
class CanaryVerdict:
    """What a run on the unpredictable series implies."""

    total_return: float
    trades: int
    threshold: float

    @property
    def leaked(self) -> bool:
        """A return this large on noise is not skill."""
        return self.trades > 0 and self.total_return > self.threshold

    def explain(self) -> str:
        if not self.trades:
            return "the strategy did not trade, so the canary proves nothing"
        if self.leaked:
            return (
                f"returned {self.total_return:.2%} over {self.trades} trade(s) "
                f"on a series with no predictable structure, above the "
                f"{self.threshold:.2%} threshold; the only way to do that is "
                "to have seen the future"
            )
        return (
            f"returned {self.total_return:.2%} over {self.trades} trade(s) on "
            "noise, which is consistent with no edge and no leak"
        )


def judge_canary(
    total_return: float, trades: int, threshold: float = 0.10
) -> CanaryVerdict:
    return CanaryVerdict(
        total_return=total_return, trades=trades, threshold=threshold
    )
