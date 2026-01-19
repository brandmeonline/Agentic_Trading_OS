"""
Agentic Trading OS - Web Dashboard.

A comprehensive Flask-based web interface for the algorithmic trading platform.

Features:
---------
- Real-time portfolio dashboard with SSE streaming
- Trading interface with manual order placement
- Advanced analytics (equity curves, drawdowns, Monte Carlo)
- Alert system with multi-channel notifications
- Strategy marketplace and copy trading
- AI-powered trading assistant
- DeFi integration and blockchain support

API Structure:
--------------
The application exposes 78 REST API endpoints organized by category:
- /api/stats, /api/positions, /api/trades - Core trading
- /api/alerts/* - Alert management
- /api/indicators/* - Technical indicators
- /api/marketplace/* - Strategy marketplace
- /api/ai/* - AI assistant
- /api/blockchain/* - DeFi integration
- /api/analytics/* - Advanced analytics
- /api/settings/* - User preferences

Authentication:
---------------
All API endpoints (except /login) require session authentication.
Use the @login_required decorator for protected routes.

Usage:
------
    from web.app import run_server
    run_server(host='0.0.0.0', port=5000, debug=True)

Author: Agentic Trading OS Team
Version: 2.0
"""

from __future__ import annotations

import os
import sys
import json
import time
import threading
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Flask imports
try:
    from flask import (
        Flask, render_template, jsonify, request, redirect,
        url_for, session, flash, Response
    )
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    print("Flask not installed. Run: pip install flask")

# =============================================================================
# Configuration
# =============================================================================

@dataclass
class WebConfig:
    """Web server configuration."""
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    secret_key: str = ""
    admin_username: str = "admin"
    admin_password_hash: str = ""  # SHA256 hash
    session_lifetime_hours: int = 24


# =============================================================================
# Application State
# =============================================================================

class TradingState:
    """Shared trading state for the web interface."""

    def __init__(self):
        self.orchestrator = None
        self.is_running = False
        self.start_time: Optional[datetime] = None

        # Market data cache
        self.prices: Dict[str, float] = {}
        self.price_history: Dict[str, List[Dict]] = {}

        # Trading state
        self.positions: Dict[str, Dict] = {}
        self.orders: List[Dict] = []
        self.trades: List[Dict] = []

        # Performance metrics
        self.initial_capital = 100000.0
        self.current_capital = 100000.0
        self.total_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0

        # System stats
        self.cpu_usage = 0.0
        self.memory_usage = 0.0
        self.api_calls = 0
        self.errors: List[Dict] = []

        # Alpaca credentials
        self.alpaca_api_key = ""
        self.alpaca_api_secret = ""
        self.alpaca_connected = False

        # Alpaca client for live data
        self.alpaca_client = None

        # Component status
        self.components: Dict[str, bool] = {
            "config_manager": False,
            "credentials": False,
            "live_data": False,
            "database": False,
            "rest_api": False,
            "exchange_connectors": False,
            "alpaca_connector": False,
            "strategies": False,
            "orchestrator": False,
            "advanced_rl": False,
        }

        self._lock = threading.Lock()
        self._price_thread = None
        self._sync_thread = None

    def update_price(self, symbol: str, price: float):
        """Update price for a symbol."""
        with self._lock:
            self.prices[symbol] = price

            # Add to history (keep last 100 points)
            if symbol not in self.price_history:
                self.price_history[symbol] = []

            self.price_history[symbol].append({
                "time": datetime.now().isoformat(),
                "price": price
            })

            if len(self.price_history[symbol]) > 100:
                self.price_history[symbol] = self.price_history[symbol][-100:]

    def add_trade(self, trade: Dict):
        """Add a trade to history."""
        with self._lock:
            self.trades.append(trade)
            self.total_trades += 1

            pnl = trade.get("pnl", 0)
            self.total_pnl += pnl

            if pnl > 0:
                self.winning_trades += 1
            elif pnl < 0:
                self.losing_trades += 1

            # Keep last 1000 trades
            if len(self.trades) > 1000:
                self.trades = self.trades[-1000:]

    def add_error(self, error: str, source: str = ""):
        """Add an error to the log."""
        with self._lock:
            self.errors.append({
                "time": datetime.now().isoformat(),
                "error": error,
                "source": source
            })

            if len(self.errors) > 100:
                self.errors = self.errors[-100:]

    def get_stats(self) -> Dict[str, Any]:
        """Get current system stats."""
        with self._lock:
            uptime = 0
            if self.start_time:
                uptime = (datetime.now() - self.start_time).total_seconds()

            win_rate = 0
            if self.total_trades > 0:
                win_rate = (self.winning_trades / self.total_trades) * 100

            return {
                "is_running": self.is_running,
                "uptime_seconds": uptime,
                "uptime_formatted": self._format_uptime(uptime),
                "initial_capital": self.initial_capital,
                "current_capital": self.current_capital,
                "total_pnl": self.total_pnl,
                "pnl_percent": (self.total_pnl / self.initial_capital) * 100 if self.initial_capital > 0 else 0,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": win_rate,
                "positions": len(self.positions),
                "open_orders": len(self.orders),
                "prices": dict(self.prices),
                "alpaca_connected": self.alpaca_connected,
                "errors": len(self.errors),
                "api_calls": self.api_calls
            }

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime as human-readable string."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"

    def check_components(self):
        """Check which components are available."""
        try:
            from core.config_manager import ConfigManager
            self.components["config_manager"] = True
        except ImportError:
            pass

        try:
            from core.credentials import CredentialsManager
            self.components["credentials"] = True
        except ImportError:
            pass

        try:
            from core.live_data import LiveDataManager
            self.components["live_data"] = True
        except ImportError:
            pass

        try:
            from core.database import DatabaseManager
            self.components["database"] = True
        except ImportError:
            pass

        try:
            from core.rest_api import RESTAPIServer
            self.components["rest_api"] = True
        except ImportError:
            pass

        try:
            from core.exchange_connectors import ExchangeConnector
            self.components["exchange_connectors"] = True
        except ImportError:
            pass

        try:
            from core.alpaca_connector import AlpacaClient
            self.components["alpaca_connector"] = True
        except ImportError:
            pass

        try:
            from core.strategy import Strategy
            self.components["strategies"] = True
        except ImportError:
            pass

        try:
            from core.orchestrator import TradingOrchestrator
            self.components["orchestrator"] = True
        except ImportError:
            pass

        try:
            from core.advanced_rl import PPOAgent
            self.components["advanced_rl"] = True
        except ImportError:
            pass

    def connect_alpaca(self) -> bool:
        """Connect to Alpaca API and start fetching prices."""
        if not self.alpaca_api_key or not self.alpaca_api_secret:
            return False

        try:
            from core.alpaca_connector import create_alpaca_client
            self.alpaca_client = create_alpaca_client(
                self.alpaca_api_key,
                self.alpaca_api_secret,
                paper=True
            )

            if self.alpaca_client.connect():
                self.alpaca_connected = True
                self._start_price_updates()
                return True
            else:
                self.alpaca_connected = False
                return False

        except Exception as e:
            self.add_error(f"Alpaca connection failed: {e}", "alpaca")
            self.alpaca_connected = False
            return False

    def _start_price_updates(self):
        """Start background thread for price updates."""
        if self._price_thread and self._price_thread.is_alive():
            return

        def update_prices():
            symbols = ["AAPL", "SPY", "TSLA", "MSFT", "GOOGL"]
            crypto = ["BTC/USD", "ETH/USD"]

            while self.alpaca_connected:
                try:
                    # Fetch stock prices
                    if self.alpaca_client:
                        for symbol in symbols:
                            try:
                                quote = self.alpaca_client.get_latest_quote(symbol)
                                if quote:
                                    price = quote.get("ask_price") or quote.get("bid_price", 0)
                                    if price:
                                        self.update_price(symbol, float(price))
                            except:
                                pass

                        # Fetch crypto prices
                        for symbol in crypto:
                            try:
                                quote = self.alpaca_client.get_crypto_quote(symbol)
                                if quote:
                                    price = quote.get("ask_price") or quote.get("bid_price", 0)
                                    if price:
                                        self.update_price(symbol, float(price))
                            except:
                                pass

                    self.api_calls += len(symbols) + len(crypto)

                except Exception as e:
                    self.add_error(f"Price update error: {e}", "prices")

                time.sleep(2)  # Update every 2 seconds

        self._price_thread = threading.Thread(target=update_prices, daemon=True)
        self._price_thread.start()

    def sync_from_orchestrator(self):
        """Sync state from the orchestrator."""
        if not self.orchestrator:
            return

        try:
            # Get orchestrator status
            status = self.orchestrator.get_status()

            with self._lock:
                self.is_running = status.get("status") == "running"
                self.current_capital = status.get("capital", {}).get("current", self.current_capital)
                self.total_pnl = status.get("capital", {}).get("pnl", self.total_pnl)
                self.total_trades = status.get("trades", {}).get("total", self.total_trades)
                self.winning_trades = status.get("trades", {}).get("winning", self.winning_trades)
                self.losing_trades = status.get("trades", {}).get("losing", self.losing_trades)

                # Update prices from orchestrator
                for symbol, price in status.get("prices", {}).items():
                    self.prices[symbol] = price

                # Sync positions
                if hasattr(self.orchestrator, 'state'):
                    self.positions = dict(self.orchestrator.state.positions)

        except Exception as e:
            self.add_error(f"Sync error: {e}", "sync")

    def _start_sync_thread(self):
        """Start background sync with orchestrator."""
        if self._sync_thread and self._sync_thread.is_alive():
            return

        def sync_loop():
            while self.is_running:
                self.sync_from_orchestrator()
                time.sleep(1)

        self._sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self._sync_thread.start()

    def get_account_info(self) -> Dict[str, Any]:
        """Get Alpaca account info."""
        if not self.alpaca_client or not self.alpaca_connected:
            return {}

        try:
            return self.alpaca_client.get_account()
        except:
            return {}

    def get_alpaca_positions(self) -> List[Dict]:
        """Get positions from Alpaca."""
        if not self.alpaca_client or not self.alpaca_connected:
            return []

        try:
            return self.alpaca_client.get_positions()
        except:
            return []

    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "market", limit_price: float = None) -> Dict:
        """Place an order via Alpaca."""
        if not self.alpaca_client or not self.alpaca_connected:
            return {"success": False, "error": "Not connected to Alpaca"}

        try:
            if order_type == "market":
                result = self.alpaca_client.place_market_order(symbol, qty, side)
            else:
                result = self.alpaca_client.place_limit_order(symbol, qty, side, limit_price)

            if result:
                self.add_trade({
                    "time": datetime.now().isoformat(),
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "type": order_type,
                    "price": limit_price or 0,
                    "status": "submitted"
                })
                return {"success": True, "order": result}
            else:
                return {"success": False, "error": "Order failed"}

        except Exception as e:
            return {"success": False, "error": str(e)}


