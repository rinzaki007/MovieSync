from __future__ import annotations

from types import SimpleNamespace

import pytest

from card_templates import maccms
from moviesync.errors import ConfigValidationError


def _public_dns(monkeypatch):
    monkeypatch.setattr(
        maccms.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", args[1])),
        ],
    )


def test_normalize_sources_assigns_stable_ids_and_defaults_enabled(monkeypatch):
    _public_dns(monkeypatch)

    sources = maccms._normalize_sources([
        {"name": "影视站 A", "url": "https://example.com/api.php/provide/vod/"},
    ])

    assert len(sources) == 1
    assert sources[0]["name"] == "影视站 A"
    assert sources[0]["enabled"] is True
    assert sources[0]["id"].startswith("source-")
    assert sources[0]["url"].startswith("https://example.com/")


def test_normalize_sources_rejects_private_addresses():
    with pytest.raises(ConfigValidationError, match="内网"):
        maccms._normalize_sources([
            {"name": "内网站", "url": "http://127.0.0.1/api.php/provide/vod/"},
        ])


def test_normalize_sources_rejects_duplicate_urls(monkeypatch):
    _public_dns(monkeypatch)

    with pytest.raises(ConfigValidationError, match="重复"):
        maccms._normalize_sources([
            {"id": "site-a", "name": "站点 A", "url": "https://example.com/api.php/provide/vod/"},
            {"id": "site-b", "name": "站点 B", "url": "https://example.com/api.php/provide/vod/"},
        ])


def test_check_reports_json_maccms_response(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(
        maccms.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"code": 1, "msg": "数据列表", "list": []},
        ),
    )
    card = maccms.MacCMSSourceManagerCard()

    result = card.check({
        "sources": [
            {"id": "site-a", "name": "站点 A", "url": "https://example.com/api.php/provide/vod/", "enabled": True},
        ],
    })

    assert result["status"] == "healthy"
    assert result["sources"][0]["status"] == "healthy"
    assert "JSON" in result["sources"][0]["message"]


def test_check_explains_non_json_response(monkeypatch):
    _public_dns(monkeypatch)
    monkeypatch.setattr(
        maccms.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        ),
    )
    card = maccms.MacCMSSourceManagerCard()

    result = card.check({
        "sources": [
            {"id": "site-a", "name": "站点 A", "url": "https://example.com/api.php/provide/vod/", "enabled": True},
        ],
    })

    assert result["status"] == "unavailable"
    assert result["sources"][0]["status"] == "invalid"
    assert "不是有效 JSON" in result["sources"][0]["message"]


def test_manifest_declares_connection_check():
    card = maccms.MacCMSSourceManagerCard()

    assert "maccms.check" in card.capabilities
    assert card.manifest.config_fields[0]["editor"] == "maccms_sources"
