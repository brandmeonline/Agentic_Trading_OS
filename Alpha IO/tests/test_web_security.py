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
