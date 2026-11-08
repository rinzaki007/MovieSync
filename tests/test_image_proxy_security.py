"""Security tests for metadata image proxy redirects."""
from types import SimpleNamespace

from flask import Flask

from moviesync.web.image_proxy import _metadata_image_policy, proxy_api


class FakeResponse:
    def __init__(self, status, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size):
        return iter(self.chunks)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FakeLogger:
    def warning(self, *_args, **_kwargs):
        pass


def _app(session):
    card = SimpleNamespace(
        manifest=SimpleNamespace(
            image_hosts=("images.example.test", "cdn.example.test"),
            image_referer="https://metadata.example.test/",
        )
    )
    app = Flask(__name__)
    app.extensions["moviesync"] = {
        "card_registry": SimpleNamespace(find_by_type=lambda _kind: [card]),
        "http": SimpleNamespace(session=session),
        "logger": FakeLogger(),
    }
    app.register_blueprint(proxy_api)
    return app


def test_image_policy_rejects_private_literal_addresses():
    card = SimpleNamespace(manifest=SimpleNamespace(image_hosts=("images.example.test",)))
    assert _metadata_image_policy("https://127.0.0.1/admin", [card]) is None
    assert _metadata_image_policy("https://10.0.0.5/poster.jpg", [card]) is None


def test_image_proxy_rejects_redirect_to_unapproved_host():
    session = FakeSession([FakeResponse(302, {"Location": "https://127.0.0.1/admin"})])
    response = _app(session).test_client().get(
        "/api/proxy-img", query_string={"url": "https://images.example.test/poster.jpg"}
    )
    assert response.status_code == 403
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


def test_image_proxy_allows_redirect_between_declared_image_hosts():
    session = FakeSession([
        FakeResponse(302, {"Location": "https://cdn.example.test/poster.jpg"}),
        FakeResponse(200, {"Content-Type": "image/jpeg", "Content-Length": "4"}, [b"jpeg"]),
    ])
    response = _app(session).test_client().get(
        "/api/proxy-img", query_string={"url": "https://images.example.test/poster.jpg"}
    )
    assert response.status_code == 200
    assert response.data == b"jpeg"
    assert len(session.calls) == 2
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
