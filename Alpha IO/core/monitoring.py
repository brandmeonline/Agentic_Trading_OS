"""
Production Monitoring & Alerting System.

Enterprise-grade monitoring for trading systems:
- System health monitoring
- Performance metrics collection
- Alerting and notifications
- Anomaly detection
- Audit logging
"""

from __future__ import annotations

import numpy as np
import json
import time
import threading
import queue
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable, Set
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from collections import deque
import hashlib


# =============================================================================
# Configuration and Enums
# =============================================================================

class AlertSeverity(Enum):
    """Alert severity levels."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4


class MetricType(Enum):
    """Types of metrics."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


class HealthStatus(Enum):
    """System health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"


@dataclass
class MonitoringConfig:
    """Monitoring configuration."""
    metrics_interval: float = 1.0  # seconds
    health_check_interval: float = 10.0
    alert_cooldown: float = 300.0  # 5 minutes
    retention_hours: int = 24
    anomaly_threshold: float = 3.0  # std deviations
    enable_profiling: bool = True
    log_level: AlertSeverity = AlertSeverity.INFO


@dataclass
class Alert:
    """Alert notification."""
    id: str
    severity: AlertSeverity
    source: str
    message: str
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    acknowledged: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "severity": self.severity.name,
            "source": self.source,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "acknowledged": self.acknowledged
        }


@dataclass
class HealthCheckResult:
    """Health check result."""
    component: str
    status: HealthStatus
    latency_ms: float
    message: str
    timestamp: datetime
    details: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Metrics Collection
# =============================================================================

class Metric:
    """Base metric class."""

    def __init__(self, name: str, description: str = "", labels: Optional[Dict[str, str]] = None):
        self.name = name
        self.description = description
        self.labels = labels or {}
        self.created_at = time.time()


class Counter(Metric):
    """Counter metric (only increases)."""

    def __init__(self, name: str, description: str = "", labels: Optional[Dict[str, str]] = None):
        super().__init__(name, description, labels)
        self.value = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0):
        """Increment counter."""
        with self._lock:
            self.value += amount

    def get(self) -> float:
        """Get current value."""
        with self._lock:
            return self.value


class Gauge(Metric):
    """Gauge metric (can increase/decrease)."""

    def __init__(self, name: str, description: str = "", labels: Optional[Dict[str, str]] = None):
        super().__init__(name, description, labels)
        self.value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float):
        """Set gauge value."""
        with self._lock:
            self.value = value

    def inc(self, amount: float = 1.0):
        """Increment gauge."""
        with self._lock:
            self.value += amount

    def dec(self, amount: float = 1.0):
        """Decrement gauge."""
        with self._lock:
            self.value -= amount

    def get(self) -> float:
        """Get current value."""
        with self._lock:
            return self.value


class Histogram(Metric):
    """Histogram metric for distributions."""

    def __init__(
        self,
        name: str,
        description: str = "",
        labels: Optional[Dict[str, str]] = None,
        buckets: Optional[List[float]] = None
    ):
        super().__init__(name, description, labels)
        self.buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self.bucket_counts = [0] * (len(self.buckets) + 1)
        self.sum = 0.0
        self.count = 0
        self._lock = threading.Lock()

    def observe(self, value: float):
        """Record an observation."""
        with self._lock:
            self.sum += value
            self.count += 1

            for i, bucket in enumerate(self.buckets):
                if value <= bucket:
                    self.bucket_counts[i] += 1
                    break
            else:
                self.bucket_counts[-1] += 1

    def get_percentile(self, p: float) -> float:
        """Get approximate percentile."""
        with self._lock:
            if self.count == 0:
                return 0.0

            target = self.count * p
            cumulative = 0

            for i, count in enumerate(self.bucket_counts[:-1]):
                cumulative += count
                if cumulative >= target:
                    return self.buckets[i]

            return self.buckets[-1]

    def get_stats(self) -> Dict[str, float]:
        """Get histogram statistics."""
        with self._lock:
            return {
                "count": self.count,
                "sum": self.sum,
                "mean": self.sum / self.count if self.count > 0 else 0,
                "p50": self.get_percentile(0.5),
                "p90": self.get_percentile(0.9),
                "p99": self.get_percentile(0.99)
            }


class MetricsCollector:
    """
    Central metrics collection system.

    Provides:
    - Metric registration
    - Time series storage
    - Aggregation
    - Export
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._metrics: Dict[str, Metric] = {}
        self._time_series: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, description: str = "", labels: Optional[Dict[str, str]] = None) -> Counter:
        """Get or create a counter metric."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = Counter(name, description, labels)
            return self._metrics[key]

    def gauge(self, name: str, description: str = "", labels: Optional[Dict[str, str]] = None) -> Gauge:
        """Get or create a gauge metric."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = Gauge(name, description, labels)
            return self._metrics[key]

    def histogram(
        self,
        name: str,
        description: str = "",
        labels: Optional[Dict[str, str]] = None,
        buckets: Optional[List[float]] = None
    ) -> Histogram:
        """Get or create a histogram metric."""
        key = self._make_key(name, labels)
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = Histogram(name, description, labels, buckets)
            return self._metrics[key]

    def _make_key(self, name: str, labels: Optional[Dict[str, str]]) -> str:
        """Create unique key for metric."""
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{name}{{{label_str}}}"
        return name

    def record_time_series(self, name: str, value: float, timestamp: Optional[float] = None):
        """Record value in time series."""
        timestamp = timestamp or time.time()
        key = name

        with self._lock:
            if key not in self._time_series:
                max_points = int(self.config.retention_hours * 3600 / self.config.metrics_interval)
                self._time_series[key] = deque(maxlen=max_points)

            self._time_series[key].append((timestamp, value))

    def get_time_series(
        self,
        name: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None
    ) -> List[Tuple[float, float]]:
        """Get time series data."""
        with self._lock:
            if name not in self._time_series:
                return []

            data = list(self._time_series[name])

            if start_time:
                data = [(t, v) for t, v in data if t >= start_time]
            if end_time:
                data = [(t, v) for t, v in data if t <= end_time]

            return data

    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all current metric values."""
        with self._lock:
            result = {}

            for key, metric in self._metrics.items():
                if isinstance(metric, Counter):
                    result[key] = {"type": "counter", "value": metric.get()}
                elif isinstance(metric, Gauge):
                    result[key] = {"type": "gauge", "value": metric.get()}
                elif isinstance(metric, Histogram):
                    result[key] = {"type": "histogram", **metric.get_stats()}

            return result

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        lines = []

        with self._lock:
            for key, metric in self._metrics.items():
                # Add help and type
                lines.append(f"# HELP {metric.name} {metric.description}")

                if isinstance(metric, Counter):
                    lines.append(f"# TYPE {metric.name} counter")
                    lines.append(f"{key} {metric.get()}")
                elif isinstance(metric, Gauge):
                    lines.append(f"# TYPE {metric.name} gauge")
                    lines.append(f"{key} {metric.get()}")
                elif isinstance(metric, Histogram):
                    lines.append(f"# TYPE {metric.name} histogram")
                    stats = metric.get_stats()
                    lines.append(f"{key}_count {stats['count']}")
                    lines.append(f"{key}_sum {stats['sum']}")

        return "\n".join(lines)


# =============================================================================
# Health Checking
# =============================================================================

class HealthChecker:
    """
    System health checker.

    Monitors:
    - Component availability
    - Response times
    - Resource usage
    - Dependencies
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._checks: Dict[str, Callable[[], HealthCheckResult]] = {}
        self._results: Dict[str, HealthCheckResult] = {}
        self._lock = threading.Lock()

    def register_check(self, name: str, check_func: Callable[[], HealthCheckResult]):
        """Register a health check."""
        self._checks[name] = check_func

    def run_check(self, name: str) -> HealthCheckResult:
        """Run a specific health check."""
        if name not in self._checks:
            return HealthCheckResult(
                component=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message="Check not found",
                timestamp=datetime.now()
            )

        start = time.time()
        try:
            result = self._checks[name]()
            result.latency_ms = (time.time() - start) * 1000
        except Exception as e:
            result = HealthCheckResult(
                component=name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                message=str(e),
                timestamp=datetime.now()
            )

        with self._lock:
            self._results[name] = result

        return result

    def run_all_checks(self) -> Dict[str, HealthCheckResult]:
        """Run all health checks."""
        results = {}
        for name in self._checks:
            results[name] = self.run_check(name)
        return results

    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status."""
        with self._lock:
            if not self._results:
                return HealthStatus.HEALTHY

            statuses = [r.status for r in self._results.values()]

            if any(s == HealthStatus.CRITICAL for s in statuses):
                return HealthStatus.CRITICAL
            if any(s == HealthStatus.UNHEALTHY for s in statuses):
                return HealthStatus.UNHEALTHY
            if any(s == HealthStatus.DEGRADED for s in statuses):
                return HealthStatus.DEGRADED
            return HealthStatus.HEALTHY

    def get_health_report(self) -> Dict:
        """Get comprehensive health report."""
        results = self.run_all_checks()

        return {
            "status": self.get_overall_status().value,
            "timestamp": datetime.now().isoformat(),
            "checks": {
                name: {
                    "status": result.status.value,
                    "latency_ms": result.latency_ms,
                    "message": result.message,
                    "details": result.details
                }
                for name, result in results.items()
            }
        }


# =============================================================================
# Alert Manager
# =============================================================================

class AlertManager:
    """
    Alert management system.

    Handles:
    - Alert creation and routing
    - Deduplication
    - Cooldown periods
    - Notification dispatch
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._alerts: Dict[str, Alert] = {}
        self._alert_history: deque = deque(maxlen=10000)
        self._cooldowns: Dict[str, float] = {}
        self._handlers: List[Callable[[Alert], None]] = []
        self._lock = threading.Lock()
        self._alert_counter = 0

    def register_handler(self, handler: Callable[[Alert], None]):
        """Register alert handler."""
        self._handlers.append(handler)

    def create_alert(
        self,
        severity: AlertSeverity,
        source: str,
        message: str,
        metadata: Optional[Dict] = None,
        dedupe_key: Optional[str] = None
    ) -> Optional[Alert]:
        """Create a new alert."""
        # Check severity threshold
        if severity.value < self.config.log_level.value:
            return None

        # Check cooldown
        dedupe_key = dedupe_key or f"{source}:{message}"
        with self._lock:
            if dedupe_key in self._cooldowns:
                if time.time() - self._cooldowns[dedupe_key] < self.config.alert_cooldown:
                    return None

            self._cooldowns[dedupe_key] = time.time()
            self._alert_counter += 1
            alert_id = f"ALT-{self._alert_counter:08d}"

        alert = Alert(
            id=alert_id,
            severity=severity,
            source=source,
            message=message,
            timestamp=datetime.now(),
            metadata=metadata or {}
        )

        with self._lock:
            self._alerts[alert_id] = alert
            self._alert_history.append(alert)

        # Dispatch to handlers
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception:
                pass

        return alert

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        with self._lock:
            if alert_id in self._alerts:
                self._alerts[alert_id].acknowledged = True
                return True
            return False

    def get_active_alerts(self, min_severity: Optional[AlertSeverity] = None) -> List[Alert]:
        """Get active (unacknowledged) alerts."""
        with self._lock:
            alerts = [a for a in self._alerts.values() if not a.acknowledged]

            if min_severity:
                alerts = [a for a in alerts if a.severity.value >= min_severity.value]

            return sorted(alerts, key=lambda a: a.severity.value, reverse=True)

    def get_alert_history(
        self,
        limit: int = 100,
        source: Optional[str] = None,
        min_severity: Optional[AlertSeverity] = None
    ) -> List[Alert]:
        """Get alert history."""
        with self._lock:
            alerts = list(self._alert_history)

        if source:
            alerts = [a for a in alerts if a.source == source]
        if min_severity:
            alerts = [a for a in alerts if a.severity.value >= min_severity.value]

        return alerts[-limit:]

    def get_alert_summary(self) -> Dict:
        """Get alert summary statistics."""
        with self._lock:
            alerts = list(self._alert_history)

        by_severity = {}
        by_source = {}

        for alert in alerts:
            sev = alert.severity.name
            by_severity[sev] = by_severity.get(sev, 0) + 1

            src = alert.source
            by_source[src] = by_source.get(src, 0) + 1

        active = len([a for a in self._alerts.values() if not a.acknowledged])

        return {
            "total_alerts": len(alerts),
            "active_alerts": active,
            "by_severity": by_severity,
            "by_source": by_source
        }


