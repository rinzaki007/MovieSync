from moviesync.services.media_classification import (
    classify_metadata_item,
    filter_metadata_items_by_category,
)


def test_explicit_movie_type_overrides_a_stale_tv_tab():
    result = classify_metadata_item(
        {"title": "潜伏6", "media_type": "movie"},
        fallback_category="电视剧",
    )

    assert result["tag"] == "电影"
    assert result["media_type"] == "movie"


def test_tv_genres_distinguish_drama_variety_animation_and_documentary():
    assert classify_metadata_item(
        {"title": "综艺测试", "media_type": "tv", "genre_ids": [10764]}
    )["tag"] == "综艺"
    assert classify_metadata_item(
        {"title": "动画测试", "media_type": "tv", "genre_ids": [16]}
    )["tag"] == "动漫"
    assert classify_metadata_item(
        {"title": "纪录片测试", "media_type": "tv", "genre_ids": [99]}
    )["tag"] == "纪录片"
    assert classify_metadata_item(
        {"title": "剧集测试", "media_type": "tv", "genre_ids": [18]}
    )["tag"] == "电视剧"


def test_legacy_provider_uses_selected_category_only_when_type_is_unknown():
    result = classify_metadata_item(
        {"title": "旧版元数据搜索结果"},
        fallback_category="电影",
    )

    assert result["tag"] == "电影"
    assert result["media_type"] == "movie"



def test_category_filter_removes_known_wrong_format_but_keeps_unknown_legacy_items():
    items = [
        {"title": "潜伏", "media_type": "tv"},
        {"title": "潜伏6", "media_type": "movie"},
        {"title": "旧版豆瓣搜索结果"},
    ]

    filtered = filter_metadata_items_by_category(items, "电影")

    assert [item["title"] for item in filtered] == ["潜伏6", "旧版豆瓣搜索结果"]


def test_category_filter_uses_available_tv_genres_without_guessing_when_missing():
    items = [
        {"title": "动画", "media_type": "tv", "genre_ids": [16]},
        {"title": "综艺", "media_type": "tv", "genre_ids": [10764]},
        {"title": "普通剧", "media_type": "tv", "genre_ids": [18]},
        {"title": "未知旧卡片", "media_type": "tv"},
    ]

    assert [item["title"] for item in filter_metadata_items_by_category(items, "动漫")] == [
        "动画",
        "未知旧卡片",
    ]
    assert [item["title"] for item in filter_metadata_items_by_category(items, "电视剧")] == [
        "普通剧",
        "未知旧卡片",
    ]
