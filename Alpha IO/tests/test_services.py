"""Tests for the background service composition root.

The property that matters most: **nothing starts unless asked**. Importing the
app, running the test suite, or launching the dashboard must not begin polling
external feeds. Every test here uses injected fakes — none touches the network.
"""

import os
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services import (
    BackgroundServices,
    ServiceConfig,
    describe_startup,
    get_background_services,
    start_background_services,
    stop_background_services,
)

ENV_KEYS = (
    "ALPHAIO_CORPUS_DB",
    "ALPHAIO_PIPELINE",
    "ALPHAIO_PIPELINE_MODE",
    "ALPHAIO_PIPELINE_INTERVAL",
    "ALPHAIO_INGEST",
    "ALPHAIO_INGEST_INTERVAL",
    "ALPHAIO_BRIEF",
    "ALPHAIO_BRIEF_AT",
    "ALPHAIO_CORPUS_ALERTS",
    "ALPHAIO_CORPUS_ALERT_INTERVAL",
    "ALPHAIO_SCHEDULER_TICK",
    "ALPHAIO_REPORT_DIR",
)


class FakeCorpus:
    def crowding(self, term, now=None):
        raise AssertionError("not expected in these tests")

    def recent(self, now=None):
        return []

    def stats(self, now=None):
        return {"items_in_window": 0}


class FakeNewsService:
    """Records start/stop instead of opening sockets."""

    def __init__(self):
        self.corpus = FakeCorpus()
        self.started_with = None
        self.stopped = False
        self.poll_count = 0

    def start(self, interval_seconds=60.0):
        self.started_with = interval_seconds

    def stop(self, timeout=5.0):
        self.stopped = True

    def stats(self):
        return {"poll_count": self.poll_count, "running": self.started_with is not None}


class FakeScheduler:
    def __init__(self):
        self.jobs_added = []
        self.started_with = None
        self.stopped = False

    def add_job(self, name, schedule, func, enabled=True):
        self.jobs_added.append(name)
        return name

    def start(self, tick_seconds=30.0):
        self.started_with = tick_seconds

    def stop(self, timeout=5.0):
        self.stopped = True

    def stats(self):
        return {"jobs": self.jobs_added}


