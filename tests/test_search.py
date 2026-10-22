"""资源搜索服务单元测试。

使用 Fake Telegram/Quark，验证多频道搜索结果按分享 ID 去重且顺序稳定，并确保 stoken 不泄露到候选结果。
"""
from moviesync.services.search import SearchService


class FakeResourceSources:
    def search(self, movie, config):
        return [
            {"source_id": "telegram", "source_name": "Telegram", "channel": "first", "pwd_id": "same123"},
            {"source_id": "telegram", "source_name": "Telegram", "channel": "first", "pwd_id": "same123"},
        ]


class FakeStorageTargets:
    def resolve_resource(self, resource, target_id=None):
        return {
            "target_id": target_id or "quark",
            "files": [{"fid": "f1", "file_name": "Show.S01E01.1080p.mkv", "size": 1}],
            "token": "token",
            "error": None,
        }


def test_search_deduplicates_share_ids_and_is_stable():
    import logging

    service = SearchService(FakeResourceSources(), FakeStorageTargets(), logging.getLogger("test"))
    candidates = service.search_movie_candidates({"title": "Show"}, {"channels": []})
    assert len(candidates) == 1
    assert candidates[0]["channel"] == "first"
    assert "stoken" not in candidates[0]


def test_search_reports_detected_episode_coverage_without_filling_gaps():
    import logging

    class EpisodeStorage(FakeStorageTargets):
        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": target_id or "quark",
                "files": [
                    {"fid": "f1", "file_name": "Show.S01E01.mkv", "size": 1},
                    {"fid": "f2", "file_name": "Show.S01E03.mkv", "size": 1},
                    {"fid": "f3", "file_name": "Show.S01E03.1080p.mkv", "size": 1},
                    {"fid": "f4", "file_name": "Show.S02E01.mkv", "size": 1},
                    {"fid": "f5", "file_name": "Show.extra.mkv", "size": 1},
                ],
                "token": "token",
                "error": None,
            }

    service = SearchService(
        FakeResourceSources(),
        EpisodeStorage(),
        logging.getLogger("test"),
    )
    candidates = service.search_movie_candidates({"title": "Show"}, {"channels": []})

    assert len(candidates) == 1
    assert candidates[0]["episode_coverage"] == "S01 E01、E03 · S02 E01（共 3 集）"
    assert candidates[0]["episode_count"] == 3
    assert candidates[0]["recognized_episode_files"] == 4
    assert candidates[0]["unrecognized_video_files"] == 1
    assert candidates[0]["additional_episode_versions"] == 1


def test_search_episode_count_is_zero_when_filenames_are_not_recognized():
    import logging

    class UnknownEpisodeStorage(FakeStorageTargets):
        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": target_id or "quark",
                "files": [{"fid": "f1", "file_name": "Show.special-version.mkv", "size": 1}],
                "token": "token",
                "error": None,
            }

    service = SearchService(
        FakeResourceSources(),
        UnknownEpisodeStorage(),
        logging.getLogger("test"),
    )
    candidates = service.search_movie_candidates({"title": "Show"}, {"channels": []})

    assert candidates[0]["episode_count"] == 0
    assert candidates[0]["episode_coverage"] == "集数暂未识别"
    assert candidates[0]["recognized_episode_files"] == 0
    assert candidates[0]["unrecognized_video_files"] == 1
    assert candidates[0]["additional_episode_versions"] == 0


class FakeDoubanResponse:
    status_code = 200


class FakeDoubanHttp:
    def request_json(self, *args, **kwargs):
        return FakeDoubanResponse(), [
            {
                "title": "测试电影",
                "img": "https://example.com/poster.jpg",
                "year": "2025",
                "id": "1234567",
            }
        ]


def test_douban_search_does_not_use_year_as_rating(monkeypatch):
    from moviesync.clients.douban import DoubanClient

    client = DoubanClient()
    monkeypatch.setattr(client, "http", FakeDoubanHttp())

    results = client.search("测试电影")

    assert len(results) == 1
    assert results[0]["title"] == "测试电影"
    assert results[0]["year"] == "2025"
    assert results[0]["rate"] == "暂无"


