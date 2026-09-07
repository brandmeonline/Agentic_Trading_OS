"""
Agentic Trading OS - Alert & Notification System.

Comprehensive alerting with:
- Price alerts (above/below/cross)
- Technical indicator alerts
- Corpus-driven alerts (news volume, mention velocity, crowd sentiment,
  asymmetry), evaluated against the ingestion layer
- Trade execution notifications
- Webhook support (Discord, Slack, Telegram, custom)
- Email notifications
- In-app notifications
"""

from __future__ import annotations

import os
import json
import time
import threading
import smtplib
import urllib.request

from core.net_guard import assert_permitted
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Callable, Tuple
from enum import Enum
from datetime import datetime
from pathlib import Path
import hashlib


# =============================================================================
# Alert Types & Conditions
# =============================================================================

class AlertType(Enum):
    """Types of alerts."""
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PRICE_CROSS = "price_cross"
    PERCENT_CHANGE = "percent_change"
    VOLUME_SPIKE = "volume_spike"
    RSI_OVERBOUGHT = "rsi_overbought"
    RSI_OVERSOLD = "rsi_oversold"
    MACD_CROSS = "macd_cross"
    TRADE_EXECUTED = "trade_executed"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    CUSTOM = "custom"

    # Corpus-driven. Evaluated by update_from_corpus() against the ingestion
    # layer, never by update_price(). See docs/ULTRA_PLAN.md Phase 5.
    NEWS_MENTIONS = "news_mentions"
    MENTION_VELOCITY = "mention_velocity"
    CROWD_SENTIMENT = "crowd_sentiment"
    ASYMMETRY_ABOVE = "asymmetry_above"


#: Alert types the price path must not evaluate. Their units are not prices, so
#: feeding them a price would fire nonsense.
CORPUS_ALERT_TYPES = frozenset({
    AlertType.NEWS_MENTIONS,
    AlertType.MENTION_VELOCITY,
    AlertType.CROWD_SENTIMENT,
    AlertType.ASYMMETRY_ABOVE,
})


class Conviction(Enum):
    """How much corroboration sits behind a corpus alert.

    Source breadth, not volume: five stories from one outlet is one outlet's
    opinion, and three from three is a story.
    """
    SINGLE_SOURCE = "single_source"
    CORROBORATED = "corroborated"
    HIGH = "high"

    @classmethod
    def from_sources(cls, sources: int) -> "Conviction":
        if sources >= 4:
            return cls.HIGH
        if sources >= 2:
            return cls.CORROBORATED
        return cls.SINGLE_SOURCE


class AlertChannel(Enum):
    """Notification channels."""
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"
    DISCORD = "discord"
    SLACK = "slack"
    TELEGRAM = "telegram"


class AlertStatus(Enum):
    """Alert status."""
    ACTIVE = "active"
    TRIGGERED = "triggered"
    EXPIRED = "expired"
    DISABLED = "disabled"


@dataclass
class AlertCondition:
    """Alert condition definition."""
    alert_type: AlertType
    symbol: str
    value: float
    comparison: str = "gte"  # gte, lte, eq, cross_above, cross_below

    def check(self, current_value: float, previous_value: float = None) -> bool:
        """Check if condition is met."""
        if self.comparison == "gte":
            return current_value >= self.value
        elif self.comparison == "lte":
            return current_value <= self.value
        elif self.comparison == "eq":
            return abs(current_value - self.value) < 0.001
        elif self.comparison == "cross_above":
            if previous_value is None:
                return False
            return previous_value < self.value <= current_value
        elif self.comparison == "cross_below":
            if previous_value is None:
                return False
            return previous_value > self.value >= current_value
        return False


@dataclass
class Alert:
    """Alert definition."""
    id: str
    name: str
    condition: AlertCondition
    channels: List[AlertChannel]
    status: AlertStatus = AlertStatus.ACTIVE

    # Optional settings
    message: str = ""
    webhook_url: str = ""
    email_to: str = ""

    # Tracking
    created_at: str = ""
    triggered_at: str = ""
    trigger_count: int = 0
    max_triggers: int = 0  # 0 = unlimited
    cooldown_seconds: int = 60
    last_trigger_time: float = 0

    # For price tracking
    last_value: float = 0


@dataclass
class Notification:
    """In-app notification."""
    id: str
    title: str
    message: str
    type: str  # info, success, warning, error
    timestamp: str
    read: bool = False
    alert_id: str = ""
    data: Dict = field(default_factory=dict)


# =============================================================================
# Webhook Handlers
# =============================================================================

