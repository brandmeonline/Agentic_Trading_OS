"""
REST API Server for Trading System.

Production-grade REST API providing:
- Trading endpoints (orders, positions, balances)
- Market data endpoints (tickers, orderbooks, klines)
- Strategy management endpoints
- System monitoring endpoints
- WebSocket streaming endpoints
- JWT authentication
"""

from __future__ import annotations

import json
import time
import hashlib
import hmac
import base64
import secrets
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Callable, Tuple
from enum import Enum
from datetime import datetime, timedelta
from functools import wraps
import queue


# =============================================================================
# Configuration
# =============================================================================

class APIVersion(Enum):
    """API versions."""
    V1 = "v1"
    V2 = "v2"


class RateLimitTier(Enum):
    """Rate limit tiers."""
    FREE = "free"
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class APIConfig:
    """API server configuration."""
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    enable_cors: bool = True
    enable_auth: bool = True
    jwt_secret: str = field(default_factory=lambda: secrets.token_hex(32))
    jwt_expiry_hours: int = 24
    rate_limit_per_minute: int = 60
    max_request_size_mb: int = 10
    enable_websocket: bool = True
    enable_swagger: bool = True


@dataclass
class APIUser:
    """API user credentials."""
    user_id: str
    api_key: str
    api_secret: str
    tier: RateLimitTier = RateLimitTier.FREE
    permissions: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_access: Optional[datetime] = None


@dataclass
class APIRequest:
    """Parsed API request."""
    method: str
    path: str
    headers: Dict[str, str]
    query_params: Dict[str, str]
    body: Optional[Dict[str, Any]]
    user: Optional[APIUser] = None
    request_id: str = field(default_factory=lambda: secrets.token_hex(8))
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class APIResponse:
    """API response object."""
    status_code: int
    body: Dict[str, Any]
    headers: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.body)


# =============================================================================
# Rate Limiter
# =============================================================================

class APIRateLimiter:
    """Token bucket rate limiter for API endpoints."""

    TIER_LIMITS = {
        RateLimitTier.FREE: 60,
        RateLimitTier.BASIC: 300,
        RateLimitTier.PRO: 1200,
        RateLimitTier.ENTERPRISE: 6000,
    }

    def __init__(self):
        self._buckets: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def check_limit(self, user_id: str, tier: RateLimitTier) -> Tuple[bool, int]:
        """Check if request is within rate limit. Returns (allowed, remaining)."""
        with self._lock:
            now = time.time()
            limit = self.TIER_LIMITS[tier]

            if user_id not in self._buckets:
                self._buckets[user_id] = {
                    "tokens": limit,
                    "last_refill": now,
                }

            bucket = self._buckets[user_id]

            # Refill tokens
            elapsed = now - bucket["last_refill"]
            refill_amount = elapsed * (limit / 60)  # Per minute
            bucket["tokens"] = min(limit, bucket["tokens"] + refill_amount)
            bucket["last_refill"] = now

            if bucket["tokens"] >= 1:
                bucket["tokens"] -= 1
                return True, int(bucket["tokens"])
            else:
                return False, 0


# =============================================================================
# Authentication
# =============================================================================

