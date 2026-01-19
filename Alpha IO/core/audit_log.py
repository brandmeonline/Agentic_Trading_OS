"""
Comprehensive Audit Logging System.

Production-grade audit logging for trading systems:
- Trade and order audit trails
- System event logging
- Compliance logging
- Performance metrics logging
- Security event logging
- Log aggregation and analysis
"""

from __future__ import annotations

import json
import time
import hashlib
import threading
import queue
import gzip
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable, Union
from enum import Enum
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from collections import deque
import traceback


# =============================================================================
# Configuration
# =============================================================================

class LogLevel(Enum):
    """Log severity levels."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4
    AUDIT = 5  # Special level for audit events


class LogCategory(Enum):
    """Log categories for filtering."""
    SYSTEM = "system"
    TRADING = "trading"
    ORDER = "order"
    POSITION = "position"
    RISK = "risk"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    PERFORMANCE = "performance"
    API = "api"
    DATA = "data"


class AuditAction(Enum):
    """Audit action types."""
    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_FILLED = "order_filled"
    ORDER_REJECTED = "order_rejected"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_MODIFIED = "position_modified"
    RISK_BREACH = "risk_breach"
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_STOPPED = "strategy_stopped"
    CONFIG_CHANGED = "config_changed"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    API_REQUEST = "api_request"
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"
    ERROR_OCCURRED = "error_occurred"


@dataclass
class LogConfig:
    """Logging configuration."""
    log_level: LogLevel = LogLevel.INFO
    log_dir: str = "logs"
    max_file_size_mb: int = 100
    max_files: int = 10
    enable_console: bool = True
    enable_file: bool = True
    enable_json: bool = True
    enable_compression: bool = True
    buffer_size: int = 1000
    flush_interval: float = 5.0
    async_logging: bool = True


@dataclass
class LogEntry:
    """Single log entry."""
    timestamp: datetime
    level: LogLevel
    category: LogCategory
    message: str
    source: str = ""
    correlation_id: str = ""
    user_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    traceback: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.name,
            "category": self.category.value,
            "message": self.message,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "user_id": self.user_id,
            "metadata": self.metadata,
            "traceback": self.traceback,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    def to_line(self) -> str:
        """Convert to single line format."""
        meta = json.dumps(self.metadata) if self.metadata else ""
        return f"{self.timestamp.isoformat()} [{self.level.name}] [{self.category.value}] {self.message} {meta}"


@dataclass
class AuditEntry:
    """Audit trail entry with immutability."""
    id: str
    timestamp: datetime
    action: AuditAction
    category: LogCategory
    user_id: str
    resource_type: str
    resource_id: str
    details: Dict[str, Any]
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    ip_address: str = ""
    checksum: str = ""

    def __post_init__(self):
        """Generate checksum for integrity verification."""
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """Compute SHA-256 checksum of entry."""
        data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "user_id": self.user_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify entry integrity."""
        return self.checksum == self._compute_checksum()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action.value,
            "category": self.category.value,
            "user_id": self.user_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "details": self.details,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "ip_address": self.ip_address,
            "checksum": self.checksum,
        }


# =============================================================================
# Log Handlers
# =============================================================================

class LogHandler(ABC):
    """Abstract log handler."""

    @abstractmethod
    def handle(self, entry: LogEntry):
        """Handle log entry."""
        pass

    @abstractmethod
    def flush(self):
        """Flush any buffered entries."""
        pass

    @abstractmethod
    def close(self):
        """Close handler."""
        pass


