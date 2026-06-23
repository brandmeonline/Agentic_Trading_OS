import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.app import WebConfig, create_app, make_admin_password_hash


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
