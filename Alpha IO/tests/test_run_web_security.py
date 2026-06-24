import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_web


def test_run_web_refuses_missing_admin_credentials(monkeypatch, tmp_path):
    for name in ("WEB_PASSWORD", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "ADMIN_PASSWORD_HASH_FILE", "WEB_SECRET_KEY", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH_FILE", str(tmp_path / "missing.hash"))
    monkeypatch.setenv("WEB_DEBUG", "true")

    assert run_web.main() == 1


def test_run_web_refuses_missing_production_secret(monkeypatch):
    for name in ("WEB_SECRET_KEY", "SECRET_KEY", "ADMIN_PASSWORD_HASH", "ADMIN_PASSWORD_HASH_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "local-only-password")
    monkeypatch.delenv("WEB_DEBUG", raising=False)

    assert run_web.main() == 1


def test_run_web_accepts_admin_password_hash_file(monkeypatch, tmp_path):
    hash_file = tmp_path / "dashboard_admin.hash"
    hash_file.write_text("pbkdf2:sha256:1000000$abc$def\n", encoding="utf-8")
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)

    for name in ("WEB_PASSWORD", "ADMIN_PASSWORD", "ADMIN_PASSWORD_HASH", "WEB_SECRET_KEY", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_DEBUG", "true")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH_FILE", str(hash_file))

    import web.app
    monkeypatch.setattr(web.app, "run_server", fake_run_server)

    assert run_web.main() == 0
    assert captured["admin_password_hash_path"] == str(hash_file)
    assert captured["admin_password"] is None