class FakeAlertManager:
    def update_from_corpus(self, corpus, **kwargs):
        return []


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.pop(key, None) for key in ENV_KEYS}
        stop_background_services()

    def tearDown(self):
        stop_background_services()
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestServiceConfig(EnvCase):
    def test_everything_is_off_by_default(self):
        config = ServiceConfig.from_env()
        self.assertFalse(config.ingest_enabled)
        self.assertFalse(config.brief_enabled)
        self.assertFalse(config.corpus_alerts_enabled)
        self.assertFalse(config.anything_enabled)
        self.assertFalse(config.needs_scheduler)

    def test_flags_accept_common_truthy_spellings(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                os.environ["ALPHAIO_INGEST"] = value
                self.assertTrue(ServiceConfig.from_env().ingest_enabled)

    def test_flags_reject_other_values(self):
        for value in ("0", "false", "no", "", "maybe"):
            with self.subTest(value=value):
                os.environ["ALPHAIO_INGEST"] = value
                self.assertFalse(ServiceConfig.from_env().ingest_enabled)

    def test_intervals_are_read_and_validated(self):
        os.environ["ALPHAIO_INGEST_INTERVAL"] = "15"
        self.assertEqual(ServiceConfig.from_env().ingest_interval_seconds, 15.0)

        # Junk and non-positive values fall back rather than producing a hot loop.
        for value in ("abc", "0", "-5"):
            with self.subTest(value=value):
                os.environ["ALPHAIO_INGEST_INTERVAL"] = value
                self.assertEqual(ServiceConfig.from_env().ingest_interval_seconds, 60.0)

    def test_brief_time_is_parsed(self):
        os.environ["ALPHAIO_BRIEF_AT"] = "07:45"
        config = ServiceConfig.from_env()
        self.assertEqual((config.brief_hour, config.brief_minute), (7, 45))

    def test_bad_brief_time_falls_back_to_a_sane_default(self):
        for value in ("nonsense", "99:99", "25:00", ":::"):
            with self.subTest(value=value):
                os.environ["ALPHAIO_BRIEF_AT"] = value
                config = ServiceConfig.from_env()
                self.assertEqual((config.brief_hour, config.brief_minute), (6, 0))

    def test_needs_scheduler_when_either_job_is_enabled(self):
        self.assertTrue(ServiceConfig(brief_enabled=True).needs_scheduler)
        self.assertTrue(ServiceConfig(corpus_alerts_enabled=True).needs_scheduler)
        self.assertFalse(ServiceConfig(ingest_enabled=True).needs_scheduler)


class TestStartup(EnvCase):
    def build(self, config, **kwargs):
        kwargs.setdefault("news_service", FakeNewsService())
        kwargs.setdefault("alert_manager", FakeAlertManager())
        kwargs.setdefault("scheduler", FakeScheduler())
        return BackgroundServices(config, **kwargs)

    def test_default_config_starts_nothing(self):
        news = FakeNewsService()
        scheduler = FakeScheduler()
        services = self.build(ServiceConfig(), news_service=news, scheduler=scheduler)

        self.assertEqual(services.start(), [])
        self.assertFalse(services.running)
        self.assertIsNone(news.started_with)
        self.assertIsNone(scheduler.started_with)

    def test_ingest_only(self):
        news = FakeNewsService()
        services = self.build(
            ServiceConfig(ingest_enabled=True, ingest_interval_seconds=15.0),
            news_service=news,
        )
        self.assertEqual(services.start(), ["ingest"])
        self.assertEqual(news.started_with, 15.0)

    def test_ingest_alone_does_not_start_a_scheduler(self):
        scheduler = FakeScheduler()
        services = self.build(ServiceConfig(ingest_enabled=True), scheduler=scheduler)
        services.start()
        self.assertIsNone(scheduler.started_with)

    def test_brief_registers_a_job_and_starts_the_scheduler(self):
        scheduler = FakeScheduler()
        services = self.build(
            ServiceConfig(brief_enabled=True, brief_hour=7, scheduler_tick_seconds=10.0),
            scheduler=scheduler,
        )
        started = services.start()
        self.assertEqual(started, ["morning-brief", "scheduler"])
        self.assertEqual(scheduler.jobs_added, ["morning-brief"])
        self.assertEqual(scheduler.started_with, 10.0)

    def test_corpus_alerts_register_a_sweep(self):
        scheduler = FakeScheduler()
        services = self.build(ServiceConfig(corpus_alerts_enabled=True), scheduler=scheduler)
        self.assertEqual(services.start(), ["corpus-alerts", "scheduler"])
        self.assertEqual(scheduler.jobs_added, ["corpus-alerts"])

    def test_everything_together(self):
        news, scheduler = FakeNewsService(), FakeScheduler()
        services = self.build(
            ServiceConfig(ingest_enabled=True, brief_enabled=True, corpus_alerts_enabled=True),
            news_service=news,
            scheduler=scheduler,
        )
        self.assertEqual(
            services.start(), ["ingest", "morning-brief", "corpus-alerts", "scheduler"]
        )
        self.assertTrue(services.running)

    def test_brief_generator_is_pointed_at_the_configured_directory(self):
        services = self.build(ServiceConfig(brief_enabled=True, report_dir=str(Path(tempfile.gettempdir()) / "alphaio-briefs")))
        services.start()
        self.assertEqual(str(services.brief_generator.report_dir), str(Path(tempfile.gettempdir()) / "alphaio-briefs"))

    def test_start_is_idempotent(self):
        news = FakeNewsService()
        services = self.build(ServiceConfig(ingest_enabled=True), news_service=news)
        first = services.start()
        news.started_with = None
        second = services.start()
        self.assertEqual(first, second)
        self.assertIsNone(news.started_with, "second start must not re-launch the loop")


class TestShutdown(EnvCase):
    def test_stop_stops_both_loops(self):
        news, scheduler = FakeNewsService(), FakeScheduler()
        services = BackgroundServices(
            ServiceConfig(ingest_enabled=True, brief_enabled=True),
            news_service=news,
            alert_manager=FakeAlertManager(),
            scheduler=scheduler,
        )
        services.start()
        services.stop()

        self.assertTrue(news.stopped)
        self.assertTrue(scheduler.stopped)
        self.assertFalse(services.running)
        self.assertEqual(services.started, [])

    def test_stop_is_safe_when_nothing_started(self):
        BackgroundServices(ServiceConfig()).stop()

    def test_a_raising_collaborator_does_not_break_shutdown(self):
        class BadScheduler(FakeScheduler):
            def stop(self, timeout=5.0):
                raise RuntimeError("stuck")

        services = BackgroundServices(
            ServiceConfig(brief_enabled=True),
            news_service=FakeNewsService(),
            alert_manager=FakeAlertManager(),
            scheduler=BadScheduler(),
        )
        services.start()
        services.stop()
        self.assertFalse(services.running)


class TestStatus(EnvCase):
    def test_status_reports_config_and_state(self):
        news = FakeNewsService()
        services = BackgroundServices(
            ServiceConfig(ingest_enabled=True, brief_enabled=True, brief_hour=7, brief_minute=30),
            news_service=news,
            alert_manager=FakeAlertManager(),
            scheduler=FakeScheduler(),
        )
        services.start()
        status = services.status()

        self.assertTrue(status["running"])
        self.assertIn("ingest", status["started"])
        self.assertTrue(status["config"]["ingest_enabled"])
        self.assertEqual(status["config"]["brief_at"], "07:30")
        self.assertIn("ingest", status)
        self.assertIn("scheduler", status)


class TestProcessWideInstance(EnvCase):
    def test_not_started_by_default(self):
        self.assertIsNone(get_background_services())

    def test_start_and_stop_round_trip(self):
        services = start_background_services(
            ServiceConfig(ingest_enabled=True),
            news_service=FakeNewsService(),
        )
        self.assertIs(get_background_services(), services)
        stop_background_services()
        self.assertIsNone(get_background_services())

    def test_second_start_returns_the_running_instance(self):
        first = start_background_services(
            ServiceConfig(ingest_enabled=True), news_service=FakeNewsService()
        )
        second = start_background_services(
            ServiceConfig(ingest_enabled=True), news_service=FakeNewsService()
        )
        self.assertIs(first, second)


class TestDescribeStartup(EnvCase):
    def test_says_so_when_nothing_is_enabled(self):
        services = BackgroundServices(ServiceConfig())
        services.start()
        message = describe_startup(services)
        self.assertIn("none enabled", message)
        self.assertIn("ALPHAIO_INGEST", message)

    def test_lists_what_started(self):
        services = BackgroundServices(
            ServiceConfig(ingest_enabled=True), news_service=FakeNewsService()
        )
        services.start()
        self.assertIn("ingest", describe_startup(services))



class FakeRiskForPipeline:
    def check_risk_limits(self, asset):
        return True

    def calculate_position_size(self, asset, confidence, entry_price=None, stop_loss_pct=None):
        return 1000.0


class FakeEngineForPipeline:
    def set_price(self, asset, price):
        pass

    def create_order(self, **kwargs):
        class Order:
            order_id = "ord-1"
        return Order()

    def submit_order(self, order, algo=None):
        return {"status": "filled"}


class TestPipelineWiring(EnvCase):
    """The pipeline is opt-in, and refuses to invent its own collaborators."""

    def build(self, config, **kwargs):
        kwargs.setdefault("news_service", FakeNewsService())
        kwargs.setdefault("alert_manager", FakeAlertManager())
        kwargs.setdefault("scheduler", FakeScheduler())
        return BackgroundServices(config, **kwargs)

    def test_pipeline_is_off_by_default(self):
        self.assertFalse(ServiceConfig.from_env().pipeline_enabled)

    def test_pipeline_mode_defaults_to_paper(self):
        self.assertEqual(ServiceConfig.from_env().pipeline_mode, "paper")

    def test_pipeline_env_is_read(self):
        os.environ["ALPHAIO_PIPELINE"] = "1"
        os.environ["ALPHAIO_PIPELINE_MODE"] = "observe"
        config = ServiceConfig.from_env()
        self.assertTrue(config.pipeline_enabled)
        self.assertEqual(config.pipeline_mode, "observe")
        self.assertTrue(config.needs_scheduler)

    def test_pipeline_is_skipped_without_risk_and_execution(self):
        services = self.build(ServiceConfig(pipeline_enabled=True))
        started = services.start()
        self.assertNotIn("pipeline", started)
        self.assertIn("requires an injected risk_manager", services.pipeline_skipped)

    def test_pipeline_registers_when_collaborators_are_present(self):
        scheduler = FakeScheduler()
        services = self.build(
            ServiceConfig(pipeline_enabled=True),
            scheduler=scheduler,
            risk_manager=FakeRiskForPipeline(),
            execution_engine=FakeEngineForPipeline(),
        )
        self.assertIn("pipeline", services.start())
        self.assertIn("pipeline", scheduler.jobs_added)

    def test_unknown_mode_is_refused_rather_than_guessed(self):
        services = self.build(
            ServiceConfig(pipeline_enabled=True, pipeline_mode="yolo"),
            risk_manager=FakeRiskForPipeline(),
            execution_engine=FakeEngineForPipeline(),
        )
        self.assertNotIn("pipeline", services.start())
        self.assertIn("unknown pipeline mode", services.pipeline_skipped)

    def test_live_mode_without_an_edge_report_runs_as_paper(self):
        services = self.build(
            ServiceConfig(pipeline_enabled=True, pipeline_mode="live"),
            risk_manager=FakeRiskForPipeline(),
            execution_engine=FakeEngineForPipeline(),
        )
        services.start()
        self.assertEqual(services.pipeline.effective_mode().value, "paper")
        self.assertTrue(services.pipeline.gate_status()["downgraded"])


class TestCorpusPersistenceConfig(EnvCase):
    def test_corpus_db_is_unset_by_default(self):
        self.assertIsNone(ServiceConfig.from_env().corpus_db)

    def test_corpus_db_is_read_from_env(self):
        os.environ["ALPHAIO_CORPUS_DB"] = str(Path(tempfile.gettempdir()) / "alphaio-corpus.db")
        self.assertEqual(ServiceConfig.from_env().corpus_db, str(Path(tempfile.gettempdir()) / "alphaio-corpus.db"))

if __name__ == "__main__":
    unittest.main()