class ConsoleHandler(LogHandler):
    """Console log handler with colors."""

    COLORS = {
        LogLevel.DEBUG: "\033[36m",    # Cyan
        LogLevel.INFO: "\033[32m",     # Green
        LogLevel.WARNING: "\033[33m",  # Yellow
        LogLevel.ERROR: "\033[31m",    # Red
        LogLevel.CRITICAL: "\033[35m", # Magenta
        LogLevel.AUDIT: "\033[34m",    # Blue
    }
    RESET = "\033[0m"

    def __init__(self, min_level: LogLevel = LogLevel.INFO, use_colors: bool = True):
        self.min_level = min_level
        self.use_colors = use_colors

    def handle(self, entry: LogEntry):
        """Print log entry to console."""
        if entry.level.value < self.min_level.value:
            return

        line = entry.to_line()

        if self.use_colors:
            color = self.COLORS.get(entry.level, "")
            line = f"{color}{line}{self.RESET}"

        print(line)

    def flush(self):
        pass

    def close(self):
        pass


class FileHandler(LogHandler):
    """Rotating file log handler."""

    def __init__(
        self,
        log_dir: str,
        filename: str = "trading.log",
        max_size_mb: int = 100,
        max_files: int = 10,
        compress: bool = True
    ):
        self.log_dir = log_dir
        self.filename = filename
        self.max_size = max_size_mb * 1024 * 1024
        self.max_files = max_files
        self.compress = compress

        os.makedirs(log_dir, exist_ok=True)
        self.filepath = os.path.join(log_dir, filename)
        self._current_size = 0
        self._lock = threading.Lock()

    def handle(self, entry: LogEntry):
        """Write log entry to file."""
        line = entry.to_line() + "\n"

        with self._lock:
            self._check_rotation()

            try:
                with open(self.filepath, "a") as f:
                    f.write(line)
                self._current_size += len(line.encode())
            except Exception as e:
                print(f"Failed to write log: {e}")

    def _check_rotation(self):
        """Check if rotation is needed."""
        if self._current_size >= self.max_size:
            self._rotate()

    def _rotate(self):
        """Rotate log files."""
        # Delete oldest file if at max
        for i in range(self.max_files - 1, 0, -1):
            old_path = f"{self.filepath}.{i}"
            new_path = f"{self.filepath}.{i + 1}"
            if self.compress:
                old_path += ".gz"
                new_path += ".gz"

            if os.path.exists(old_path):
                if i == self.max_files - 1:
                    os.remove(old_path)
                else:
                    os.rename(old_path, new_path)

        # Rotate current file
        if os.path.exists(self.filepath):
            new_path = f"{self.filepath}.1"
            if self.compress:
                with open(self.filepath, 'rb') as f_in:
                    with gzip.open(f"{new_path}.gz", 'wb') as f_out:
                        f_out.writelines(f_in)
                os.remove(self.filepath)
            else:
                os.rename(self.filepath, new_path)

        self._current_size = 0

    def flush(self):
        pass

    def close(self):
        pass


class JSONFileHandler(LogHandler):
    """JSON file log handler for structured logging."""

    def __init__(self, log_dir: str, filename: str = "trading.json"):
        self.log_dir = log_dir
        self.filepath = os.path.join(log_dir, filename)
        self._lock = threading.Lock()

        os.makedirs(log_dir, exist_ok=True)

    def handle(self, entry: LogEntry):
        """Write JSON log entry."""
        with self._lock:
            try:
                with open(self.filepath, "a") as f:
                    f.write(entry.to_json() + "\n")
            except Exception as e:
                print(f"Failed to write JSON log: {e}")

    def flush(self):
        pass

    def close(self):
        pass


