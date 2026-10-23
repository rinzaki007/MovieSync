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


def test_foreign_title_is_not_hidden_by_target_name_in_mixed_source_text():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [
        {"file_name": "第01集.mp4"},
        {"file_name": "第02集.mp4"},
    ]

    assert SearchService._has_conflicting_media_title(
        movie,
        "兰香如故相关频道文案 #斗罗大陆2：绝世唐门",
        videos,
        display_title="兰香如故相关频道文案 #斗罗大陆2：绝世唐门",
    )


def test_pansou_series_relevance_needs_target_title_in_result_or_file_path():
    movie = {
        "title": "兰香如故",
        "media_type": "tv",
        "tag": "电视剧",
        "aliases": ["旧梦如故"],
    }
    generic_files = [
        {"file_name": "第287集.mp4", "relative_path": "第287集.mp4"},
        {"file_name": "第288集.mp4", "relative_path": "第288集.mp4"},
    ]

    assert not SearchService._has_target_title_evidence(movie, "", generic_files)
    assert not SearchService._has_target_title_evidence(
        movie, "#斗罗大陆2：绝世唐门", generic_files
    )
    assert SearchService._has_target_title_evidence(
        movie, "#兰香如故 全47集", generic_files
    )
    assert SearchService._has_target_title_evidence(
        movie, "分享标题不明确",
        [{"file_name": "第01集.mp4", "relative_path": "兰香如故/第01集.mp4"}],
    )
    assert SearchService._has_target_title_evidence(
        movie, "#旧梦如故 全集", generic_files
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


def test_filters_explicit_foreign_series_titles_when_video_names_only_show_episodes():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    # Some cloud shares expose generic episode filenames, so filename evidence alone
    # cannot reveal that the share belongs to a different show.
    videos = [
        {"file_name": "第287集.mp4"},
        {"file_name": "第288集.mp4"},
    ]
    source_titles = [
        "#斗罗大陆2：绝世唐门",
        "#完美世界（2024）4K 更新至288集 晚上更",
        "#斗破苍穹 年番 4K 更新至212集",
    ]

    for source_title in source_titles:
        assert SearchService._has_conflicting_media_title(
            movie, source_title, videos, display_title=source_title
        ), source_title


def test_keeps_generic_series_source_titles_without_a_specific_work_name():
    movie = {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"}
    videos = [{"file_name": "第01集.mp4"}, {"file_name": "第02集.mp4"}]
    for source_title in ("更新至第02集 1080P", "古装仙侠剧合集 全47集"):
        assert not SearchService._has_conflicting_media_title(
            movie, source_title, videos, display_title=source_title
        ), source_title


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
    movie = {
        "title": "兰香如故",
        "original_title": "Different Original Film",
        "media_type": "movie",
        "tag": "电影",
    }
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


def test_keeps_possible_english_alias_when_metadata_has_no_original_title():
    movie = {"title": "兰香如故", "media_type": "movie", "tag": "电影"}
    videos = [
        {"file_name": "The.Matrix.1999.BluRay.1080p.mkv"},
        {"file_name": "The.Matrix.1999.BluRay.Extras.mkv"},
    ]

    assert not SearchService._has_conflicting_media_title(
        movie, "The Matrix 1999 BluRay", videos
    )



def test_declared_episode_count_extracts_full_series_markers_only():
    from moviesync.services.search import _declared_episode_count

    assert _declared_episode_count("兰香如故 全47集 4K") == 47
    assert _declared_episode_count("综艺 共12期 中文字幕") == 12
    assert _declared_episode_count("动画 24集全") == 24
    assert _declared_episode_count("更新至第47集") is None
    assert _declared_episode_count("电影 4K 1080P") is None


def test_declared_episode_count_can_be_extracted_without_forcing_movie_warning():
    from moviesync.services.search import _declared_episode_count

    # Category gating is performed when candidate metadata is assembled.
    assert _declared_episode_count("电影合集 全47集") == 47


def test_candidate_priority_ignores_parenthesized_release_year_for_sequel_titles():
    exact_sequel = {
        "title": "潜伏6 4K BluRay 1080p",
        "file_name": "潜伏6.mkv",
    }
    older_franchise_entry = {
        "title": "潜伏 第06季 全集",
        "file_name": "潜伏.S06E01.mkv",
    }

    exact_score = SearchService._candidate_priority(
        exact_sequel, "潜伏6 (2025)", "movie"
    )
    older_score = SearchService._candidate_priority(
        older_franchise_entry, "潜伏6 (2025)", "movie"
    )
    assert exact_score > older_score


def test_candidate_priority_keeps_numbered_title_as_identity():
    sequel_score = SearchService._candidate_priority(
        {"title": "潜伏6 4K", "file_name": "潜伏6.mkv"},
        "潜伏6",
        "movie",
    )
    franchise_score = SearchService._candidate_priority(
        {"title": "潜伏 第06季", "file_name": "潜伏.S06E01.mkv"},
        "潜伏6",
        "movie",
    )
    assert sequel_score > franchise_score


def test_pansou_series_search_drops_candidates_without_title_evidence():
    import logging
    from types import SimpleNamespace

    class Sources:
        def search(self, movie, config):
            return [
                {
                    "source_id": "pansou", "source_name": "PanSou",
                    "resource_id": "missing-title", "resource_type": "quark_share",
                    "title": "",
                },
                {
                    "source_id": "pansou", "source_name": "PanSou",
                    "resource_id": "foreign-title", "resource_type": "quark_share",
                    "title": "#斗罗大陆2：绝世唐门",
                },
                {
                    "source_id": "pansou", "source_name": "PanSou",
                    "resource_id": "foreign-description", "resource_type": "quark_share",
                    "title": "更新至第288集",
                    "description": "#完美世界 更新至288集",
                },
                {
                    "source_id": "pansou", "source_name": "PanSou",
                    "resource_id": "matching-title", "resource_type": "quark_share",
                    "title": "#兰香如故 全47集",
                },
            ]

    class Storage:
        capabilities = ("storage.accepts.quark_share",)

        def get(self, target_id=None):
            return SimpleNamespace(
                card_id="quark",
                capabilities=self.capabilities,
            )

        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": "quark",
                "files": [
                    {"fid": f"{resource['resource_id']}-1", "file_name": "第01集.mp4", "size": 1},
                    {"fid": f"{resource['resource_id']}-2", "file_name": "第02集.mp4", "size": 1},
                ],
                "token": "test-token",
                "error": None,
            }

    service = SearchService(Sources(), Storage(), logging.getLogger("test"))
    candidates = service.search_movie_candidates(
        {
            "title": "兰香如故",
            "media_type": "tv",
            "tag": "电视剧",
            "storage_target_id": "quark",
        },
        {},
    )

    assert {item["resource_id"] for item in candidates} == {"missing-title", "matching-title"}


def test_pansou_series_search_expands_empty_first_page_until_a_match_is_found():
    import logging
    from types import SimpleNamespace

    class Sources:
        def search(self, movie, config):
            # These shares rank ahead of the target because they contain episode and
            # resolution markers, but all ten belong to another series.
            unrelated = [
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": f"foreign-{index}",
                    "resource_type": "quark_share",
                    "title": f"吞噬星空 S01E01 第01集 1080p #{index}",
                }
                for index in range(10)
            ]
            return unrelated + [
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": "matching-title",
                    "resource_type": "quark_share",
                    "title": "#兰香如故 全47集",
                }
            ]

    class Storage:
        capabilities = ("storage.accepts.quark_share",)

        def get(self, target_id=None):
            return SimpleNamespace(card_id="quark", capabilities=self.capabilities)

        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": "quark",
                "files": [
                    {"fid": f"{resource['resource_id']}-1", "file_name": "第01集.mp4", "size": 1},
                    {"fid": f"{resource['resource_id']}-2", "file_name": "第02集.mp4", "size": 1},
                ],
                "token": "test-token",
                "error": None,
            }

    service = SearchService(Sources(), Storage(), logging.getLogger("test"))
    page_info = {}
    candidates = service.search_movie_candidates(
        {
            "title": "兰香如故",
            "media_type": "tv",
            "tag": "电视剧",
            "storage_target_id": "quark",
        },
        {},
        scan_limit=10,
        page_info=page_info,
    )

    assert [item["resource_id"] for item in candidates] == ["matching-title"]
    assert page_info["next_offset"] == 11
    assert page_info["has_more"] is False