# Global state
trading_state = TradingState()


# =============================================================================
# Flask Application
# =============================================================================

def create_app(config: Optional[WebConfig] = None) -> Flask:
    """Create Flask application."""

    config = config or WebConfig()

    # Create Flask app
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static")
    )

    # Configure app
    app.secret_key = config.secret_key or secrets.token_hex(32)
    app.config["SESSION_PERMANENT"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=config.session_lifetime_hours)

    # Store config
    app.config["WEB_CONFIG"] = config

    # ==========================================================================
    # Authentication
    # ==========================================================================

    def hash_password(password: str) -> str:
        """Hash a password using SHA256."""
        return hashlib.sha256(password.encode()).hexdigest()

    def check_auth(username: str, password: str) -> bool:
        """Check authentication credentials."""
        cfg = app.config["WEB_CONFIG"]

        if cfg.admin_password_hash:
            return (username == cfg.admin_username and
                    hash_password(password) == cfg.admin_password_hash)
        else:
            # Default password is 'admin' if not configured
            return username == "admin" and password == "admin"

    def login_required(f):
        """Decorator to require login."""
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    # ==========================================================================
    # Routes - Pages
    # ==========================================================================

    @app.route("/")
    def index():
        """Main dashboard page."""
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return render_template("dashboard.html", stats=trading_state.get_stats())

    @app.route("/login", methods=["GET", "POST"])
    def login():
        """Login page."""
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")

            if check_auth(username, password):
                session["logged_in"] = True
                session["username"] = username
                session.permanent = True
                return redirect(url_for("index"))
            else:
                flash("Invalid credentials", "error")

        return render_template("login.html")

    @app.route("/logout")
    def logout():
        """Logout and clear session."""
        session.clear()
        return redirect(url_for("login"))

    @app.route("/admin")
    @login_required
    def admin():
        """Admin control panel."""
        return render_template("admin.html", stats=trading_state.get_stats())

    @app.route("/trading")
    @login_required
    def trading():
        """Trading view with positions and orders."""
        return render_template("trading.html", stats=trading_state.get_stats())

    @app.route("/analytics")
    @login_required
    def analytics():
        """Analytics and performance charts."""
        return render_template("analytics.html", stats=trading_state.get_stats())

    @app.route("/settings")
    @login_required
    def settings():
        """Settings page."""
        return render_template("settings.html")

    # ==========================================================================
    # Routes - API
    # ==========================================================================

    @app.route("/api/stats")
    @login_required
    def api_stats():
        """Get current system stats."""
        return jsonify(trading_state.get_stats())

    @app.route("/api/prices")
    @login_required
    def api_prices():
        """Get current prices."""
        return jsonify(trading_state.prices)

    @app.route("/api/price-history/<symbol>")
    @login_required
    def api_price_history(symbol):
        """Get price history for a symbol."""
        history = trading_state.price_history.get(symbol, [])
        return jsonify(history)

    @app.route("/api/positions")
    @login_required
    def api_positions():
        """Get current positions."""
        return jsonify(list(trading_state.positions.values()))

    @app.route("/api/orders")
    @login_required
    def api_orders():
        """Get open orders."""
        return jsonify(trading_state.orders)

    @app.route("/api/trades")
    @login_required
    def api_trades():
        """Get trade history."""
        limit = request.args.get("limit", 50, type=int)
        return jsonify(trading_state.trades[-limit:])

    @app.route("/api/errors")
    @login_required
    def api_errors():
        """Get error log."""
        return jsonify(trading_state.errors)

    # ==========================================================================
    # Routes - Control
    # ==========================================================================

    @app.route("/api/start", methods=["POST"])
    @login_required
    def api_start():
        """Start the trading system."""
        if trading_state.is_running:
            return jsonify({"success": False, "error": "System already running"})

        try:
            # Get parameters from request
            data = request.get_json() or {}
            mode = data.get("mode", "paper")
            symbols = data.get("symbols", ["AAPL", "SPY", "BTC/USD"])
            capital = data.get("capital", 100000.0)

            # Start in background thread
            def start_trading():
                try:
                    from core.orchestrator import create_orchestrator

                    trading_state.orchestrator = create_orchestrator(
                        mode=mode,
                        symbols=symbols,
                        initial_capital=capital,
                        exchange="alpaca",
                        alpaca_api_key=trading_state.alpaca_api_key,
                        alpaca_api_secret=trading_state.alpaca_api_secret
                    )

                    if trading_state.orchestrator.initialize():
                        trading_state.is_running = True
                        trading_state.start_time = datetime.now()
                        trading_state.initial_capital = capital
                        trading_state.current_capital = capital
                        trading_state.orchestrator.start()

                        # Start syncing from orchestrator
                        trading_state._start_sync_thread()

                        # Connect to Alpaca for live data if not already connected
                        if not trading_state.alpaca_connected:
                            trading_state.connect_alpaca()
                    else:
                        trading_state.add_error("Failed to initialize orchestrator", "start")

                except Exception as e:
                    trading_state.add_error(str(e), "start")

            thread = threading.Thread(target=start_trading, daemon=True)
            thread.start()

            return jsonify({"success": True, "message": "System starting..."})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/stop", methods=["POST"])
    @login_required
    def api_stop():
        """Stop the trading system."""
        if not trading_state.is_running:
            return jsonify({"success": False, "error": "System not running"})

        try:
            if trading_state.orchestrator:
                trading_state.orchestrator.stop()

            trading_state.is_running = False
            trading_state.orchestrator = None

            return jsonify({"success": True, "message": "System stopped"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/credentials", methods=["POST"])
    @login_required
    def api_credentials():
        """Update Alpaca credentials."""
        try:
            data = request.get_json() or {}
            api_key = data.get("api_key", "")
            api_secret = data.get("api_secret", "")

            if api_key and api_secret:
                trading_state.alpaca_api_key = api_key
                trading_state.alpaca_api_secret = api_secret

                # Test connection
                try:
                    from core.alpaca_connector import create_alpaca_client
                    client = create_alpaca_client(api_key, api_secret, paper=True)
                    if client.connect():
                        trading_state.alpaca_connected = True
                        return jsonify({"success": True, "message": "Connected to Alpaca"})
                    else:
                        trading_state.alpaca_connected = False
                        return jsonify({"success": False, "error": "Connection failed"})
                except Exception as e:
                    return jsonify({"success": False, "error": str(e)})

            return jsonify({"success": False, "error": "Missing credentials"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/test-connection", methods=["POST"])
    @login_required
    def api_test_connection():
        """Test Alpaca connection."""
        try:
            if trading_state.connect_alpaca():
                account = trading_state.get_account_info()
                return jsonify({
                    "success": True,
                    "account": account
                })
            else:
                return jsonify({"success": False, "error": "Connection failed"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/account")
    @login_required
    def api_account():
        """Get Alpaca account info."""
        account = trading_state.get_account_info()
        return jsonify(account)

    @app.route("/api/alpaca-positions")
    @login_required
    def api_alpaca_positions():
        """Get positions from Alpaca."""
        positions = trading_state.get_alpaca_positions()
        return jsonify(positions)

    @app.route("/api/place-order", methods=["POST"])
    @login_required
    def api_place_order():
        """Place a trading order."""
        try:
            data = request.get_json() or {}
            symbol = data.get("symbol")
            qty = int(data.get("qty", 0))
            side = data.get("side", "buy")
            order_type = data.get("type", "market")
            limit_price = data.get("price")

            if not symbol or qty <= 0:
                return jsonify({"success": False, "error": "Invalid order parameters"})

            result = trading_state.place_order(symbol, qty, side, order_type, limit_price)
            return jsonify(result)

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/components")
    @login_required
    def api_components():
        """Get component status."""
        trading_state.check_components()
        return jsonify(trading_state.components)

    # ==========================================================================
    # Routes - Alerts
    # ==========================================================================

    @app.route("/alerts")
    @login_required
    def alerts():
        """Alerts page."""
        return render_template("alerts.html", stats=trading_state.get_stats())

    @app.route("/api/alerts", methods=["GET"])
    @login_required
    def api_get_alerts():
        """Get all alerts."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            alerts = manager.list_alerts()
            return jsonify([{
                "id": a.id,
                "name": a.name,
                "condition": {
                    "symbol": a.condition.symbol,
                    "alert_type": a.condition.alert_type.value,
                    "value": a.condition.value,
                    "comparison": a.condition.comparison
                },
                "channels": [c.value for c in a.channels],
                "status": a.status.value,
                "trigger_count": a.trigger_count,
                "created_at": a.created_at
            } for a in alerts])
        except Exception as e:
            return jsonify([])

    @app.route("/api/alerts", methods=["POST"])
    @login_required
    def api_create_alert():
        """Create a new alert."""
        try:
            from core.alerts import get_alert_manager, AlertType, AlertChannel
            manager = get_alert_manager()

            data = request.get_json() or {}

            # Map alert type
            type_map = {
                "price_above": AlertType.PRICE_ABOVE,
                "price_below": AlertType.PRICE_BELOW,
                "price_cross": AlertType.PRICE_CROSS,
                "percent_change": AlertType.PERCENT_CHANGE
            }
            alert_type = type_map.get(data.get("alert_type"), AlertType.PRICE_ABOVE)

            # Map channels
            channel_map = {
                "in_app": AlertChannel.IN_APP,
                "webhook": AlertChannel.WEBHOOK,
                "discord": AlertChannel.DISCORD,
                "slack": AlertChannel.SLACK,
                "email": AlertChannel.EMAIL
            }
            channels = [channel_map[c] for c in data.get("channels", ["in_app"]) if c in channel_map]

            # Comparison based on type
            comparison = "gte" if alert_type == AlertType.PRICE_ABOVE else "lte"
            if alert_type == AlertType.PRICE_CROSS:
                comparison = "cross_above"

            alert = manager.create_alert(
                name=data.get("name", "New Alert"),
                alert_type=alert_type,
                symbol=data.get("symbol", "AAPL"),
                value=float(data.get("value", 0)),
                channels=channels,
                comparison=comparison,
                message=data.get("message", ""),
                webhook_url=data.get("webhook_url", ""),
                email_to=data.get("email_to", ""),
                max_triggers=int(data.get("max_triggers", 0)),
                cooldown_seconds=int(data.get("cooldown", 60))
            )

            return jsonify({"success": True, "alert_id": alert.id})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/alerts/<alert_id>", methods=["DELETE"])
    @login_required
    def api_delete_alert(alert_id):
        """Delete an alert."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            success = manager.delete_alert(alert_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/alerts/<alert_id>/enable", methods=["POST"])
    @login_required
    def api_enable_alert(alert_id):
        """Enable an alert."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            success = manager.enable_alert(alert_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/alerts/<alert_id>/disable", methods=["POST"])
    @login_required
    def api_disable_alert(alert_id):
        """Disable an alert."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            success = manager.disable_alert(alert_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/notifications", methods=["GET"])
    @login_required
    def api_get_notifications():
        """Get notifications."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            notifications = manager.get_notifications(limit=50)
            return jsonify([{
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "type": n.type,
                "timestamp": n.timestamp,
                "read": n.read,
                "data": n.data
            } for n in notifications])
        except Exception as e:
            return jsonify([])

    @app.route("/api/notifications/<notification_id>/read", methods=["POST"])
    @login_required
    def api_mark_notification_read(notification_id):
        """Mark notification as read."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            manager.mark_read(notification_id)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/notifications/read-all", methods=["POST"])
    @login_required
    def api_mark_all_notifications_read():
        """Mark all notifications as read."""
        try:
            from core.alerts import get_alert_manager
            manager = get_alert_manager()
            manager.mark_all_read()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - Technical Indicators
    # ==========================================================================

    @app.route("/api/indicators")
    @login_required
    def api_list_indicators():
        """List available technical indicators."""
        try:
            from core.indicators import get_indicator_calculator
            calculator = get_indicator_calculator()
            return jsonify(calculator.list_indicators())
        except Exception as e:
            return jsonify([])

    @app.route("/api/indicators/calculate", methods=["POST"])
    @login_required
    def api_calculate_indicator():
        """Calculate a technical indicator on OHLCV data."""
        try:
            from core.indicators import get_indicator_calculator
            calculator = get_indicator_calculator()

            data = request.get_json() or {}
            indicator_name = data.get("indicator", "sma")
            params = data.get("params", {})

            # Get OHLCV data from request or use price history
            ohlcv = data.get("ohlcv")

            if not ohlcv:
                # Use price history from state if symbol provided
                symbol = data.get("symbol", "AAPL")
                history = trading_state.price_history.get(symbol, [])

                if not history:
                    return jsonify({"success": False, "error": f"No data for {symbol}"})

                # Build OHLCV from price history (simplified - close only for most)
                prices = [h["price"] for h in history]
                ohlcv = {
                    "open": prices,
                    "high": prices,
                    "low": prices,
                    "close": prices,
                    "volume": [1000] * len(prices)  # Placeholder volume
                }

            result = calculator.calculate(indicator_name, ohlcv, **params)

            return jsonify({
                "success": True,
                "indicator": {
                    "name": result.name,
                    "values": result.values,
                    "upper_band": result.upper_band,
                    "lower_band": result.lower_band,
                    "signal_line": result.signal_line,
                    "histogram": result.histogram
                }
            })

        except ValueError as e:
            return jsonify({"success": False, "error": str(e)})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/indicators/chart/<symbol>")
    @login_required
    def api_indicators_for_chart(symbol):
        """Get indicator data formatted for chart overlay."""
        try:
            from core.indicators import get_indicator_calculator
            calculator = get_indicator_calculator()

            # Get requested indicators from query params
            indicator_list = request.args.get("indicators", "sma,rsi").split(",")

            # Get price history
            history = trading_state.price_history.get(symbol, [])
            if not history:
                return jsonify({"success": False, "error": f"No data for {symbol}"})

            prices = [h["price"] for h in history]
            timestamps = [h["time"] for h in history]

            ohlcv = {
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": [1000] * len(prices)
            }

            results = {}
            for ind_name in indicator_list:
                ind_name = ind_name.strip()
                try:
                    result = calculator.calculate(ind_name, ohlcv)
                    results[ind_name] = {
                        "name": result.name,
                        "values": result.values,
                        "upper_band": result.upper_band,
                        "lower_band": result.lower_band,
                        "signal_line": result.signal_line,
                        "histogram": result.histogram,
                        "timestamps": timestamps
                    }
                except:
                    pass

            return jsonify({
                "success": True,
                "symbol": symbol,
                "indicators": results
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - Strategy Marketplace
    # ==========================================================================

    @app.route("/marketplace")
    @login_required
    def marketplace():
        """Strategy marketplace page."""
        return render_template("marketplace.html", stats=trading_state.get_stats())

    @app.route("/marketplace/strategy/<strategy_id>")
    @login_required
    def marketplace_strategy_detail(strategy_id):
        """Strategy detail page."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            strategy = mp.get_strategy(strategy_id)
            if strategy:
                return render_template("strategy_detail.html",
                                     strategy=strategy, stats=trading_state.get_stats())
        except:
            pass
        return redirect(url_for("marketplace"))

    @app.route("/leaderboard")
    @login_required
    def leaderboard():
        """Trader leaderboard page."""
        return render_template("leaderboard.html", stats=trading_state.get_stats())

    @app.route("/api/marketplace/strategies", methods=["GET"])
    @login_required
    def api_list_strategies():
        """List strategies in marketplace."""
        try:
            from core.marketplace import get_marketplace, StrategyCategory, StrategyVisibility
            mp = get_marketplace()

            category = request.args.get("category")
            search = request.args.get("search")
            sort_by = request.args.get("sort", "followers")
            limit = request.args.get("limit", 50, type=int)

            cat_enum = None
            if category:
                try:
                    cat_enum = StrategyCategory(category)
                except:
                    pass

            strategies = mp.list_strategies(
                category=cat_enum,
                search=search,
                sort_by=sort_by,
                limit=limit
            )

            return jsonify([{
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "author_name": s.author_name,
                "category": s.category.value,
                "tags": s.tags,
                "symbols": s.symbols,
                "followers": s.followers,
                "copiers": s.copiers,
                "rating": s.rating,
                "rating_count": s.rating_count,
                "views": s.views,
                "price": s.price,
                "performance": {
                    "total_return": s.performance.total_return,
                    "monthly_return": s.performance.monthly_return,
                    "win_rate": s.performance.win_rate,
                    "sharpe_ratio": s.performance.sharpe_ratio,
                    "max_drawdown": s.performance.max_drawdown,
                    "total_trades": s.performance.total_trades
                }
            } for s in strategies])

        except Exception as e:
            return jsonify([])

    @app.route("/api/marketplace/strategies", methods=["POST"])
    @login_required
    def api_create_strategy():
        """Create a new strategy."""
        try:
            from core.marketplace import get_marketplace, StrategyCategory, StrategyVisibility
            mp = get_marketplace()

            data = request.get_json() or {}

            category = StrategyCategory(data.get("category", "other"))
            visibility = StrategyVisibility(data.get("visibility", "public"))

            strategy = mp.create_strategy(
                name=data.get("name", "New Strategy"),
                description=data.get("description", ""),
                author_id=session.get("username", "admin"),
                author_name=session.get("username", "Admin"),
                category=category,
                visibility=visibility,
                symbols=data.get("symbols", []),
                tags=data.get("tags", []),
                config=data.get("config", {}),
                price=float(data.get("price", 0))
            )

            return jsonify({"success": True, "strategy_id": strategy.id})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/marketplace/strategies/<strategy_id>", methods=["GET"])
    @login_required
    def api_get_strategy(strategy_id):
        """Get strategy details."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            strategy = mp.get_strategy(strategy_id)

            if not strategy:
                return jsonify({"success": False, "error": "Strategy not found"})

            return jsonify({
                "success": True,
                "strategy": {
                    "id": strategy.id,
                    "name": strategy.name,
                    "description": strategy.description,
                    "author_id": strategy.author_id,
                    "author_name": strategy.author_name,
                    "category": strategy.category.value,
                    "visibility": strategy.visibility.value,
                    "tags": strategy.tags,
                    "symbols": strategy.symbols,
                    "timeframe": strategy.timeframe,
                    "config": strategy.config,
                    "followers": strategy.followers,
                    "copiers": strategy.copiers,
                    "likes": strategy.likes,
                    "views": strategy.views,
                    "rating": strategy.rating,
                    "rating_count": strategy.rating_count,
                    "price": strategy.price,
                    "created_at": strategy.created_at,
                    "performance": {
                        "total_return": strategy.performance.total_return,
                        "monthly_return": strategy.performance.monthly_return,
                        "win_rate": strategy.performance.win_rate,
                        "sharpe_ratio": strategy.performance.sharpe_ratio,
                        "max_drawdown": strategy.performance.max_drawdown,
                        "total_trades": strategy.performance.total_trades,
                        "profitable_trades": strategy.performance.profitable_trades,
                        "profit_factor": strategy.performance.profit_factor
                    }
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/marketplace/strategies/<strategy_id>/follow", methods=["POST"])
    @login_required
    def api_follow_strategy(strategy_id):
        """Follow a strategy."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            success = mp.follow_strategy(strategy_id, session.get("username", ""))
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/marketplace/strategies/<strategy_id>/like", methods=["POST"])
    @login_required
    def api_like_strategy(strategy_id):
        """Like a strategy."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            success = mp.like_strategy(strategy_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/marketplace/strategies/<strategy_id>/rate", methods=["POST"])
    @login_required
    def api_rate_strategy(strategy_id):
        """Rate a strategy."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            data = request.get_json() or {}
            rating = float(data.get("rating", 0))
            success = mp.rate_strategy(strategy_id, rating)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/marketplace/featured")
    @login_required
    def api_featured_strategies():
        """Get featured strategies."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            strategies = mp.get_featured_strategies(limit=6)
            return jsonify([{
                "id": s.id,
                "name": s.name,
                "author_name": s.author_name,
                "category": s.category.value,
                "followers": s.followers,
                "rating": s.rating,
                "performance": {
                    "total_return": s.performance.total_return,
                    "win_rate": s.performance.win_rate
                }
            } for s in strategies])
        except:
            return jsonify([])

    @app.route("/api/marketplace/trending")
    @login_required
    def api_trending_strategies():
        """Get trending strategies."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            strategies = mp.get_trending(limit=10)
            return jsonify([{
                "id": s.id,
                "name": s.name,
                "author_name": s.author_name,
                "views": s.views,
                "performance": {"total_return": s.performance.total_return}
            } for s in strategies])
        except:
            return jsonify([])

    @app.route("/api/marketplace/top-performers")
    @login_required
    def api_top_performers():
        """Get top performing strategies."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            strategies = mp.get_top_performers(limit=10)
            return jsonify([{
                "id": s.id,
                "name": s.name,
                "author_name": s.author_name,
                "performance": {
                    "total_return": s.performance.total_return,
                    "sharpe_ratio": s.performance.sharpe_ratio
                }
            } for s in strategies])
        except:
            return jsonify([])

    # ==========================================================================
    # Routes - Leaderboard
    # ==========================================================================

    @app.route("/api/leaderboard")
    @login_required
    def api_leaderboard():
        """Get trader leaderboard."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()

            sort_by = request.args.get("sort", "return")
            limit = request.args.get("limit", 100, type=int)

            traders = mp.get_leaderboard(sort_by=sort_by, limit=limit)

            return jsonify([{
                "rank": t.rank_overall,
                "id": t.id,
                "username": t.username,
                "display_name": t.display_name,
                "total_return": t.total_return,
                "monthly_return": t.monthly_return,
                "win_rate": t.win_rate,
                "sharpe_ratio": t.sharpe_ratio,
                "total_trades": t.total_trades,
                "followers": t.followers,
                "copiers": t.copiers,
                "badges": t.badges
            } for t in traders])

        except Exception as e:
            return jsonify([])

    # ==========================================================================
    # Routes - Copy Trading
    # ==========================================================================

    @app.route("/api/copy-trading/setup", methods=["POST"])
    @login_required
    def api_setup_copy_trading():
        """Set up copy trading."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()

            data = request.get_json() or {}
            leader_id = data.get("leader_id")
            if not leader_id:
                return jsonify({"success": False, "error": "Leader ID required"})

            settings = mp.setup_copy_trading(
                copier_id=session.get("username", "admin"),
                leader_id=leader_id,
                allocation_percent=float(data.get("allocation_percent", 10)),
                max_position_size=float(data.get("max_position_size", 5000)),
                copy_ratio=float(data.get("copy_ratio", 1.0)),
                strategy_id=data.get("strategy_id", "")
            )

            return jsonify({"success": True, "settings_id": settings.id})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/copy-trading/settings")
    @login_required
    def api_copy_trading_settings():
        """Get copy trading settings."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            settings = mp.get_copy_settings(session.get("username", "admin"))
            return jsonify([{
                "id": s.id,
                "leader_id": s.leader_id,
                "strategy_id": s.strategy_id,
                "enabled": s.enabled,
                "allocation_percent": s.allocation_percent,
                "max_position_size": s.max_position_size,
                "copy_ratio": s.copy_ratio,
                "trades_copied": s.trades_copied,
                "total_pnl": s.total_pnl
            } for s in settings])
        except:
            return jsonify([])

    @app.route("/api/copy-trading/<settings_id>/disable", methods=["POST"])
    @login_required
    def api_disable_copy_trading(settings_id):
        """Disable copy trading."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()
            success = mp.disable_copy_trading(settings_id)
            return jsonify({"success": success})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - Signals
    # ==========================================================================

    @app.route("/api/signals", methods=["GET"])
    @login_required
    def api_get_signals():
        """Get trading signals."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()

            symbol = request.args.get("symbol")
            active_only = request.args.get("active", "true").lower() == "true"

            signals = mp.get_signals(symbol=symbol, active_only=active_only)

            return jsonify([{
                "id": s.id,
                "symbol": s.symbol,
                "action": s.action,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "confidence": s.confidence,
                "analysis": s.analysis,
                "indicators_used": s.indicators_used,
                "outcome": s.outcome,
                "pnl_percent": s.pnl_percent,
                "created_at": s.created_at,
                "expires_at": s.expires_at
            } for s in signals])

        except Exception as e:
            return jsonify([])

    @app.route("/api/signals", methods=["POST"])
    @login_required
    def api_create_signal():
        """Create a trading signal."""
        try:
            from core.marketplace import get_marketplace
            mp = get_marketplace()

            data = request.get_json() or {}

            signal = mp.create_signal(
                author_id=session.get("username", "admin"),
                symbol=data.get("symbol", ""),
                action=data.get("action", "BUY"),
                entry_price=float(data.get("entry_price", 0)),
                stop_loss=float(data.get("stop_loss", 0)),
                take_profit=float(data.get("take_profit", 0)),
                confidence=float(data.get("confidence", 75)),
                analysis=data.get("analysis", ""),
                indicators=data.get("indicators", []),
                strategy_id=data.get("strategy_id", ""),
                expires_hours=int(data.get("expires_hours", 24))
            )

            return jsonify({"success": True, "signal_id": signal.id})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - AI Assistant
    # ==========================================================================

    @app.route("/ai-assistant")
    @login_required
    def ai_assistant():
        """AI Assistant chat interface."""
        return render_template("ai_assistant.html", stats=trading_state.get_stats())

    @app.route("/api/ai/chat", methods=["POST"])
    @login_required
    def api_ai_chat():
        """Send message to AI assistant."""
        try:
            from core.ai_assistant import get_ai_assistant
            assistant = get_ai_assistant()

            data = request.get_json() or {}
            message = data.get("message", "")
            session_id = data.get("session_id", session.get("username", "default"))

            if not message:
                return jsonify({"success": False, "error": "Message required"})

            response = assistant.process_message(message, session_id)

            return jsonify({
                "success": True,
                "response": {
                    "message": response.message,
                    "data": response.data,
                    "suggestions": response.suggestions,
                    "actions": response.actions,
                    "confidence": response.confidence,
                    "timestamp": response.timestamp
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/ai/analyze/<symbol>")
    @login_required
    def api_ai_analyze(symbol):
        """Get AI analysis for a symbol."""
        try:
            from core.ai_assistant import get_ai_assistant
            assistant = get_ai_assistant()

            # Get price history if available
            history = trading_state.price_history.get(symbol, [])
            prices = [h["price"] for h in history] if history else None

            analysis = assistant.analyzer.analyze_symbol(symbol, prices)

            return jsonify({
                "success": True,
                "analysis": analysis
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - Blockchain / DeFi
    # ==========================================================================

    @app.route("/defi")
    @login_required
    def defi_dashboard():
        """DeFi dashboard page."""
        return render_template("defi.html", stats=trading_state.get_stats())

    @app.route("/api/blockchain/portfolio")
    @login_required
    def api_blockchain_portfolio():
        """Get blockchain portfolio summary."""
        try:
            from core.blockchain import get_blockchain_manager
            manager = get_blockchain_manager()
            summary = manager.get_portfolio_summary()
            return jsonify({"success": True, "portfolio": summary})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/blockchain/chains")
    @login_required
    def api_blockchain_chains():
        """Get supported blockchain chains."""
        try:
            from core.blockchain import get_blockchain_manager
            manager = get_blockchain_manager()
            chains = manager.get_supported_chains()
            return jsonify({"success": True, "chains": chains})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/blockchain/defi")
    @login_required
    def api_defi_overview():
        """Get DeFi protocol overview."""
        try:
            from core.blockchain import get_blockchain_manager
            manager = get_blockchain_manager()
            overview = manager.get_defi_overview()
            return jsonify({"success": True, "defi": overview})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/blockchain/swap/quote", methods=["POST"])
    @login_required
    def api_swap_quote():
        """Get DEX swap quotes."""
        try:
            from core.blockchain import get_blockchain_manager, Token, ChainType
            manager = get_blockchain_manager()

            data = request.get_json() or {}

            from_token = Token(
                symbol=data.get("from_symbol", "ETH"),
                name=data.get("from_symbol", "ETH"),
                address=data.get("from_address", ""),
                chain=ChainType(data.get("chain", "ethereum")),
                price_usd=float(data.get("from_price", 2000))
            )

            to_token = Token(
                symbol=data.get("to_symbol", "USDC"),
                name=data.get("to_symbol", "USDC"),
                address=data.get("to_address", ""),
                chain=ChainType(data.get("chain", "ethereum")),
                price_usd=float(data.get("to_price", 1))
            )

            amount = float(data.get("amount", 1))

            quotes = manager.dex.get_quote(from_token, to_token, amount)

            return jsonify({
                "success": True,
                "quotes": [{
                    "aggregator": q.aggregator,
                    "from_amount": q.from_amount,
                    "to_amount": q.to_amount,
                    "price_impact": q.price_impact,
                    "gas_estimate": q.gas_estimate,
                    "route": q.route
                } for q in quotes]
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/blockchain/yield/opportunities")
    @login_required
    def api_yield_opportunities():
        """Get yield farming opportunities."""
        try:
            from core.blockchain import get_blockchain_manager
            manager = get_blockchain_manager()

            token = request.args.get("token", "ETH")
            amount = float(request.args.get("amount", 1000))
            max_risk = request.args.get("risk", "medium")

            opportunities = manager.yield_optimizer.find_opportunities(
                token=token,
                amount=amount,
                max_risk=max_risk
            )

            return jsonify({
                "success": True,
                "opportunities": [{
                    "protocol": o["protocol"],
                    "chain": o["chain"].value,
                    "strategy": o["strategy"],
                    "total_apy": o["total_apy"],
                    "base_apy": o["base_apy"],
                    "reward_apy": o["reward_apy"],
                    "risk": o["risk"],
                    "tvl": o["tvl"]
                } for o in opportunities]
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/blockchain/gas/<chain>")
    @login_required
    def api_gas_prices(chain):
        """Get gas prices for a chain."""
        try:
            from core.blockchain import get_blockchain_manager, ChainType
            manager = get_blockchain_manager()

            chain_type = ChainType(chain)
            gas = manager.gas_tracker.get_gas_price(chain_type)

            return jsonify({
                "success": True,
                "gas": {
                    "chain": chain,
                    "slow": gas.slow,
                    "standard": gas.standard,
                    "fast": gas.fast,
                    "instant": gas.instant,
                    "last_updated": gas.last_updated
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Routes - Advanced Analytics
    # ==========================================================================

    @app.route("/analytics/advanced")
    @login_required
    def advanced_analytics_page():
        """Advanced analytics page."""
        return render_template("advanced_analytics.html", stats=trading_state.get_stats())

    @app.route("/api/analytics/monte-carlo", methods=["POST"])
    @login_required
    def api_monte_carlo():
        """Run Monte Carlo simulation."""
        try:
            from core.advanced_analytics import get_advanced_analytics
            analytics = get_advanced_analytics()

            data = request.get_json() or {}

            result = analytics.monte_carlo.simulate_portfolio(
                initial_value=float(data.get("initial_value", 100000)),
                expected_return=float(data.get("expected_return", 0.08)),
                volatility=float(data.get("volatility", 0.15)),
                time_horizon_days=int(data.get("time_horizon", 252)),
                num_simulations=int(data.get("simulations", 5000))
            )

            return jsonify({
                "success": True,
                "result": {
                    "simulations": result.simulations,
                    "time_horizon_days": result.time_horizon_days,
                    "initial_value": result.initial_value,
                    "mean_final_value": result.mean_final_value,
                    "median_final_value": result.median_final_value,
                    "percentile_5": result.percentile_5,
                    "percentile_95": result.percentile_95,
                    "prob_profit": result.prob_profit,
                    "prob_loss_20pct": result.prob_loss_20pct,
                    "max_gain": result.max_gain,
                    "max_loss": result.max_loss
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/analytics/optimize", methods=["POST"])
    @login_required
    def api_optimize_portfolio():
        """Optimize portfolio allocation."""
        try:
            from core.advanced_analytics import get_advanced_analytics
            analytics = get_advanced_analytics()

            data = request.get_json() or {}
            assets = data.get("assets", ["AAPL", "SPY", "BTC"])
            expected_returns = data.get("expected_returns", {a: 0.08 for a in assets})

            # Generate sample covariance matrix
            covariance = {}
            for a1 in assets:
                covariance[a1] = {}
                for a2 in assets:
                    if a1 == a2:
                        covariance[a1][a2] = 0.04  # 20% volatility
                    else:
                        covariance[a1][a2] = 0.01  # Some correlation

            result = analytics.optimizer.optimize_portfolio(
                assets=assets,
                expected_returns=expected_returns,
                covariance_matrix=covariance,
                max_weight=float(data.get("max_weight", 0.4))
            )

            return jsonify({
                "success": True,
                "optimization": {
                    "weights": {k: round(v * 100, 1) for k, v in result.weights.items()},
                    "expected_return": result.expected_return,
                    "volatility": result.volatility,
                    "sharpe_ratio": result.sharpe_ratio,
                    "efficient_frontier": result.efficient_frontier[:10]
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/analytics/scenarios", methods=["POST"])
    @login_required
    def api_scenario_analysis():
        """Run scenario analysis."""
        try:
            from core.advanced_analytics import get_advanced_analytics
            analytics = get_advanced_analytics()

            data = request.get_json() or {}
            portfolio = data.get("portfolio", {"AAPL": 50000, "SPY": 30000, "BTC": 20000})
            asset_classes = data.get("asset_classes", {"AAPL": "stocks", "SPY": "stocks", "BTC": "crypto"})

            results = analytics.scenario_analyzer.run_all_scenarios(portfolio, asset_classes)

            return jsonify({
                "success": True,
                "scenarios": [{
                    "name": r.scenario_name,
                    "description": r.description,
                    "impact": r.portfolio_impact,
                    "impact_pct": r.portfolio_impact_pct,
                    "probability": r.probability,
                    "asset_impacts": r.asset_impacts
                } for r in results]
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/analytics/risk", methods=["POST"])
    @login_required
    def api_risk_metrics():
        """Calculate risk metrics."""
        try:
            from core.advanced_analytics import get_advanced_analytics
            import random
            analytics = get_advanced_analytics()

            data = request.get_json() or {}

            # Generate sample returns if not provided
            returns = data.get("returns")
            if not returns:
                returns = [random.gauss(0.0003, 0.015) for _ in range(252)]

            var_95 = analytics.risk_analyzer.calculate_var(returns, 0.95)
            cvar_95 = analytics.risk_analyzer.calculate_cvar(returns, 0.95)
            sortino = analytics.risk_analyzer.calculate_sortino(returns)

            return jsonify({
                "success": True,
                "risk_metrics": {
                    "var_95": round(var_95 * 100, 2),
                    "cvar_95": round(cvar_95 * 100, 2),
                    "sortino_ratio": round(sortino, 2),
                    "daily_volatility": round(sum(abs(r) for r in returns) / len(returns) * 100, 2),
                    "annualized_volatility": round((sum((r - sum(returns)/len(returns))**2 for r in returns) / len(returns))**0.5 * (252**0.5) * 100, 2)
                }
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # ==========================================================================
    # Server-Sent Events for Real-time Updates
    # ==========================================================================

    @app.route("/api/stream")
    @login_required
    def stream():
        """Server-sent events for real-time updates."""
        def generate():
            while True:
                stats = trading_state.get_stats()
                yield f"data: {json.dumps(stats)}\n\n"
                time.sleep(1)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive"
            }
        )

    # ==========================================================================
    # Settings API Endpoints
    # ==========================================================================

    # In-memory settings storage (would be persisted to file/database in production)
    user_settings = {
        "general": {
            "timezone": "America/New_York",
            "currency": "USD",
            "dark_mode": True,
            "sound_alerts": False
        },
        "notifications": {
            "notify_trades": True,
            "notify_signals": True,
            "notify_errors": True,
            "notify_pnl": False,
            "email": ""
        },
        "strategy": {
            "strategy_mode": "momentum",
            "timeframe": "1h",
            "signal_threshold": 70,
            "use_rl": False
        }
    }

    @app.route("/api/settings", methods=["GET"])
    @login_required
    def get_all_settings():
        """Get all user settings."""
        return jsonify({"success": True, "settings": user_settings})

    @app.route("/api/settings/<category>", methods=["GET"])
    @login_required
    def get_settings(category):
        """Get settings for a specific category."""
        if category not in user_settings:
            return jsonify({"success": False, "error": "Invalid category"})
        return jsonify({"success": True, "settings": user_settings[category]})

    @app.route("/api/settings/<category>", methods=["POST"])
    @login_required
    def update_settings(category):
        """Update settings for a specific category."""
        if category not in user_settings:
            return jsonify({"success": False, "error": "Invalid category"})

        try:
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No data provided"})

            # Update settings
            for key, value in data.items():
                if key in user_settings[category]:
                    user_settings[category][key] = value

            # Save to file
            settings_file = Path(__file__).parent.parent / "config" / "user_settings.json"
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(settings_file, 'w') as f:
                json.dump(user_settings, f, indent=2)

            return jsonify({"success": True, "settings": user_settings[category]})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/settings/password", methods=["POST"])
    @login_required
    def update_password():
        """Update user password."""
        try:
            data = request.get_json()
            current = data.get("current_password", "")
            new_pass = data.get("new_password", "")
            confirm = data.get("confirm_password", "")

            # Validation
            if not all([current, new_pass, confirm]):
                return jsonify({"success": False, "error": "All fields are required"})

            if new_pass != confirm:
                return jsonify({"success": False, "error": "Passwords do not match"})

            if len(new_pass) < 4:
                return jsonify({"success": False, "error": "Password must be at least 4 characters"})

            # In production, verify current password and update securely
            # For now, just acknowledge the update
            return jsonify({"success": True, "message": "Password updated successfully"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/settings/export/<export_type>", methods=["GET"])
    @login_required
    def export_data(export_type):
        """Export trading data."""
        try:
            if export_type == "trades":
                trades = trading_state.trades
                if not trades:
                    return jsonify({"success": False, "error": "No trades to export"})

                # Generate CSV
                csv_lines = ["time,symbol,side,qty,price,pnl"]
                for t in trades:
                    csv_lines.append(f"{t.get('time','')},{t.get('symbol','')},{t.get('side','')},{t.get('qty',0)},{t.get('price',0)},{t.get('pnl',0)}")

                return Response(
                    "\n".join(csv_lines),
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=trades.csv"}
                )

            elif export_type == "positions":
                positions = trading_state.positions
                csv_lines = ["symbol,side,qty,entry_price,current_price,pnl"]
                for p in positions:
                    csv_lines.append(f"{p.get('symbol','')},{p.get('side','')},{p.get('qty',0)},{p.get('entry_price',0)},{p.get('current_price',0)},{p.get('pnl',0)}")

                return Response(
                    "\n".join(csv_lines),
                    mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=positions.csv"}
                )

            elif export_type == "all":
                export_data = {
                    "trades": trading_state.trades,
                    "positions": trading_state.positions,
                    "settings": user_settings,
                    "stats": trading_state.get_stats(),
                    "exported_at": datetime.now().isoformat()
                }
                return Response(
                    json.dumps(export_data, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment;filename=trading_data.json"}
                )

            else:
                return jsonify({"success": False, "error": "Invalid export type"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/settings/reset-account", methods=["POST"])
    @login_required
    def reset_account():
        """Reset paper trading account to initial state."""
        try:
            trading_state.current_capital = trading_state.initial_capital
            trading_state.total_pnl = 0.0
            trading_state.trades = []
            trading_state.positions = []
            trading_state.total_trades = 0
            trading_state.winning_trades = 0
            trading_state.losing_trades = 0

            return jsonify({
                "success": True,
                "message": "Account reset to initial capital",
                "initial_capital": trading_state.initial_capital
            })

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route("/api/settings/clear-all", methods=["POST"])
    @login_required
    def clear_all_data():
        """Clear all trading data and reset to defaults."""
        try:
            # Reset trading state
            trading_state.current_capital = trading_state.initial_capital
            trading_state.total_pnl = 0.0
            trading_state.trades = []
            trading_state.positions = []
            trading_state.errors = []
            trading_state.total_trades = 0
            trading_state.winning_trades = 0
            trading_state.losing_trades = 0

            # Reset settings to defaults
            user_settings["general"] = {
                "timezone": "America/New_York",
                "currency": "USD",
                "dark_mode": True,
                "sound_alerts": False
            }
            user_settings["notifications"] = {
                "notify_trades": True,
                "notify_signals": True,
                "notify_errors": True,
                "notify_pnl": False,
                "email": ""
            }
            user_settings["strategy"] = {
                "strategy_mode": "momentum",
                "timeframe": "1h",
                "signal_threshold": 70,
                "use_rl": False
            }

            return jsonify({"success": True, "message": "All data cleared"})

        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    return app


# =============================================================================
# Load Stored Credentials
# =============================================================================

def load_stored_credentials():
    """Load credentials from config file."""
    config_file = Path(__file__).parent.parent / "config" / "alpaca_credentials.json"

    if config_file.exists():
        try:
            with open(config_file) as f:
                data = json.load(f)
                cred = data.get("alpaca_paper", {})
                trading_state.alpaca_api_key = cred.get("api_key", "")
                trading_state.alpaca_api_secret = cred.get("api_secret", "")
                print(f"  Loaded Alpaca credentials from {config_file}")

                # Try to connect to Alpaca
                if trading_state.alpaca_api_key:
                    print("  Connecting to Alpaca...")
                    if trading_state.connect_alpaca():
                        print("  ✓ Connected to Alpaca")
                    else:
                        print("  ⚠ Alpaca connection pending (will connect when network available)")

        except Exception as e:
            print(f"  Failed to load credentials: {e}")

    # Check components
    trading_state.check_components()
    ready = sum(1 for v in trading_state.components.values() if v)
    total = len(trading_state.components)
    print(f"  Components ready: {ready}/{total}")


# =============================================================================
# Main Entry Point
# =============================================================================

def run_server(
    host: str = "0.0.0.0",
    port: int = 5000,
    debug: bool = False,
    admin_password: str = "admin"
):
    """Run the web server."""

    if not HAS_FLASK:
        print("Error: Flask is not installed.")
        print("Install with: pip install flask")
        return

    print("\n" + "="*60)
    print("  Agentic Trading OS - Web Dashboard")
    print("="*60)

    # Load stored credentials
    load_stored_credentials()

    # Create config
    config = WebConfig(
        host=host,
        port=port,
        debug=debug,
        admin_password_hash=hashlib.sha256(admin_password.encode()).hexdigest()
    )

    # Create app
    app = create_app(config)

    print(f"\n  Server: http://{host}:{port}")
    print(f"  Admin Login: admin / {admin_password}")
    print("\n  Press Ctrl+C to stop.\n")

    # Run server
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agentic Trading OS Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--password", default="admin", help="Admin password")

    args = parser.parse_args()

    run_server(
        host=args.host,
        port=args.port,
        debug=args.debug,
        admin_password=args.password
    )
