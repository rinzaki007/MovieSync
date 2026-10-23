from pathlib import Path

from card_templates.pansou import PanSouResourceSource


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeHttpClient:
    def __init__(self, search_payload=None, search_payloads=None, health_payload=None, progressive_supported=True):
        self.search_payload = search_payload or {
            "code": 200,
            "data": {
                "total": 2,
                "results": [
                    {
                        "title": "测试电影 1080P",
                        "channel": "测试频道",
                        "links": [
                            {
                                "type": "quark",
                                "url": "https://pan.quark.cn/s/share123",
                                "password": "abcd",
                            },
                            {
                                "type": "baidu",
                                "url": "https://pan.baidu.com/s/unsupported",
                            },
                        ],
                    }
                ],
                "merged_by_type": {},
            },
        }
        self.search_payloads = list(search_payloads or [])
        self.search_payload_index = 0
        self.progressive_supported = progressive_supported
        self.health_payload = health_payload or {
            "status": "ok",
            "plugins_enabled": True,
            "plugin_count": 3,
        }
        self.calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def request_json(self, method, url, *, timeout, retries=0, **kwargs):
        self.calls.append((method, url, timeout, kwargs))
        if url.endswith("/api/health"):
            return FakeResponse(), self.health_payload
        if url.endswith("/api/search/first") and not self.progressive_supported:
            return FakeResponse(404), {}
        if url.endswith("/api/search") or url.endswith("/api/search/first"):
            if self.search_payloads:
                index = min(self.search_payload_index, len(self.search_payloads) - 1)
                self.search_payload_index += 1
                return FakeResponse(), self.search_payloads[index]
            return FakeResponse(), self.search_payload
        raise AssertionError(f"Unexpected request: {method} {url}")


def test_pansou_does_not_replace_missing_result_title_with_the_search_query():
    http = FakeHttpClient(
        search_payload={
            "code": 200,
            "data": {
                "results": [
                    {
                        "links": [
                            {"type": "quark", "url": "https://pan.quark.cn/s/share123"}
                        ]
                    }
                ],
                "merged_by_type": {
                    "quark": [
                        {"url": "https://pan.quark.cn/s/share456"}
                    ]
                },
            },
        }
    )
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "兰香如故"},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    assert [item["resource_id"] for item in results] == ["share123", "share456"]
    assert all(item["title"] == "" for item in results)
    assert all(item["title"] != "兰香如故" for item in results)


def test_pansou_search_normalizes_quark_links_and_skips_other_drives():
    http = FakeHttpClient()
    source = PanSouResourceSource(http)
    results = source.search(
        {"title": "测试电影"},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    assert len(results) == 1
    assert results[0]["pwd_id"] == "share123"
    assert results[0]["resource_type"] == "quark_share"
    assert "storage_target_id" not in results[0]
    assert results[0]["password"] == "abcd"
    assert results[0]["url"] == "https://pan.quark.cn/s/share123"
    method, url, timeout, kwargs = http.calls[0]
    assert method == "GET"
    assert url == "http://pansou.local/api/search/first"
    assert timeout == 45
    assert kwargs["params"]["kw"] == "测试电影"
    assert kwargs["params"]["cloud_types"] == "quark"
    assert kwargs["params"]["wait_ms"] == 6000
    assert kwargs["params"]["refresh"] == "true"




def test_pansou_search_polls_and_merges_progressive_results():
    first = {
        "results": [
            {"title": "测试电影", "links": [
                {"type": "quark", "url": "https://pan.quark.cn/s/share123"}
            ]}
        ],
        "merged_by_type": {},
    }
    later = {
        "results": [
            {"title": "测试电影", "links": [
                {"type": "quark", "url": "https://pan.quark.cn/s/share456"}
            ]}
        ],
        "merged_by_type": {},
    }
    http = FakeHttpClient(search_payloads=[first, later, later, later])
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试电影"},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    assert {item["resource_id"] for item in results} == {"share123", "share456"}
    assert len([call for call in http.calls if call[1].endswith("/api/search/first")]) == 4


def test_pansou_keeps_polling_for_late_results_after_unchanged_snapshots(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)
    payload = {
        "results": [
            {"title": "更新至第47集", "links": [
                {"type": "quark", "url": "https://pan.quark.cn/s/share123"}
            ]}
        ],
        "merged_by_type": {},
    }
    later = {
        "results": [
            {"title": "兰香如故 (2026) 4K 全47完结", "links": [
                {"type": "quark", "url": "https://pan.quark.cn/s/share456"}
            ]}
        ],
        "merged_by_type": {},
    }
    # The first six snapshots are unchanged; the exact-title result arrives later.
    http = FakeHttpClient(search_payloads=[payload, payload, payload, payload, payload, payload, payload, later])
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "兰香如故"},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    assert {item["resource_id"] for item in results} == {"share123", "share456"}
    assert len([call for call in http.calls if call[1].endswith("/api/search/first")]) == 8


