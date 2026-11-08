from __future__ import annotations

from moviesync.app import create_app
from moviesync.auth import AuthStore


def test_setup_token_is_persistent_private_and_one_time(tmp_path):
    auth = AuthStore(tmp_path / "auth.json")
    token, created = auth.get_or_create_setup_token()

    assert created is True
    assert token
    assert auth.setup_token_path.stat().st_mode & 0o777 == 0o600

    same_token, created_again = auth.get_or_create_setup_token()
    assert same_token == token
    assert created_again is False

    auth.clear_setup_token()
    assert not auth.setup_token_path.exists()


def test_first_admin_setup_requires_token_outside_test_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("MOVIESYNC_SETUP_TOKEN", raising=False)
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    app.testing = False
    auth = app.extensions["moviesync"]["auth"]
    token, _ = auth.get_or_create_setup_token()
    client = app.test_client()

    denied = client.post(
        "/api/setup",
        json={"username": "admin", "password": "password123"},
    )
    assert denied.status_code == 403
    assert auth.is_initialized() is False

    accepted = client.post(
        "/api/setup",
        json={
            "username": "admin",
            "password": "password123",
            "setup_token": token,
        },
    )
    assert accepted.status_code == 200
    assert auth.is_initialized() is True
    assert not auth.setup_token_path.exists()
