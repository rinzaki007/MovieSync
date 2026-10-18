import pytest

from card_templates.openlist import OpenListPlaybackCard, _clean_path
from moviesync.cards import PlaybackProviderCard
from moviesync.errors import ConfigValidationError


class FakeConfigStore:
    def __init__(self, config=None):
        self.config = config or {}

    def get_card_config(self, card_id):
        assert card_id == "openlist"
        return dict(self.config)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_openlist_card_manifest_and_config_validation():
    card = OpenListPlaybackCard({"config_store": FakeConfigStore()})

    assert isinstance(card, PlaybackProviderCard)
    assert card.card_id == "openlist"
    assert card.card_type == "playback_provider"
    assert "playback.list_files" in card.capabilities
    assert "playback.resolve" in card.capabilities
    assert "playback.delete_files" in card.capabilities

    config = card.validate_config({
        "base_url": "http://openlist.local:5244",
        "root_path": "media/movies",
        "token": "secret-token",
    })
    assert config["base_url"] == "http://openlist.local:5244"
    assert config["root_path"] == "/media/movies"
    assert config["token"] == "secret-token"


def test_openlist_optional_playback_prefix_accepts_empty_value():
    card = OpenListPlaybackCard({"config_store": FakeConfigStore()})

    config = card.validate_config({
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "playback_base_url": "",
    })

    assert config["playback_base_url"] == ""


def test_openlist_optional_playback_prefix_rewrites_only_when_configured():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "playback_base_url": "",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({
        "code": 200,
        "data": {"raw_url": "http://openlist.local:5244/d/film.mp4"},
    })

    result = card.resolve_playback("/media/film.mp4")
    assert result["play_url"] == "http://openlist.local:5244/d/film.mp4"


def test_openlist_path_validation_rejects_parent_traversal():
    assert _clean_path("media/movies") == "/media/movies"
    with pytest.raises(ConfigValidationError):
        _clean_path("/media/../private")


def test_openlist_lists_and_normalizes_video_files():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "token": "secret-token",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({
        "code": 200,
        "data": {
            "content": [
                {"name": "z.mp4", "is_dir": False, "size": 1234},
                {"name": "A folder", "is_dir": True},
                {"name": "notes.txt", "is_dir": False},
            ]
        },
    })

    items = card.list_files()

    assert [item["name"] for item in items] == ["A folder", "notes.txt", "z.mp4"]
    video = next(item for item in items if item["name"] == "z.mp4")
    assert video["path"] == "/media/z.mp4"
    assert video["is_video"] is True
    assert card.session.calls[0][0] == "http://openlist.local:5244/api/fs/list"
    assert card.session.calls[0][1]["headers"]["Authorization"] == "secret-token"


def test_openlist_resolves_raw_playback_url_and_enforces_root():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({
        "code": 200,
        "data": {
            "raw_url": "http://openlist.local:5244/d/film.mp4",
            "header": {"User-Agent": "test"},
        },
    })

    result = card.resolve_playback("/media/film.mp4")
    assert result["play_url"] == "http://openlist.local:5244/d/film.mp4"
    assert result["headers"] == {"User-Agent": "test"}

    with pytest.raises(ValueError):
        card.resolve_playback("/outside/film.mp4")



def test_openlist_delete_file_uses_remove_api_and_refreshes_parent_listing():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "token": "secret-token",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({
        "code": 200,
        "data": {
            "content": [
                {"name": "film.mp4", "is_dir": False, "size": 1234},
            ]
        },
    })

    result = card.delete_file("/media/film.mp4")

    assert result == {"path": "/media/film.mp4", "name": "film.mp4"}
    assert card.session.calls[0][0] == "http://openlist.local:5244/api/fs/list"
    assert card.session.calls[0][1]["json"]["refresh"] is True
    assert card.session.calls[1][0] == "http://openlist.local:5244/api/fs/remove"
    assert card.session.calls[1][1]["json"] == {"dir": "/media", "names": ["film.mp4"]}
    assert card.session.calls[1][1]["headers"]["Authorization"] == "secret-token"


def test_openlist_delete_rejects_outside_root_and_directories():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "token": "secret-token",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({
        "code": 200,
        "data": {"content": [{"name": "folder", "is_dir": True}]},
    })

    with pytest.raises(ValueError, match="媒体根目录"):
        card.delete_file("/outside/film.mp4")
    with pytest.raises(ValueError, match="只允许删除单个文件"):
        card.delete_file("/media/folder")
    assert all("/api/fs/remove" not in url for url, _ in card.session.calls)


def test_openlist_delete_rejects_missing_file_and_reports_permission_denial():
    config = {
        "base_url": "http://openlist.local:5244",
        "root_path": "/media",
        "token": "secret-token",
    }
    card = OpenListPlaybackCard({"config_store": FakeConfigStore(config)})
    card.session = FakeSession({"code": 200, "data": {"content": []}})
    with pytest.raises(ValueError, match="文件不存在"):
        card.delete_file("/media/missing.mp4")

    card.session = FakeSession({"code": 403, "message": "forbidden"})
    with pytest.raises(RuntimeError, match="权限不足"):
        card._request("/api/fs/remove", {"dir": "/media", "names": ["film.mp4"]})



def test_openlist_delete_requires_configured_token():
    card = OpenListPlaybackCard({
        "config_store": FakeConfigStore({
            "base_url": "http://openlist.local:5244",
            "root_path": "/media",
        })
    })
    card.session = FakeSession({"code": 200, "data": {"content": []}})

    with pytest.raises(RuntimeError, match="需要配置"):
        card.delete_file("/media/film.mp4")
    assert card.session.calls == []
