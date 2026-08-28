#!/usr/bin/env python3
"""Reproducible calibration report for the asymmetry score.

Run:  python tools/asymmetry_calibration.py

Answers one question with evidence instead of assertion: under each novelty
shape, what would SignalRouter actually route? The shipped shape is hyperbolic
with half_life 1 — the original ``1 / (n + 1)`` — under which any term with two
or more mentions cannot clear the 0.4 watchlist threshold at any confidence.

This tool does not change anything. It prints the reachability ceiling per
shape and, on a fixture corpus, what each shape would decide. The choice of
shape (or of thresholds) changes what the system would trade and is the
owner's call; see docs/ULTRA_PLAN.md Phase 2.1.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.asymmetry_index import NOVELTY_MODELS, AsymmetryIndex
from core.news_feed import NewsCorpus, NewsItem, SourceCategory, content_id, extract_tickers
from core.signal_router import SignalRouter

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)

TRADE_THRESHOLD = 0.6
WATCHLIST_THRESHOLD = 0.4

# Coverage levels that matter: silence, a single wire, a story picked up, and a
# saturated tape.
MENTION_LEVELS = (0, 1, 2, 3, 5, 10, 25)

FIXTURE = [
    # (headline, copies) — a saturated name, a developing story, and a quiet one.
    ("NVDA surges to a record high on strong data center growth", 12),
    ("TSMC beats on capacity expansion guidance", 4),
    ("AMD quietly wins a design slot", 1),
]


def _item(title: str, index: int) -> NewsItem:
    url = f"https://example.com/{index}"
    return NewsItem(
        item_id=content_id(f"{title} {index}", url),
        title=f"{title} {index}",
        summary="",
        url=url,
        source=f"wire-{index % 4}",
        category=SourceCategory.WIRE,
        credibility=0.85,
        published=NOW,
        fetched=NOW,
        entities=extract_tickers(title),
    )


def build_corpus() -> NewsCorpus:
    corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
    counter = 0
    for headline, copies in FIXTURE:
        for _ in range(copies):
            corpus.add(_item(headline, counter))
            counter += 1
    return corpus


def ceiling_table() -> None:
    """Max achievable score per shape, at the most generous possible inputs."""
    print("== REACHABILITY CEILING (confidence=1.0, crowd=0.0, gis=0.0) ==")
    header = "  mentions " + "".join(f"{name:>16}" for name in NOVELTY_MODELS)
    print(header)
    for count in MENTION_LEVELS:
        row = f"  {count:>8} "
        for name, model in NOVELTY_MODELS.items():
            index = AsymmetryIndex(novelty_model=model)
            row += f"{index.compute_asymmetry('x', 1.0, 0.0, count, 0.0):>16.4f}"
        print(row)

    print()
    print(f"  router thresholds: trade >= {TRADE_THRESHOLD}, watchlist >= {WATCHLIST_THRESHOLD}")
    print("  max mentions that can still reach watchlist, per shape:")
    for name, model in NOVELTY_MODELS.items():
        index = AsymmetryIndex(novelty_model=model)
        reachable = -1
        for count in range(0, 200):
            if index.compute_asymmetry("x", 1.0, 0.0, count, 0.0) >= WATCHLIST_THRESHOLD:
                reachable = count
            else:
                break
        verdict = "never" if reachable < 0 else f"{reachable}"
        print(f"    {name:>14}  {verdict}")


def decision_table(corpus: NewsCorpus) -> None:
    """What each shape would actually decide on the fixture corpus."""
    print()
    print("== DECISIONS ON FIXTURE CORPUS (confidence=0.85) ==")
    print(f"  corpus: {len(corpus)} documents, {len(corpus.stats(NOW)['sources'])} sources")
    print()
    print(f"  {'shape':>14} {'term':>6} {'mentions':>9} {'crowd':>7} {'score':>9}  decision")

    terms = ("NVDA", "TSMC", "AMD")
    for name, model in NOVELTY_MODELS.items():
        router = SignalRouter(corpus=corpus)
        router.ai.novelty_model = model
        for term in terms:
            result = router.route(f"{term} setup", confidence=0.85, term=term)
            measured = result["asymmetry"]
            print(
                f"  {name:>14} {term:>6} {measured['news_count']:>9} "
                f"{measured['crowd_sentiment']:>7.2f} {measured['score']:>9.4f}  "
                f"{result['decision']}"
            )


def scenario_table() -> None:
    """Reachability on the cases the index actually exists to catch.

    The fixture corpus is uniformly bullish, so everything in it is correctly
    scored as low-asymmetry. The interesting cases are a term nobody is
    covering, and a term with thin bearish coverage.
    """
    print()
    print("== REACHABILITY BY SCENARIO (confidence=0.85) ==")
    print(f"  {'shape':>14} {'scenario':>28} {'score':>9}  decision")

    scenarios = (
        ("silent (0 mentions, crowd 0.50)", 0, 0.50),
        ("thin bearish (1, crowd 0.17)", 1, 0.17),
        ("thin bearish (3, crowd 0.20)", 3, 0.20),
        ("covered bearish (8, crowd 0.25)", 8, 0.25),
        ("saturated bullish (25, crowd 0.85)", 25, 0.85),
    )

    for name, model in NOVELTY_MODELS.items():
        index = AsymmetryIndex(novelty_model=model)
        for label, mentions, crowd in scenarios:
            score = index.compute_asymmetry("x", 0.85, crowd, mentions, 0.0)
            if score >= TRADE_THRESHOLD:
                decision = "trade"
            elif score >= WATCHLIST_THRESHOLD:
                decision = "watchlist"
            else:
                decision = "ignore"
            print(f"  {name:>14} {label:>28} {score:>9.4f}  {decision}")


def ordering_guard(corpus: NewsCorpus) -> int:
    """Every shape must preserve the thesis: uncrowded outscores crowded."""
    print()
    print("== ORDERING GUARD (uncrowded must outscore crowded, all shapes) ==")
    failures = 0
    for name, model in NOVELTY_MODELS.items():
        index = AsymmetryIndex(corpus=corpus, novelty_model=model)
        crowded = index.compute_measured("NVDA setup", 0.85, term="NVDA").score
        quiet = index.compute_measured("AMD setup", 0.85, term="AMD").score
        ok = quiet > crowded
        failures += 0 if ok else 1
        print(f"  {name:>14}  AMD {quiet:.4f} > NVDA {crowded:.4f}  {'ok' if ok else 'FAIL'}")
    return failures


def main() -> int:
    corpus = build_corpus()
    ceiling_table()
    decision_table(corpus)
    scenario_table()
    failures = ordering_guard(corpus)

    print()
    if failures:
        print(f"FAIL: {failures} shape(s) inverted the ordering property")
        return 1
    print("PASS: ordering property holds under every shape")
    print()
    print("Shipped default is unchanged (hyperbolic, half_life=1). Selecting a")
    print("different shape, or moving the router thresholds, changes what the")
    print("system would trade — see docs/ULTRA_PLAN.md Phase 2.1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
