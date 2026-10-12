"""电视剧搜索标题准确率回归测试。"""

from moviesync.services.search import SearchService


def test_rejects_share_whose_title_and_video_names_belong_to_another_show():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "吞噬星空.S01E01.1080p.mkv"},
        {"file_name": "吞噬星空.S01E02.1080p.mkv"},
        {"file_name": "吞噬星空.S01E03.1080p.mkv"},
    ]

    assert SearchService._has_conflicting_tv_title(
        movie, "吞噬星空 更新至第03集", videos
    )


def test_keeps_matching_title_even_when_share_has_other_descriptive_words():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "兰香如故.S01E01.1080p.mkv"},
        {"file_name": "兰香如故.S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_tv_title(
        movie, "古装剧合集 兰香如故 更新至第02集", videos
    )


def test_does_not_reject_titleless_share_without_clear_foreign_title_evidence():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "S01E01.1080p.mkv"},
        {"file_name": "S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_tv_title(
        movie, "更新至第02集 1080P", videos
    )


def test_movie_search_is_not_subject_to_tv_title_filter():
    movie = {"title": "兰香如故", "media_type": "movie", "tag": "电影"}
    videos = [
        {"file_name": "吞噬星空.S01E01.1080p.mkv"},
        {"file_name": "吞噬星空.S01E02.1080p.mkv"},
    ]

    assert not SearchService._has_conflicting_tv_title(
        movie, "吞噬星空", videos
    )
