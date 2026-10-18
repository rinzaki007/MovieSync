"""Authenticated client for managing storage mounts in the bundled OpenList runtime.

The client deliberately uses OpenList's public admin API instead of editing its
SQLite database, so driver initialization and lifecycle hooks remain upstream-owned.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import requests


class OpenListManager:
    def __init__(self, config_store, *, session=None):
        self.config_store = config_store
        self.session = session or requests.Session()

    def _config(self):
        getter = getattr(self.config_store, "get_card_config", None)
        config = getter("openlist") if callable(getter) else {}
        if not isinstance(config, dict):
            config = {}
        base_url = str(config.get("base_url") or "http://127.0.0.1:5244").strip().rstrip("/")
        token = str(config.get("admin_token") or "").strip()
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("OpenList 服务地址无效")
        if not token:
            raise PermissionError("请先在 OpenList 卡片中配置“管理员 Token（挂载管理）”")
        return base_url, token, config.get("verify_tls", True) is not False

    def request(self, endpoint, *, method="GET", params=None, payload=None):
        base_url, token, verify_tls = self._config()
        url = urljoin(base_url + "/", endpoint.lstrip("/"))
        try:
            response = self.session.request(
                method, url, params=params, json=payload,
                headers={"Authorization": token, "Accept": "application/json"},
                timeout=(4, 20), verify=verify_tls,
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise PermissionError("OpenList Token 无效或没有管理员存储管理权限") from exc
            raise RuntimeError(f"OpenList 请求失败（{type(exc).__name__}）") from exc
        except ValueError as exc:
            raise RuntimeError("OpenList 返回了无效 JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("OpenList 返回的数据格式无效")
        code = body.get("code")
        if code in (401, 403):
            raise PermissionError("OpenList Token 无效或没有管理员存储管理权限")
        if code not in (None, 200):
            message = str(body.get("message") or body.get("msg") or "接口请求失败")
            raise RuntimeError(f"OpenList API 错误（{code}）：{message[:160]}")
        return body

    def drivers(self):
        return self.request("/api/admin/driver/list")["data"]

    def driver_names(self):
        return self.request("/api/admin/driver/names")["data"]

    def driver_info(self, driver):
        driver = str(driver or "").strip()
        if not driver or len(driver) > 128:
            raise ValueError("驱动名称无效")
        return self.request("/api/admin/driver/info", params={"driver": driver})["data"]

    def storages(self, *, page=1, per_page=0):
        page = max(1, int(page))
        per_page = max(0, min(500, int(per_page)))
        data = self.request(
            "/api/admin/storage/list",
            params={"page": page, "per_page": per_page},
        )["data"]
        if not isinstance(data, dict):
            raise RuntimeError("OpenList 挂载列表格式无效")
        return data

    def create_storage(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("挂载配置必须是对象")
        return self.request("/api/admin/storage/create", method="POST", payload=payload).get("data", {})

    def update_storage(self, payload):
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError("修改挂载时必须提供有效的挂载 ID")
        return self.request("/api/admin/storage/update", method="POST", payload=payload).get("data", {})

    def delete_storage(self, storage_id):
        storage_id = str(storage_id or "").strip()
        if not storage_id.isdigit() or int(storage_id) <= 0:
            raise ValueError("挂载 ID 无效")
        return self.request("/api/admin/storage/delete", method="POST", params={"id": storage_id}).get("data", {})

    def set_storage_enabled(self, storage_id, enabled):
        storage_id = str(storage_id or "").strip()
        if not storage_id.isdigit() or int(storage_id) <= 0:
            raise ValueError("挂载 ID 无效")
        action = "enable" if enabled else "disable"
        return self.request(f"/api/admin/storage/{action}", method="POST", params={"id": storage_id}).get("data", {})
