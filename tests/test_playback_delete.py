from moviesync.app import create_app


class FakePlaybackCard:
    card_id = "openlist"
    capabilities = ("playback.list_files", "playback.resolve", "playback.delete_files")

    def __init__(self, error=None, capabilities=None):
        self.deleted = []
        self.error = error
        self.list_calls = []
        if capabilities is not None:
            self.capabilities = capabilities

    def list_files(self, path=""):
        self.list_calls.append(("legacy", path))
        return []

    def delete_file(self, path):
        if self.error:
            raise RuntimeError(self.error)
        self.deleted.append(path)
        return {"path": path, "name": path.rsplit("/", 1)[-1]}

    def delete_item(self, path, expected_is_dir=None):
        if self.error:
            raise RuntimeError(self.error)
        self.deleted.append((path, expected_is_dir))
        return {"path": path, "name": path.rsplit("/", 1)[-1], "is_dir": expected_is_dir}


class RefreshPlaybackCard(FakePlaybackCard):
    def list_files(self, path="", refresh=False):
        self.list_calls.append(("refresh", path, refresh))
        return []


def make_logged_in_client(tmp_path):
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    client = app.test_client()
    response = client.post(
        "/api/setup",
        json={"username": "admin", "password": "password123"},
    )
    assert response.status_code == 200
    with client.session_transaction() as session:
        csrf_token = session["csrf_token"]
    return app, client, csrf_token


def test_playback_refresh_supports_legacy_card_signature(tmp_path, monkeypatch):
    app, client, _ = make_logged_in_client(tmp_path)
    card = FakePlaybackCard()
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.get("/api/playback/files?path=%2Fmedia&_refresh=1")

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert card.list_calls == [("legacy", "/media")]


def test_playback_refresh_flag_reaches_cards_that_support_it(tmp_path, monkeypatch):
    app, client, _ = make_logged_in_client(tmp_path)
    card = RefreshPlaybackCard()
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.get("/api/playback/files?path=%2Fmedia&_refresh=1")

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert card.list_calls == [("refresh", "/media", True)]


def test_playback_remove_requires_csrf_and_explicit_confirmation(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard()
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    anonymous_client = app.test_client()
    response = anonymous_client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": True},
    )
    assert response.status_code == 401
    assert card.deleted == []

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": True},
    )
    assert response.status_code == 403
    assert card.deleted == []

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 400
    assert "未确认" in response.get_json()["message"]
    assert card.deleted == []

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert card.deleted == [("/media/film.mp4", False)]



def test_playback_remove_folder_requires_item_capability_and_passes_type(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard()
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/Season 01", "is_dir": True, "confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert "文件夹" in response.get_json()["message"]
    assert card.deleted == [("/media/Season 01", True)]


def test_playback_remove_folder_rejected_by_legacy_file_only_card(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard(capabilities=("playback.list_files", "playback.resolve", "playback.delete_files"))
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/Season 01", "is_dir": True, "confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 501
    assert "不支持删除文件夹" in response.get_json()["message"]
    assert card.deleted == []

def test_playback_remove_requires_provider_delete_capability(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard(capabilities=("playback.list_files", "playback.resolve"))
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 501
    assert "不支持删除文件或文件夹" in response.get_json()["message"]
    assert card.deleted == []


def test_playback_remove_returns_openlist_permission_failure(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard(error="OpenList 权限不足或 Token 无效")
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.post(
        "/api/playback/remove",
        json={"path": "/media/film.mp4", "confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 502
    assert response.get_json()["success"] is False
    assert "权限不足" in response.get_json()["message"]


def test_playback_remove_rejects_missing_path(tmp_path, monkeypatch):
    app, client, csrf_token = make_logged_in_client(tmp_path)
    card = FakePlaybackCard()
    monkeypatch.setattr("moviesync.web.playback._provider", lambda: (card, ""))

    response = client.post(
        "/api/playback/remove",
        json={"confirmed": True},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    assert "路径" in response.get_json()["message"]
    assert card.deleted == []
