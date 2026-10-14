"""Regression tests for the server-backed filename recognition preview."""

from moviesync.app import create_app


def test_filename_preview_uses_shared_parser_and_unsaved_rules(tmp_path):
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    try:
        setup = client.post(
            "/api/setup",
            json={"username": "admin", "password": "password123"},
        )
        assert setup.status_code == 200
        with client.session_transaction() as session:
            csrf_token = session.get("csrf_token")

        response = client.post(
            "/api/filename-recognition/preview",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "file_name": "Raw_S02_E07.mkv",
                "source_id": "pansou",
                "rules": [{
                    "pattern": r"^Raw_S(\\d+)_E(\\d+)[.]mkv$",
                    "replace": r"Show.S\\1E\\2.mkv",
                    "source_ids": ["pansou"],
                }],
            },
        )

        assert response.status_code == 200
        result = response.get_json()["result"]
        assert result["season"] == 2
        assert result["episode"] == 7
        assert result["rule_source"] == "自定义规则"
        assert result["media_type"] == "tv"
    finally:
        services["tasks"].shutdown()
        services["subscriptions"].stop_scheduler()


def test_filename_preview_rejects_unsafe_unsaved_rules(tmp_path):
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    try:
        setup = client.post(
            "/api/setup",
            json={"username": "admin", "password": "password123"},
        )
        assert setup.status_code == 200
        with client.session_transaction() as session:
            csrf_token = session.get("csrf_token")

        response = client.post(
            "/api/filename-recognition/preview",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "file_name": "OddName.mkv",
                "rules": [{"pattern": r"(.+)+$", "replace": r"Show.S01E\\1.mkv"}],
            },
        )

        assert response.status_code == 400
        assert "不安全" in response.get_json()["message"]
    finally:
        services["tasks"].shutdown()
        services["subscriptions"].stop_scheduler()
