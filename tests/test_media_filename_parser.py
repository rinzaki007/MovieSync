"""Coverage for structured movie, TV, anime, and variety filename parsing."""
import pytest

from moviesync.services.filename_rules import parse_media_filename


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        (
            "肖申克的救赎 (1994) 1080p BluRay.mkv",
            {"name": "肖申克的救赎", "media_type": "movie", "year": 1994,
             "season": None, "episode": None},
        ),
        (
            "The.Matrix.1999.1080p.BluRay.x265.mkv",
            {"name": "The Matrix", "media_type": "movie", "year": 1999,
             "season": None, "episode": None},
        ),
        (
            "庆余年.S02E07.1080p.WEB-DL.mkv",
            {"name": "庆余年", "media_type": "tv", "year": None,
             "season": 2, "episode": 7},
        ),
        (
            "甄嬛传 第12集.mp4",
            {"name": "甄嬛传", "media_type": "tv", "year": None,
             "season": None, "episode": 12},
        ),
        (
            "进击的巨人 动漫 S04E05 1080p.mkv",
            {"name": "进击的巨人", "media_type": "anime", "year": None,
             "season": 4, "episode": 5},
        ),
        (
            "Running Man 2024.06.02 E705.mp4",
            {"name": "Running Man", "media_type": "variety", "year": 2024,
             "season": None, "episode": 705},
        ),
        (
            "某综艺 第12期 2025.mp4",
            {"name": "某综艺", "media_type": "variety", "year": 2025,
             "season": None, "episode": 12},
        ),
    ],
)
def test_parse_media_filename_common_formats(file_name, expected):
    parsed = parse_media_filename(file_name)
    for key, value in expected.items():
        assert parsed[key] == value


def test_parse_media_filename_captures_multi_episode_range():
    parsed = parse_media_filename("某剧.S01E07-E08.1080p.mkv")
    assert parsed["media_type"] == "tv"
    assert parsed["season"] == 1
    assert parsed["episode"] == 7
    assert parsed["episode_end"] == 8


def test_parse_media_filename_does_not_treat_resolution_as_episode():
    parsed = parse_media_filename("一部没有年份的电影 1080p.mkv")
    assert parsed["episode"] is None
    assert parsed["season"] is None
    assert parsed["media_type"] == "unknown"


def test_parse_media_filename_keeps_unrecognized_name_and_extension():
    parsed = parse_media_filename("自定义名称.avi")
    assert parsed["name"] == "自定义名称"
    assert parsed["media_type"] == "unknown"
    assert parsed["extension"] == "avi"