def test_folder_creation_exception_is_not_returned_to_user():
    import logging

    class ExplodingStorageTargets(FakeStorageTargets):
        def create_folder(self, *_args, **_kwargs):
            raise RuntimeError("upstream cookie=SECRET_COOKIE_VALUE")

    service = SearchService(
        FakeResourceSources(),
        ExplodingStorageTargets(),
        logging.getLogger("test"),
    )

    ok, message, _counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is False
    assert message == "创建专属文件夹失败: 请检查存储目标配置或查看服务日志"
    assert "SECRET_COOKIE_VALUE" not in message


def test_resource_resolution_error_is_not_returned_to_transfer_task():
    import logging

    class ExplodingResolveTargets(FakeStorageTargets):
        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": "quark",
                "files": [],
                "token": None,
                "error": "upstream cookie=SECRET_COOKIE_VALUE",
            }

    service = SearchService(
        FakeResourceSources(),
        ExplodingResolveTargets(),
        logging.getLogger("test"),
    )

    ok, message, _counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is False
    assert message == "分享资源解析失败，请检查分享链接或查看服务日志"
    assert "SECRET_COOKIE_VALUE" not in message


def test_transfer_plugin_failure_message_is_sanitized():
    import logging

    class ExplodingTransferTargets(FakeStorageTargets):
        def create_folder(self, *_args, **_kwargs):
            return "folder-1"

        def transfer(self, *_args, **_kwargs):
            return False, "upstream cookie=SECRET_COOKIE_VALUE"

    service = SearchService(
        FakeResourceSources(),
        ExplodingTransferTargets(),
        logging.getLogger("test"),
    )

    ok, message, counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is False
    assert message == "转存失败，请检查存储目标配置或查看服务日志"
    assert counts["uncertain"] is False
    assert "SECRET_COOKIE_VALUE" not in message



def test_transfer_refreshes_share_state_and_temporary_token():
    import logging

    class RotatingTokenStorage(FakeStorageTargets):
        def __init__(self):
            self.resolve_count = 0
            self.transfer_token = None

        def resolve_resource(self, resource, target_id=None):
            self.resolve_count += 1
            return {
                "target_id": target_id or "quark",
                "files": [
                    {"fid": "f1", "file_name": "Show.S01E01.1080p.mkv", "size": 1}
                ],
                "token": f"token-{self.resolve_count}",
                "error": None,
            }

        def create_folder(self, *_args, **_kwargs):
            return "folder-1"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.transfer_token = token
            return True, "ok"

    storage = RotatingTokenStorage()
    service = SearchService(FakeResourceSources(), storage, logging.getLogger("test"))
    resource = {"pwd_id": "same123", "storage_target_id": "quark"}

    # Search can populate the short-lived cache, but its token must not be reused for transfer.
    _files, search_token, _error, _target_id = service._get_resource_files(resource)
    assert search_token == "token-1"

    ok, _message, _counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "storage_target_id": "quark", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is True
    assert storage.resolve_count == 2
    assert storage.transfer_token == "token-2"


def test_search_and_transfer_accept_provider_neutral_resource_ids():
    import logging

    class GenericSources:
        def search(self, movie, config):
            return [
                {
                    "source_id": "cloud-b",
                    "source_name": "Cloud B",
                    "resource_id": "share-456",
                    "resource_type": "cloud_b_share",
                    "storage_target_id": "cloud-b",
                    "url": "https://example.invalid/share-456",
                },
                {
                    "source_id": "cloud-b-secondary",
                    "source_name": "Cloud B mirror",
                    "resource_id": "share-456",
                    "resource_type": "cloud_b_share",
                    "storage_target_id": "cloud-b",
                    "url": "https://example.invalid/share-456-mirror",
                },
                {
                    "source_id": "cloud-c",
                    "source_name": "Cloud C",
                    "resource_id": "share-456",
                    "resource_type": "cloud_c_share",
                    "storage_target_id": "cloud-c",
                    "url": "https://example.invalid/cloud-c/share-456",
                },
            ]

    class GenericStorage:
        def __init__(self):
            self.transferred = None

        def resolve_resource(self, resource, target_id=None):
            assert resource["resource_id"] == "share-456"
            assert resource["resource_type"] in {"cloud_b_share", "cloud_c_share"}
            assert "pwd_id" not in resource
            return {
                "target_id": target_id or "cloud-b",
                "files": [{"fid": "file-1", "file_name": "Show.S01E01.mkv", "size": 1}],
                "token": "fresh-token",
                "error": None,
            }

        def create_folder(self, *_args, **_kwargs):
            return "folder-1"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.transferred = (resource, files, storage_target_id, token)
            return True, "ok"

    storage = GenericStorage()
    service = SearchService(GenericSources(), storage, logging.getLogger("test"))
    candidates = service.search_movie_candidates({"title": "Show"}, {})
    assert len(candidates) == 2
    assert {candidate["resource_type"] for candidate in candidates} == {
        "cloud_b_share", "cloud_c_share"
    }
    candidate = next(item for item in candidates if item["resource_type"] == "cloud_b_share")
    assert candidate["resource_id"] == "share-456"
    assert "pwd_id" not in candidate

    ok, message, counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {**candidate, "files": [{"fid": "file-1"}]},
        "0",
    )
    assert ok is True
    assert counts["success"] == 1
    assert storage.transferred[0]["resource_id"] == "share-456"
    assert storage.transferred[0]["resource_type"] == "cloud_b_share"
    assert "pwd_id" not in storage.transferred[0]
    assert storage.transferred[2] == "cloud-b"
    assert storage.transferred[3] == "fresh-token"



