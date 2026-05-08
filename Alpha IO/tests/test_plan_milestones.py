import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.app import WebConfig, create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def _authed_client():
    app = create_app(WebConfig(secret_key="test-secret", debug=False))
    app.config["TESTING"] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["username"] = "admin"
    return client


# Milestone 1

def test_m1_health_endpoint_contract():
    app = create_app(WebConfig(secret_key="test-secret", debug=False))
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.get("/api/health")
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["status"] == "ok"
    assert "timestamp" in payload
    assert "version" in payload


def test_m1_route_guard_redirects_anonymous_users():
    app = create_app(WebConfig(secret_key="test-secret", debug=False))
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302)
    assert "/login" in r.location


def test_m1_build_smoke_python_compile():
    cmd = [sys.executable, "-m", "compileall", "-q", str(REPO_ROOT / "Alpha IO")]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# Milestones 2-5 test placeholders with concrete sanity checks aligned to plan

def test_m2_market_data_api_is_non_blocking():
    client = _authed_client()
    r = client.get("/api/prices")
    assert r.status_code == 200
    assert isinstance(r.get_json(), dict)


def test_m3_training_workspace_route_renders():
    client = _authed_client()
    r = client.get("/analytics")
    assert r.status_code == 200


def test_m4_manual_trade_validation_blocks_bad_payload():
    client = _authed_client()
    r = client.post("/api/place-order", json={"symbol": "", "qty": 0, "side": "buy"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is False


def test_m5_readiness_surface_components_endpoint():
    client = _authed_client()
    r = client.get("/api/components")
    assert r.status_code == 200
    payload = r.get_json()
    assert isinstance(payload, dict)
    assert "orchestrator" in payload
