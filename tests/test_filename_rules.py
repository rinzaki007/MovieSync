from moviesync.services.filename_rules import (
    DEFAULT_TV_MAGIC_REGEX,
    extract_filename_tokens,
    parse_tv_episode,
    render_filename_template,
)


def test_tv_magic_regex_parses_common_release_names():
    assert parse_tv_episode("Stranger.Things.S04E01.2022.NF.WEB-DL.2160p.HEVC.HDR.DDP.mkv") == (4, 1)
    assert parse_tv_episode("Stranger.Things.S02E09.2160p.BluRay.x265.10bit.DTS.mkv") == (2, 9)
    assert parse_tv_episode("Show.S01E08.mkv", DEFAULT_TV_MAGIC_REGEX) == (1, 8)


def test_episode_parser_keeps_legacy_episode_only_names():
    assert parse_tv_episode("兰香如故 第47集.mkv") == (None, 47)
    assert parse_tv_episode("Show.EP12.mp4") == (None, 12)


def test_invalid_custom_magic_regex_falls_back_to_standard_parser():
    assert parse_tv_episode("Show.S02E03.mkv", {"pattern": "(", "replace": ""}) == (2, 3)


def test_filename_template_tokens():
    file_name = "黑镜.S02E03.2023-10-26.高清版.mkv"
    tokens = extract_filename_tokens(file_name, "黑镜")
    assert tokens["TASKNAME"] == "黑镜"
    assert tokens["S"] == "02"
    assert tokens["SXX"] == "S02"
    assert tokens["E"] == "3"
    assert tokens["EXT"] == "mkv"
    assert tokens["DATE"] == "20231026"
    assert tokens["YEAR"] == "2023"
    assert tokens["CHINESE"] == "黑镜"
    assert tokens["VER"] == "高清版"
    assert render_filename_template(file_name, "{TASKNAME}.{SXX}E{II}.{EXT}", "黑镜") == "黑镜.S02E03.mkv"
    assert render_filename_template(file_name, "{TASKNAME}.{SXX}E{III}.{EXT}", "黑镜") == "黑镜.S02E003.mkv"


def test_episode_parser_supports_pure_numeric_filenames():
    assert parse_tv_episode("1.mkv") == (None, 1)
    assert parse_tv_episode("2.mp4") == (None, 2)
    assert parse_tv_episode("003.avi") == (None, 3)
    assert parse_tv_episode("1080.mkv") == (None, None)
    assert parse_tv_episode("2024.mp4") == (None, None)
    assert parse_tv_episode("Show.1080p.mkv") == (None, None)

def test_quark_legacy_episode_parser_uses_shared_filename_rules():
    from moviesync.clients.quark import clean_tv_filename

    cases = [
        ("Show.S02E09.mkv", 9),
        ("Show.EP12.mp4", 12),
        ("兰香如故 第47集.mkv", 47),
        ("1.mkv", 1),
        ("Show.1080p.mkv", None),
        ("2024.mp4", None),
    ]
    for file_name, expected_episode in cases:
        episode, unchanged_name = clean_tv_filename(file_name)
        assert episode == expected_episode
        assert unchanged_name == file_name

def test_legacy_nested_unbounded_magic_regex_is_skipped_at_runtime():
    # Old/imported config may bypass current save-time validation. Parsing must
    # skip an obviously dangerous pattern instead of executing it on remote names.
    assert parse_tv_episode(
        "A filename with no episode marker.mkv",
        {"pattern": r"(.+)+$", "replace": r"\\1"},
    ) == (None, None)




def test_episode_parser_supports_season_episode_separator_variants():
    assert parse_tv_episode("Show.S02.E07.mkv") == (2, 7)
    assert parse_tv_episode("Show.S02_EP07.mkv") == (2, 7)
    assert parse_tv_episode("Show.S 02 E 07.mkv") == (2, 7)
    assert parse_tv_episode("Show.S02EP007.mkv") == (2, 7)


def test_episode_parser_supports_chinese_season_and_episode_names():
    assert parse_tv_episode("某某剧 第2季第7集.mkv") == (2, 7)
    assert parse_tv_episode("某某剧 第 12 季 第 103 话.mp4") == (12, 103)


def test_episode_parser_does_not_mistake_year_or_resolution_for_episode():
    assert parse_tv_episode("Show.2024.1080p.WEB-DL.mkv") == (None, None)
    assert parse_tv_episode("Show.2160p.2023.mkv") == (None, None)
    assert parse_tv_episode("Show.S02.2024.mkv") == (None, None)
