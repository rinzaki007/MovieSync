from types import SimpleNamespace

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
    card = OpenListPlaybackCard(FakeConfigStore())

    assert isinstance(card, PlaybackProviderCard)
    assert card.card_id == "openlist"
    assert card.card_type == "playback_provider"
    assert "playback.list_files" in card.capabilities
    assert "playback.resolve" in card.capabilities

    config = card.validate_config({
        "base_url": "http://openlist.local:5244",
        "root_path": "media/movies",
        "token": "secret-token",
    })
    assert config["base_url"] == "http://openlist.local:5244"
    assert config["root_path"] == "/media/movies"
    assert config["token"] == "secret-token"


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
    card = OpenListPlaybackCard(FakeConfigStore(config))
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
    card = OpenListPlaybackCard(FakeConfigStore(config))
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
