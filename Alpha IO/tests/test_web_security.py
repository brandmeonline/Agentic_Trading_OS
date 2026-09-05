import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.app import (
    TradingState,
    WebConfig,
    create_app,
    make_admin_password_hash,
    trading_state,
)


def _csrf_from_html(response) -> str:
    match = re.search(
        rb'name="_csrf_token"\s+value="([^"]+)"',
        response.data
    )
    assert match, response.data.decode(errors="ignore")
    return match.group(1).decode()


def _app():
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password")
    ))
    app.config["TESTING"] = True
    return app


def test_create_app_fails_closed_without_admin_credentials(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    with pytest.raises(RuntimeError):
        create_app(WebConfig(secret_key="test-secret"))


def test_create_app_fails_closed_without_secret_key(monkeypatch):
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError):
        create_app(WebConfig(admin_password_hash=make_admin_password_hash("correct-password")))


def test_admin_admin_is_not_accepted_by_default():
    client = _app().test_client()
    login_page = client.get("/login")
    token = _csrf_from_html(login_page)

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "admin",
            "_csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert b"Invalid credentials" in response.data


def test_configured_admin_password_logs_in_and_rotates_csrf_token():
    client = _app().test_client()
    login_page = client.get("/login")
    token = _csrf_from_html(login_page)

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "_csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code in (301, 302)
    assert response.location.endswith("/")
    with client.session_transaction() as sess:
        assert sess["logged_in"] is True
        assert sess["_csrf_token"] != token


def test_api_mutation_requires_csrf_token():
    client = _app().test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post("/api/place-order", json={"symbol": "", "qty": 0})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid or missing CSRF token"


def test_api_mutation_accepts_valid_csrf_token():
    client = _app().test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/place-order",
        json={"symbol": "", "qty": 0},
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": False,
        "error": "Invalid order parameters",
    }


def test_login_throttles_repeated_failures():
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        login_max_attempts=2,
        login_lockout_seconds=60,
    ))
    app.config["TESTING"] = True
    client = app.test_client()

    for _ in range(2):
        login_page = client.get("/login")
        token = _csrf_from_html(login_page)
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": "wrong",
                "_csrf_token": token,
            },
        )
        assert b"Invalid credentials" in response.data

    login_page = client.get("/login")
    token = _csrf_from_html(login_page)
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "_csrf_token": token,
        },
    )

    assert b"Too many failed login attempts" in response.data


def test_security_headers_are_set():
    client = _app().test_client()
    response = client.get("/api/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" in response.headers


def test_password_update_requires_current_password():
    client = _app().test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/settings/password",
        json={
            "current_password": "wrong-password",
            "new_password": "new-password",
            "confirm_password": "new-password",
        },
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": False,
        "error": "Current password is incorrect",
    }


def test_password_update_changes_runtime_admin_hash_and_rotates_csrf():
    app = _app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/settings/password",
        json={
            "current_password": "correct-password",
            "new_password": "better-password",
            "confirm_password": "better-password",
        },
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    with client.session_transaction() as sess:
        assert sess["_csrf_token"] != "known-token"

    client.get("/logout")
    login_page = client.get("/login")
    token = _csrf_from_html(login_page)
    old_login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "_csrf_token": token,
        },
    )
    assert b"Invalid credentials" in old_login.data

    login_page = client.get("/login")
    token = _csrf_from_html(login_page)
    new_login = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "better-password",
            "_csrf_token": token,
        },
        follow_redirects=False,
    )
    assert new_login.status_code in (301, 302)
    assert new_login.location.endswith("/")