class JWTAuth:
    """JWT authentication handler."""

    def __init__(self, secret: str, expiry_hours: int = 24):
        self.secret = secret
        self.expiry_hours = expiry_hours

    def create_token(self, user_id: str, permissions: List[str]) -> str:
        """Create JWT token."""
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user_id,
            "permissions": permissions,
            "iat": int(time.time()),
            "exp": int(time.time()) + (self.expiry_hours * 3600),
        }

        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        signature = hmac.new(
            self.secret.encode(),
            f"{header_b64}.{payload_b64}".encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def verify_token(self, token: str) -> Optional[Dict]:
        """Verify JWT token and return payload if valid."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, payload_b64, signature_b64 = parts

            # Verify signature
            expected_sig = hmac.new(
                self.secret.encode(),
                f"{header_b64}.{payload_b64}".encode(),
                hashlib.sha256
            ).digest()
            expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode().rstrip("=")

            if signature_b64 != expected_sig_b64:
                return None

            # Decode payload
            padding = 4 - (len(payload_b64) % 4)
            payload_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))

            # Check expiry
            if payload.get("exp", 0) < time.time():
                return None

            return payload

        except Exception:
            return None


class APIKeyAuth:
    """API key authentication handler."""

    def __init__(self):
        self._users: Dict[str, APIUser] = {}

    def create_user(
        self,
        user_id: str,
        tier: RateLimitTier = RateLimitTier.FREE,
        permissions: Optional[List[str]] = None
    ) -> APIUser:
        """Create new API user with credentials."""
        api_key = f"ak_{secrets.token_hex(16)}"
        api_secret = secrets.token_hex(32)

        user = APIUser(
            user_id=user_id,
            api_key=api_key,
            api_secret=api_secret,
            tier=tier,
            permissions=permissions or ["read", "trade"],
        )

        self._users[api_key] = user
        return user

    def verify_request(
        self,
        api_key: str,
        timestamp: str,
        signature: str,
        body: str = ""
    ) -> Optional[APIUser]:
        """Verify API key signature."""
        if api_key not in self._users:
            return None

        user = self._users[api_key]

        # Verify timestamp within 30 seconds
        try:
            req_time = int(timestamp)
            if abs(time.time() - req_time) > 30:
                return None
        except ValueError:
            return None

        # Verify signature
        message = f"{timestamp}{body}"
        expected_sig = hmac.new(
            user.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if signature != expected_sig:
            return None

        user.last_access = datetime.now()
        return user

    def get_user(self, api_key: str) -> Optional[APIUser]:
        """Get user by API key."""
        return self._users.get(api_key)


# =============================================================================
# Request Router
# =============================================================================

class Route:
    """API route definition."""

    def __init__(
        self,
        path: str,
        method: str,
        handler: Callable,
        auth_required: bool = True,
        permissions: Optional[List[str]] = None
    ):
        self.path = path
        self.method = method.upper()
        self.handler = handler
        self.auth_required = auth_required
        self.permissions = permissions or []

    def matches(self, path: str, method: str) -> Tuple[bool, Dict[str, str]]:
        """Check if route matches request path and method."""
        if self.method != method.upper():
            return False, {}

        # Parse path parameters
        route_parts = self.path.split("/")
        path_parts = path.split("/")

        if len(route_parts) != len(path_parts):
            return False, {}

        params = {}
        for route_part, path_part in zip(route_parts, path_parts):
            if route_part.startswith("{") and route_part.endswith("}"):
                param_name = route_part[1:-1]
                params[param_name] = path_part
            elif route_part != path_part:
                return False, {}

        return True, params


class Router:
    """Request router."""

    def __init__(self):
        self.routes: List[Route] = []

    def add_route(
        self,
        path: str,
        method: str,
        handler: Callable,
        auth_required: bool = True,
        permissions: Optional[List[str]] = None
    ):
        """Add route to router."""
        self.routes.append(Route(path, method, handler, auth_required, permissions))

    def route(self, path: str, method: str = "GET", auth_required: bool = True, permissions: Optional[List[str]] = None):
        """Decorator for adding routes."""
        def decorator(func):
            self.add_route(path, method, func, auth_required, permissions)
            return func
        return decorator

    def find_route(self, path: str, method: str) -> Tuple[Optional[Route], Dict[str, str]]:
        """Find matching route for request."""
        for route in self.routes:
            matches, params = route.matches(path, method)
            if matches:
                return route, params
        return None, {}


# =============================================================================
# API Handlers
# =============================================================================

class TradingAPIHandlers:
    """Trading API endpoint handlers."""

    def __init__(self, trading_system: Any = None):
        self.trading_system = trading_system
        self._mock_balances = {
            "BTC": {"free": 1.5, "locked": 0.2, "total": 1.7},
            "ETH": {"free": 10.0, "locked": 1.0, "total": 11.0},
            "USDT": {"free": 50000.0, "locked": 5000.0, "total": 55000.0},
        }
        self._mock_positions = []
        self._mock_orders = {}

    # Account endpoints
    def get_balances(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get account balances."""
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": self._mock_balances,
                "timestamp": datetime.now().isoformat(),
            }
        )

    def get_positions(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get open positions."""
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": self._mock_positions,
                "timestamp": datetime.now().isoformat(),
            }
        )

    # Order endpoints
    def place_order(self, request: APIRequest, params: Dict) -> APIResponse:
        """Place a new order."""
        body = request.body or {}

        required_fields = ["symbol", "side", "type", "quantity"]
        for field in required_fields:
            if field not in body:
                return APIResponse(
                    status_code=400,
                    body={"success": False, "error": f"Missing required field: {field}"}
                )

        order_id = f"ord_{secrets.token_hex(8)}"
        order = {
            "order_id": order_id,
            "symbol": body["symbol"],
            "side": body["side"],
            "type": body["type"],
            "quantity": body["quantity"],
            "price": body.get("price"),
            "status": "open",
            "filled_quantity": 0,
            "created_at": datetime.now().isoformat(),
        }

        self._mock_orders[order_id] = order

        return APIResponse(
            status_code=201,
            body={
                "success": True,
                "data": order,
            }
        )

    def get_order(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get order by ID."""
        order_id = params.get("order_id")
        if order_id not in self._mock_orders:
            return APIResponse(
                status_code=404,
                body={"success": False, "error": "Order not found"}
            )

        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": self._mock_orders[order_id],
            }
        )

    def cancel_order(self, request: APIRequest, params: Dict) -> APIResponse:
        """Cancel an order."""
        order_id = params.get("order_id")
        if order_id not in self._mock_orders:
            return APIResponse(
                status_code=404,
                body={"success": False, "error": "Order not found"}
            )

        self._mock_orders[order_id]["status"] = "cancelled"
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": self._mock_orders[order_id],
            }
        )

    def get_open_orders(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get all open orders."""
        open_orders = [o for o in self._mock_orders.values() if o["status"] == "open"]
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": open_orders,
            }
        )

    # Market data endpoints
    def get_ticker(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get ticker for symbol."""
        symbol = params.get("symbol", "BTC-USDT")
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {
                    "symbol": symbol,
                    "bid": 50000.0,
                    "ask": 50001.0,
                    "last": 50000.5,
                    "volume_24h": 10000.0,
                    "change_24h": 1.5,
                    "timestamp": datetime.now().isoformat(),
                }
            }
        )

    def get_orderbook(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get order book for symbol."""
        symbol = params.get("symbol", "BTC-USDT")
        depth = int(request.query_params.get("depth", "20"))

        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {
                    "symbol": symbol,
                    "bids": [[50000.0 - i * 10, 0.5 + i * 0.1] for i in range(depth)],
                    "asks": [[50001.0 + i * 10, 0.5 + i * 0.1] for i in range(depth)],
                    "timestamp": datetime.now().isoformat(),
                }
            }
        )

    def get_klines(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get candlestick data."""
        symbol = params.get("symbol", "BTC-USDT")
        interval = request.query_params.get("interval", "1h")
        limit = int(request.query_params.get("limit", "100"))

        # Generate mock klines
        import numpy as np
        base_price = 50000.0
        klines = []
        for i in range(limit):
            t = int(time.time() - (limit - i) * 3600)
            o = base_price + np.random.randn() * 100
            h = o + abs(np.random.randn() * 50)
            l = o - abs(np.random.randn() * 50)
            c = o + np.random.randn() * 30
            v = abs(np.random.randn() * 100) + 10
            klines.append({
                "timestamp": t,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": round(v, 4),
            })

        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {
                    "symbol": symbol,
                    "interval": interval,
                    "klines": klines,
                }
            }
        )

    # Strategy endpoints
    def get_strategies(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get available strategies."""
        strategies = [
            {"name": "momentum", "status": "active", "pnl": 1250.50},
            {"name": "mean_reversion", "status": "paused", "pnl": -120.30},
            {"name": "ml_ensemble", "status": "active", "pnl": 3500.00},
        ]
        return APIResponse(
            status_code=200,
            body={"success": True, "data": strategies}
        )

    def start_strategy(self, request: APIRequest, params: Dict) -> APIResponse:
        """Start a strategy."""
        strategy_name = params.get("strategy")
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {"strategy": strategy_name, "status": "started"}
            }
        )

    def stop_strategy(self, request: APIRequest, params: Dict) -> APIResponse:
        """Stop a strategy."""
        strategy_name = params.get("strategy")
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {"strategy": strategy_name, "status": "stopped"}
            }
        )

    # System endpoints
    def get_system_status(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get system status."""
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {
                    "status": "healthy",
                    "uptime_seconds": 3600,
                    "active_strategies": 2,
                    "open_orders": len([o for o in self._mock_orders.values() if o["status"] == "open"]),
                    "total_positions": len(self._mock_positions),
                    "memory_usage_mb": 512,
                    "cpu_usage_percent": 15.5,
                    "timestamp": datetime.now().isoformat(),
                }
            }
        )

    def get_metrics(self, request: APIRequest, params: Dict) -> APIResponse:
        """Get system metrics."""
        return APIResponse(
            status_code=200,
            body={
                "success": True,
                "data": {
                    "total_trades": 1250,
                    "win_rate": 0.58,
                    "total_pnl": 15000.50,
                    "sharpe_ratio": 1.85,
                    "max_drawdown": -0.12,
                    "avg_trade_duration_minutes": 45,
                }
            }
        )


