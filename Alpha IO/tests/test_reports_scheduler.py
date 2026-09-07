"""Tests for the scheduler and the morning brief.

Two load-bearing properties:

1. A daily job whose window was missed fires exactly once when the process
   returns — not once per missed day, and not zero times.
2. The brief is deterministic: same corpus, same ledger, byte-identical markdown.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.news_feed import NewsCorpus, NewsItem, SourceCategory, content_id, extract_tickers
from core.reports import BriefGenerator, MorningBrief, TermDigest, install_morning_brief
from core.scheduler import DailyAt, Interval, Job, ScheduleError, Scheduler, next_fire

# A Friday.
NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def item(title, source_name="wire", published=None, credibility=0.8):
    published = published or NOW
    url = f"https://example.com/{abs(hash(title)) % 100000}"
    return NewsItem(
        item_id=content_id(title, url),
        title=title,
        summary="",
        url=url,
        source=source_name,
        category=SourceCategory.WIRE,
        credibility=credibility,
        published=published,
        fetched=published,
        entities=extract_tickers(title),
    )


# =============================================================================
# Schedules
# =============================================================================

class TestInterval(unittest.TestCase):
    def test_rejects_non_positive(self):
        with self.assertRaises(ScheduleError):
            Interval(0)

    def test_fires_immediately_when_never_run(self):
        self.assertTrue(Interval(60).is_due(NOW, None))

    def test_waits_for_the_interval(self):
        schedule = Interval(60)
        self.assertFalse(schedule.is_due(NOW, NOW - timedelta(seconds=59)))
        self.assertTrue(schedule.is_due(NOW, NOW - timedelta(seconds=60)))


class TestDailyAt(unittest.TestCase):
    def test_validates_bounds(self):
        with self.assertRaises(ScheduleError):
            DailyAt(hour=24)
        with self.assertRaises(ScheduleError):
            DailyAt(hour=6, minute=60)
        with self.assertRaises(ScheduleError):
            DailyAt(hour=6, weekdays=frozenset())

    def test_not_due_before_the_window(self):
        schedule = DailyAt(hour=6)
        before = NOW.replace(hour=5, minute=59)
        self.assertFalse(schedule.is_due(before, None))

    def test_due_inside_the_window(self):
        self.assertTrue(DailyAt(hour=6).is_due(NOW, None))

    def test_not_due_twice_in_one_day(self):
        schedule = DailyAt(hour=6)
        fired = NOW.replace(hour=6, minute=1)
        self.assertFalse(schedule.is_due(NOW, fired))

    def test_missed_window_fires_exactly_once_on_return(self):
        # Process was down for three days; last run was Monday.
        schedule = DailyAt(hour=6)
        monday = datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc)
        self.assertTrue(schedule.is_due(NOW, monday))
        # After that single catch-up fire, it is quiet again.
        self.assertFalse(schedule.is_due(NOW, NOW))

    def test_skips_non_trading_days_by_default(self):
        saturday = datetime(2026, 8, 29, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(saturday.weekday(), 5)
        self.assertFalse(DailyAt(hour=6).is_due(saturday, None))

    def test_honours_custom_weekdays(self):
        saturday = datetime(2026, 8, 29, 7, 0, tzinfo=timezone.utc)
        self.assertTrue(DailyAt(hour=6, weekdays={5}).is_due(saturday, None))

    def test_describe_is_human_readable(self):
        self.assertIn("06:00", DailyAt(hour=6).describe())


class TestNextFire(unittest.TestCase):
    def test_interval(self):
        self.assertEqual(next_fire(Interval(60), NOW), NOW + timedelta(seconds=60))

    def test_daily_rolls_to_next_trading_day(self):
        friday_evening = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)
        upcoming = next_fire(DailyAt(hour=6), friday_evening)
        self.assertEqual(upcoming.weekday(), 0)  # Monday
        self.assertEqual(upcoming.hour, 6)


# =============================================================================
# Scheduler
# =============================================================================

class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler(clock=lambda: NOW)

    def test_runs_a_due_job(self):
        calls = []
        self.scheduler.add_job("tick", Interval(1), lambda: calls.append(1))
        report = self.scheduler.run_pending(NOW)
        self.assertEqual(report.ran, ["tick"])
        self.assertEqual(len(calls), 1)

    def test_does_not_rerun_before_the_interval(self):
        calls = []
        self.scheduler.add_job("tick", Interval(60), lambda: calls.append(1))
        self.scheduler.run_pending(NOW)
        self.scheduler.run_pending(NOW + timedelta(seconds=30))
        self.assertEqual(len(calls), 1)
        self.scheduler.run_pending(NOW + timedelta(seconds=61))
        self.assertEqual(len(calls), 2)

    def test_duplicate_job_names_are_rejected(self):
        self.scheduler.add_job("tick", Interval(1), lambda: None)
        with self.assertRaises(ValueError):
            self.scheduler.add_job("tick", Interval(1), lambda: None)

    def test_disabled_job_does_not_run(self):
        calls = []
        self.scheduler.add_job("tick", Interval(1), lambda: calls.append(1), enabled=False)
        self.scheduler.run_pending(NOW)
        self.assertEqual(calls, [])
        self.scheduler.set_enabled("tick", True)
        self.scheduler.run_pending(NOW)
        self.assertEqual(len(calls), 1)

    def test_raising_job_is_recorded_and_others_still_run(self):
        ok = []

        def boom():
            raise RuntimeError("nope")

        self.scheduler.add_job("bad", Interval(1), boom)
        self.scheduler.add_job("good", Interval(1), lambda: ok.append(1))
        report = self.scheduler.run_pending(NOW)

        self.assertEqual(report.failed, ["bad"])
        self.assertEqual(report.ran, ["good"])
        self.assertEqual(len(ok), 1)
        self.assertEqual(self.scheduler.get_job("bad").error_count, 1)
        self.assertIn("RuntimeError", self.scheduler.get_job("bad").last_error)

    def test_failed_job_still_stamps_last_run(self):
        # Otherwise a permanently failing job re-fires on every single tick.
        self.scheduler.add_job("bad", Interval(60), lambda: (_ for _ in ()).throw(RuntimeError("x")))
        self.scheduler.run_pending(NOW)
        self.assertEqual(self.scheduler.due(NOW), [])

    def test_remove_job(self):
        self.scheduler.add_job("tick", Interval(1), lambda: None)
        self.assertTrue(self.scheduler.remove_job("tick"))
        self.assertFalse(self.scheduler.remove_job("tick"))

    def test_set_enabled_on_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            self.scheduler.set_enabled("nope", True)

    def test_event_bus_failure_does_not_break_a_job(self):
        class BadBus:
            def publish(self, _):
                raise RuntimeError("bus down")

        scheduler = Scheduler(clock=lambda: NOW, event_bus=BadBus())
        calls = []
        scheduler.add_job("tick", Interval(1), lambda: calls.append(1))
        report = scheduler.run_pending(NOW)
        self.assertEqual(report.ran, ["tick"])
        self.assertEqual(len(calls), 1)

    def test_events_are_published_when_a_bus_is_attached(self):
        published = []

        class Bus:
            def publish(self, event):
                published.append(event)

        scheduler = Scheduler(clock=lambda: NOW, event_bus=Bus())
        scheduler.add_job("tick", Interval(1), lambda: None)
        scheduler.run_pending(NOW)
        self.assertEqual(published[0]["type"], "job_ran")
        self.assertEqual(published[0]["job"], "tick")

    def test_stats_shape(self):
        self.scheduler.add_job("tick", Interval(1), lambda: None)
        stats = self.scheduler.stats()
        self.assertEqual(len(stats["jobs"]), 1)
        self.assertFalse(stats["running"])


# =============================================================================
# Brief
# =============================================================================

class FakePosition:
    def __init__(self, quantity, exposure):
        self.quantity = quantity
        self.exposure = exposure


class FakeSnapshot:
    def __init__(self):
        self.cash = 25000.0
        self.realized_pnl = 1250.5
        self.positions = {
            "NVDA": FakePosition(10.0, 9500.0),
            "FLAT": FakePosition(0.0, 0.0),
        }
        self.open_orders = [object()]
        self.total_exposure = 9500.0


class FakeLedger:
    def snapshot(self):
        return FakeSnapshot()


class TestBriefGeneration(unittest.TestCase):
    def setUp(self):
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        for n in range(6):
            self.corpus.add(item(f"NVDA surges to record high on strong growth {n}",
                                 source_name=f"wire-{n % 3}"))
        for n in range(2):
            self.corpus.add(item(f"AMD quietly wins a design slot {n}"))
        self.generator = BriefGenerator(corpus=self.corpus, clock=lambda: NOW)

    def test_digest_ranks_by_mentions(self):
        brief = self.generator.generate(now=NOW)
        self.assertEqual(brief.digest[0].term, "NVDA")
        self.assertEqual(brief.digest[0].mentions, 6)
        self.assertEqual(brief.digest[0].sources, 3)
        self.assertEqual(brief.digest[0].lean, "bullish")

    def test_min_mentions_filters_the_digest(self):
        self.corpus.add(item("TSLA does something once"))
        brief = self.generator.generate(now=NOW, min_mentions=3)
        self.assertEqual([d.term for d in brief.digest], ["NVDA"])

    def test_candidates_favour_the_uncrowded_term(self):
        brief = self.generator.generate(now=NOW)
        self.assertEqual(brief.candidates[0].term, "AMD")
        self.assertTrue(brief.candidates[0].score > brief.candidates[-1].score)

    def test_headline_and_source_counts(self):
        brief = self.generator.generate(now=NOW)
        self.assertEqual(brief.headlines, 8)
        self.assertEqual(len(brief.sources), 4)  # wire-0/1/2 plus the default

    def test_generation_is_deterministic(self):
        first = self.generator.generate(now=NOW).to_markdown()
        second = self.generator.generate(now=NOW).to_markdown()
        self.assertEqual(first, second)

    def test_markdown_contains_every_section(self):
        markdown = self.generator.generate(now=NOW).to_markdown()
        for heading in ("# Morning Brief", "## Overnight tape", "## Uncrowded candidates",
                        "## Book", "## Risk posture"):
            self.assertIn(heading, markdown)

    def test_empty_corpus_says_so_rather_than_fabricating(self):
        generator = BriefGenerator(corpus=NewsCorpus(clock=lambda: NOW), clock=lambda: NOW)
        brief = generator.generate(now=NOW)
        self.assertEqual(brief.headlines, 0)
        self.assertIn("check feed health", brief.to_markdown())

    def test_no_corpus_is_noted(self):
        brief = BriefGenerator(clock=lambda: NOW).generate(now=NOW)
        self.assertTrue(any("No corpus attached" in note for note in brief.notes))

    def test_ledger_section_renders_positions_and_skips_flat_ones(self):
        generator = BriefGenerator(corpus=self.corpus, ledger=FakeLedger(), clock=lambda: NOW)
        brief = generator.generate(now=NOW)
        self.assertIn("NVDA", brief.book["positions"])
        self.assertNotIn("FLAT", brief.book["positions"])
        markdown = brief.to_markdown()
        self.assertIn("25,000.00", markdown)
        self.assertIn("1 order(s) still working.", markdown)

    def test_failing_ledger_still_renders_a_brief(self):
        class BadLedger:
            def snapshot(self):
                raise RuntimeError("ledger down")

        generator = BriefGenerator(corpus=self.corpus, ledger=BadLedger(), clock=lambda: NOW)
        brief = generator.generate(now=NOW)
        self.assertEqual(brief.book, {})
        self.assertTrue(any("Ledger snapshot failed" in note for note in brief.notes))
        self.assertIn("# Morning Brief", brief.to_markdown())

    def test_risk_summary_is_picked_up(self):
        class Risk:
            def get_risk_summary(self):
                return {"max_drawdown": 0.04, "risk_level": "MODERATE", "positions": [1, 2]}

        generator = BriefGenerator(corpus=self.corpus, risk_manager=Risk(), clock=lambda: NOW)
        brief = generator.generate(now=NOW)
        self.assertEqual(brief.risk["risk_level"], "MODERATE")
        # Non-scalar values are dropped rather than rendered as junk.
        self.assertNotIn("positions", brief.risk)

    def test_unrecognized_risk_manager_is_noted(self):
        generator = BriefGenerator(corpus=self.corpus, risk_manager=object(), clock=lambda: NOW)
        brief = generator.generate(now=NOW)
        self.assertTrue(any("no recognized summary" in note for note in brief.notes))

    def test_synthesis_is_off_by_default(self):
        os.environ.pop("ALPHAIO_LLM_BRIEF", None)
        self.assertIsNone(self.generator.generate(now=NOW).synthesis)

    def test_synthesis_stays_none_when_enabled_without_credentials(self):
        saved_flag = os.environ.get("ALPHAIO_LLM_BRIEF")
        saved_key = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["ALPHAIO_LLM_BRIEF"] = "1"
        try:
            self.assertIsNone(self.generator.generate(now=NOW).synthesis)
        finally:
            if saved_flag is None:
                os.environ.pop("ALPHAIO_LLM_BRIEF", None)
            else:
                os.environ["ALPHAIO_LLM_BRIEF"] = saved_flag
            if saved_key is not None:
                os.environ["OPENAI_API_KEY"] = saved_key


class TestBriefOutput(unittest.TestCase):
    def setUp(self):
        self.corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
        self.corpus.add(item("NVDA beats"))
        self.corpus.add(item("NVDA rallies"))

    def test_write_uses_dated_slug(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = BriefGenerator(corpus=self.corpus, clock=lambda: NOW, report_dir=directory)
            path = generator.write(generator.generate(now=NOW))
            self.assertEqual(path.name, "macro-20260828.md")
            self.assertIn("# Morning Brief", path.read_text(encoding="utf-8"))

    def test_run_returns_a_summary_and_writes_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = BriefGenerator(corpus=self.corpus, clock=lambda: NOW, report_dir=directory)
            result = generator.run(now=NOW)
            self.assertTrue(Path(result["path"]).exists())
            self.assertEqual(result["headlines"], 2)

    def test_failing_notifier_does_not_fail_the_report(self):
        class BadNotifier:
            def notify(self, **kwargs):
                raise RuntimeError("channel down")

        with tempfile.TemporaryDirectory() as directory:
            generator = BriefGenerator(corpus=self.corpus, clock=lambda: NOW, report_dir=directory)
            result = generator.run(now=NOW, notifier=BadNotifier())
            self.assertTrue(Path(result["path"]).exists())

    def test_notifier_receives_the_announcement(self):
        received = {}

        class Notifier:
            def notify(self, title, message):
                received["title"] = title
                received["message"] = message

        with tempfile.TemporaryDirectory() as directory:
            generator = BriefGenerator(corpus=self.corpus, clock=lambda: NOW, report_dir=directory)
            generator.run(now=NOW, notifier=Notifier())
            self.assertIn("2026-08-28", received["title"])


class TestInstallMorningBrief(unittest.TestCase):
    def test_registers_a_daily_job_that_writes_a_brief(self):
        with tempfile.TemporaryDirectory() as directory:
            corpus = NewsCorpus(window_hours=24, clock=lambda: NOW)
            corpus.add(item("NVDA beats"))
            generator = BriefGenerator(corpus=corpus, clock=lambda: NOW, report_dir=directory)
            scheduler = Scheduler(clock=lambda: NOW)

            job = install_morning_brief(scheduler, generator, hour=6)
            self.assertIsInstance(job, Job)

            report = scheduler.run_pending(NOW)
            self.assertEqual(report.ran, ["morning-brief"])
            self.assertTrue((Path(directory) / "macro-20260828.md").exists())

            # Second sweep on the same day must not regenerate.
            self.assertEqual(scheduler.run_pending(NOW).ran, [])


class TestTermDigestLean(unittest.TestCase):
    def test_lean_thresholds(self):
        self.assertEqual(TermDigest("X", 1, 1, 0.9).lean, "bullish")
        self.assertEqual(TermDigest("X", 1, 1, 0.5).lean, "mixed")
        self.assertEqual(TermDigest("X", 1, 1, 0.1).lean, "bearish")


class TestMorningBriefSerialization(unittest.TestCase):
    def test_to_dict_round_trips_the_sections(self):
        brief = MorningBrief(generated_at=NOW, headlines=3, sources=["wire"])
        payload = brief.to_dict()
        self.assertEqual(payload["headlines"], 3)
        self.assertEqual(payload["sources"], ["wire"])
        self.assertEqual(brief.slug, "macro-20260828")


if __name__ == "__main__":
    unittest.main()
