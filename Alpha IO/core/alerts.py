"""
Agentic Trading OS - Alert & Notification System.

A robust alert and notification system supporting multiple channels and
conditions for real-time trading notifications.

Alert Types:
------------
**Price Alerts:**
- PRICE_ABOVE - Triggers when price exceeds threshold
- PRICE_BELOW - Triggers when price drops below threshold
- PRICE_CROSS - Triggers on crossing a price level
- PERCENT_CHANGE - Triggers on percentage movement

**Technical Alerts:**
- RSI_OVERBOUGHT - RSI exceeds 70
- RSI_OVERSOLD - RSI drops below 30
- MACD_CROSS - MACD line crosses signal line
- VOLUME_SPIKE - Volume exceeds average by threshold

**Trade Alerts:**
- TRADE_EXECUTED - Order filled notification
- POSITION_OPENED - New position opened
- POSITION_CLOSED - Position closed
- STOP_LOSS_HIT - Stop loss triggered
- TAKE_PROFIT_HIT - Take profit reached

Notification Channels:
----------------------
- IN_APP - Web dashboard notifications
- EMAIL - SMTP email delivery
- DISCORD - Discord webhook integration
- SLACK - Slack webhook integration
- TELEGRAM - Telegram Bot API
- WEBHOOK - Custom HTTP webhooks

Usage:
------
    from core.alerts import get_alert_manager

    manager = get_alert_manager()

    # Create a price alert
    alert_id = manager.create_alert(
        name="AAPL Alert",
        symbol="AAPL",
        condition=AlertCondition(
            alert_type=AlertType.PRICE_ABOVE,
            symbol="AAPL",
            value=200.0
        ),
        channels=[AlertChannel.DISCORD, AlertChannel.EMAIL],
        message="AAPL has reached $200!"
    )

    # Check alerts against current prices
    manager.check_alerts({"AAPL": 201.50})

API Integration:
----------------
- GET /api/alerts - List all alerts
- POST /api/alerts - Create new alert
- DELETE /api/alerts/<id> - Remove alert
- POST /api/alerts/<id>/enable - Enable alert
- POST /api/alerts/<id>/disable - Disable alert
- GET /api/notifications - Get notification history

Author: Agentic Trading OS Team
Version: 2.0
"""

from __future__ import annotations

import os
import json
import time
import threading
import smtplib
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable
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
            with urllib.request.urlopen(req, timeout=10) as response:
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
        return hashlib.md5(f"{time.time()}".encode()).hexdigest()[:12]

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

        # Check all alerts for this symbol
        for alert in self.alerts.values():
            if alert.condition.symbol != symbol:
                continue
            if alert.status != AlertStatus.ACTIVE:
                continue

            # Check cooldown
            if time.time() - alert.last_trigger_time < alert.cooldown_seconds:
                continue

            # Check max triggers
            if alert.max_triggers > 0 and alert.trigger_count >= alert.max_triggers:
                alert.status = AlertStatus.EXPIRED
                continue

            # Check condition
            if alert.condition.check(price, previous):
                self._trigger_alert(alert, price)

    def _trigger_alert(self, alert: Alert, current_value: float):
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

        # Create notification
        notification = Notification(
            id=self._generate_id(),
            title=alert.name,
            message=message,
            type="warning",
            timestamp=datetime.now().isoformat(),
            alert_id=alert.id,
            data={"symbol": alert.condition.symbol, "price": current_value}
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
            except:
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
            except:
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