def test_pansou_all_resource_types_excludes_ed2k():
    http = FakeHttpClient()
    source = PanSouResourceSource(http)

    source.search(
        {"title": "测试电影", "all_resource_types": True},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    cloud_types = http.calls[0][3]["params"]["cloud_types"].split(",")
    assert "magnet" in cloud_types
    assert "ed2k" not in cloud_types


def test_pansou_search_supports_merged_response_and_deduplicates():
    http = FakeHttpClient(
        search_payload={
            "code": 200,
            "data": {
                "total": 1,
                "results": [
                    {
                        "title": "测试剧",
                        "links": [
                            {"type": "quark", "url": "https://pan.quark.cn/s/share123"}
                        ],
                    }
                ],
                "merged_by_type": {
                    "quark": [
                        {
                            "url": "https://pan.quark.cn/s/share123",
                            "note": "测试剧",
                            "password": "",
                        },
                        {
                            "url": "https://pan.quark.cn/s/share456",
                            "note": "测试剧第二集",
                        },
                    ]
                },
            },
        }
    )
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试剧"},
        {"base_url": "http://pansou.local"},
    )

    assert [item["pwd_id"] for item in results] == ["share123", "share456"]


def test_pansou_health_check_reports_unconfigured_and_healthy():
    source = PanSouResourceSource(FakeHttpClient())

    assert source.check({})["status"] == "unconfigured"
    result = source.check({"base_url": "http://pansou.local", "timeout": 45})

    assert result["status"] == "healthy"
    assert result["total"] == 3


def test_pansou_rejects_invalid_base_url():
    source = PanSouResourceSource(FakeHttpClient())

    try:
        source.validate_config({"base_url": "file:///etc/passwd"})
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid PanSou URL to be rejected")


def test_pansou_does_not_accept_untrusted_share_hosts():
    assert PanSouResourceSource._quark_share_id(
        "https://pan.quark.cn.evil.example/s/share123"
    ) == ""
    assert PanSouResourceSource._quark_share_id(
        "https://pan.baidu.com/s/share123"
    ) == ""



def test_pansou_uses_bundled_service_url_when_card_address_is_empty(monkeypatch):
    monkeypatch.setenv("MOVIESYNC_PANSOU_URL", "http://127.0.0.1:8888")
    http = FakeHttpClient()
    source = PanSouResourceSource(http)

    results = source.search({"title": "测试电影"}, {})

    assert len(results) == 1
    assert http.calls[0][1] == "http://127.0.0.1:8888/api/search/first"



def test_bundled_pansou_can_be_disabled_without_marking_movie_sync_unhealthy():
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "MOVIESYNC_BUNDLED_PANSOU_ENABLED" in entrypoint
    assert "MOVIESYNC_BUNDLED_PANSOU_ENABLED=true" in dockerfile
    healthcheck = dockerfile.split("HEALTHCHECK", 1)[1]
    assert "/healthz" in healthcheck
    assert "/api/health" not in healthcheck



def test_pansou_search_filters_aliyun_links_when_aliyun_storage_is_selected():
    http = FakeHttpClient(
        search_payload={
            "code": 200,
            "data": {
                "results": [
                    {
                        "title": "测试剧",
                        "links": [
                            {"type": "aliyun", "url": "https://www.alipan.com/s/aliyun123"},
                            {"type": "quark", "url": "https://pan.quark.cn/s/quark123"},
                            {"type": "baidu", "url": "https://pan.baidu.com/s/baidu123"},
                        ],
                    }
                ],
                "merged_by_type": {},
            },
        }
    )
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试剧", "storage_target_id": "aliyun", "resource_types": ["aliyun_share"]},
        {"base_url": "http://pansou.local"},
    )

    assert [item["resource_id"] for item in results] == ["aliyun123"]
    assert results[0]["resource_type"] == "aliyun_share"
    assert "pwd_id" not in results[0]
    query_cloud_types = set(http.calls[0][3]["params"]["cloud_types"].split(","))
    assert {"quark", "aliyun", "baidu", "magnet"}.issubset(query_cloud_types)
    assert "ed2k" not in query_cloud_types


