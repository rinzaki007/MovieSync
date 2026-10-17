"""Search candidate explanation regression tests."""
from moviesync.services.search import _candidate_match_explanation


def test_explanation_names_direct_title_and_episode_evidence():
    explanation = _candidate_match_explanation(
        {"title": "兰香如故", "media_type": "tv"},
        "兰香如故 全47集",
        recognized_episode_files=8,
        total_video_count=8,
        declared_episode_count=47,
        scan_complete=True,
    )

    assert "分享标题包含目标名称" in explanation
    assert "已识别 8/8 个视频文件的集数" in explanation
    assert "标注共 47 集" in explanation
    assert "不代表实际文件齐全" in explanation


def test_explanation_does_not_hide_ambiguous_title_or_incomplete_scan():
    explanation = _candidate_match_explanation(
        {"title": "兰香如故", "media_type": "tv"},
        "更新至第08集",
        recognized_episode_files=5,
        total_video_count=8,
        declared_episode_count=None,
        scan_complete=False,
    )

    assert "标题未直接匹配" in explanation
    assert "分享扫描不完整" in explanation
    assert "已识别 5/8 个视频文件的集数" in explanation


def test_explanation_accepts_known_alias_and_original_title():
    alias = _candidate_match_explanation(
        {"title": "兰香如故", "aliases": ["旧梦如故"], "media_type": "tv"},
        "旧梦如故 更新至第08集",
        8, 8, None, True,
    )
    original = _candidate_match_explanation(
        {"title": "兰香如故", "original_title": "The Matrix", "media_type": "movie"},
        "The Matrix 1999 BluRay",
        0, 1, None, True,
    )

    assert "匹配已知别名" in alias
    assert "匹配影视原名" in original