def test_pansou_aggregate_title_does_not_reject_every_share_in_the_bundle():
    import logging
    from types import SimpleNamespace

    aggregate_title = (
        "#斗罗大陆2：绝世唐门 更新至172集 https://pan.quark.cn/s/share-a "
        "#完美世界 更新至288集 https://pan.baidu.com/s/share-b "
        "电视剧：我不是大师 更新至6集"
    )

    class Sources:
        def search(self, movie, config):
            return [
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": "aggregate-share",
                    "resource_type": "quark_share",
                    "title": aggregate_title,
                },
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": "direct-foreign-title",
                    "resource_type": "quark_share",
                    "title": "#斗罗大陆2：绝世唐门",
                },
            ]

    class Storage:
        capabilities = ("storage.accepts.quark_share",)

        def get(self, target_id=None):
            return SimpleNamespace(card_id="quark", capabilities=self.capabilities)

        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": "quark",
                "files": [
                    {"fid": f"{resource['resource_id']}-1", "file_name": "第01集.mp4", "size": 1},
                    {"fid": f"{resource['resource_id']}-2", "file_name": "第02集.mp4", "size": 1},
                ],
                "token": "test-token",
                "error": None,
            }

    service = SearchService(Sources(), Storage(), logging.getLogger("test"))
    candidates = service.search_movie_candidates(
        {
            "title": "兰香如故",
            "media_type": "tv",
            "tag": "电视剧",
            "storage_target_id": "quark",
        },
        {},
    )

    assert [item["resource_id"] for item in candidates] == ["aggregate-share"]