# =============================================================================
# Anomaly Detection
# =============================================================================

class AnomalyDetector:
    """
    Statistical anomaly detection for metrics.

    Uses:
    - Z-score detection
    - Moving average deviation
    - Trend analysis
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._baselines: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def update_baseline(self, metric_name: str, values: List[float]):
        """Update baseline statistics for a metric."""
        if len(values) < 10:
            return

        with self._lock:
            self._baselines[metric_name] = {
                "mean": np.mean(values),
                "std": np.std(values),
                "min": np.min(values),
                "max": np.max(values),
                "p95": np.percentile(values, 95),
                "p99": np.percentile(values, 99),
                "updated_at": time.time()
            }

    def check_anomaly(self, metric_name: str, value: float) -> Optional[Dict]:
        """Check if a value is anomalous."""
        with self._lock:
            if metric_name not in self._baselines:
                return None

            baseline = self._baselines[metric_name]

        # Z-score check
        if baseline["std"] > 0:
            z_score = abs(value - baseline["mean"]) / baseline["std"]
            if z_score > self.config.anomaly_threshold:
                return {
                    "type": "z_score",
                    "metric": metric_name,
                    "value": value,
                    "z_score": z_score,
                    "threshold": self.config.anomaly_threshold,
                    "baseline_mean": baseline["mean"],
                    "baseline_std": baseline["std"]
                }

        # Extreme value check
        if value > baseline["p99"] * 1.5 or value < baseline["min"] * 0.5:
            return {
                "type": "extreme_value",
                "metric": metric_name,
                "value": value,
                "p99": baseline["p99"],
                "min": baseline["min"]
            }

        return None


# =============================================================================
# Performance Profiler
# =============================================================================

class PerformanceProfiler:
    """
    Performance profiling for critical code paths.

    Tracks:
    - Execution times
    - Call frequencies
    - Bottlenecks
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._timings: Dict[str, deque] = {}
        self._call_counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def profile(self, name: str):
        """Decorator for profiling a function."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                start = time.time()
                try:
                    return func(*args, **kwargs)
                finally:
                    elapsed = time.time() - start
                    self.record_timing(name, elapsed)
            return wrapper
        return decorator

    def record_timing(self, name: str, elapsed: float):
        """Record a timing measurement."""
        with self._lock:
            if name not in self._timings:
                self._timings[name] = deque(maxlen=10000)
                self._call_counts[name] = 0

            self._timings[name].append((time.time(), elapsed))
            self._call_counts[name] += 1

    def get_profile(self, name: str) -> Dict:
        """Get profile statistics for a function."""
        with self._lock:
            if name not in self._timings:
                return {}

            times = [t[1] for t in self._timings[name]]
            if not times:
                return {}

            return {
                "name": name,
                "call_count": self._call_counts[name],
                "total_time": sum(times),
                "mean_time": np.mean(times),
                "std_time": np.std(times),
                "min_time": np.min(times),
                "max_time": np.max(times),
                "p50_time": np.percentile(times, 50),
                "p95_time": np.percentile(times, 95),
                "p99_time": np.percentile(times, 99)
            }

    def get_all_profiles(self) -> List[Dict]:
        """Get all profile statistics."""
        with self._lock:
            names = list(self._timings.keys())

        profiles = [self.get_profile(name) for name in names]
        return sorted(profiles, key=lambda p: p.get("total_time", 0), reverse=True)

    def get_slow_functions(self, threshold_ms: float = 100.0) -> List[Dict]:
        """Get functions with slow p95 times."""
        profiles = self.get_all_profiles()
        return [p for p in profiles if p.get("p95_time", 0) * 1000 > threshold_ms]


# =============================================================================
# System Monitor
# =============================================================================

class SystemMonitor:
    """
    Main system monitoring orchestrator.

    Coordinates:
    - Metrics collection
    - Health checking
    - Alerting
    - Anomaly detection
    """

    def __init__(self, config: Optional[MonitoringConfig] = None):
        self.config = config or MonitoringConfig()

        self.metrics = MetricsCollector(self.config)
        self.health_checker = HealthChecker(self.config)
        self.alert_manager = AlertManager(self.config)
        self.anomaly_detector = AnomalyDetector(self.config)
        self.profiler = PerformanceProfiler(self.config)

        self._running = False
        self._threads: List[threading.Thread] = []

        # Register default health checks
        self._register_default_checks()

        # Standard metrics
        self._setup_standard_metrics()

    def _setup_standard_metrics(self):
        """Setup standard system metrics."""
        self.orders_total = self.metrics.counter("orders_total", "Total orders processed")
        self.orders_failed = self.metrics.counter("orders_failed", "Failed orders")
        self.latency = self.metrics.histogram(
            "request_latency_seconds",
            "Request latency",
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0]
        )
        self.active_connections = self.metrics.gauge("active_connections", "Active connections")
        self.portfolio_value = self.metrics.gauge("portfolio_value", "Portfolio value")
        self.pnl = self.metrics.gauge("pnl", "Current P&L")

    def _register_default_checks(self):
        """Register default health checks."""
        def memory_check() -> HealthCheckResult:
            import sys
            # Simplified memory check
            status = HealthStatus.HEALTHY
            message = "Memory usage normal"
            return HealthCheckResult(
                component="memory",
                status=status,
                latency_ms=0,
                message=message,
                timestamp=datetime.now()
            )

        def cpu_check() -> HealthCheckResult:
            # Simplified CPU check
            return HealthCheckResult(
                component="cpu",
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                message="CPU usage normal",
                timestamp=datetime.now()
            )

        self.health_checker.register_check("memory", memory_check)
        self.health_checker.register_check("cpu", cpu_check)

    def start(self):
        """Start monitoring threads."""
        if self._running:
            return

        self._running = True

        # Metrics collection thread
        metrics_thread = threading.Thread(target=self._metrics_loop, daemon=True)
        metrics_thread.start()
        self._threads.append(metrics_thread)

        # Health check thread
        health_thread = threading.Thread(target=self._health_loop, daemon=True)
        health_thread.start()
        self._threads.append(health_thread)

    def stop(self):
        """Stop monitoring threads."""
        self._running = False
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []

    def _metrics_loop(self):
        """Background metrics collection loop."""
        while self._running:
            try:
                # Collect and record standard metrics
                for key, metric in self.metrics._metrics.items():
                    if isinstance(metric, (Counter, Gauge)):
                        self.metrics.record_time_series(key, metric.get())

                time.sleep(self.config.metrics_interval)

            except Exception:
                time.sleep(5)

    def _health_loop(self):
        """Background health check loop."""
        while self._running:
            try:
                # Run health checks
                results = self.health_checker.run_all_checks()

                # Create alerts for unhealthy components
                for name, result in results.items():
                    if result.status in [HealthStatus.UNHEALTHY, HealthStatus.CRITICAL]:
                        severity = (
                            AlertSeverity.CRITICAL
                            if result.status == HealthStatus.CRITICAL
                            else AlertSeverity.ERROR
                        )
                        self.alert_manager.create_alert(
                            severity=severity,
                            source=f"health_check:{name}",
                            message=result.message,
                            metadata=result.details,
                            dedupe_key=f"health:{name}"
                        )

                time.sleep(self.config.health_check_interval)

            except Exception:
                time.sleep(5)

    def record_order(self, success: bool, latency: float):
        """Record order execution."""
        self.orders_total.inc()
        if not success:
            self.orders_failed.inc()
        self.latency.observe(latency)

    def record_pnl(self, pnl: float, portfolio_value: float):
        """Record P&L metrics."""
        self.pnl.set(pnl)
        self.portfolio_value.set(portfolio_value)

        # Check for anomalies
        anomaly = self.anomaly_detector.check_anomaly("pnl", pnl)
        if anomaly:
            self.alert_manager.create_alert(
                severity=AlertSeverity.WARNING,
                source="anomaly_detector",
                message=f"Anomalous P&L detected: {pnl}",
                metadata=anomaly
            )

    def get_dashboard_data(self) -> Dict:
        """Get data for monitoring dashboard."""
        return {
            "health": self.health_checker.get_health_report(),
            "metrics": self.metrics.get_all_metrics(),
            "alerts": {
                "active": [a.to_dict() for a in self.alert_manager.get_active_alerts()],
                "summary": self.alert_manager.get_alert_summary()
            },
            "performance": {
                "profiles": self.profiler.get_all_profiles()[:10],
                "slow_functions": self.profiler.get_slow_functions()
            },
            "timestamp": datetime.now().isoformat()
        }

    def create_alert(
        self,
        severity: AlertSeverity,
        source: str,
        message: str,
        metadata: Optional[Dict] = None
    ) -> Optional[Alert]:
        """Create a manual alert."""
        return self.alert_manager.create_alert(severity, source, message, metadata)

    def register_health_check(self, name: str, check_func: Callable[[], HealthCheckResult]):
        """Register a custom health check."""
        self.health_checker.register_check(name, check_func)

    def profile(self, name: str):
        """Decorator for profiling."""
        return self.profiler.profile(name)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Create system monitor
    config = MonitoringConfig(
        metrics_interval=1.0,
        health_check_interval=5.0,
        log_level=AlertSeverity.INFO
    )
    monitor = SystemMonitor(config)

    # Register alert handler
    def print_alert(alert: Alert):
        print(f"[{alert.severity.name}] {alert.source}: {alert.message}")

    monitor.alert_manager.register_handler(print_alert)

    # Register custom health check
    def database_check() -> HealthCheckResult:
        # Simulate database check
        latency = np.random.exponential(10)
        if latency > 50:
            return HealthCheckResult(
                component="database",
                status=HealthStatus.DEGRADED,
                latency_ms=latency,
                message="Database slow",
                timestamp=datetime.now()
            )
        return HealthCheckResult(
            component="database",
            status=HealthStatus.HEALTHY,
            latency_ms=latency,
            message="Database healthy",
            timestamp=datetime.now()
        )

    monitor.register_health_check("database", database_check)

    # Start monitoring
    print("Starting monitoring system...")
    monitor.start()

    # Simulate trading activity
    print("\nSimulating trading activity...")
    for i in range(20):
        # Simulate orders
        success = np.random.random() > 0.1
        latency = np.random.exponential(0.05)
        monitor.record_order(success, latency)

        # Simulate P&L
        pnl = np.random.randn() * 1000
        portfolio_value = 100000 + pnl
        monitor.record_pnl(pnl, portfolio_value)

        time.sleep(0.5)

    # Create manual alert
    monitor.create_alert(
        severity=AlertSeverity.WARNING,
        source="manual",
        message="Test alert from example"
    )

    # Get dashboard data
    print("\nDashboard Data:")
    dashboard = monitor.get_dashboard_data()

    print(f"\nHealth Status: {dashboard['health']['status']}")

    print(f"\nMetrics:")
    for name, data in list(dashboard['metrics'].items())[:5]:
        print(f"  {name}: {data}")

    print(f"\nActive Alerts: {len(dashboard['alerts']['active'])}")
    print(f"Alert Summary: {dashboard['alerts']['summary']}")

    # Stop monitoring
    print("\nStopping monitoring...")
    monitor.stop()