def test_candidate_search_uses_selected_target_capabilities_and_pins_target():
    import logging
    from types import SimpleNamespace

    class SelectedSources:
        def __init__(self):
            self.search_context = None

        def search(self, movie, config):
            self.search_context = movie
            return [{
                "source_id": "pansou",
                "source_name": "PanSou",
                "resource_id": "aliyun-share-1",
                "resource_type": "aliyun_share",
                "url": "https://www.alipan.com/s/aliyun-share-1",
            }]

    class SelectedTargets(FakeStorageTargets):
        def __init__(self):
            self.resolved_target = None

        def get(self, target_id=None):
            assert target_id == "aliyun"
            return SimpleNamespace(
                card_id="aliyun",
                capabilities=("storage.accepts.aliyun_share",),
            )

        def resolve_resource(self, resource, target_id=None):
            self.resolved_target = target_id
            return {
                "target_id": target_id,
                "files": [{"fid": "v1", "file_name": "Show.S01E01.1080p.mkv", "size": 10}],
                "token": None,
                "error": None,
            }

    sources = SelectedSources()
    targets = SelectedTargets()
    service = SearchService(sources, targets, logging.getLogger("test"))

    candidates = service.search_movie_candidates(
        {"title": "Show"},
        {"cards": {}},
        storage_target_id="aliyun",
    )

    assert sources.search_context["storage_target_id"] == "aliyun"
    assert sources.search_context["resource_types"] == ["aliyun_share"]
    assert targets.resolved_target == "aliyun"
    assert len(candidates) == 1
    assert candidates[0]["resource_type"] == "aliyun_share"
    assert candidates[0]["storage_target_id"] == "aliyun"



def test_candidate_search_pages_prioritize_recognizable_names_without_dropping_unknowns():
    import logging

    class PagedSources:
        def search(self, movie, config):
            return [
                {"source_id": "unknown", "source_name": "Unknown", "resource_id": "r-unknown",
                 "title": "mystery release"},
                {"source_id": "episode-2", "source_name": "Episode", "resource_id": "r-2",
                 "title": "Show S01E02 1080p.mkv"},
                {"source_id": "episode-1", "source_name": "Episode", "resource_id": "r-1",
                 "title": "Show S01E01 1080p.mkv"},
            ]

    class PagedStorage(FakeStorageTargets):
        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": target_id or "quark",
                "files": [{"fid": resource["resource_id"], "file_name": "Show.S01E01.mkv", "size": 1}],
                "token": None,
                "error": None,
            }

    service = SearchService(PagedSources(), PagedStorage(), logging.getLogger("test"))
    first_page = {}
    first = service.search_movie_candidates(
        {"title": "Show"}, {}, offset=0, scan_limit=2, page_info=first_page
    )

    assert [item["resource_id"] for item in first] == ["r-2", "r-1"]
    assert first_page == {"next_offset": 2, "has_more": True, "total_discovered": 3}

    second_page = {}
    second = service.search_movie_candidates(
        {"title": "Show"}, {}, offset=first_page["next_offset"],
        scan_limit=2, page_info=second_page,
    )

    assert [item["resource_id"] for item in second] == ["r-unknown"]
    assert second_page == {"next_offset": 3, "has_more": False, "total_discovered": 3}


