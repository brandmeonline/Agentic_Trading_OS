import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_web


def test_run_web_refuses_missing_admin_credentials(monkeypatch):
    for name in ("WEB_PASSWORD", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "WEB_SECRET_KEY", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_DEBUG", "true")

    assert run_web.main() == 1


def test_run_web_refuses_missing_production_secret(monkeypatch):
    for name in ("WEB_SECRET_KEY", "SECRET_KEY", "ADMIN_PASSWORD_HASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "local-only-password")
    monkeypatch.delenv("WEB_DEBUG", raising=False)

    assert run_web.main() == 1
