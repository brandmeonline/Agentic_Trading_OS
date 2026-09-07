"""
Recurring job scheduler.

The repo had no scheduler of any kind, which is why the report layer did not
exist: the metrics were already there, the cadence was not. This is a small
in-process scheduler on the standard library — no new dependency, and it
publishes to the existing ``EventBus`` when one is attached.

The property that matters is **catch-up without double-firing**. A daily job
whose window was missed because the process was down fires exactly once when the
process returns, not once per missed day and not zero times. Getting that wrong
either spams a desk at 9am or silently skips the brief nobody noticed was gone.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


class ScheduleError(Exception):
    """Raised for an unusable schedule definition."""


# =============================================================================
# Schedules
# =============================================================================

class Schedule:
    """Base schedule. Subclasses answer one question: is this job due now?"""

    def is_due(self, now: datetime, last_run: Optional[datetime]) -> bool:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass
class Interval(Schedule):
    """Fire every ``seconds``, measured from the last completed run."""
    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ScheduleError("Interval.seconds must be > 0")

    def is_due(self, now: datetime, last_run: Optional[datetime]) -> bool:
        if last_run is None:
            return True
        return (now - last_run).total_seconds() >= self.seconds

    def describe(self) -> str:
        return f"every {self.seconds:g}s"


@dataclass
class DailyAt(Schedule):
    """Fire once per day at ``hour:minute``, on the given weekdays.

    ``weekdays`` uses ``datetime.weekday()`` numbering (Monday=0). The default
    is Monday–Friday, since the jobs this exists for are trading-day jobs.
    """
    hour: int
    minute: int = 0
    weekdays: frozenset = frozenset({0, 1, 2, 3, 4})

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ScheduleError("DailyAt.hour must be in 0..23")
        if not 0 <= self.minute <= 59:
            raise ScheduleError("DailyAt.minute must be in 0..59")
        if not self.weekdays:
            raise ScheduleError("DailyAt.weekdays must not be empty")
        object.__setattr__(self, "weekdays", frozenset(self.weekdays))

    @property
    def at(self) -> dtime:
        return dtime(self.hour, self.minute)

    def is_due(self, now: datetime, last_run: Optional[datetime]) -> bool:
        if now.weekday() not in self.weekdays:
            return False
        if now.timetz().replace(tzinfo=None) < self.at:
            return False
        # Already fired for today's window. This is what prevents a missed
        # window from firing repeatedly once the process is back.
        if last_run is not None and last_run.date() >= now.date():
            return False
        return True

    def describe(self) -> str:
        days = "".join("MTWTFSS"[d] for d in sorted(self.weekdays))
        return f"daily at {self.hour:02d}:{self.minute:02d} [{days}]"


# =============================================================================
# Jobs
# =============================================================================

@dataclass
class Job:
    """One scheduled callable and its run history."""
    name: str
    schedule: Schedule
    func: Callable[[], Any]
    enabled: bool = True
    last_run: Optional[datetime] = None
    last_error: Optional[str] = None
    run_count: int = 0
    error_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schedule": self.schedule.describe(),
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "error_count": self.error_count,
        }


@dataclass
class RunReport:
    """What one ``run_pending`` sweep did."""
    ran: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    results: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"ran": self.ran, "failed": self.failed}


# =============================================================================
# Scheduler
# =============================================================================

class Scheduler:
    """In-process scheduler with an injectable clock.

    Every time source is injectable so that schedule behaviour — including the
    catch-up rule — is testable without waiting on a wall clock.
    """

    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        event_bus: Optional[Any] = None,
    ) -> None:
        self._clock = clock
        self.event_bus = event_bus
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- registration ------------------------------------------------------

    def add_job(self, name: str, schedule: Schedule, func: Callable[[], Any],
                enabled: bool = True) -> Job:
        with self._lock:
            if name in self._jobs:
                raise ValueError(f"duplicate job name: {name!r}")
            job = Job(name=name, schedule=schedule, func=func, enabled=enabled)
            self._jobs[name] = job
            return job

    def remove_job(self, name: str) -> bool:
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def get_job(self, name: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(name)

    @property
    def jobs(self) -> List[Job]:
        with self._lock:
            return list(self._jobs.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                raise KeyError(name)
            job.enabled = enabled

    # -- execution ---------------------------------------------------------

    def due(self, now: Optional[datetime] = None) -> List[Job]:
        now = now or self._clock()
        with self._lock:
            return [
                job for job in self._jobs.values()
                if job.enabled and job.schedule.is_due(now, job.last_run)
            ]

    def run_pending(self, now: Optional[datetime] = None) -> RunReport:
        """Run every due job once. A raising job never stops the sweep."""
        now = now or self._clock()
        report = RunReport()

        for job in self.due(now):
            # Stamp before running: a job that takes longer than its interval
            # must not be re-entered by the next sweep.
            job.last_run = now
            try:
                result = job.func()
            except Exception as exc:  # noqa: BLE001 - one bad job must not stop the rest
                job.error_count += 1
                job.last_error = f"{type(exc).__name__}: {exc}"
                report.failed.append(job.name)
                self._publish("job_failed", job, {"error": job.last_error})
                continue

            job.run_count += 1
            job.last_error = None
            report.ran.append(job.name)
            report.results[job.name] = result
            self._publish("job_ran", job, {})

        return report

    def _publish(self, kind: str, job: Job, extra: Dict[str, Any]) -> None:
        """Best-effort publish to an attached EventBus.

        Duck-typed and contained: the scheduler works with no bus, and a bus
        that raises does not take a job down with it.
        """
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish({"type": kind, "job": job.name, **extra})
        except Exception:  # noqa: BLE001 - telemetry must never break scheduling
            pass

    # -- background loop ---------------------------------------------------

    def start(self, tick_seconds: float = 30.0) -> None:
        """Run ``run_pending`` on a tick until ``stop`` is called."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.run_pending()
                except Exception:  # noqa: BLE001 - the loop outlives any sweep
                    pass
                self._stop.wait(tick_seconds)

        self._thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def stats(self) -> Dict[str, Any]:
        return {
            "jobs": [job.to_dict() for job in self.jobs],
            "running": self._thread is not None and self._thread.is_alive(),
        }


def next_fire(schedule: Schedule, now: datetime) -> Optional[datetime]:
    """Best-effort next fire time, for display. None when unknown."""
    if isinstance(schedule, Interval):
        return now + timedelta(seconds=schedule.seconds)
    if isinstance(schedule, DailyAt):
        candidate = now.replace(hour=schedule.hour, minute=schedule.minute,
                                second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        for _ in range(8):
            if candidate.weekday() in schedule.weekdays:
                return candidate
            candidate += timedelta(days=1)
    return None