def test_search_reports_video_file_truncation_without_losing_total_statistics():
    import logging

    class LargeShareStorage(FakeStorageTargets):
        def resolve_resource(self, resource, target_id=None):
            files = [
                {
                    "fid": f"f{index}",
                    "file_name": f"Show.S01E{index:03d}.mkv",
                    "size": 1024,
                }
                for index in range(1, 206)
            ]
            return {
                "target_id": target_id or "quark",
                "files": files,
                "token": "token",
                "error": None,
            }

    service = SearchService(
        FakeResourceSources(),
        LargeShareStorage(),
        logging.getLogger("test"),
    )
    candidates = service.search_movie_candidates({"title": "Show"}, {"channels": []})

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["file_count"] == 200
    assert len(candidate["files"]) == 200
    assert candidate["total_video_count"] == 205
    assert candidate["files_truncated"] is True
    assert candidate["episode_count"] == 205
    assert candidate["total_size"] == 205 * 1024
    assert "实际共 205 个" in candidate["summary"]
    assert "仅展示前 200 个" in candidate["summary"]


def test_candidate_priority_prefers_clear_episode_ranges_but_keeps_unknown_candidates():
    clear = {
        "title": "Show S01E01-E08 1080p WEB-DL.mkv",
        "resource_id": "clear",
    }
    vague = {"title": "Show share resource", "resource_id": "vague"}
    assert SearchService._candidate_priority(clear, "Show") > SearchService._candidate_priority(
        vague, "Show"
    )
    # Unknown/low-signal candidates remain eligible; ranking is not a filter.
    assert isinstance(SearchService._candidate_priority(vague, "Show"), int)


def test_transfer_success_is_verified_when_storage_card_supports_destination_listing():
    import logging

    class VerifiableStorage(FakeStorageTargets):
        capabilities = ("storage.list_destination_files",)

        def get(self, _target_id=None):
            return self

        def create_folder(self, name, parent_id="0", target_id=None):
            return "folder-1"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            return True, "转存成功"

        def list_destination_files(self, parent_id="0"):
            assert parent_id == "folder-1"
            return [{"fid": "saved-1", "file_name": "Show.S01E01.1080p.mkv", "is_dir": False}]

    service = SearchService(FakeResourceSources(), VerifiableStorage(), logging.getLogger("test"))

    ok, message, counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "storage_target_id": "quark", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is True
    assert "核实 1 个文件" in message
    assert counts == {"success": 1, "skipped": 0, "failed": 0}


def test_transfer_verification_retries_when_destination_listing_is_eventually_consistent():
    import logging

    class EventuallyConsistentStorage(FakeStorageTargets):
        capabilities = ("storage.list_destination_files",)

        def __init__(self):
            self.list_calls = 0

        def get(self, _target_id=None):
            return self

        def create_folder(self, name, parent_id="0", target_id=None):
            return "folder-1"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            return True, "转存成功"

        def list_destination_files(self, parent_id="0"):
            assert parent_id == "folder-1"
            self.list_calls += 1
            if self.list_calls < 2:
                return []
            return [{"fid": "saved-1", "file_name": "Show.S01E01.1080p.mkv", "is_dir": False}]

    storage = EventuallyConsistentStorage()
    service = SearchService(FakeResourceSources(), storage, logging.getLogger("test"))

    ok, message, counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "storage_target_id": "quark", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is True
    assert "核实 1 个文件" in message
    assert storage.list_calls == 2
    assert counts == {"success": 1, "skipped": 0, "failed": 0}


def test_transfer_success_without_visible_destination_file_is_uncertain_not_retryable_as_success():
    import logging

    class DelayedStorage(FakeStorageTargets):
        capabilities = ("storage.list_destination_files",)

        def get(self, _target_id=None):
            return self

        def create_folder(self, name, parent_id="0", target_id=None):
            return "folder-1"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            return True, "转存成功"

        def list_destination_files(self, parent_id="0"):
            return []

    service = SearchService(FakeResourceSources(), DelayedStorage(), logging.getLogger("test"))

    ok, message, counts = service.transfer_selected_resource_with_progress(
        {"title": "Show"},
        {"pwd_id": "same123", "storage_target_id": "quark", "files": [{"fid": "f1"}]},
        "0",
    )

    assert ok is False
    assert "结果" in message and "核实" in message
    assert "请检查目标网盘后再决定是否重试" in message
    assert counts["uncertain"] is True
    assert counts["success"] == 0



