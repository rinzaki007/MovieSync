"""Regression tests for Quark share parsing and transfer safety."""

from types import SimpleNamespace

import pytest

from moviesync.clients.http import ApiError
from moviesync.clients.quark import QuarkClient


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


class FakeHttp:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.responses.pop(0)


def test_get_share_files_walks_nested_directories():
    response = FakeResponse(200)
    http = FakeHttp([
        (response, {"code": 0, "data": {"stoken": "share-token"}}),
        (response, {"code": 0, "data": {"list": [
            {"fid": "folder-1", "file_name": "Season 1", "dir_file": True},
            {"fid": "root-video", "file_name": "Show.S01E02.mkv"},
        ]}}),
        # A short root page is not necessarily the last page; terminate with an empty page.
        (response, {"code": 0, "data": {"list": []}}),
        (response, {"code": 0, "data": {"list": [
            {"fid": "nested-video", "file_name": "Show.S01E01.mkv"},
        ]}}),
        (response, {"code": 0, "data": {"list": []}}),
    ])
    client = QuarkClient("Cookie: session=value")
    client.http = http

    files, token, error = client.get_share_files("https://pan.quark.cn/s/share123")

    assert error is None
    assert token == "share-token"
    assert {item["fid"] for item in files} == {"root-video", "nested-video"}
    paths = {item["fid"]: item.get("relative_path") for item in files}
    assert paths == {"root-video": "", "nested-video": "Season 1"}
    assert len(http.calls) == 5
    assert all(call[2]["timeout"] == 8 for call in http.calls[1:])


def test_save_files_deduplicates_and_filters_file_ids():
    response = FakeResponse(200)
    http = FakeHttp([(response, {"code": 0, "message": "ok"})])
    client = QuarkClient("session=value")
    client.http = http

    ok, message = client.save_files(
        "share123",
        [
            {"fid": "file_1"},
            {"fid": "file_1"},
            {"fid": "../escape"},
            {"fid": "bad fid"},
            {"fid": "file-2"},
        ],
        "share-token",
        "target_123",
    )

    assert ok is True
    assert message == "转存成功"
    payload = http.calls[0][2]["json"]
    assert payload["pwd_id"] == "share123"
    assert payload["stoken"] == "share-token"
    assert payload["fid_list"] == ["file_1", "file-2"]
    assert payload["to_pdir_fid"] == "target_123"


def test_save_files_marks_transport_errors_as_uncertain():
    client = QuarkClient("session=value")
    client.http = FakeHttp(error=ApiError("upstream timeout"))

    ok, message = client.save_files(
        "share123",
        [{"fid": "file_1"}],
        "share-token",
        "0",
    )

    assert ok is False
    assert "转存结果不确定" in message
    assert "检查目标目录" in message


def test_share_parser_rejects_invalid_id_before_network_request():
    client = QuarkClient("session=value")
    client.http = FakeHttp()

    files, token, error = client.get_share_files("https://example.com/not-a-share")

    assert files is None
    assert token is None
    assert error == "分享链接 ID 无效"
    assert client.http.calls == []

def test_list_drive_files_reports_api_failure_instead_of_empty_directory(monkeypatch):
    client = QuarkClient("cookie=test")
    response = SimpleNamespace(status_code=200)
    monkeypatch.setattr(
        client.http,
        "request_json",
        lambda *args, **kwargs: (response, {"code": 14001, "message": "invalid cookie"}),
    )

    with pytest.raises(RuntimeError, match="目录读取失败"):
        client.list_drive_files("0")


def test_list_drive_files_rejects_malformed_listing_payload(monkeypatch):
    client = QuarkClient("cookie=test")
    response = SimpleNamespace(status_code=200)
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda *args, **kwargs: (response, {"code": 0, "data": {"list": None}}),
    )

    with pytest.raises(RuntimeError, match="目录格式无效"):
        client.list_drive_files("0")


def test_list_drive_files_fails_closed_when_page_limit_is_reached(monkeypatch):
    client = QuarkClient("cookie=test")
    response = SimpleNamespace(status_code=200)
    calls = []

    def full_page(*args, **kwargs):
        calls.append(args[1])
        page = len(calls)
        return response, {"code": 0, "data": {"list": [
            {"fid": f"file-{page}-{index}", "file_name": f"video-{page}-{index}.mkv"}
            for index in range(200)
        ]}}

    monkeypatch.setattr(client, "_request_json", full_page)
    with pytest.raises(RuntimeError, match="分页扫描上限"):
        client.list_drive_files("0")
    assert len(calls) == 20


def test_folder_lookup_does_not_create_folder_after_listing_error(monkeypatch):
    client = QuarkClient("cookie=test")
    response = SimpleNamespace(status_code=200)
    calls = []

    def listing(*args, **kwargs):
        calls.append(args[1])
        return response, {"code": 14001, "message": "invalid cookie"}

    monkeypatch.setattr(client, "_request_json", listing)
    with pytest.raises(RuntimeError, match="invalid cookie"):
        client.get_or_create_subfolder("电影", "0")
    assert len(calls) == 1
    assert all("/file?" not in url for url in calls)


