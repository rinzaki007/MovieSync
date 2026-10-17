"""Additional media-name regression cases for Chinese and international releases."""
import pytest

from moviesync.services.filename_rules import parse_media_filename, parse_tv_episode


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("繁花 第2季 第08集 4K.mkv", (2, 8)),
        ("Some.Show.S02E011.WEB-DL.mkv", (2, 11)),
        ("Running.Man.E705.2024.06.02.mp4", (None, 705)),
        ("动画番剧 S01E24 1080p.mkv", (1, 24)),
        ("第12期 某综艺 2025.mp4", (None, 12)),
    ],
)
def test_episode_parser_handles_media_categories_without_using_year_or_quality(file_name, expected):
    assert parse_tv_episode(file_name) == expected


@pytest.mark.parametrize(
    ("file_name", "expected_type", "expected_episode"),
    [
        ("Running.Man.2024.06.02.E705.mp4", "variety", 705),
        ("动画番剧.S01E24.1080p.mkv", "anime", 24),
        ("老剧 第2季 第08集.mkv", "tv", 8),
        ("电影 2024 2160p BluRay.mkv", "movie", None),
        ("纪录片 第03集.mp4", "tv", 3),
    ],
)
def test_media_type_and_episode_regressions(file_name, expected_type, expected_episode):
    parsed = parse_media_filename(file_name)
    assert parsed["media_type"] == expected_type
    assert parsed["episode"] == expected_episode
