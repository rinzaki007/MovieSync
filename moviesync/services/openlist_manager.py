"""Compatibility adapter for the bundled OpenList admin client.

The actual admin token is discovered by OpenListAdminClient from the bundled
OpenList runtime. This adapter intentionally never asks users to paste it.
"""
from __future__ import annotations


class OpenListManager:
    """Expose the legacy mount-manager UI API over MovieSync's shared client."""

    def __init__(self, admin_client):
        if admin_client is None:
            raise RuntimeError("MovieSync 内置 OpenList 管理服务尚未初始化")
        self.client = admin_client

    def request(self, endpoint, *, method="GET", params=None, payload=None):
        return self.client._request(
            method,
            endpoint,
            params=params,
            payload=payload,
        )

    def drivers(self):
        return self.client.driver_map()

    def driver_names(self):
        return list(self.client.driver_map().keys())

    def driver_info(self, driver):
        driver = str(driver or "").strip()
        if not driver or len(driver) > 128:
            raise ValueError("驱动名称无效")
        drivers = self.client.driver_map()
        info = drivers.get(driver)
        if info is None:
            match = next(
                (name for name in drivers if name.casefold() == driver.casefold()),
                None,
            )
            if match is None:
                raise ValueError(f"OpenList 官方驱动列表中没有「{driver}」")
            info = drivers[match]
        return info

    def storages(self, *, page=1, per_page=0):
        page = max(1, int(page))
        per_page = max(0, min(500, int(per_page)))
        data = self.request(
            "/api/admin/storage/list",
            params={"page": page, "per_page": per_page},
        ).get("data")
        if not isinstance(data, dict):
            raise RuntimeError("OpenList 挂载列表格式无效")
        return data

    def create_storage(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("挂载配置必须是对象")
        return self.request(
            "/api/admin/storage/create", method="POST", payload=payload
        ).get("data", {})

    def update_storage(self, payload):
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError("修改挂载时必须提供有效的挂载 ID")
        return self.request(
            "/api/admin/storage/update", method="POST", payload=payload
        ).get("data", {})

    def delete_storage(self, storage_id):
        storage_id = str(storage_id or "").strip()
        if not storage_id.isdigit() or int(storage_id) <= 0:
            raise ValueError("挂载 ID 无效")
        return self.request(
            "/api/admin/storage/delete",
            method="POST",
            params={"id": storage_id},
        ).get("data", {})

    def set_storage_enabled(self, storage_id, enabled):
        storage_id = str(storage_id or "").strip()
        if not storage_id.isdigit() or int(storage_id) <= 0:
            raise ValueError("挂载 ID 无效")
        action = "enable" if enabled else "disable"
        return self.request(
            f"/api/admin/storage/{action}",
            method="POST",
            params={"id": storage_id},
        ).get("data", {})