def test_pansou_search_filters_baidu_merged_results_when_baidu_storage_is_selected():
    http = FakeHttpClient(
        search_payload={
            "code": 200,
            "data": {
                "results": [],
                "merged_by_type": {
                    "baidu": [
                        {"url": "https://pan.baidu.com/s/baidu123", "note": "百度资源"},
                    ],
                    "aliyun": [
                        {"url": "https://www.alipan.com/s/aliyun123", "note": "阿里资源"},
                    ],
                },
            },
        }
    )
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试剧", "storage_target_id": "baidu", "resource_types": ["baidu_share"]},
        {"base_url": "http://pansou.local"},
    )

    assert [item["resource_id"] for item in results] == ["baidu123"]
    assert results[0]["resource_type"] == "baidu_share"
    query_cloud_types = set(http.calls[0][3]["params"]["cloud_types"].split(","))
    assert {"quark", "aliyun", "baidu", "magnet"}.issubset(query_cloud_types)
    assert "ed2k" not in query_cloud_types


def test_pansou_does_not_fall_back_to_quark_for_unknown_selected_storage_type():
    http = FakeHttpClient()
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试剧", "storage_target_id": "unsupported", "resource_types": ["unsupported_share"]},
        {"base_url": "http://pansou.local"},
    )

    assert results == []
    assert http.calls == []



def test_pansou_card_close_releases_http_client():
    http = FakeHttpClient()
    source = PanSouResourceSource(http)

    source.close()

    assert getattr(http, "closed", False) is True


def test_pansou_falls_back_for_external_instances_without_progressive_endpoint():
    http = FakeHttpClient(progressive_supported=False)
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "测试电影"},
        {"base_url": "http://external-pansou.local", "timeout": 45},
    )

    assert len(results) == 1
    assert [call[1] for call in http.calls] == [
        "http://external-pansou.local/api/search/first",
        "http://external-pansou.local/api/search",
    ]
    assert "wait_ms" not in http.calls[1][3]["params"]


def test_pansou_broadens_selected_quark_search_but_returns_only_quark_links():
    http = FakeHttpClient(
        search_payload={
            "code": 200,
            "data": {
                "results": [
                    {
                        "title": "兰香如故 全47集",
                        "links": [
                            {"type": "quark", "url": "https://pan.quark.cn/s/quark-match"},
                            {"type": "baidu", "url": "https://pan.baidu.com/s/baidu-other"},
                            {"type": "aliyun", "url": "https://www.alipan.com/s/aliyun-other"},
                        ],
                    }
                ],
                "merged_by_type": {
                    "quark": [
                        {"url": "https://pan.quark.cn/s/quark-match", "note": "兰香如故 全47集"},
                        {"url": "https://pan.quark.cn/s/quark-merged", "note": "兰香如故 全集"},
                    ],
                    "baidu": [
                        {"url": "https://pan.baidu.com/s/baidu-merged", "note": "兰香如故"},
                    ],
                },
            },
        }
    )
    source = PanSouResourceSource(http)

    results = source.search(
        {
            "title": "兰香如故",
            "storage_target_id": "quark",
            "resource_types": ["quark_share"],
        },
        {"base_url": "http://pansou.local"},
    )

    assert {item["resource_id"] for item in results} == {"quark-match", "quark-merged"}
    assert all(item["resource_type"] == "quark_share" for item in results)
    queried_types = set(http.calls[0][3]["params"]["cloud_types"].split(","))
    assert {"quark", "aliyun", "baidu", "magnet"}.issubset(queried_types)
    assert "ed2k" not in queried_types



def test_pansou_retries_douban_alternate_title_when_primary_search_is_empty(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)
    empty = {"data": {"results": [], "merged_by_type": {}}}
    alternate = {
        "data": {
            "results": [{
                "title": "Lan Xiang Ru Gu 2024",
                "links": [{"type": "quark", "url": "https://pan.quark.cn/s/alternate123"}],
            }],
            "merged_by_type": {},
        }
    }
    http = FakeHttpClient(search_payloads=[empty, empty, empty, alternate, alternate, alternate])
    source = PanSouResourceSource(http)

    results = source.search(
        {"title": "兰香如故", "year": "2024", "original_title": ["Lan Xiang Ru Gu"]},
        {"base_url": "http://pansou.local", "timeout": 45},
    )

    queries = [
        call[3]["params"]["kw"]
        for call in http.calls
        if call[1].endswith("/api/search/first")
    ]
    assert queries[0] == "兰香如故"
    assert "Lan Xiang Ru Gu" in queries
    assert any(item["resource_id"] == "alternate123" for item in results)