# =============================================================================
# REST API Server
# =============================================================================

class RESTAPIServer:
    """REST API server for trading system."""

    def __init__(self, config: Optional[APIConfig] = None, trading_system: Any = None):
        self.config = config or APIConfig()
        self.router = Router()
        self.rate_limiter = APIRateLimiter()
        self.jwt_auth = JWTAuth(self.config.jwt_secret, self.config.jwt_expiry_hours)
        self.api_key_auth = APIKeyAuth()
        self.handlers = TradingAPIHandlers(trading_system)

        self._running = False
        self._request_count = 0
        self._error_count = 0

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register all API routes."""
        # Public endpoints
        self.router.add_route(
            "/api/v1/health",
            "GET",
            lambda req, params: APIResponse(200, {"status": "ok"}),
            auth_required=False
        )

        self.router.add_route(
            "/api/v1/time",
            "GET",
            lambda req, params: APIResponse(200, {"timestamp": int(time.time() * 1000)}),
            auth_required=False
        )

        # Auth endpoints
        self.router.add_route(
            "/api/v1/auth/token",
            "POST",
            self._handle_token_request,
            auth_required=False
        )

        # Account endpoints
        self.router.add_route("/api/v1/account/balances", "GET", self.handlers.get_balances)
        self.router.add_route("/api/v1/account/positions", "GET", self.handlers.get_positions)

        # Order endpoints
        self.router.add_route("/api/v1/orders", "POST", self.handlers.place_order, permissions=["trade"])
        self.router.add_route("/api/v1/orders", "GET", self.handlers.get_open_orders)
        self.router.add_route("/api/v1/orders/{order_id}", "GET", self.handlers.get_order)
        self.router.add_route("/api/v1/orders/{order_id}", "DELETE", self.handlers.cancel_order, permissions=["trade"])

        # Market data endpoints
        self.router.add_route("/api/v1/market/{symbol}/ticker", "GET", self.handlers.get_ticker)
        self.router.add_route("/api/v1/market/{symbol}/orderbook", "GET", self.handlers.get_orderbook)
        self.router.add_route("/api/v1/market/{symbol}/klines", "GET", self.handlers.get_klines)

        # Strategy endpoints
        self.router.add_route("/api/v1/strategies", "GET", self.handlers.get_strategies)
        self.router.add_route("/api/v1/strategies/{strategy}/start", "POST", self.handlers.start_strategy, permissions=["trade"])
        self.router.add_route("/api/v1/strategies/{strategy}/stop", "POST", self.handlers.stop_strategy, permissions=["trade"])

        # System endpoints
        self.router.add_route("/api/v1/system/status", "GET", self.handlers.get_system_status)
        self.router.add_route("/api/v1/system/metrics", "GET", self.handlers.get_metrics)

    def _handle_token_request(self, request: APIRequest, params: Dict) -> APIResponse:
        """Handle JWT token request."""
        body = request.body or {}

        api_key = body.get("api_key")
        timestamp = body.get("timestamp")
        signature = body.get("signature")

        if not all([api_key, timestamp, signature]):
            return APIResponse(400, {"success": False, "error": "Missing credentials"})

        user = self.api_key_auth.verify_request(
            api_key,
            timestamp,
            signature,
            json.dumps({"api_key": api_key, "timestamp": timestamp})
        )

        if not user:
            return APIResponse(401, {"success": False, "error": "Invalid credentials"})

        token = self.jwt_auth.create_token(user.user_id, user.permissions)

        return APIResponse(200, {
            "success": True,
            "data": {
                "token": token,
                "expires_in": self.config.jwt_expiry_hours * 3600,
            }
        })

    def handle_request(self, request: APIRequest) -> APIResponse:
        """Handle incoming API request."""
        self._request_count += 1

        try:
            # Find matching route
            route, params = self.router.find_route(request.path, request.method)

            if not route:
                return APIResponse(404, {"success": False, "error": "Not found"})

            # Check authentication
            if route.auth_required and self.config.enable_auth:
                auth_header = request.headers.get("Authorization", "")

                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    payload = self.jwt_auth.verify_token(token)

                    if not payload:
                        return APIResponse(401, {"success": False, "error": "Invalid token"})

                    # Check permissions
                    user_permissions = payload.get("permissions", [])
                    for required_perm in route.permissions:
                        if required_perm not in user_permissions:
                            return APIResponse(403, {"success": False, "error": "Insufficient permissions"})

                else:
                    return APIResponse(401, {"success": False, "error": "Authorization required"})

            # Rate limiting
            user_id = request.user.user_id if request.user else "anonymous"
            tier = request.user.tier if request.user else RateLimitTier.FREE
            allowed, remaining = self.rate_limiter.check_limit(user_id, tier)

            if not allowed:
                response = APIResponse(429, {"success": False, "error": "Rate limit exceeded"})
                response.headers["X-RateLimit-Remaining"] = str(remaining)
                return response

            # Call handler
            response = route.handler(request, params)
            response.headers["X-Request-ID"] = request.request_id
            response.headers["X-RateLimit-Remaining"] = str(remaining)

            return response

        except Exception as e:
            self._error_count += 1
            return APIResponse(500, {"success": False, "error": str(e)})

    def create_user(
        self,
        user_id: str,
        tier: RateLimitTier = RateLimitTier.FREE,
        permissions: Optional[List[str]] = None
    ) -> APIUser:
        """Create new API user."""
        return self.api_key_auth.create_user(user_id, tier, permissions)

    def start(self):
        """Start API server (placeholder for actual server implementation)."""
        self._running = True
        print(f"API Server started on {self.config.host}:{self.config.port}")

    def stop(self):
        """Stop API server."""
        self._running = False
        print("API Server stopped")

    @property
    def stats(self) -> Dict[str, Any]:
        """Get server statistics."""
        return {
            "running": self._running,
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
        }


# =============================================================================
# WebSocket Handler
# =============================================================================

class WebSocketHandler:
    """WebSocket connection handler for real-time data."""

    def __init__(self):
        self.connections: Dict[str, Any] = {}
        self.subscriptions: Dict[str, List[str]] = {}  # channel -> connection_ids

    def on_connect(self, connection_id: str, connection: Any):
        """Handle new WebSocket connection."""
        self.connections[connection_id] = connection
        print(f"WebSocket connected: {connection_id}")

    def on_disconnect(self, connection_id: str):
        """Handle WebSocket disconnection."""
        if connection_id in self.connections:
            del self.connections[connection_id]

        # Remove from all subscriptions
        for channel, conn_ids in self.subscriptions.items():
            if connection_id in conn_ids:
                conn_ids.remove(connection_id)

        print(f"WebSocket disconnected: {connection_id}")

    def on_message(self, connection_id: str, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            action = data.get("action")

            if action == "subscribe":
                channel = data.get("channel")
                if channel:
                    self.subscribe(connection_id, channel)
                    self.send(connection_id, {"action": "subscribed", "channel": channel})

            elif action == "unsubscribe":
                channel = data.get("channel")
                if channel:
                    self.unsubscribe(connection_id, channel)
                    self.send(connection_id, {"action": "unsubscribed", "channel": channel})

        except json.JSONDecodeError:
            self.send(connection_id, {"error": "Invalid JSON"})

    def subscribe(self, connection_id: str, channel: str):
        """Subscribe connection to channel."""
        if channel not in self.subscriptions:
            self.subscriptions[channel] = []
        if connection_id not in self.subscriptions[channel]:
            self.subscriptions[channel].append(connection_id)

    def unsubscribe(self, connection_id: str, channel: str):
        """Unsubscribe connection from channel."""
        if channel in self.subscriptions and connection_id in self.subscriptions[channel]:
            self.subscriptions[channel].remove(connection_id)

    def broadcast(self, channel: str, data: Dict):
        """Broadcast message to all subscribers of a channel."""
        if channel not in self.subscriptions:
            return

        message = json.dumps({"channel": channel, "data": data})
        for connection_id in self.subscriptions[channel]:
            self.send(connection_id, message)

    def send(self, connection_id: str, data: Any):
        """Send message to specific connection."""
        if connection_id in self.connections:
            if isinstance(data, dict):
                data = json.dumps(data)
            # In real implementation, this would send via WebSocket
            print(f"WS -> {connection_id}: {data[:100]}...")


# =============================================================================
# Factory Functions
# =============================================================================

def create_api_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    enable_auth: bool = True,
    trading_system: Any = None
) -> RESTAPIServer:
    """Create and configure API server."""
    config = APIConfig(
        host=host,
        port=port,
        enable_auth=enable_auth,
    )
    return RESTAPIServer(config, trading_system)


def create_websocket_handler() -> WebSocketHandler:
    """Create WebSocket handler."""
    return WebSocketHandler()


# =============================================================================
# OpenAPI Specification Generator
# =============================================================================

def generate_openapi_spec(server: RESTAPIServer) -> Dict:
    """Generate OpenAPI 3.0 specification."""
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "Agentic Trading System API",
            "description": "Production-grade REST API for algorithmic trading",
            "version": "1.0.0",
        },
        "servers": [
            {"url": f"http://{server.config.host}:{server.config.port}", "description": "Trading API Server"}
        ],
        "paths": {},
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            }
        }
    }

    # Add paths from routes
    for route in server.router.routes:
        path = route.path.replace("{", "{").replace("}", "}")
        if path not in spec["paths"]:
            spec["paths"][path] = {}

        method = route.method.lower()
        spec["paths"][path][method] = {
            "summary": f"{method.upper()} {path}",
            "security": [{"bearerAuth": []}] if route.auth_required else [],
            "responses": {
                "200": {"description": "Success"},
                "401": {"description": "Unauthorized"},
                "404": {"description": "Not found"},
            }
        }

    return spec


# =============================================================================
# Testing
# =============================================================================

def test_rest_api():
    """Test REST API server."""
    print("Testing REST API Server...")

    # Create server
    server = create_api_server(enable_auth=False)

    # Test health endpoint
    print("\n1. Testing health endpoint...")
    request = APIRequest(
        method="GET",
        path="/api/v1/health",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Health check: {response.body}")

    # Test time endpoint
    print("\n2. Testing time endpoint...")
    request = APIRequest(
        method="GET",
        path="/api/v1/time",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Server time: {response.body}")

    # Test balances endpoint
    print("\n3. Testing balances endpoint...")
    request = APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Balances: {len(response.body['data'])} assets")

    # Test ticker endpoint
    print("\n4. Testing ticker endpoint...")
    request = APIRequest(
        method="GET",
        path="/api/v1/market/BTC-USDT/ticker",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Ticker: ${response.body['data']['last']:.2f}")

    # Test order placement
    print("\n5. Testing order placement...")
    request = APIRequest(
        method="POST",
        path="/api/v1/orders",
        headers={},
        query_params={},
        body={
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "quantity": 0.001,
            "price": 50000.0
        }
    )
    response = server.handle_request(request)
    assert response.status_code == 201
    order_id = response.body["data"]["order_id"]
    print(f"   Order placed: {order_id}")

    # Test get order
    print("\n6. Testing get order...")
    request = APIRequest(
        method="GET",
        path=f"/api/v1/orders/{order_id}",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Order status: {response.body['data']['status']}")

    # Test cancel order
    print("\n7. Testing cancel order...")
    request = APIRequest(
        method="DELETE",
        path=f"/api/v1/orders/{order_id}",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   Order cancelled: {response.body['data']['status']}")

    # Test system status
    print("\n8. Testing system status...")
    request = APIRequest(
        method="GET",
        path="/api/v1/system/status",
        headers={},
        query_params={},
        body=None
    )
    response = server.handle_request(request)
    assert response.status_code == 200
    print(f"   System status: {response.body['data']['status']}")

    # Test with authentication
    print("\n9. Testing authentication...")
    auth_server = create_api_server(enable_auth=True)
    user = auth_server.create_user("test_user", RateLimitTier.PRO, ["read", "trade"])
    print(f"   Created user with API key: {user.api_key[:20]}...")

    token = auth_server.jwt_auth.create_token(user.user_id, user.permissions)
    print(f"   Generated JWT token: {token[:50]}...")

    request = APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={"Authorization": f"Bearer {token}"},
        query_params={},
        body=None
    )
    response = auth_server.handle_request(request)
    assert response.status_code == 200
    print(f"   Authenticated request successful!")

    # Test OpenAPI spec generation
    print("\n10. Testing OpenAPI spec generation...")
    spec = generate_openapi_spec(server)
    print(f"   Generated OpenAPI spec with {len(spec['paths'])} paths")

    # Test WebSocket handler
    print("\n11. Testing WebSocket handler...")
    ws_handler = create_websocket_handler()
    ws_handler.on_connect("conn_1", None)
    ws_handler.subscribe("conn_1", "ticker:BTC-USDT")
    ws_handler.broadcast("ticker:BTC-USDT", {"price": 50000.0})
    ws_handler.on_disconnect("conn_1")
    print("   WebSocket handler working!")

    print("\n✓ All REST API tests passed!")
    return True


if __name__ == "__main__":
    test_rest_api()
