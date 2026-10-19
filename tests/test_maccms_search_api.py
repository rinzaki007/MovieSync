from __future__ import annotations

from card_templates.vod_source_manager import _normalize_sources
from moviesync.web.vod_source_manager import _parse_play_sources


def test_parse_play_sources_aligns_multiple_lines_and_filters_invalid_urls():
    parsed = _parse_play_sources(
        "在线播放$$$备用线路",
        "正片$https://cdn.example/movie.m3u8#备用$javascript:alert(1)$$$正片$https://backup.example/movie.mp4",
    )

    assert parsed == [
        {
            "name": "在线播放",
            "episodes": [{"name": "正片", "url": "https://cdn.example/movie.m3u8"}],
        },
        {
            "name": "备用线路",
            "episodes": [{"name": "正片", "url": "https://backup.example/movie.mp4"}],
        },
    ]


def test_search_source_configuration_only_includes_enabled_sources():
    sources = _normalize_sources([
        {"id": "site-on", "name": "启用站点", "url": "https://example.com/api.php/provide/vod/", "enabled": True},
        {"id": "site-off", "name": "停用站点", "url": "https://backup.example/api.php/provide/vod/", "enabled": False},
    ])

    assert [source["id"] for source in sources if source["enabled"]] == ["site-on"]