class AuditFileHandler(LogHandler):
    """Immutable audit trail file handler."""

    def __init__(self, log_dir: str, filename: str = "audit.log"):
        self.log_dir = log_dir
        self.filepath = os.path.join(log_dir, filename)
        self._lock = threading.Lock()
        self._last_checksum = ""

        os.makedirs(log_dir, exist_ok=True)

    def handle(self, entry: LogEntry):
        """This handler is for LogEntry compatibility."""
        pass

    def handle_audit(self, entry: AuditEntry):
        """Write audit entry with chain verification."""
        with self._lock:
            # Chain with previous entry
            chain_data = f"{self._last_checksum}{entry.checksum}"
            chain_checksum = hashlib.sha256(chain_data.encode()).hexdigest()

            record = {
                **entry.to_dict(),
                "chain_checksum": chain_checksum,
            }

            try:
                with open(self.filepath, "a") as f:
                    f.write(json.dumps(record) + "\n")
                self._last_checksum = entry.checksum
            except Exception as e:
                print(f"Failed to write audit log: {e}")

    def verify_chain(self) -> Tuple[bool, List[str]]:
        """Verify integrity of audit chain."""
        errors = []
        last_checksum = ""

        try:
            with open(self.filepath, "r") as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        record = json.loads(line)
                        entry = AuditEntry(
                            id=record["id"],
                            timestamp=datetime.fromisoformat(record["timestamp"]),
                            action=AuditAction(record["action"]),
                            category=LogCategory(record["category"]),
                            user_id=record["user_id"],
                            resource_type=record["resource_type"],
                            resource_id=record["resource_id"],
                            details=record["details"],
                            checksum=record["checksum"],
                        )

                        # Verify entry integrity
                        if not entry.verify_integrity():
                            errors.append(f"Line {line_num}: Entry integrity check failed")

                        # Verify chain
                        expected_chain = hashlib.sha256(
                            f"{last_checksum}{entry.checksum}".encode()
                        ).hexdigest()

                        if record.get("chain_checksum") != expected_chain:
                            errors.append(f"Line {line_num}: Chain integrity check failed")

                        last_checksum = entry.checksum

                    except Exception as e:
                        errors.append(f"Line {line_num}: Parse error - {e}")

        except FileNotFoundError:
            pass

        return len(errors) == 0, errors

    def flush(self):
        pass

    def close(self):
        pass


# =============================================================================
# Async Log Queue
# =============================================================================