def test_list_drive_files_normalizes_destination_items(monkeypatch):
    client = QuarkClient("cookie=test")
    response = SimpleNamespace(status_code=200)
    payload = {
        "code": 0,
        "data": {"list": [
            {"fid": "folder1", "file_name": "电影", "dir_file": True, "size": 0},
            {"fid": "file1", "file_name": "sample.mkv", "file_type": 1, "size": 1024},
        ]},
    }
    monkeypatch.setattr(client.http, "request_json", lambda *args, **kwargs: (response, payload))

    files = client.list_drive_files("0")

    assert files[0]["is_dir"] is True
    assert files[1]["is_video"] is True
    assert files[1]["size"] == 1024



def test_get_share_files_reports_when_nested_directory_exceeds_depth_limit():
    response = FakeResponse(200)
    http = FakeHttp([
        (response, {"code": 0, "data": {"stoken": "share-token"}}),
        (response, {"code": 0, "data": {"list": [
            {"fid": "folder-1", "file_name": "Season 1", "dir_file": True},
        ]}}),
        # Recursive directory reads happen before the parent directory's next page.
        (response, {"code": 0, "data": {"list": [
            {"fid": "folder-2", "file_name": "Nested", "dir_file": True},
        ]}}),
        (response, {"code": 0, "data": {"list": []}}),
        (response, {"code": 0, "data": {"list": []}}),
    ])
    client = QuarkClient("session=value")
    client.http = http

    files, token, error = client.get_share_files("share123", max_depth=1)

    assert error is None
    assert token == "share-token"
    assert files.scan_complete is False
    assert any("扫描深度上限" in warning for warning in files.scan_warnings)


def test_get_share_files_reports_when_file_limit_is_reached():
    response = FakeResponse(200)
    http = FakeHttp([
        (response, {"code": 0, "data": {"stoken": "share-token"}}),
        (response, {"code": 0, "data": {"list": [
            {"fid": "file-1", "file_name": "One.mkv"},
            {"fid": "file-2", "file_name": "Two.mkv"},
        ]}}),
    ])
    client = QuarkClient("session=value")
    client.http = http

    files, _token, error = client.get_share_files("share123", max_files=1)

    assert error is None
    assert len(files) == 1
    assert files.scan_complete is False
    assert any("扫描上限" in warning for warning in files.scan_warnings)


def test_get_share_files_uses_reported_total_when_page_is_short_and_keeps_all_47_episodes():
    response = FakeResponse(200)
    first_page = [
        {"fid": f"episode-{number}", "file_name": f"Show.E{number:02d}.mkv"}
        for number in range(1, 24)
    ]
    second_page = [
        {"fid": f"episode-{number}", "file_name": f"Show.E{number:02d}.mkv"}
        for number in range(24, 48)
    ]
    http = FakeHttp([
        (response, {"code": 0, "data": {"stoken": "share-token"}}),
        (
            response,
            {
                "code": 0,
                "metadata": {"_total": 47},
                "data": {"list": first_page},
            },
        ),
        (
            response,
            {
                "code": 0,
                "metadata": {"_total": 47},
                "data": {"list": second_page},
            },
        ),
    ])
    client = QuarkClient("session=value")
    client.http = http

    parsed, token, error = client.get_share_files("share123")

    assert error is None
    assert token == "share-token"
    assert len(parsed) == 47
    assert [item["fid"] for item in parsed] == [f"episode-{number}" for number in range(1, 48)]
    first_url, second_url = http.calls[1][1], http.calls[2][1]
    assert "_page=1" in first_url
    assert "_page=2" in second_url
    assert "_size=50" in first_url
    assert "_fetch_total=1" in first_url
    assert "_sort=file_type:asc,file_name:asc" in first_url
    assert "&p=1&num=200" not in first_url


def test_get_share_files_continues_after_short_page_without_total_metadata():
    response = FakeResponse(200)
    first_page = [
        {"fid": f"episode-{number}", "file_name": f"Show.E{number:02d}.mkv"}
        for number in range(1, 24)
    ]
    second_page = [
        {"fid": f"episode-{number}", "file_name": f"Show.E{number:02d}.mkv"}
        for number in range(24, 48)
    ]
    http = FakeHttp([
        (response, {"code": 0, "data": {"stoken": "share-token"}}),
        (response, {"code": 0, "data": {"list": first_page}}),
        (response, {"code": 0, "data": {"list": second_page}}),
        (response, {"code": 0, "data": {"list": []}}),
    ])
    client = QuarkClient("session=value")
    client.http = http

    parsed, token, error = client.get_share_files("share123")

    assert error is None
    assert token == "share-token"
    assert len(parsed) == 47
    assert parsed.scan_complete is True
    assert len(http.calls) == 4
    assert "_page=1" in http.calls[1][1]
    assert "_page=2" in http.calls[2][1]
    assert "_page=3" in http.calls[3][1]