def test_password_update_persists_hash_file_and_survives_restart(tmp_path):
    hash_file = tmp_path / "dashboard_admin.hash"
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        admin_password_hash_path=str(hash_file),
    ))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/settings/password",
        json={
            "current_password": "correct-password",
            "new_password": "durable-password",
            "confirm_password": "durable-password",
        },
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    persisted_hash = hash_file.read_text(encoding="utf-8").strip()
    assert persisted_hash
    assert "durable-password" not in persisted_hash

    restarted = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash_path=str(hash_file),
    ))
    restarted.config["TESTING"] = True
    restarted_client = restarted.test_client()
    login_page = restarted_client.get("/login")
    token = _csrf_from_html(login_page)
    old_login = restarted_client.post(
        "/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "_csrf_token": token,
        },
    )
    assert b"Invalid credentials" in old_login.data

    login_page = restarted_client.get("/login")
    token = _csrf_from_html(login_page)
    new_login = restarted_client.post(
        "/login",
        data={
            "username": "admin",
            "password": "durable-password",
            "_csrf_token": token,
        },
        follow_redirects=False,
    )
    assert new_login.status_code in (301, 302)
    assert new_login.location.endswith("/")


def test_empty_admin_password_hash_file_fails_closed(tmp_path):
    hash_file = tmp_path / "dashboard_admin.hash"
    hash_file.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError):
        create_app(WebConfig(
            secret_key="test-secret",
            admin_password_hash_path=str(hash_file),
        ))


def test_trading_state_stop_joins_sync_thread():
    state = TradingState()
    state.is_running = True

    state._start_sync_thread()
    assert state._sync_thread is not None
    assert state._sync_thread.is_alive()

    state.stop_background_tasks(timeout=1.0)

    assert state.is_running is False
    assert state._sync_thread is None


def test_trading_state_stop_joins_price_thread():
    class FakeAlpacaClient:
        def get_latest_quote(self, symbol):
            return {"ask_price": 100}

        def get_crypto_quote(self, symbol):
            return {"ask_price": 100}

    state = TradingState()
    state.alpaca_client = FakeAlpacaClient()
    state.alpaca_connected = True

    state._start_price_updates()
    assert state._price_thread is not None
    assert state._price_thread.is_alive()

    state.stop_background_tasks(timeout=1.0)

    assert state.alpaca_connected is False
    assert state._price_thread is None


def test_manual_order_without_an_execution_engine_is_refused():
    """ATOS-P1-AGENT-001: the dashboard must not reach the broker directly.

    This method used to call alpaca_client.place_market_order, so a dashboard
    button created real exposure while skipping the order-intent WAL, the
    lifecycle state machine, idempotency, the risk engine and live
    authorization. With no engine attached it now refuses rather than falling
    back to the broker.
    """
    class ShouldNeverBeCalled:
        def place_market_order(self, symbol, qty, side):
            raise AssertionError("the dashboard reached the broker directly")

    state = TradingState()
    state.alpaca_client = ShouldNeverBeCalled()
    state.alpaca_connected = True

    response = state.submit_manual_order("AAPL", 1, "buy")

    assert response["success"] is False
    assert "execution boundary" in response["error"]


def test_order_placement_error_does_not_leak_exception_details():
    """A failure inside the engine must not surface broker internals."""
    class FailingEngine:
        def create_order(self, **kwargs):
            raise RuntimeError("broker-secret-token leaked")

    state = TradingState()
    state.execution_engine = FailingEngine()
    state.alpaca_client = object()
    state.alpaca_connected = True

    response = state.submit_manual_order("AAPL", 1, "buy")

    assert response == {
        "success": False,
        "error": "Order placement failed",
    }
    assert "broker-secret-token" not in str(response)


def test_credentials_connection_error_does_not_leak_exception_details(monkeypatch):
    import core.alpaca_connector as alpaca_connector

    def boom(*args, **kwargs):
        raise RuntimeError("broker-secret-token leaked")

    monkeypatch.setattr(alpaca_connector, "create_alpaca_client", boom)
    client = _app().test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/credentials",
        json={
            "api_key": "key",
            "api_secret": "secret",
        },
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert payload["error"] == "Internal server error"
    assert "error_id" in payload
    assert "broker-secret-token" not in response.get_data(as_text=True)


