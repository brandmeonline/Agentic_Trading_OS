"""
Professional logging framework for the trading system.

Provides structured logging with multiple outputs and levels.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import json


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


class TradingLogger:
    """
    Centralized logging for the trading system.

    Supports multiple log levels and outputs:
    - Console: Human-readable format
    - File: JSON structured logs
    - Trade log: Specialized trade event logging
    """

    _instance: Optional['TradingLogger'] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, log_dir: str = "logs", log_level: str = "INFO"):
        if self._initialized:
            return

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)

        # Main logger
        self.logger = logging.getLogger("trading_system")
        self.logger.setLevel(self.log_level)
        self.logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_format = logging.Formatter(
            "[%(asctime)s] %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)

        # File handler (JSON)
        log_file = self.log_dir / f"trading_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JSONFormatter())
        self.logger.addHandler(file_handler)

        # Trade-specific logger
        self.trade_logger = logging.getLogger("trades")
        self.trade_logger.setLevel(logging.INFO)
        trade_file = self.log_dir / f"trades_{datetime.now().strftime('%Y%m%d')}.log"
        trade_handler = logging.FileHandler(trade_file)
        trade_handler.setFormatter(JSONFormatter())
        self.trade_logger.addHandler(trade_handler)

        self._initialized = True

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self._log(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs) -> None:
        """Log critical message."""
        self._log(logging.CRITICAL, message, **kwargs)

    def _log(self, level: int, message: str, **kwargs) -> None:
        """Internal logging method with extra data support."""
        record = self.logger.makeRecord(
            self.logger.name, level, "", 0, message, (), None
        )
        if kwargs:
            record.extra_data = kwargs
        self.logger.handle(record)

    def log_signal(self, signal_text: str, confidence: float, asset: str, **kwargs) -> None:
        """Log a trading signal."""
        self.info(
            f"SIGNAL: {asset} | conf={confidence:.2f} | {signal_text[:50]}...",
            signal_type="signal",
            asset=asset,
            confidence=confidence,
            **kwargs
        )

    def log_trade(self, action: str, asset: str, size: float, price: float, **kwargs) -> None:
        """Log a trade execution."""
        self.trade_logger.info(
            f"TRADE: {action} {size} {asset} @ {price}",
            extra={"extra_data": {
                "action": action,
                "asset": asset,
                "size": size,
                "price": price,
                **kwargs
            }}
        )

    def log_risk_event(self, event_type: str, details: str, **kwargs) -> None:
        """Log a risk management event."""
        self.warning(
            f"RISK: {event_type} | {details}",
            risk_event=event_type,
            **kwargs
        )

    def log_performance(self, pnl: float, win_rate: float, trade_count: int, **kwargs) -> None:
        """Log performance metrics."""
        self.info(
            f"PERFORMANCE: P&L={pnl:.2f} | Win Rate={win_rate:.1%} | Trades={trade_count}",
            metric_type="performance",
            pnl=pnl,
            win_rate=win_rate,
            trade_count=trade_count,
            **kwargs
        )


# Global logger instance
_logger: Optional[TradingLogger] = None


def get_logger(log_dir: str = "logs", log_level: str = "INFO") -> TradingLogger:
    """Get or create the global logger instance."""
    global _logger
    if _logger is None:
        _logger = TradingLogger(log_dir, log_level)
    return _logger
