"""MovieSync Web 层。

用途：提供登录限流、认证前置检查、安全响应头和 Blueprint 注册。
维护说明：所有需要登录的 API 会在 before_request 阶段拦截；具体写接口再由路由装饰器执行 CSRF 校验。
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from flask import jsonify, redirect, request, session

from .openlist import openlist_api
from .playback import playback_api, playback_pages
from .routes import api, pages


class LoginRateLimiter:
    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 600,
        max_keys: int = 4096,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.lock = Lock()

    def _cleanup(self, now: float) -> None:
        stale = [
            key
            for key, queue in self.failures.items()
            if not queue or now - queue[-1] > self.window_seconds
        ]
        for key in stale:
            self.failures.pop(key, None)

        if len(self.failures) > self.max_keys:
            oldest = sorted(
                self.failures.items(),
                key=lambda item: item[1][-1] if item[1] else 0,
            )[: len(self.failures) - self.max_keys]
            for key, _ in oldest:
                self.failures.pop(key, None)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self.lock:
            self._cleanup(now)
            queue = self.failures[key]
            while queue and now - queue[0] > self.window_seconds:
                queue.popleft()
            if len(queue) < self.max_attempts:
                return True, 0
            return False, max(1, int(self.window_seconds - (now - queue[0])))

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self.lock:
            self._cleanup(now)
            self.failures[key].append(now)

    def record_success(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)


def register_web(app, services):
    app.register_blueprint(pages)
    app.register_blueprint(api)
    app.register_blueprint(playback_pages)
    app.register_blueprint(playback_api)
    app.register_blueprint(openlist_api)

    @app.before_request
    def protect_requests():
        if request.path.startswith("/static"):
            return None
        auth = services["auth"]
        if not auth.is_initialized():
            public_paths = {
                "/setup", "/api/setup", "/login", "/api/login",
                "/api/login-backdrop", "/api/proxy-img", "/favicon.ico", "/healthz",
            }
            if request.path not in public_paths:
                if request.path.startswith("/api/"):
                    return jsonify({
                        "success": False,
                        "message": "需要先完成初始化",
                        "need_setup": True,
                    }), 401
                return redirect("/setup")
            return None
        if request.path in {"/setup", "/api/setup"}:
            return redirect("/login")
        if request.path not in {"/login", "/api/login"} and not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({
                    "success": False,
                    "message": "未登录",
                    "need_login": True,
                }), 401
            return redirect("/login")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path not in {
            "/api/login", "/api/setup",
        }:
            if not session.get("csrf_token"):
                session["csrf_token"] = secrets.token_urlsafe(32)
        return None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.path.startswith("/api/") or request.path == "/admin":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.errorhandler(404)
    def not_found(error):
        services["logger"].warning("请求路径不存在: %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "接口不存在"}), 404
        return "Not Found", 404

    @app.errorhandler(413)
    def payload_too_large(_):
        return jsonify({"success": False, "message": "请求数据过大"}), 413

    @app.errorhandler(Exception)
    def unhandled(error):
        services["logger"].exception("未处理异常: %s", error)
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "message": "服务器内部错误"}), 500
        return "Internal Server Error", 500
