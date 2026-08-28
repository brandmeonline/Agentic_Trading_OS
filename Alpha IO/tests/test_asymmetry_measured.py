"""Tests for corpus-measured asymmetry scoring and safe ticker parsing.

Two properties are load-bearing:

1. Without a corpus, ``compute_asymmetry`` is byte-identical to what it was —
   the measured path is strictly additive, so no existing caller changes
   behaviour.
2. With a corpus, an uncrowded term outscores a saturated one at equal
   confidence. That is the whole thesis of the index.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.asymmetry_index import (
    NOVELTY_MODELS,
    AsymmetryIndex,
    HyperbolicNovelty,
    LogNovelty,
    MeasuredAsymmetry,
)
from core.news_feed import (
    NewsCorpus,
    NewsItem,
    SourceCategory,
    _lexicon_polarity,
    content_id,
    extract_tickers,
)
from core.score_signals import MalformedTickerField, parse_tickers
from core.signal_router import SignalRouter

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def item(title, source_name="wire"):
    url = f"https://example.com/{abs(hash(title)) % 100000}"
    return NewsItem(
        item_id=content_id(title, url),
        title=title,
        summary="",
        url=url,
        source=source_name,
        category=SourceCategory.WIRE,
        credibility=0.8,
        published=NOW,
        fetched=NOW,
        entities=extract_tickers(title),
    )


class TestBackwardCompatibility(unittest.TestCase):
    def test_literal_path_is_unchanged(self):
        index = AsymmetryIndex()
        # Values from the module's own worked example.
        self.assertEqual(
            index.compute_asymmetry("Bullish activity on ADA rising in LATAM", 0.82, 0.3, 4, 0.4),
            0.1607,
        )

    def test_measured_without_corpus_falls_back_to_supplied_values(self):
        index = AsymmetryIndex()
        result = index.compute_measured(
            "Bullish activity on ADA rising in LATAM", 0.82,
            crowd_sentiment=0.3, news_count=4, gis_factor=0.4,
        )
        self.assertIsInstance(result, MeasuredAsymmetry)
        self.assertFalse(result.measured)
        self.assertEqual(result.score, 0.1607)

    def test_record_signal_still_tracks_hashes(self):
        index = AsymmetryIndex()
        index.record_signal("ADA breakout", 0.8)
        self.assertEqual(len(index.signal_hashes), 1)


class TestMeasuredPath(unittest.TestCase):
    def setUp(self):
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        self.index = AsymmetryIndex(corpus=self.corpus)

    def test_uncrowded_term_outscores_saturated_term(self):
        for n in range(30):
            self.corpus.add(item(f"NVDA surges to a record high on strong growth {n}"))
        self.corpus.add(item("AMD quietly wins a design slot"))

        crowded = self.index.compute_measured("NVDA looks extended here", 0.8, term="NVDA")
        quiet = self.index.compute_measured("AMD looks interesting here", 0.8, term="AMD")

        self.assertTrue(crowded.measured)
        self.assertTrue(quiet.measured)
        self.assertGreater(quiet.score, crowded.score)

    def test_measurement_reports_its_inputs(self):
        for n in range(3):
            self.corpus.add(item(f"NVDA beats estimates again {n}", source_name=f"wire-{n % 2}"))
        result = self.index.compute_measured("NVDA setup", 0.8, term="NVDA")
        self.assertEqual(result.news_count, 3)
        self.assertEqual(result.sources, 2)
        self.assertEqual(result.term, "NVDA")

    def test_silent_term_is_measured_as_silent(self):
        result = self.index.compute_measured("Nothing has been written about this", 0.9, term="ZZZZ")
        self.assertTrue(result.measured)
        self.assertEqual(result.news_count, 0)
        self.assertEqual(result.crowd_sentiment, 0.5)

    def test_term_is_inferred_from_signal_text(self):
        self.corpus.add(item("TSLA delivers record quarter"))
        result = self.index.compute_measured("TSLA momentum building", 0.8)
        self.assertEqual(result.term, "TSLA")
        self.assertTrue(result.measured)

    def test_unmeasurable_signal_is_labelled_not_guessed(self):
        # No ticker in the text, so there is nothing honest to look up.
        result = self.index.compute_measured("something vague is happening", 0.8)
        self.assertIsNone(result.term)
        self.assertFalse(result.measured)

    def test_supplied_analyzer_is_used(self):
        self.corpus.add(item("NVDA does a thing"))
        index = AsymmetryIndex(corpus=self.corpus, analyzer=lambda _: 1.0)
        result = index.compute_measured("NVDA", 0.8, term="NVDA")
        self.assertAlmostEqual(result.crowd_sentiment, 1.0)


class TestSignalRouter(unittest.TestCase):
    def test_router_without_corpus_behaves_as_before(self):
        router = SignalRouter()
        result = router.route("Cardano smart wallet activity up in Africa", confidence=0.81,
                              sentiment=0.35, news_mentions=2, gis_factor=0.5)
        self.assertIn(result["decision"], {"trade", "watchlist", "ignore"})
        self.assertFalse(result["asymmetry"]["measured"])

    def test_router_with_corpus_measures(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(20):
            corpus.add(item(f"NVDA rallies on strong guidance {n}"))
        router = SignalRouter(corpus=corpus)
        result = router.route("NVDA breakout continues", confidence=0.9, term="NVDA")
        self.assertTrue(result["asymmetry"]["measured"])
        self.assertEqual(result["asymmetry"]["news_count"], 20)

    def test_crowded_signal_is_not_routed_to_trade(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(50):
            corpus.add(item(f"NVDA surges to record high on strong growth {n}"))
        router = SignalRouter(corpus=corpus)
        result = router.route("NVDA breakout continues", confidence=0.95, term="NVDA")
        self.assertNotEqual(result["decision"], "trade")


class TestSafeTickerParsing(unittest.TestCase):
    def test_parses_json_array(self):
        self.assertEqual(parse_tickers('["BTC", "ETH"]'), ["BTC", "ETH"])

    def test_parses_python_list_literal(self):
        self.assertEqual(parse_tickers("['btc', 'ada']"), ["BTC", "ADA"])

    def test_parses_comma_separated_and_strips_dollar_prefix(self):
        self.assertEqual(parse_tickers("$BTC, $ETH"), ["BTC", "ETH"])

    def test_handles_empty_and_missing(self):
        self.assertEqual(parse_tickers(""), [])
        self.assertEqual(parse_tickers(None), [])

    def test_does_not_execute_an_expression(self):
        # The old implementation called eval() on this field.
        with self.assertRaises(MalformedTickerField):
            parse_tickers("__import__('os').system('echo pwned')")

    def test_rejects_non_list_literal(self):
        with self.assertRaises(MalformedTickerField):
            parse_tickers("{'a': 1}")



class TestNoveltyModels(unittest.TestCase):
    """Phase 2.1: the score shape is configurable; the default is unchanged."""

    def test_default_is_the_original_shape(self):
        index = AsymmetryIndex()
        self.assertIsInstance(index.novelty_model, HyperbolicNovelty)
        self.assertEqual(index.novelty_model.half_life, 1.0)
        for n in (0, 1, 2, 5, 10):
            self.assertAlmostEqual(index.novelty_model(n), 1.0 / (n + 1))

    def test_hyperbolic_half_life_is_where_novelty_halves(self):
        model = HyperbolicNovelty(half_life=8.0)
        self.assertAlmostEqual(model(8), 0.5)

    def test_log_decays_more_slowly_than_hyperbolic(self):
        hyperbolic, log = HyperbolicNovelty(), LogNovelty()
        for n in (2, 5, 10, 25):
            self.assertGreater(log(n), hyperbolic(n))

    def test_models_validate_their_parameters(self):
        with self.assertRaises(ValueError):
            HyperbolicNovelty(half_life=0)
        with self.assertRaises(ValueError):
            LogNovelty(scale=0)

    def test_negative_counts_are_treated_as_zero(self):
        self.assertEqual(HyperbolicNovelty()(-5), 1.0)
        self.assertEqual(LogNovelty()(-5), 1.0)

    def test_every_registered_model_preserves_the_ordering_property(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(15):
            corpus.add(item(f"NVDA surges to a record high on strong growth {n}"))
        corpus.add(item("AMD quietly wins a design slot"))

        for name, model in NOVELTY_MODELS.items():
            with self.subTest(shape=name):
                index = AsymmetryIndex(corpus=corpus, novelty_model=model)
                crowded = index.compute_measured("NVDA", 0.85, term="NVDA").score
                quiet = index.compute_measured("AMD", 0.85, term="AMD").score
                self.assertGreater(quiet, crowded)

    def test_swapping_the_shape_raises_the_reachable_ceiling(self):
        # The defect Phase 2.1 documents: at the shipped shape, two mentions
        # cannot reach the router's watchlist threshold at any confidence.
        shipped = AsymmetryIndex()
        self.assertLess(shipped.compute_asymmetry("x", 1.0, 0.0, 2, 0.0), 0.4)
        slower = AsymmetryIndex(novelty_model=NOVELTY_MODELS["hyperbolic-8"])
        self.assertGreater(slower.compute_asymmetry("x", 1.0, 0.0, 2, 0.0), 0.4)


class TestPolarityDamping(unittest.TestCase):
    """Crowd sentiment must not saturate on a single matched word."""

    def test_one_matched_word_is_a_weak_opinion(self):
        weak = _lexicon_polarity("shares surge")
        self.assertGreater(weak, 0.0)
        self.assertLess(weak, 0.5)

    def test_more_evidence_means_more_confidence(self):
        weak = _lexicon_polarity("shares surge")
        strong = _lexicon_polarity("shares surge to a record on strong profit growth")
        self.assertGreater(strong, weak)

    def test_polarity_stays_bounded(self):
        saturated = _lexicon_polarity(" ".join(["surge record strong growth profit gains"] * 20))
        self.assertLessEqual(saturated, 1.0)

    def test_mixed_and_empty_text_is_neutral(self):
        self.assertEqual(_lexicon_polarity("surge and plunge"), 0.0)
        self.assertEqual(_lexicon_polarity(""), 0.0)
        self.assertEqual(_lexicon_polarity("no polarity words at all here"), 0.0)

    def test_crowd_sentiment_no_longer_pins_rarity_to_its_floor(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(12):
            corpus.add(item(f"NVDA surges to a record high on strong growth {n}"))
        # Saturation would put this at exactly 1.0, flooring rarity at 0.01.
        self.assertLess(corpus.crowd_sentiment("NVDA", NOW), 0.95)


class TestRouterReachability(unittest.TestCase):
    """The router must be able to say something other than 'ignore'."""

    def test_an_uncovered_term_reaches_watchlist(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        corpus.add(item("NVDA beats"))
        router = SignalRouter(corpus=corpus)
        result = router.route("QQQQ setup", confidence=0.85, term="QQQQ")
        self.assertEqual(result["asymmetry"]["news_count"], 0)
        self.assertEqual(result["decision"], "watchlist")

    def test_a_saturated_bullish_term_is_ignored(self):
        corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(25):
            corpus.add(item(f"NVDA surges to a record high on strong growth {n}"))
        router = SignalRouter(corpus=corpus)
        self.assertEqual(router.route("NVDA setup", confidence=0.95, term="NVDA")["decision"], "ignore")

if __name__ == "__main__":
    unittest.main()