def test_account_reset_preserves_positions_mapping():
    client = _app().test_client()
    original_positions = trading_state.positions
    try:
        trading_state.positions = {
            "AAPL": {
                "symbol": "AAPL",
                "side": "long",
                "qty": 2,
                "entry_price": 100,
                "current_price": 105,
                "pnl": 10,
            }
        }
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = "admin"
            sess["_csrf_token"] = "known-token"

        export_response = client.get("/api/settings/export/positions")
        assert export_response.status_code == 200
        assert b"AAPL,long,2,100,105,10" in export_response.data

        reset_response = client.post(
            "/api/settings/reset-account",
            headers={"X-CSRF-Token": "known-token"},
        )
        assert reset_response.status_code == 200
        assert reset_response.get_json()["success"] is True
        assert trading_state.positions == {}

        positions_response = client.get("/api/positions")
        assert positions_response.status_code == 200
        assert positions_response.get_json() == []
    finally:
        trading_state.positions = original_positions


def test_settings_load_from_persisted_file_with_allowlisted_keys(tmp_path):
    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text(
        json.dumps({
            "general": {
                "timezone": "UTC",
                "currency": "EUR",
                "unknown": "ignored",
            },
            "strategy": {
                "signal_threshold": 88,
            },
            "intruder": {
                "enabled": True,
            },
        }),
        encoding="utf-8",
    )
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        user_settings_path=str(settings_file),
    ))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.get("/api/settings")

    assert response.status_code == 200
    settings = response.get_json()["settings"]
    assert settings["general"]["timezone"] == "UTC"
    assert settings["general"]["currency"] == "EUR"
    assert settings["general"]["dark_mode"] is True
    assert settings["strategy"]["signal_threshold"] == 88
    assert "unknown" not in settings["general"]
    assert "intruder" not in settings


def test_settings_update_persists_across_app_restart(tmp_path):
    settings_file = tmp_path / "user_settings.json"
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        user_settings_path=str(settings_file),
    ))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    update_response = client.post(
        "/api/settings/general",
        json={
            "timezone": "UTC",
            "currency": "EUR",
            "unknown": "ignored",
        },
        headers={"X-CSRF-Token": "known-token"},
    )
    assert update_response.status_code == 200
    assert update_response.get_json()["settings"]["timezone"] == "UTC"

    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["general"]["timezone"] == "UTC"
    assert "unknown" not in persisted["general"]

    restarted = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        user_settings_path=str(settings_file),
    ))
    restarted.config["TESTING"] = True
    restarted_client = restarted.test_client()
    with restarted_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = restarted_client.get("/api/settings/general")
    assert response.status_code == 200
    assert response.get_json()["settings"]["timezone"] == "UTC"
    assert response.get_json()["settings"]["currency"] == "EUR"


def test_settings_internal_errors_do_not_leak_exception_details(tmp_path, monkeypatch):
    settings_file = tmp_path / "user_settings.json"
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        user_settings_path=str(settings_file),
    ))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    def fail_atomic_write(path, data):
        raise RuntimeError("secret-config-path leaked")

    monkeypatch.setitem(create_app.__globals__, "_write_json_atomic", fail_atomic_write)

    response = client.post(
        "/api/settings/general",
        json={"timezone": "UTC"},
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 500
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "Internal server error"
    assert "error_id" in body
    assert "secret-config-path" not in response.data.decode()


def test_clear_all_persists_default_settings(tmp_path):
    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text(
        json.dumps({"general": {"timezone": "UTC", "currency": "EUR"}}),
        encoding="utf-8",
    )
    app = create_app(WebConfig(
        secret_key="test-secret",
        admin_password_hash=make_admin_password_hash("correct-password"),
        user_settings_path=str(settings_file),
    ))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
        sess["_csrf_token"] = "known-token"

    response = client.post(
        "/api/settings/clear-all",
        headers={"X-CSRF-Token": "known-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["general"]["timezone"] == "America/New_York"
    assert persisted["general"]["currency"] == "USD"
