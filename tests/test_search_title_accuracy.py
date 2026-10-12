"""影视资源搜索标题准确率回归测试。"""

from moviesync.services.search import SearchService


def test_rejects_tv_share_clearly_belonging_to_another_show():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "吞噬星空.S01E01.1080p.mkv"},
        {"file_name": "吞噬星空.S01E02.1080p.mkv"},
        {"file_name": "吞噬星空.S01E03.1080p.mkv"},
    ]

    assert SearchService._has_conflicting_media_title(
        movie, "吞噬星空 更新至第03集", videos
    )


def test_keeps_matching_title_even_with_descriptive_words():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "兰香如故.S01E01.1080p.mkv"},
        {"file_name": "兰香如故.S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_media_title(
        movie, "古装剧合集 兰香如故 更新至第02集", videos
    )


def test_keeps_ambiguous_share_without_clear_foreign_title_evidence():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "S01E01.1080p.mkv"},
        {"file_name": "S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_media_title(
        movie, "更新至第02集 1080P", videos
    )


def test_movie_search_filters_clear_mismatched_chinese_title():
    movie = {"title": "兰香如故", "media_type": "movie", "tag": "电影"}
    videos = [
        {"file_name": "吞噬星空.S01E01.1080p.mkv"},
        {"file_name": "吞噬星空.S01E02.1080p.mkv"},
    ]

    assert SearchService._has_conflicting_media_title(
        movie, "吞噬星空", videos
    )


def test_movie_search_can_check_english_original_titles():
    movie = {"title": "兰香如故", "media_type": "movie", "tag": "电影"}
    videos = [
        {"file_name": "The.Matrix.1999.BluRay.1080p.mkv"},
        {"file_name": "The.Matrix.1999.BluRay.Extras.mkv"},
    ]

    assert SearchService._has_conflicting_media_title(
        movie, "The Matrix 1999 BluRay", videos
    )


def test_keeps_candidate_matching_known_alternate_title():
    movie = {
        "title": "兰香如故",
        "aliases": ["旧梦如故"],
        "media_type": "tv",
        "tag": "电视剧",
    }
    videos = [
        {"file_name": "旧梦如故.S01E01.1080p.mkv"},
        {"file_name": "旧梦如故.S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_media_title(
        movie, "旧梦如故 更新至第02集", videos
    )


def test_filters_anime_and_variety_mismatches_too():
    anime = {"title": "兰香如故", "media_type": "anime", "tag": "动画"}
    anime_files = [
        {"file_name": "吞噬星空.S01E01.1080p.mkv"},
        {"file_name": "吞噬星空.S01E02.1080p.mkv"},
    ]
    variety = {"title": "兰香如故", "media_type": "variety", "tag": "综艺"}
    variety_files = [
        {"file_name": "奔跑吧 第2024期.mp4"},
        {"file_name": "奔跑吧 第2025期.mp4"},
    ]

    assert SearchService._has_conflicting_media_title(anime, "吞噬星空 动画", anime_files)
    assert SearchService._has_conflicting_media_title(variety, "奔跑吧 综艺合集", variety_files)
