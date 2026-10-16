"""通用 HTTP 客户端。

用途：统一 requests Session、线程隔离、超时、JSON 解析和有限次数的指数退避重试。
维护说明：豆瓣、夸克等客户端通过本类复用请求逻辑，遇到非 JSON 上游响应会转换成 ApiError。
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests


class ApiError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, headers: dict[str, str] | None = None):
        self._local = threading.local()
        self.headers = headers or {}
        self._sessions: set[requests.Session] = set()
        self._sessions_lock = threading.RLock()
        self._closed = False

    @property
    def session(self) -> requests.Session:
        with self._sessions_lock:
            if self._closed:
                raise ApiError("HTTP 客户端已关闭")
            session = getattr(self._local, "session", None)
            if session is None:
                session = requests.Session()
                session.headers.update(self.headers)
                self._sessions.add(session)
                self._local.session = session
            return session

    def close(self) -> None:
        """Close every thread-local session owned by this client exactly once."""
        with self._sessions_lock:
            self._closed = True
            sessions = set(self._sessions)
            self._sessions.clear()
            # Tests and legacy adapters may inject a session directly into the
            # current thread-local slot instead of creating it via this client.
            current_session = getattr(self._local, "session", None)
            if current_session is not None:
                sessions.add(current_session)
            self._local.session = None

        for session in sessions:
            try:
                session.close()
            except Exception:
                # One broken adapter must not prevent closing the remaining sessions.
                continue

    def request_json(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        retries: int = 1,
        allow_non_dict: bool = False,
        **kwargs: Any,
    ) -> tuple[requests.Response, Any]:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(0.25 * (2**attempt))
                    continue
                try:
                    data = response.json()
                except ValueError as exc:
                    raise ApiError(f"上游返回非 JSON 响应（HTTP {response.status_code}）") from exc
                if not isinstance(data, dict) and not (allow_non_dict and isinstance(data, list)):
                    raise ApiError(f"上游返回的 JSON 结构无效（HTTP {response.status_code}）")
                return response, data
            except (requests.RequestException, ApiError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(0.25 * (2**attempt))
        raise ApiError(str(last_error or "请求失败"))
