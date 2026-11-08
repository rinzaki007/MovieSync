"""Health endpoint tests for scheduler and bundled PanSou dependencies."""
from flask import Flask

from moviesync.web.routes import pages


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeLogger:
    def warning(self, *_args, **_kwargs):
        pass


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response


def _client(session):
    app = Flask(__name__)
    app.extensions["moviesync"] = {
        "subscriptions": None,
        "scheduler_enabled": False,
        "pansou_enabled": True,
        "pansou_url": "http://127.0.0.1:8888",
        "http": type("Http", (), {"session": session})(),
        "logger": FakeLogger(),
    }
    app.register_blueprint(pages)
    return app.test_client()


def test_healthz_reports_pansou_health():
    response = _client(FakeSession(FakeResponse({"status": "ok"}))).get("/healthz")
    assert response.status_code == 200
    assert response.json == {"status": "ok", "scheduler": "disabled", "pansou": "healthy"}


def test_healthz_degrades_when_pansou_is_unavailable():
    response = _client(FakeSession(error=OSError("connection refused"))).get("/healthz")
    assert response.status_code == 503
    assert response.json["status"] == "degraded"
    assert response.json["pansou"] == "unavailable"