def test_pansou_aggregate_title_is_scoped_to_the_share_url():
    from moviesync.services.search import SearchService

    aggregate_title = (
        "#斗罗大陆2：绝世唐门 更新至172集 https://pan.quark.cn/s/foreign-share "
        "#兰香如故 全47集 https://pan.quark.cn/s/matching-share"
    )

    foreign_title = SearchService._pansou_title_for_share(
        aggregate_title, "https://pan.quark.cn/s/foreign-share"
    )
    matching_title = SearchService._pansou_title_for_share(
        aggregate_title, "https://pan.quark.cn/s/matching-share"
    )

    assert "斗罗大陆2" in foreign_title
    assert "兰香如故" not in foreign_title
    assert "兰香如故" in matching_title
    assert "斗罗大陆2" not in matching_title
    assert SearchService._has_conflicting_media_title(
        {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"},
        foreign_title,
        [{"file_name": "第01集.mp4"}, {"file_name": "第02集.mp4"}],
        display_title=foreign_title,
    )
    assert not SearchService._has_conflicting_media_title(
        {"title": "兰香如故", "media_type": "tv", "tag": "电视剧"},
        matching_title,
        [{"file_name": "第01集.mp4"}, {"file_name": "第02集.mp4"}],
        display_title=matching_title,
    )



def test_candidate_match_explanation_uses_full_title_evidence():
    from moviesync.services.search import _candidate_match_explanation

    explanation = _candidate_match_explanation(
        {"title": "兰香如故", "media_type": "tv"},
        "#兰香如故 全47集",
        recognized_episode_files=1,
        total_video_count=1,
        declared_episode_count=47,
        scan_complete=True,
    )

    assert "分享标题包含目标名称" in explanation
    assert "标题未直接匹配" not in explanation


def test_search_passes_movie_metadata_to_priority_and_expands_until_direct_match():
    import logging
    from types import SimpleNamespace

    class Sources:
        def search(self, movie, config):
            # Generic, playable episode-only shares should not stop the first-page
            # scan before a direct title match is discovered.
            return [
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": f"ambiguous-{index}",
                    "resource_type": "quark_share",
                    "title": f"更新至第{index:02d}集",
                }
                for index in range(1, 11)
            ] + [
                {
                    "source_id": "pansou",
                    "source_name": "PanSou",
                    "resource_id": "direct-match",
                    "resource_type": "quark_share",
                    "title": "#兰香如故 全47集",
                }
            ]

    class Storage:
        capabilities = ("storage.accepts.quark_share",)

        def get(self, target_id=None):
            return SimpleNamespace(card_id="quark", capabilities=self.capabilities)

        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": "quark",
                "files": [
                    {"fid": f"{resource['resource_id']}-1", "file_name": "第01集.mp4", "size": 1},
                ],
                "token": "test-token",
                "error": None,
            }

    service = SearchService(Sources(), Storage(), logging.getLogger("test"))
    page_info = {}
    candidates = service.search_movie_candidates(
        {
            "title": "兰香如故",
            "media_type": "tv",
            "tag": "电视剧",
            "storage_target_id": "quark",
        },
        {},
        scan_limit=10,
        page_info=page_info,
    )

    assert any(item["resource_id"] == "direct-match" for item in candidates)
    assert page_info["next_offset"] == 11