class WebhookHandler:
    """Handles webhook notifications."""

    @staticmethod
    def send_discord(webhook_url: str, title: str, message: str,
                     color: int = 0x00ff00) -> bool:
        """Send Discord webhook."""
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "Agentic Trading OS"}
            }]
        }
        return WebhookHandler._send_webhook(webhook_url, payload)

    @staticmethod
    def send_slack(webhook_url: str, title: str, message: str) -> bool:
        """Send Slack webhook."""
        payload = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message}
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"_Agentic Trading OS • {datetime.now().strftime('%H:%M:%S')}_"}
                    ]
                }
            ]
        }
        return WebhookHandler._send_webhook(webhook_url, payload)

    @staticmethod
    def send_telegram(bot_token: str, chat_id: str, message: str) -> bool:
        """Send Telegram message."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        return WebhookHandler._send_webhook(url, payload)

    @staticmethod
    def send_custom(webhook_url: str, payload: Dict) -> bool:
        """Send custom webhook."""
        return WebhookHandler._send_webhook(webhook_url, payload)

    @staticmethod
    def _send_webhook(url: str, payload: Dict) -> bool:
        """Send HTTP POST request."""
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            assert_permitted(req)
            # Scheme checked by assert_permitted() immediately above.
            with urllib.request.urlopen(req, timeout=10) as response:  # nosec B310
                return response.status == 200 or response.status == 204
        except Exception as e:
            print(f"Webhook error: {e}")
            return False


# =============================================================================
# Email Handler
# =============================================================================

class EmailHandler:
    """Handles email notifications."""

    def __init__(self, smtp_server: str = "", smtp_port: int = 587,
                 username: str = "", password: str = "", from_email: str = ""):
        self.smtp_server = smtp_server or os.environ.get("SMTP_SERVER", "")
        self.smtp_port = smtp_port
        self.username = username or os.environ.get("SMTP_USERNAME", "")
        self.password = password or os.environ.get("SMTP_PASSWORD", "")
        self.from_email = from_email or self.username

    def send(self, to_email: str, subject: str, body: str, html: bool = False) -> bool:
        """Send email."""
        if not self.smtp_server or not self.username:
            return False

        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = to_email

            if html:
                msg.attach(MIMEText(body, 'html'))
            else:
                msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.sendmail(self.from_email, to_email, msg.as_string())

            return True
        except Exception as e:
            print(f"Email error: {e}")
            return False


# =============================================================================
# Alert Manager
# =============================================================================

class AlertManager:
    """Manages all alerts and notifications."""

    def __init__(self, storage_path: str = "data/alerts.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        self.alerts: Dict[str, Alert] = {}
        self.notifications: List[Notification] = []
        self.price_cache: Dict[str, float] = {}
        # Last observed value per (corpus alert type, term), so that crossing
        # comparisons work across sweeps the same way price crossings do.
        self._corpus_cache: Dict[Tuple[str, str], float] = {}
        self._corpus_errors: Deque[str] = deque(maxlen=50)

        self.webhook_handler = WebhookHandler()
        self.email_handler = EmailHandler()

        self._callbacks: List[Callable[[Notification], None]] = []
        self._running = False
        self._check_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Load saved alerts
        self._load_alerts()

    def _generate_id(self) -> str:
        """Generate unique ID."""
        return hashlib.md5(
            f"{time.time()}".encode(), usedforsecurity=False
        ).hexdigest()[:12]

    # =========================================================================
    # Alert Management
    # =========================================================================

    def create_alert(
        self,
        name: str,
        alert_type: AlertType,
        symbol: str,
        value: float,
        channels: List[AlertChannel],
        comparison: str = "gte",
        message: str = "",
        webhook_url: str = "",
        email_to: str = "",
        max_triggers: int = 0,
        cooldown_seconds: int = 60
    ) -> Alert:
        """Create a new alert."""
        alert_id = self._generate_id()

        condition = AlertCondition(
            alert_type=alert_type,
            symbol=symbol,
            value=value,
            comparison=comparison
        )

        alert = Alert(
            id=alert_id,
            name=name,
            condition=condition,
            channels=channels,
            message=message or f"{symbol} alert: {alert_type.value} {value}",
            webhook_url=webhook_url,
            email_to=email_to,
            max_triggers=max_triggers,
            cooldown_seconds=cooldown_seconds,
            created_at=datetime.now().isoformat()
        )

        with self._lock:
            self.alerts[alert_id] = alert

        self._save_alerts()
        return alert

    def create_price_alert(
        self,
        symbol: str,
        price: float,
        direction: str = "above",  # above, below, cross
        channels: List[AlertChannel] = None,
        webhook_url: str = "",
        message: str = ""
    ) -> Alert:
        """Convenience method to create price alert."""
        if direction == "above":
            alert_type = AlertType.PRICE_ABOVE
            comparison = "gte"
        elif direction == "below":
            alert_type = AlertType.PRICE_BELOW
            comparison = "lte"
        else:
            alert_type = AlertType.PRICE_CROSS
            comparison = "cross_above"

        return self.create_alert(
            name=f"{symbol} {direction} ${price}",
            alert_type=alert_type,
            symbol=symbol,
            value=price,
            channels=channels or [AlertChannel.IN_APP],
            comparison=comparison,
            message=message or f"{symbol} is now {direction} ${price}",
            webhook_url=webhook_url
        )

    def get_alert(self, alert_id: str) -> Optional[Alert]:
        """Get alert by ID."""
        return self.alerts.get(alert_id)

    def list_alerts(self, status: AlertStatus = None) -> List[Alert]:
        """List all alerts, optionally filtered by status."""
        with self._lock:
            alerts = list(self.alerts.values())

        if status:
            alerts = [a for a in alerts if a.status == status]

        return alerts

    def update_alert(self, alert_id: str, **kwargs) -> Optional[Alert]:
        """Update alert properties."""
        alert = self.alerts.get(alert_id)
        if not alert:
            return None

        for key, value in kwargs.items():
            if hasattr(alert, key):
                setattr(alert, key, value)

        self._save_alerts()
        return alert

    def delete_alert(self, alert_id: str) -> bool:
        """Delete an alert."""
        with self._lock:
            if alert_id in self.alerts:
                del self.alerts[alert_id]
                self._save_alerts()
                return True
        return False

    def disable_alert(self, alert_id: str) -> bool:
        """Disable an alert."""
        return self.update_alert(alert_id, status=AlertStatus.DISABLED) is not None

    def enable_alert(self, alert_id: str) -> bool:
        """Enable an alert."""
        return self.update_alert(alert_id, status=AlertStatus.ACTIVE) is not None

    # =========================================================================
    # Price Checking
    # =========================================================================

    def update_price(self, symbol: str, price: float):
        """Update price and check alerts."""
        previous = self.price_cache.get(symbol)
        self.price_cache[symbol] = price

        for alert in self.alerts.values():
            if alert.condition.symbol != symbol:
                continue
            if alert.condition.alert_type in CORPUS_ALERT_TYPES:
                continue
            self._consider(alert, price, previous)

    def _consider(self, alert: Alert, value: float, previous: Optional[float] = None,
                  extra: Optional[Dict] = None) -> bool:
        """Gate one alert against a value and fire it if it passes.

        The single place status, cooldown, max-triggers and the condition are
        evaluated. Price updates and corpus updates both come through here, so
        a corpus alert honours exactly the same rate limits a price alert does.
        """
        if alert.status != AlertStatus.ACTIVE:
            return False

        if time.time() - alert.last_trigger_time < alert.cooldown_seconds:
            return False

        if alert.max_triggers > 0 and alert.trigger_count >= alert.max_triggers:
            alert.status = AlertStatus.EXPIRED
            return False

        if not alert.condition.check(value, previous):
            return False

        self._trigger_alert(alert, value, extra)
        return True

    def update_from_corpus(
        self,
        corpus,
        now=None,
        asymmetry_index=None,
        velocity_hours: float = 1.0,
        confidence: float = 0.85,
    ) -> List[str]:
        """Evaluate every corpus-driven alert against the ingestion layer.

        Shaped like ``update_price``: compute the metric, remember it so
        crossing comparisons work on the next sweep, and pass it through the
        same gate. Returns the ids of alerts that fired.

        Safe to call on a schedule — cooldown and max-triggers are enforced by
        ``_consider``, so a one-minute sweep does not mean a one-minute alert.
        """
        fired: List[str] = []

        for alert in list(self.alerts.values()):
            alert_type = alert.condition.alert_type
            if alert_type not in CORPUS_ALERT_TYPES:
                continue
            if alert.status != AlertStatus.ACTIVE:
                continue

            term = alert.condition.symbol
            try:
                value, extra = self._corpus_metric(
                    alert_type, corpus, term, now, asymmetry_index,
                    velocity_hours, confidence,
                )
            except Exception as exc:  # noqa: BLE001 - one bad alert must not stop the sweep
                self._corpus_errors.append(f"{alert.id}: {type(exc).__name__}: {exc}")
                continue

            cache_key = (alert_type.value, term)
            previous = self._corpus_cache.get(cache_key)
            self._corpus_cache[cache_key] = value

            if self._consider(alert, value, previous, extra):
                fired.append(alert.id)

        return fired

    def _corpus_metric(self, alert_type, corpus, term, now, asymmetry_index,
                       velocity_hours, confidence):
        """Compute one corpus metric and the provenance that goes with it."""
        stats = corpus.crowding(term, now)
        extra = {
            "term": term,
            "mentions": stats.mentions,
            "sources": stats.sources,
            "crowd_sentiment": round(stats.crowd_sentiment, 4),
            "conviction": Conviction.from_sources(stats.sources).value,
            "window_hours": stats.window_hours,
        }

        if alert_type == AlertType.NEWS_MENTIONS:
            return float(stats.mentions), extra

        if alert_type == AlertType.MENTION_VELOCITY:
            value = corpus.mention_velocity(term, hours=velocity_hours, now=now)
            extra["velocity_per_hour"] = round(value, 4)
            extra["velocity_window_hours"] = velocity_hours
            return value, extra

        if alert_type == AlertType.CROWD_SENTIMENT:
            return stats.crowd_sentiment, extra

        if alert_type == AlertType.ASYMMETRY_ABOVE:
            index = asymmetry_index
            if index is None:
                from core.asymmetry_index import AsymmetryIndex
                index = AsymmetryIndex(corpus=corpus)
            measurement = index.compute_measured(term, confidence, term=term)
            extra["asymmetry"] = measurement.score
            extra["measured"] = measurement.measured
            return measurement.score, extra

        raise ValueError(f"unhandled corpus alert type: {alert_type}")

    def corpus_errors(self) -> List[str]:
        """Errors from the last sweeps, newest last. Bounded."""
        return list(self._corpus_errors)

    def _trigger_alert(self, alert: Alert, current_value: float, extra: Optional[Dict] = None):
        """Trigger an alert."""
        alert.triggered_at = datetime.now().isoformat()
        alert.trigger_count += 1
        alert.last_trigger_time = time.time()
        alert.last_value = current_value

        # Format message
        message = alert.message.format(
            symbol=alert.condition.symbol,
            value=current_value,
            target=alert.condition.value
        )

        data = {"symbol": alert.condition.symbol, "price": current_value}
        if extra:
            # Corpus alerts carry their measurement and conviction so a channel
            # can differentiate a saturated story from a single-source rumour.
            data.update(extra)

        # Create notification
        notification = Notification(
            id=self._generate_id(),
            title=alert.name,
            message=message,
            type="warning",
            timestamp=datetime.now().isoformat(),
            alert_id=alert.id,
            data=data
        )

        self.notifications.append(notification)

        # Keep only last 100 notifications
        if len(self.notifications) > 100:
            self.notifications = self.notifications[-100:]

        # Send to channels
        for channel in alert.channels:
            self._send_notification(channel, alert, message, current_value)

        # Call registered callbacks
        for callback in self._callbacks:
            try:
                callback(notification)
            except Exception:
                # A notification callback that raised must not stop the
                # remaining callbacks. Exception, not bare: a bare except
                # here would swallow KeyboardInterrupt too.
                pass

        self._save_alerts()

    def _send_notification(self, channel: AlertChannel, alert: Alert,
                          message: str, value: float):
        """Send notification to channel."""
        title = f"🔔 {alert.name}"

        if channel == AlertChannel.DISCORD and alert.webhook_url:
            color = 0x00ff00 if "above" in alert.name.lower() else 0xff0000
            WebhookHandler.send_discord(alert.webhook_url, title, message, color)

        elif channel == AlertChannel.SLACK and alert.webhook_url:
            WebhookHandler.send_slack(alert.webhook_url, title, message)

        elif channel == AlertChannel.WEBHOOK and alert.webhook_url:
            payload = {
                "alert_id": alert.id,
                "alert_name": alert.name,
                "symbol": alert.condition.symbol,
                "value": value,
                "target": alert.condition.value,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
            WebhookHandler.send_custom(alert.webhook_url, payload)

        elif channel == AlertChannel.EMAIL and alert.email_to:
            self.email_handler.send(
                alert.email_to,
                f"Trading Alert: {alert.name}",
                message
            )

    # =========================================================================
    # Notification Management
    # =========================================================================

    def get_notifications(self, unread_only: bool = False,
                         limit: int = 50) -> List[Notification]:
        """Get notifications."""
        notifs = self.notifications
        if unread_only:
            notifs = [n for n in notifs if not n.read]
        return notifs[-limit:]

    def mark_read(self, notification_id: str):
        """Mark notification as read."""
        for notif in self.notifications:
            if notif.id == notification_id:
                notif.read = True
                break

    def mark_all_read(self):
        """Mark all notifications as read."""
        for notif in self.notifications:
            notif.read = True

    def get_unread_count(self) -> int:
        """Get count of unread notifications."""
        return sum(1 for n in self.notifications if not n.read)

    def register_callback(self, callback: Callable[[Notification], None]):
        """Register callback for new notifications."""
        self._callbacks.append(callback)

    # =========================================================================
    # Trade Notifications
    # =========================================================================

    def notify_trade(self, symbol: str, side: str, qty: float,
                    price: float, pnl: float = None):
        """Create trade notification."""
        pnl_str = f" (P&L: ${pnl:+.2f})" if pnl is not None else ""
        message = f"{side.upper()} {qty} {symbol} @ ${price:.2f}{pnl_str}"

        notification = Notification(
            id=self._generate_id(),
            title=f"Trade Executed: {symbol}",
            message=message,
            type="success" if (pnl or 0) >= 0 else "warning",
            timestamp=datetime.now().isoformat(),
            data={"symbol": symbol, "side": side, "qty": qty, "price": price, "pnl": pnl}
        )

        self.notifications.append(notification)

        for callback in self._callbacks:
            try:
                callback(notification)
            except Exception:
                # A notification callback that raised must not stop the
                # remaining callbacks. Exception, not bare: a bare except
                # here would swallow KeyboardInterrupt too.
                pass

    def notify_position_opened(self, symbol: str, side: str, qty: float, price: float):
        """Notify position opened."""
        self.notify_trade(symbol, side, qty, price)

    def notify_position_closed(self, symbol: str, qty: float,
                               entry_price: float, exit_price: float):
        """Notify position closed."""
        pnl = (exit_price - entry_price) * qty
        message = f"Closed {qty} {symbol}: Entry ${entry_price:.2f} → Exit ${exit_price:.2f}"

        notification = Notification(
            id=self._generate_id(),
            title=f"Position Closed: {symbol}",
            message=message,
            type="success" if pnl >= 0 else "error",
            timestamp=datetime.now().isoformat(),
            data={"symbol": symbol, "qty": qty, "entry": entry_price,
                  "exit": exit_price, "pnl": pnl}
        )

        self.notifications.append(notification)

    # =========================================================================
    # Persistence
    # =========================================================================

    def _save_alerts(self):
        """Save alerts to file."""
        try:
            data = {
                "alerts": {
                    aid: {
                        "id": a.id,
                        "name": a.name,
                        "condition": {
                            "alert_type": a.condition.alert_type.value,
                            "symbol": a.condition.symbol,
                            "value": a.condition.value,
                            "comparison": a.condition.comparison
                        },
                        "channels": [c.value for c in a.channels],
                        "status": a.status.value,
                        "message": a.message,
                        "webhook_url": a.webhook_url,
                        "email_to": a.email_to,
                        "created_at": a.created_at,
                        "triggered_at": a.triggered_at,
                        "trigger_count": a.trigger_count,
                        "max_triggers": a.max_triggers,
                        "cooldown_seconds": a.cooldown_seconds
                    }
                    for aid, a in self.alerts.items()
                }
            }

            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to save alerts: {e}")

    def _load_alerts(self):
        """Load alerts from file."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            for aid, a in data.get("alerts", {}).items():
                condition = AlertCondition(
                    alert_type=AlertType(a["condition"]["alert_type"]),
                    symbol=a["condition"]["symbol"],
                    value=a["condition"]["value"],
                    comparison=a["condition"]["comparison"]
                )

                alert = Alert(
                    id=a["id"],
                    name=a["name"],
                    condition=condition,
                    channels=[AlertChannel(c) for c in a["channels"]],
                    status=AlertStatus(a["status"]),
                    message=a.get("message", ""),
                    webhook_url=a.get("webhook_url", ""),
                    email_to=a.get("email_to", ""),
                    created_at=a.get("created_at", ""),
                    triggered_at=a.get("triggered_at", ""),
                    trigger_count=a.get("trigger_count", 0),
                    max_triggers=a.get("max_triggers", 0),
                    cooldown_seconds=a.get("cooldown_seconds", 60)
                )

                self.alerts[aid] = alert

        except Exception as e:
            print(f"Failed to load alerts: {e}")


# =============================================================================
# Factory Function
# =============================================================================

_global_manager: Optional[AlertManager] = None

def get_alert_manager() -> AlertManager:
    """Get or create global alert manager."""
    global _global_manager
    if _global_manager is None:
        _global_manager = AlertManager()
    return _global_manager


def create_alert_manager(storage_path: str = "data/alerts.json") -> AlertManager:
    """Create new alert manager."""
    return AlertManager(storage_path)