class AsyncLogQueue:
    """Asynchronous log queue for non-blocking logging."""

    def __init__(self, handlers: List[LogHandler], flush_interval: float = 5.0):
        self.handlers = handlers
        self.flush_interval = flush_interval
        self._queue = queue.Queue(maxsize=10000)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start async processing."""
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop async processing."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._flush_all()

    def enqueue(self, entry: LogEntry):
        """Add entry to queue."""
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            # Drop oldest if queue is full
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(entry)
            except queue.Empty:
                pass

    def _process_loop(self):
        """Process queue entries."""
        last_flush = time.time()

        while self._running:
            try:
                entry = self._queue.get(timeout=0.1)
                for handler in self.handlers:
                    handler.handle(entry)
            except queue.Empty:
                pass

            # Periodic flush
            if time.time() - last_flush >= self.flush_interval:
                self._flush_all()
                last_flush = time.time()

    def _flush_all(self):
        """Flush all handlers."""
        for handler in self.handlers:
            handler.flush()


# =============================================================================
# Logger
# =============================================================================

class Logger:
    """Main logger class."""

    def __init__(self, config: Optional[LogConfig] = None):
        self.config = config or LogConfig()
        self.handlers: List[LogHandler] = []
        self._audit_handler: Optional[AuditFileHandler] = None
        self._queue: Optional[AsyncLogQueue] = None
        self._lock = threading.Lock()

        self._setup_handlers()

    def _setup_handlers(self):
        """Setup log handlers based on config."""
        if self.config.enable_console:
            self.handlers.append(ConsoleHandler(self.config.log_level))

        if self.config.enable_file:
            self.handlers.append(FileHandler(
                self.config.log_dir,
                max_size_mb=self.config.max_file_size_mb,
                max_files=self.config.max_files,
                compress=self.config.enable_compression,
            ))

        if self.config.enable_json:
            self.handlers.append(JSONFileHandler(self.config.log_dir))

        # Audit handler
        self._audit_handler = AuditFileHandler(self.config.log_dir)

        # Async queue
        if self.config.async_logging:
            self._queue = AsyncLogQueue(self.handlers, self.config.flush_interval)
            self._queue.start()

    def _log(
        self,
        level: LogLevel,
        category: LogCategory,
        message: str,
        source: str = "",
        correlation_id: str = "",
        user_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        exc_info: bool = False
    ):
        """Internal log method."""
        entry = LogEntry(
            timestamp=datetime.now(),
            level=level,
            category=category,
            message=message,
            source=source,
            correlation_id=correlation_id,
            user_id=user_id,
            metadata=metadata or {},
            traceback=traceback.format_exc() if exc_info else "",
        )

        if self._queue and self.config.async_logging:
            self._queue.enqueue(entry)
        else:
            for handler in self.handlers:
                handler.handle(entry)

    def debug(self, message: str, category: LogCategory = LogCategory.SYSTEM, **kwargs):
        """Log debug message."""
        self._log(LogLevel.DEBUG, category, message, **kwargs)

    def info(self, message: str, category: LogCategory = LogCategory.SYSTEM, **kwargs):
        """Log info message."""
        self._log(LogLevel.INFO, category, message, **kwargs)

    def warning(self, message: str, category: LogCategory = LogCategory.SYSTEM, **kwargs):
        """Log warning message."""
        self._log(LogLevel.WARNING, category, message, **kwargs)

    def error(self, message: str, category: LogCategory = LogCategory.SYSTEM, exc_info: bool = True, **kwargs):
        """Log error message."""
        self._log(LogLevel.ERROR, category, message, exc_info=exc_info, **kwargs)

    def critical(self, message: str, category: LogCategory = LogCategory.SYSTEM, exc_info: bool = True, **kwargs):
        """Log critical message."""
        self._log(LogLevel.CRITICAL, category, message, exc_info=exc_info, **kwargs)

    def audit(
        self,
        action: AuditAction,
        resource_type: str,
        resource_id: str,
        details: Dict[str, Any],
        user_id: str = "system",
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        ip_address: str = ""
    ):
        """Log audit event."""
        entry = AuditEntry(
            id=hashlib.md5(f"{time.time()}{action.value}{resource_id}".encode()).hexdigest(),
            timestamp=datetime.now(),
            action=action,
            category=self._action_to_category(action),
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            before_state=before_state,
            after_state=after_state,
            ip_address=ip_address,
        )

        if self._audit_handler:
            self._audit_handler.handle_audit(entry)

        # Also log as regular entry
        self._log(
            LogLevel.AUDIT,
            entry.category,
            f"AUDIT: {action.value} on {resource_type}/{resource_id}",
            user_id=user_id,
            metadata=details,
        )

    def _action_to_category(self, action: AuditAction) -> LogCategory:
        """Map audit action to log category."""
        mapping = {
            AuditAction.ORDER_PLACED: LogCategory.ORDER,
            AuditAction.ORDER_CANCELLED: LogCategory.ORDER,
            AuditAction.ORDER_FILLED: LogCategory.ORDER,
            AuditAction.ORDER_REJECTED: LogCategory.ORDER,
            AuditAction.POSITION_OPENED: LogCategory.POSITION,
            AuditAction.POSITION_CLOSED: LogCategory.POSITION,
            AuditAction.POSITION_MODIFIED: LogCategory.POSITION,
            AuditAction.RISK_BREACH: LogCategory.RISK,
            AuditAction.STRATEGY_STARTED: LogCategory.TRADING,
            AuditAction.STRATEGY_STOPPED: LogCategory.TRADING,
            AuditAction.CONFIG_CHANGED: LogCategory.SYSTEM,
            AuditAction.USER_LOGIN: LogCategory.SECURITY,
            AuditAction.USER_LOGOUT: LogCategory.SECURITY,
            AuditAction.API_REQUEST: LogCategory.API,
            AuditAction.SYSTEM_START: LogCategory.SYSTEM,
            AuditAction.SYSTEM_STOP: LogCategory.SYSTEM,
            AuditAction.ERROR_OCCURRED: LogCategory.SYSTEM,
        }
        return mapping.get(action, LogCategory.SYSTEM)

    def close(self):
        """Close logger and all handlers."""
        if self._queue:
            self._queue.stop()

        for handler in self.handlers:
            handler.close()


# =============================================================================
# Trading Logger (Specialized)
# =============================================================================

class TradingLogger(Logger):
    """Specialized logger for trading operations."""

    def order_placed(self, order_id: str, symbol: str, side: str, quantity: float, price: Optional[float], user_id: str = "system"):
        """Log order placement."""
        self.audit(
            AuditAction.ORDER_PLACED,
            "order",
            order_id,
            {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
            },
            user_id=user_id,
        )

    def order_filled(self, order_id: str, fill_price: float, fill_quantity: float, fees: float):
        """Log order fill."""
        self.audit(
            AuditAction.ORDER_FILLED,
            "order",
            order_id,
            {
                "fill_price": fill_price,
                "fill_quantity": fill_quantity,
                "fees": fees,
            },
        )

    def order_cancelled(self, order_id: str, reason: str):
        """Log order cancellation."""
        self.audit(
            AuditAction.ORDER_CANCELLED,
            "order",
            order_id,
            {"reason": reason},
        )

    def position_opened(self, position_id: str, symbol: str, side: str, size: float, entry_price: float):
        """Log position opening."""
        self.audit(
            AuditAction.POSITION_OPENED,
            "position",
            position_id,
            {
                "symbol": symbol,
                "side": side,
                "size": size,
                "entry_price": entry_price,
            },
        )

    def position_closed(self, position_id: str, exit_price: float, pnl: float):
        """Log position closing."""
        self.audit(
            AuditAction.POSITION_CLOSED,
            "position",
            position_id,
            {
                "exit_price": exit_price,
                "pnl": pnl,
            },
        )

    def risk_breach(self, breach_type: str, limit: float, actual: float, action_taken: str):
        """Log risk limit breach."""
        self.audit(
            AuditAction.RISK_BREACH,
            "risk",
            breach_type,
            {
                "limit": limit,
                "actual": actual,
                "action_taken": action_taken,
            },
        )
        self.critical(
            f"Risk breach: {breach_type} - limit={limit}, actual={actual}",
            category=LogCategory.RISK,
        )

    def strategy_event(self, strategy_id: str, event: str, details: Dict[str, Any]):
        """Log strategy event."""
        action = AuditAction.STRATEGY_STARTED if event == "started" else AuditAction.STRATEGY_STOPPED
        self.audit(action, "strategy", strategy_id, details)


# =============================================================================
# Log Analyzer
# =============================================================================

class LogAnalyzer:
    """Analyze and aggregate logs."""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir

    def get_error_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of errors in time period."""
        cutoff = datetime.now() - timedelta(hours=hours)
        errors = []

        json_file = os.path.join(self.log_dir, "trading.json")
        if os.path.exists(json_file):
            with open(json_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        entry_time = datetime.fromisoformat(entry["timestamp"])
                        if entry_time >= cutoff and entry["level"] in ["ERROR", "CRITICAL"]:
                            errors.append(entry)
                    except Exception:
                        pass

        return {
            "total_errors": len(errors),
            "by_category": self._group_by(errors, "category"),
            "recent_errors": errors[-10:],
        }

    def get_audit_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of audit events."""
        cutoff = datetime.now() - timedelta(hours=hours)
        events = []

        audit_file = os.path.join(self.log_dir, "audit.log")
        if os.path.exists(audit_file):
            with open(audit_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        entry_time = datetime.fromisoformat(entry["timestamp"])
                        if entry_time >= cutoff:
                            events.append(entry)
                    except Exception:
                        pass

        return {
            "total_events": len(events),
            "by_action": self._group_by(events, "action"),
            "by_user": self._group_by(events, "user_id"),
        }

    def _group_by(self, items: List[Dict], key: str) -> Dict[str, int]:
        """Group items by key."""
        counts = {}
        for item in items:
            value = item.get(key, "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts


# =============================================================================
# Factory Functions
# =============================================================================

def create_logger(
    log_dir: str = "logs",
    log_level: LogLevel = LogLevel.INFO,
    async_logging: bool = True
) -> Logger:
    """Create standard logger."""
    config = LogConfig(
        log_dir=log_dir,
        log_level=log_level,
        async_logging=async_logging,
    )
    return Logger(config)


def create_trading_logger(
    log_dir: str = "logs",
    log_level: LogLevel = LogLevel.INFO
) -> TradingLogger:
    """Create trading logger."""
    config = LogConfig(
        log_dir=log_dir,
        log_level=log_level,
    )
    return TradingLogger(config)


# =============================================================================
# Global Logger Instance
# =============================================================================

_global_logger: Optional[Logger] = None


def get_logger() -> Logger:
    """Get global logger instance."""
    global _global_logger
    if _global_logger is None:
        _global_logger = create_logger()
    return _global_logger


def set_logger(logger: Logger):
    """Set global logger instance."""
    global _global_logger
    _global_logger = logger


# =============================================================================
# Testing
# =============================================================================

def test_audit_logging():
    """Test audit logging system."""
    import tempfile
    import shutil

    print("Testing Audit Logging System...")

    # Create temp directory
    test_dir = tempfile.mkdtemp()

    try:
        # Test basic logger
        print("\n1. Testing Basic Logger...")
        config = LogConfig(
            log_dir=test_dir,
            log_level=LogLevel.DEBUG,
            async_logging=False,
        )
        logger = Logger(config)

        logger.debug("Debug message", category=LogCategory.SYSTEM)
        logger.info("Info message", category=LogCategory.TRADING)
        logger.warning("Warning message", category=LogCategory.RISK)
        logger.error("Error message", category=LogCategory.ORDER, exc_info=False)

        print("   Basic logging passed!")

        # Test trading logger
        print("\n2. Testing Trading Logger...")
        trading_logger = TradingLogger(config)

        trading_logger.order_placed(
            "order_123",
            "BTC/USDT",
            "buy",
            0.1,
            50000.0,
            user_id="trader_1"
        )

        trading_logger.order_filled("order_123", 50000.0, 0.1, 5.0)
        trading_logger.position_opened("pos_123", "BTC/USDT", "long", 0.1, 50000.0)
        trading_logger.position_closed("pos_123", 51000.0, 100.0)

        print("   Trading logging passed!")

        # Test audit trail integrity
        print("\n3. Testing Audit Trail Integrity...")
        audit_handler = AuditFileHandler(test_dir)

        for i in range(5):
            entry = AuditEntry(
                id=f"audit_{i}",
                timestamp=datetime.now(),
                action=AuditAction.ORDER_PLACED,
                category=LogCategory.ORDER,
                user_id="test_user",
                resource_type="order",
                resource_id=f"order_{i}",
                details={"test": i},
            )
            audit_handler.handle_audit(entry)

        valid, errors = audit_handler.verify_chain()
        assert valid, f"Audit chain verification failed: {errors}"
        print("   Audit trail integrity verified!")

        # Test log analyzer
        print("\n4. Testing Log Analyzer...")
        analyzer = LogAnalyzer(test_dir)
        error_summary = analyzer.get_error_summary()
        audit_summary = analyzer.get_audit_summary()
        print(f"   Error summary: {error_summary['total_errors']} errors")
        print(f"   Audit summary: {audit_summary['total_events']} events")

        # Test async logging
        print("\n5. Testing Async Logging...")
        async_config = LogConfig(
            log_dir=test_dir,
            log_level=LogLevel.INFO,
            async_logging=True,
        )
        async_logger = Logger(async_config)

        for i in range(100):
            async_logger.info(f"Async message {i}", category=LogCategory.DATA)

        time.sleep(1)  # Wait for queue processing
        async_logger.close()
        print("   Async logging passed!")

        # Cleanup
        logger.close()
        trading_logger.close()

    finally:
        shutil.rmtree(test_dir)

    print("\n✓ All audit logging tests passed!")
    return True


if __name__ == "__main__":
    test_audit_logging()
