import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.rest_api import APIConfig, APIRequest, JWTAuth, RESTAPIServer


class FakeLiveTradingAdapter:
    def __init__(self):
        self.placed_orders = []

    def get_balances(self):
        return [{"asset": "USDT", "free": 1000.0, "locked": 0.0, "total": 1000.0}]

    def get_positions(self):
        return [{"symbol": "BTC-USDT", "quantity": 1.0, "avg_price": 50000.0}]

    def place_order(self, order):
        self.placed_orders.append(order)
        return {
            "order_id": "live-1",
            "symbol": order["symbol"],
            "status": "accepted",
        }

    def get_order(self, order_id):
        return {"order_id": order_id, "status": "accepted"}

    def cancel_order(self, order_id):
        return {"order_id": order_id, "status": "cancelled"}

    def get_open_orders(self):
        return [{"order_id": "live-1", "status": "accepted"}]

    def get_ticker(self, symbol):
        return {"symbol": symbol, "bid": 49999.0, "ask": 50001.0, "last": 50000.0}

    def get_orderbook(self, symbol, depth):
        return {"symbol": symbol, "bids": [[49999.0, 1.0]], "asks": [[50001.0, 1.0]], "depth": depth}

    def get_klines(self, symbol, interval, limit):
        return [{"symbol": symbol, "interval": interval, "limit": limit, "close": 50000.0}]


def test_jwt_tampered_signature_is_rejected():
    auth = JWTAuth("test-secret")
    token = auth.create_token("user-1", ["read"])
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-1]}x"

    assert auth.verify_token(token)["sub"] == "user-1"
    assert auth.verify_token(tampered) is None


def test_rest_api_simulation_responses_are_explicitly_marked():
    server = RESTAPIServer(APIConfig(enable_auth=False, simulation_mode=True))
    response = server.handle_request(APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={},
        query_params={},
        body=None,
    ))

    assert response.status_code == 200
    assert response.body["success"] is True
    assert response.body["mode"] == "simulation"
    assert response.body["simulated"] is True


def test_rest_api_live_mode_fails_closed_without_adapter_for_balances():
    server = RESTAPIServer(APIConfig(enable_auth=False, simulation_mode=False))
    response = server.handle_request(APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={},
        query_params={},
        body=None,
    ))

    assert response.status_code == 503
    assert response.body == {
        "success": False,
        "error": "Live trading adapter is not configured",
        "mode": "live",
        "simulated": False,
    }


def test_rest_api_live_mode_fails_closed_without_adapter_for_orders():
    server = RESTAPIServer(APIConfig(enable_auth=False, simulation_mode=False))
    response = server.handle_request(APIRequest(
        method="POST",
        path="/api/v1/orders",
        headers={},
        query_params={},
        body={
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "market",
            "quantity": 1,
        },
    ))

    assert response.status_code == 503
    assert response.body["error"] == "Live trading adapter is not configured"
    assert response.body["simulated"] is False


def test_rest_api_live_mode_uses_configured_adapter_for_account_and_orders():
    adapter = FakeLiveTradingAdapter()
    server = RESTAPIServer(
        APIConfig(enable_auth=False, simulation_mode=False),
        trading_system=adapter,
    )

    balance_response = server.handle_request(APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={},
        query_params={},
        body=None,
    ))
    order_response = server.handle_request(APIRequest(
        method="POST",
        path="/api/v1/orders",
        headers={},
        query_params={},
        body={
            "symbol": "BTC-USDT",
            "side": "buy",
            "type": "market",
            "quantity": 1,
        },
    ))

    assert balance_response.status_code == 200
    assert balance_response.body["mode"] == "live"
    assert balance_response.body["simulated"] is False
    assert balance_response.body["data"][0]["asset"] == "USDT"
    assert order_response.status_code == 201
    assert order_response.body["mode"] == "live"
    assert order_response.body["simulated"] is False
    assert order_response.body["data"]["order_id"] == "live-1"
    assert adapter.placed_orders == [{
        "symbol": "BTC-USDT",
        "side": "buy",
        "type": "market",
        "quantity": 1,
    }]


def test_rest_api_live_mode_uses_configured_adapter_for_market_data():
    adapter = FakeLiveTradingAdapter()
    server = RESTAPIServer(
        APIConfig(enable_auth=False, simulation_mode=False),
        trading_system=adapter,
    )

    response = server.handle_request(APIRequest(
        method="GET",
        path="/api/v1/market/BTC-USDT/ticker",
        headers={},
        query_params={},
        body=None,
    ))

    assert response.status_code == 200
    assert response.body["mode"] == "live"
    assert response.body["simulated"] is False
    assert response.body["data"]["symbol"] == "BTC-USDT"
    assert response.body["data"]["last"] == 50000.0


def test_rest_api_live_adapter_errors_do_not_leak_exception_details():
    class FailingAdapter:
        def get_balances(self):
            raise RuntimeError("broker-secret-token leaked")

    server = RESTAPIServer(
        APIConfig(enable_auth=False, simulation_mode=False),
        trading_system=FailingAdapter(),
    )
    request = APIRequest(
        method="GET",
        path="/api/v1/account/balances",
        headers={},
        query_params={},
        body=None,
    )

    response = server.handle_request(request)

    assert response.status_code == 502
    assert response.body == {
        "success": False,
        "error": "Live trading adapter request failed",
        "mode": "live",
        "simulated": False,
    }
    assert "broker-secret-token" not in response.to_json()


def test_rest_api_internal_errors_do_not_leak_exception_details():
    server = RESTAPIServer(APIConfig(enable_auth=False))

    def boom(request, params):
        raise RuntimeError("broker-secret-token leaked")

    server.router.add_route("/api/v1/boom", "GET", boom, auth_required=False)
    request = APIRequest(
        method="GET",
        path="/api/v1/boom",
        headers={},
        query_params={},
        body=None,
    )

    response = server.handle_request(request)

    assert response.status_code == 500
    assert response.body == {
        "success": False,
        "error": "Internal server error",
        "request_id": request.request_id,
    }
    assert "broker-secret-token" not in response.to_json()
