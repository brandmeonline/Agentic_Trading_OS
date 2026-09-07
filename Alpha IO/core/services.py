"""
Background service composition root.

Phases 1 through 6 delivered components: an ingestion layer, a scheduler, a
report generator, a corpus alert sweep, and a terminal view that reads them.
Nothing composed them. The feed service was a singleton nobody polled, the
scheduler had no jobs, and the terminal rendered an empty corpus because the
corpus was empty and always would be.

This module is the one place those pieces are assembled and started.

**Everything is off by default.** Starting the dashboard must not silently begin
polling external feeds — that is a behaviour change an operator should opt into,
consistent with how live trading and the Headroom adapter are gated in this
codebase. Set the environment variables below to turn each piece on.

    ALPHAIO_INGEST=1                  poll the configured feeds
    ALPHAIO_INGEST_INTERVAL=60        seconds between sweeps
    ALPHAIO_BRIEF=1                   generate the morning brief on a schedule
    ALPHAIO_BRIEF_AT=06:00            when, UTC, on trading days
    ALPHAIO_CORPUS_ALERTS=1           evaluate corpus alerts on a schedule
    ALPHAIO_CORPUS_ALERT_INTERVAL=60  seconds between alert sweeps
    ALPHAIO_REPORT_DIR=data/reports   where briefs are written
    ALPHAIO_CORPUS_DB=data/corpus.db  persist the corpus across restarts
    ALPHAIO_PIPELINE=1                run the signal-to-execution pipeline
    ALPHAIO_PIPELINE_MODE=paper       observe | paper | live
    ALPHAIO_PIPELINE_INTERVAL=300     seconds between pipeline cycles

The pipeline additionally requires a risk manager and an execution engine to be
injected. It will not invent them: a pipeline that manufactured its own risk
limits and starting capital would be sizing orders against fiction.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

DEFAULT_INGEST_INTERVAL = 60.0
DEFAULT_ALERT_INTERVAL = 60.0
DEFAULT_SCHEDULER_TICK = 30.0
DEFAULT_BRIEF_AT = "06:00"
DEFAULT_REPORT_DIR = "data/reports"
DEFAULT_PIPELINE_INTERVAL = 300.0


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _number(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _time_of_day(name: str, default: str) -> tuple:
    """Parse ``HH:MM`` into (hour, minute), falling back on anything unusable."""
    raw = os.getenv(name, "").strip() or default
    try:
        hour_text, _, minute_text = raw.partition(":")
        hour, minute = int(hour_text), int(minute_text or 0)
    except ValueError:
        hour, minute = 6, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = 6, 0
    return hour, minute


@dataclass
class ServiceConfig:
    """What to run. Nothing is enabled unless asked for."""
    ingest_enabled: bool = False
    ingest_interval_seconds: float = DEFAULT_INGEST_INTERVAL
    brief_enabled: bool = False
    brief_hour: int = 6
    brief_minute: int = 0
    corpus_alerts_enabled: bool = False
    corpus_alert_interval_seconds: float = DEFAULT_ALERT_INTERVAL
    scheduler_tick_seconds: float = DEFAULT_SCHEDULER_TICK
    report_dir: str = DEFAULT_REPORT_DIR
    corpus_db: Optional[str] = None
    pipeline_enabled: bool = False
    pipeline_mode: str = "paper"
    pipeline_interval_seconds: float = DEFAULT_PIPELINE_INTERVAL

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        hour, minute = _time_of_day("ALPHAIO_BRIEF_AT", DEFAULT_BRIEF_AT)
        return cls(
            ingest_enabled=_flag("ALPHAIO_INGEST"),
            ingest_interval_seconds=_number("ALPHAIO_INGEST_INTERVAL", DEFAULT_INGEST_INTERVAL),
            brief_enabled=_flag("ALPHAIO_BRIEF"),
            brief_hour=hour,
            brief_minute=minute,
            corpus_alerts_enabled=_flag("ALPHAIO_CORPUS_ALERTS"),
            corpus_alert_interval_seconds=_number(
                "ALPHAIO_CORPUS_ALERT_INTERVAL", DEFAULT_ALERT_INTERVAL
            ),
            scheduler_tick_seconds=_number("ALPHAIO_SCHEDULER_TICK", DEFAULT_SCHEDULER_TICK),
            report_dir=os.getenv("ALPHAIO_REPORT_DIR", "").strip() or DEFAULT_REPORT_DIR,
            corpus_db=os.getenv("ALPHAIO_CORPUS_DB", "").strip() or None,
            pipeline_enabled=_flag("ALPHAIO_PIPELINE"),
            pipeline_mode=(os.getenv("ALPHAIO_PIPELINE_MODE", "").strip().lower() or "paper"),
            pipeline_interval_seconds=_number(
                "ALPHAIO_PIPELINE_INTERVAL", DEFAULT_PIPELINE_INTERVAL
            ),
        )

    @property
    def needs_scheduler(self) -> bool:
        return self.brief_enabled or self.corpus_alerts_enabled or self.pipeline_enabled

    @property
    def anything_enabled(self) -> bool:
        return self.ingest_enabled or self.needs_scheduler


class BackgroundServices:
    """Owns the lifetime of the ingestion loop and the scheduled jobs.

    Collaborators are injectable so that tests never touch the network and never
    depend on process-wide singletons.
    """

    def __init__(
        self,
        config: Optional[ServiceConfig] = None,
        news_service: Optional[Any] = None,
        alert_manager: Optional[Any] = None,
        ledger: Optional[Any] = None,
        risk_manager: Optional[Any] = None,
        scheduler: Optional[Any] = None,
        execution_engine: Optional[Any] = None,
        edge_report: Optional[Any] = None,
        price_lookup: Optional[Any] = None,
    ) -> None:
        self.config = config or ServiceConfig()
        self._news_service = news_service
        self._alert_manager = alert_manager
        self.ledger = ledger
        self.risk_manager = risk_manager
        self.scheduler = scheduler
        self.execution_engine = execution_engine
        self.edge_report = edge_report
        self.price_lookup = price_lookup
        self.brief_generator: Optional[Any] = None
        self.pipeline: Optional[Any] = None
        self.pipeline_skipped: Optional[str] = None
        self.started: List[str] = []
        self._running = False

    # -- lazily resolved collaborators -------------------------------------

    @property
    def news_service(self) -> Any:
        """The feed service, installing a persisted corpus when one is configured.

        The web layer reads the same process-wide service, so persistence has to
        be installed here — before the app serves a request — rather than
        constructed separately, or the terminal would read a different corpus
        from the one the scheduler writes.
        """
        if self._news_service is not None:
            return self._news_service

        from core.news_feed import NewsCorpus, NewsFeedService, get_news_service, set_news_service

        service = get_news_service()
        if self.config.corpus_db and service.corpus.sqlite_path is None:
            service = NewsFeedService(corpus=NewsCorpus(sqlite_path=self.config.corpus_db))
            set_news_service(service)

        self._news_service = service
        return self._news_service

    @property
    def alert_manager(self) -> Any:
        if self._alert_manager is None:
            from core.alerts import get_alert_manager
            self._alert_manager = get_alert_manager()
        return self._alert_manager

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> List[str]:
        """Start whatever the config asks for. Returns what was started."""
        if self._running:
            return list(self.started)

        started: List[str] = []

        if self.config.ingest_enabled:
            self.news_service.start(interval_seconds=self.config.ingest_interval_seconds)
            started.append("ingest")

        if self.config.needs_scheduler:
            if self.scheduler is None:
                from core.scheduler import Scheduler
                self.scheduler = Scheduler()

            if self.config.brief_enabled:
                from core.reports import BriefGenerator, install_morning_brief
                self.brief_generator = BriefGenerator(
                    corpus=self.news_service.corpus,
                    ledger=self.ledger,
                    risk_manager=self.risk_manager,
                    report_dir=self.config.report_dir,
                )
                install_morning_brief(
                    self.scheduler,
                    self.brief_generator,
                    hour=self.config.brief_hour,
                    minute=self.config.brief_minute,
                )
                started.append("morning-brief")

            if self.config.corpus_alerts_enabled:
                from core.reports import install_corpus_alert_sweep
                install_corpus_alert_sweep(
                    self.scheduler,
                    self.alert_manager,
                    self.news_service.corpus,
                    interval_seconds=self.config.corpus_alert_interval_seconds,
                )
                started.append("corpus-alerts")

            if self.config.pipeline_enabled:
                if self._install_pipeline():
                    started.append("pipeline")

            self.scheduler.start(tick_seconds=self.config.scheduler_tick_seconds)
            started.append("scheduler")

        self.started = started
        self._running = bool(started)
        return started

    def _install_pipeline(self) -> bool:
        """Register the signal-to-execution cycle, if it can be built.

        Returns False and records why when a collaborator is missing. The
        pipeline will not manufacture a risk manager or an execution engine —
        inventing risk limits and starting capital would mean sizing real orders
        against fiction.
        """
        if self.risk_manager is None or self.execution_engine is None:
            self.pipeline_skipped = (
                "pipeline requires an injected risk_manager and execution_engine"
            )
            return False

        from core.pipeline import PipelineMode, TradingPipeline
        from core.scheduler import Interval
        from core.signal_router import SignalRouter

        try:
            mode = PipelineMode(self.config.pipeline_mode)
        except ValueError:
            self.pipeline_skipped = f"unknown pipeline mode {self.config.pipeline_mode!r}"
            return False

        self.pipeline = TradingPipeline(
            corpus=self.news_service.corpus,
            router=SignalRouter(corpus=self.news_service.corpus),
            risk_manager=self.risk_manager,
            execution_engine=self.execution_engine,
            mode=mode,
            edge_report=self.edge_report,
            price_lookup=self.price_lookup,
        )
        self.scheduler.add_job(
            "pipeline",
            Interval(self.config.pipeline_interval_seconds),
            self.pipeline.run_cycle,
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop everything this instance started. Safe to call when not running."""
        if self.scheduler is not None:
            try:
                self.scheduler.stop(timeout=timeout)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

        if self._news_service is not None:
            try:
                self._news_service.stop(timeout=timeout)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass

        self._running = False
        self.started = []

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> Dict[str, Any]:
        """What is configured, what is running, and what it has done."""
        payload: Dict[str, Any] = {
            "running": self._running,
            "started": list(self.started),
            "config": {
                "ingest_enabled": self.config.ingest_enabled,
                "ingest_interval_seconds": self.config.ingest_interval_seconds,
                "brief_enabled": self.config.brief_enabled,
                "brief_at": f"{self.config.brief_hour:02d}:{self.config.brief_minute:02d}",
                "corpus_alerts_enabled": self.config.corpus_alerts_enabled,
                "report_dir": self.config.report_dir,
                "corpus_db": self.config.corpus_db,
                "pipeline_enabled": self.config.pipeline_enabled,
                "pipeline_mode": self.config.pipeline_mode,
            },
        }
        if self.pipeline is not None:
            payload["pipeline"] = self.pipeline.stats()
        if self.pipeline_skipped:
            payload["pipeline_skipped"] = self.pipeline_skipped
        if self._news_service is not None:
            payload["ingest"] = self._news_service.stats()
        if self.scheduler is not None:
            payload["scheduler"] = self.scheduler.stats()
        return payload


