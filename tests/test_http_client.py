"""HTTP client contract tests for upstream response shape and retries."""

import pytest

from moviesync.clients.http import ApiError, HttpClient


class FakeResponse:
    def __init__(self, status_code, payload=None, *, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def make_client(monkeypatch, responses):
    client = HttpClient({"X-Test": "yes"})
    session = FakeSession(responses)
    client._local.session = session
    monkeypatch.setattr("moviesync.clients.http.time.sleep", lambda _delay: None)
    return client, session


def test_http_client_retries_server_error_then_returns_json_object(monkeypatch):
    failed = FakeResponse(503, {"message": "temporary"})
    success = FakeResponse(200, {"code": 0, "data": {"ok": True}})
    client, session = make_client(monkeypatch, [failed, success])

    response, data = client.request_json("GET", "https://example.test/api", timeout=2)

    assert response.status_code == 200
    assert data == {"code": 0, "data": {"ok": True}}
    assert len(session.calls) == 2


def test_http_client_rejects_non_object_json_payload(monkeypatch):
    client, session = make_client(
        monkeypatch,
        [FakeResponse(200, []), FakeResponse(200, None)],
    )

    with pytest.raises(ApiError, match="JSON 结构无效"):
        client.request_json("GET", "https://example.test/api", timeout=2)

    assert len(session.calls) == 2


def test_http_client_can_explicitly_accept_json_array_payload(monkeypatch):
    payload = [{"id": "123", "title": "测试电影"}]
    client, session = make_client(monkeypatch, [FakeResponse(200, payload)])

    response, data = client.request_json(
        "GET", "https://example.test/search", timeout=2, allow_non_dict=True
    )

    assert response.status_code == 200
    assert data == payload
    assert len(session.calls) == 1


def test_http_client_rejects_non_json_response_after_retry(monkeypatch):
    client, session = make_client(
        monkeypatch,
        [
            FakeResponse(502, json_error=ValueError("not json")),
            FakeResponse(502, json_error=ValueError("still not json")),
        ],
    )

    with pytest.raises(ApiError, match="非 JSON 响应"):
        client.request_json("GET", "https://example.test/api", timeout=2)

    assert len(session.calls) == 2



def test_http_client_close_releases_sessions_and_rejects_future_requests(monkeypatch):
    from threading import Thread

    created = []

    class ClosableSession(FakeSession):
        def __init__(self):
            super().__init__([])
            self.closed = False
            self.headers = {}

        def close(self):
            self.closed = True

    def make_session():
        session = ClosableSession()
        created.append(session)
        return session

    monkeypatch.setattr("moviesync.clients.http.requests.Session", make_session)
    client = HttpClient({"X-Test": "yes"})
    client.session
    thread = Thread(target=lambda: client.session)
    thread.start()
    thread.join(timeout=2)

    assert len(created) == 2
    client.close()

    assert all(session.closed for session in created)
    with pytest.raises(ApiError, match="已关闭"):
        client.request_json("GET", "https://example.test/api", timeout=1, retries=0)


def test_http_client_close_continues_after_one_session_raises():
    class ClosableSession(FakeSession):
        def __init__(self, should_raise=False):
            super().__init__([])
            self.headers = {}
            self.closed = False
            self.should_raise = should_raise

        def close(self):
            self.closed = True
            if self.should_raise:
                raise RuntimeError("close failed")

    sessions = [ClosableSession(should_raise=True), ClosableSession()]
    client = HttpClient()
    client._sessions.update(sessions)

    client.close()

    assert all(session.closed for session in sessions)