def test_search_preserves_selected_media_identity_for_resource_cards():
    import logging

    class ContextSources:
        received = None

        def search(self, movie, config):
            self.received = dict(movie)
            return []

    sources = ContextSources()
    service = SearchService(sources, FakeStorageTargets(), logging.getLogger("test"))

    assert service.search_movie_candidates(
        {
            "title": "潜伏6",
            "tag": "电影",
            "media_type": "movie",
            "year": "2026",
            "original_title": "The Final Chapter",
            "genres": ["恐怖"],
        },
        {},
    ) == []
    assert sources.received == {
        "title": "潜伏6",
        "tag": "电影",
        "media_type": "movie",
        "year": "2026",
        "original_title": "The Final Chapter",
        "genres": ["恐怖"],
    }



def test_candidate_ranking_demotes_tv_shares_when_searching_for_numbered_movie():
    from moviesync.services.search import SearchService

    tv_share = {"title": "潜伏 第1季 S01E01 1080p"}
    matching_movie = {"title": "潜伏6 2026 1080p"}
    wrong_score = SearchService._candidate_priority(tv_share, "潜伏6", "movie")
    matching_score = SearchService._candidate_priority(matching_movie, "潜伏6", "movie")

    assert matching_score > wrong_score



def test_candidate_priority_demotes_numbered_title_base_without_episode_markers():
    from moviesync.services.search import SearchService

    broad = {"title": "潜伏 第1季 1080p"}
    exact = {"title": "潜伏6 1080p"}

    broad_score = SearchService._candidate_priority(broad, "潜伏6", "")
    exact_score = SearchService._candidate_priority(exact, "潜伏6", "")

    assert exact_score - broad_score >= 8


def test_candidate_priority_ignores_parenthesized_year_when_matching_title():
    from moviesync.services.search import SearchService

    candidate = {"title": "潜伏6 1080p"}
    without_year = SearchService._candidate_priority(candidate, "潜伏6", "")
    with_year = SearchService._candidate_priority(candidate, "潜伏6（2025）", "")

    assert with_year == without_year


def test_search_flags_numbered_title_and_episode_format_mismatches():
    import logging

    class MismatchSources:
        def search(self, movie, config):
            return [{
                "source_id": "test-source",
                "source_name": "Test",
                "title": "潜伏 第1季 S01E01",
                "pwd_id": "series-share",
            }]

    service = SearchService(MismatchSources(), FakeStorageTargets(), logging.getLogger("test"))
    candidates = service.search_movie_candidates(
        {"title": "潜伏6", "tag": "电影", "media_type": "movie"},
        {},
    )

    assert len(candidates) == 1
    assert candidates[0]["resource_title"] == "潜伏 第1季 S01E01"
    assert candidates[0]["title_mismatch"] is True
    assert candidates[0]["format_mismatch"] is True



def test_search_exposes_source_diagnostics_through_page_info():
    import logging

    class DiagnosticSources:
        def search_with_diagnostics(self, movie, config, *, source_ids=None):
            return [], [
                {
                    "id": "broken-source",
                    "name": "Broken Source",
                    "status": "error",
                    "result_count": 0,
                    "invalid_items": 0,
                    "duration_ms": 12,
                    "message": "资源来源请求失败，请检查卡片配置或查看服务日志",
                }
            ]

        def search(self, movie, config, *, source_ids=None):
            raise AssertionError("diagnostic-capable managers should use search_with_diagnostics")

    service = SearchService(DiagnosticSources(), FakeStorageTargets(), logging.getLogger("test"))
    page_info = {}

    candidates = service.search_movie_candidates(
        {"title": "Show"},
        {},
        page_info=page_info,
    )

    assert candidates == []
    assert page_info["total_discovered"] == 0
    assert page_info["source_search_failed"] == 1
    assert page_info["source_search_diagnostics"][0]["id"] == "broken-source"
    assert page_info["source_search_diagnostics"][0]["status"] == "error"
