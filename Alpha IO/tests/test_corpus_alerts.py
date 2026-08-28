"""Tests for corpus-driven alerts.

Two properties are load-bearing:

1. Existing price alerts are completely unaffected — same gate, same cooldown,
   same behaviour.
2. A corpus alert is subject to exactly the same rate limits as a price alert,
   so sweeping the corpus every minute does not mean alerting every minute.
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alerts import (
    CORPUS_ALERT_TYPES,
    AlertChannel,
    AlertManager,
    AlertStatus,
    AlertType,
    Conviction,
)
from core.asymmetry_index import AsymmetryIndex
from core.news_feed import NewsCorpus, NewsItem, SourceCategory, content_id, extract_tickers

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def item(title, source_name="wire", published=None):
    published = published or NOW
    url = f"https://example.com/{abs(hash((title, source_name))) % 1000000}"
    return NewsItem(
        item_id=content_id(title, url),
        title=title,
        summary="",
        url=url,
        source=source_name,
        category=SourceCategory.WIRE,
        credibility=0.85,
        published=published,
        fetched=published,
        entities=extract_tickers(title),
    )


class CorpusAlertCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.manager = AlertManager(storage_path=os.path.join(self.tmp, "alerts.json"))
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_alert(self, alert_type, value, symbol="NVDA", comparison="gte", **kwargs):
        return self.manager.create_alert(
            name=f"{alert_type.value} {symbol}",
            alert_type=alert_type,
            symbol=symbol,
            value=value,
            channels=[AlertChannel.IN_APP],
            comparison=comparison,
            message="{symbol} at {value}",
            **kwargs,
        )


class TestConviction(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(Conviction.from_sources(1), Conviction.SINGLE_SOURCE)
        self.assertEqual(Conviction.from_sources(0), Conviction.SINGLE_SOURCE)
        self.assertEqual(Conviction.from_sources(2), Conviction.CORROBORATED)
        self.assertEqual(Conviction.from_sources(4), Conviction.HIGH)


class TestMentionVelocity(unittest.TestCase):
    def setUp(self):
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)

    def test_velocity_is_a_rate_not_a_count(self):
        for n in range(6):
            self.corpus.add(item(f"NVDA breaking {n}", published=NOW - timedelta(minutes=10)))
        self.assertEqual(self.corpus.mention_velocity("NVDA", hours=1.0, now=NOW), 6.0)
        self.assertEqual(self.corpus.mention_velocity("NVDA", hours=2.0, now=NOW), 3.0)

    def test_old_mentions_do_not_count_toward_velocity(self):
        for n in range(20):
            self.corpus.add(item(f"NVDA old story {n}", published=NOW - timedelta(hours=10)))
        self.assertEqual(self.corpus.mention_velocity("NVDA", hours=1.0, now=NOW), 0.0)
        self.assertEqual(self.corpus.mention_count("NVDA", NOW), 20)

    def test_rejects_non_positive_window(self):
        with self.assertRaises(ValueError):
            self.corpus.mention_velocity("NVDA", hours=0)


class TestPriceAlertsUnaffected(CorpusAlertCase):
    def test_price_alert_still_fires(self):
        self.make_alert(AlertType.PRICE_ABOVE, 100.0, symbol="AAPL")
        self.manager.update_price("AAPL", 150.0)
        self.assertEqual(len(self.manager.notifications), 1)

    def test_price_alert_respects_cooldown(self):
        self.make_alert(AlertType.PRICE_ABOVE, 100.0, symbol="AAPL", cooldown_seconds=3600)
        self.manager.update_price("AAPL", 150.0)
        self.manager.update_price("AAPL", 160.0)
        self.assertEqual(len(self.manager.notifications), 1)

    def test_price_update_never_evaluates_a_corpus_alert(self):
        # A mention-count alert fed a price of 150 would fire nonsense.
        self.make_alert(AlertType.NEWS_MENTIONS, 5.0)
        self.manager.update_price("NVDA", 150.0)
        self.assertEqual(self.manager.notifications, [])

    def test_corpus_types_are_registered_as_such(self):
        for alert_type in (AlertType.NEWS_MENTIONS, AlertType.MENTION_VELOCITY,
                           AlertType.CROWD_SENTIMENT, AlertType.ASYMMETRY_ABOVE):
            self.assertIn(alert_type, CORPUS_ALERT_TYPES)
        self.assertNotIn(AlertType.PRICE_ABOVE, CORPUS_ALERT_TYPES)


class TestNewsMentionAlerts(CorpusAlertCase):
    def test_fires_when_coverage_crosses_the_threshold(self):
        alert = self.make_alert(AlertType.NEWS_MENTIONS, 3.0)
        for n in range(2):
            self.corpus.add(item(f"NVDA story {n}"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])

        self.corpus.add(item("NVDA story 3"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [alert.id])

    def test_notification_carries_the_measurement_and_conviction(self):
        self.make_alert(AlertType.NEWS_MENTIONS, 2.0)
        for n in range(3):
            self.corpus.add(item(f"NVDA story {n}", source_name=f"wire-{n}"))
        self.manager.update_from_corpus(self.corpus, now=NOW)

        data = self.manager.notifications[-1].data
        self.assertEqual(data["term"], "NVDA")
        self.assertEqual(data["mentions"], 3)
        self.assertEqual(data["sources"], 3)
        self.assertEqual(data["conviction"], Conviction.CORROBORATED.value)
        self.assertIn("crowd_sentiment", data)

    def test_single_source_is_labelled_as_such(self):
        self.make_alert(AlertType.NEWS_MENTIONS, 2.0)
        for n in range(5):
            self.corpus.add(item(f"NVDA story {n}", source_name="one-outlet"))
        self.manager.update_from_corpus(self.corpus, now=NOW)
        self.assertEqual(
            self.manager.notifications[-1].data["conviction"],
            Conviction.SINGLE_SOURCE.value,
        )


class TestRateLimiting(CorpusAlertCase):
    def test_repeated_sweeps_respect_cooldown(self):
        self.make_alert(AlertType.NEWS_MENTIONS, 1.0, cooldown_seconds=3600)
        self.corpus.add(item("NVDA story"))
        for _ in range(10):
            self.manager.update_from_corpus(self.corpus, now=NOW)
        self.assertEqual(len(self.manager.notifications), 1)

    def test_max_triggers_expires_the_alert(self):
        self.make_alert(AlertType.NEWS_MENTIONS, 1.0, cooldown_seconds=0, max_triggers=2)
        self.corpus.add(item("NVDA story"))
        for _ in range(5):
            self.manager.update_from_corpus(self.corpus, now=NOW)
        self.assertEqual(len(self.manager.notifications), 2)
        self.assertEqual(list(self.manager.alerts.values())[0].status, AlertStatus.EXPIRED)

    def test_disabled_alert_is_skipped(self):
        alert = self.make_alert(AlertType.NEWS_MENTIONS, 1.0, cooldown_seconds=0)
        alert.status = AlertStatus.DISABLED
        self.corpus.add(item("NVDA story"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])


class TestCrossingComparisons(CorpusAlertCase):
    def test_cross_above_needs_a_previous_observation(self):
        alert = self.make_alert(AlertType.NEWS_MENTIONS, 3.0, comparison="cross_above",
                                cooldown_seconds=0)
        for n in range(5):
            self.corpus.add(item(f"NVDA story {n}"))
        # First sweep has no prior value, so a crossing cannot be established.
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])

    def test_cross_above_fires_on_the_transition_only(self):
        alert = self.make_alert(AlertType.NEWS_MENTIONS, 3.0, comparison="cross_above",
                                cooldown_seconds=0)
        self.corpus.add(item("NVDA story 0"))
        self.manager.update_from_corpus(self.corpus, now=NOW)  # seeds previous=1

        for n in range(1, 4):
            self.corpus.add(item(f"NVDA story {n}"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [alert.id])

        # Still above, but no longer crossing.
        self.corpus.add(item("NVDA story 9"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])


class TestVelocityAlerts(CorpusAlertCase):
    def test_breaking_story_fires_while_a_stale_one_does_not(self):
        alert = self.make_alert(AlertType.MENTION_VELOCITY, 4.0, cooldown_seconds=0)

        for n in range(20):
            self.corpus.add(item(f"NVDA old {n}", published=NOW - timedelta(hours=8)))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])

        for n in range(5):
            self.corpus.add(item(f"NVDA breaking {n}", published=NOW - timedelta(minutes=5)))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [alert.id])

    def test_velocity_payload_records_its_window(self):
        # One mention across a two-hour window is 0.5/hour, so the threshold
        # has to be stated in the same units the window produces.
        self.make_alert(AlertType.MENTION_VELOCITY, 0.5, cooldown_seconds=0)
        self.corpus.add(item("NVDA breaking", published=NOW - timedelta(minutes=5)))
        self.manager.update_from_corpus(self.corpus, now=NOW, velocity_hours=2.0)
        data = self.manager.notifications[-1].data
        self.assertEqual(data["velocity_window_hours"], 2.0)
        self.assertEqual(data["velocity_per_hour"], 0.5)


class TestSentimentAndAsymmetryAlerts(CorpusAlertCase):
    def test_bearish_crowd_fires_a_lte_alert(self):
        alert = self.make_alert(AlertType.CROWD_SENTIMENT, 0.3, comparison="lte",
                                cooldown_seconds=0)
        for n in range(4):
            self.corpus.add(item(f"NVDA plunges as analysts downgrade on weak guidance {n}"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [alert.id])

    def test_bullish_crowd_does_not_fire_a_lte_alert(self):
        self.make_alert(AlertType.CROWD_SENTIMENT, 0.3, comparison="lte", cooldown_seconds=0)
        for n in range(4):
            self.corpus.add(item(f"NVDA surges to a record on strong growth {n}"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])

    def test_asymmetry_alert_uses_the_measured_path(self):
        alert = self.make_alert(AlertType.ASYMMETRY_ABOVE, 0.3, symbol="ZZZZ",
                                cooldown_seconds=0)
        self.corpus.add(item("NVDA beats"))
        fired = self.manager.update_from_corpus(self.corpus, now=NOW)
        self.assertEqual(fired, [alert.id])
        data = self.manager.notifications[-1].data
        self.assertTrue(data["measured"])
        self.assertEqual(data["mentions"], 0)

    def test_saturated_term_does_not_clear_an_asymmetry_alert(self):
        self.make_alert(AlertType.ASYMMETRY_ABOVE, 0.3, cooldown_seconds=0)
        for n in range(25):
            self.corpus.add(item(f"NVDA surges to a record on strong growth {n}"))
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])

    def test_supplied_asymmetry_index_is_used(self):
        self.make_alert(AlertType.ASYMMETRY_ABOVE, 0.3, symbol="ZZZZ", cooldown_seconds=0)
        self.corpus.add(item("NVDA beats"))
        index = AsymmetryIndex(corpus=self.corpus)
        self.assertEqual(
            len(self.manager.update_from_corpus(self.corpus, now=NOW, asymmetry_index=index)),
            1,
        )


class TestResilience(CorpusAlertCase):
    def test_a_failing_corpus_does_not_stop_the_sweep(self):
        class HalfBrokenCorpus:
            def __init__(self, good):
                self.good = good

            def crowding(self, term, now=None):
                if term == "BAD":
                    raise RuntimeError("corpus down")
                return self.good.crowding(term, now)

            def mention_velocity(self, term, hours=1.0, now=None):
                return self.good.mention_velocity(term, hours=hours, now=now)

        self.corpus.add(item("NVDA story"))
        self.make_alert(AlertType.NEWS_MENTIONS, 1.0, symbol="BAD", cooldown_seconds=0)
        good = self.make_alert(AlertType.NEWS_MENTIONS, 1.0, symbol="NVDA", cooldown_seconds=0)

        fired = self.manager.update_from_corpus(HalfBrokenCorpus(self.corpus), now=NOW)
        self.assertEqual(fired, [good.id])
        self.assertTrue(any("corpus down" in error for error in self.manager.corpus_errors()))

    def test_no_corpus_alerts_means_no_work(self):
        self.make_alert(AlertType.PRICE_ABOVE, 100.0, symbol="AAPL")
        self.assertEqual(self.manager.update_from_corpus(self.corpus, now=NOW), [])



class TestSchedulerIntegration(CorpusAlertCase):
    """The sweep is a scheduled job like any other."""

    def test_sweep_runs_on_the_scheduler_and_honours_cooldown(self):
        from core.reports import install_corpus_alert_sweep
        from core.scheduler import Scheduler

        clock = [NOW]
        scheduler = Scheduler(clock=lambda: clock[0])
        self.make_alert(AlertType.NEWS_MENTIONS, 1.0, cooldown_seconds=3600)
        self.corpus.add(item("NVDA story"))

        install_corpus_alert_sweep(scheduler, self.manager, self.corpus, interval_seconds=60)

        scheduler.run_pending(clock[0])
        self.assertEqual(len(self.manager.notifications), 1)

        # Sweeping again a minute later is allowed; alerting again is not.
        clock[0] = NOW + timedelta(seconds=61)
        report = scheduler.run_pending(clock[0])
        self.assertEqual(report.ran, ["corpus-alerts"])
        self.assertEqual(len(self.manager.notifications), 1)

if __name__ == "__main__":
    unittest.main()