# =============================================================================
# Process-wide instance
# =============================================================================

_services: Optional[BackgroundServices] = None
_services_lock = threading.Lock()


def start_background_services(config: Optional[ServiceConfig] = None, **kwargs) -> BackgroundServices:
    """Start the process-wide services, building them from the environment.

    Idempotent: calling twice returns the already-running instance rather than
    starting a second ingestion loop against the same sources.
    """
    global _services
    with _services_lock:
        if _services is not None and _services.running:
            return _services
        _services = BackgroundServices(config or ServiceConfig.from_env(), **kwargs)
        _services.start()
        return _services


def get_background_services() -> Optional[BackgroundServices]:
    """The process-wide services, or None if they were never started."""
    return _services


def stop_background_services(timeout: float = 5.0) -> None:
    """Stop and clear the process-wide services."""
    global _services
    with _services_lock:
        if _services is not None:
            _services.stop(timeout=timeout)
            _services = None


def describe_startup(services: BackgroundServices) -> str:
    """One line for the launcher to print, so an operator can see the state."""
    if not services.config.anything_enabled:
        return (
            "Background services: none enabled. "
            "Set ALPHAIO_INGEST=1 to poll feeds; see core/services.py for the rest."
        )
    return "Background services started: " + ", ".join(services.started)
