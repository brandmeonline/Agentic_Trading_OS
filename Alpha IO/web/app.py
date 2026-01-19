"""
Agentic Trading OS - Web Dashboard.

A modern, interactive web interface for the trading system.
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

        self._lock = threading.Lock()

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
            from core.alpaca_connector import create_alpaca_client

            client = create_alpaca_client(
                trading_state.alpaca_api_key,
                trading_state.alpaca_api_secret,
                paper=True
            )

            if client.connect():
                account = client.get_account()
                trading_state.alpaca_connected = True
                return jsonify({
                    "success": True,
                    "account": account
                })
            else:
                trading_state.alpaca_connected = False
                return jsonify({"success": False, "error": "Connection failed"})

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
        except Exception as e:
            print(f"  Failed to load credentials: {e}")


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
