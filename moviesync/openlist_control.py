"""Server-side adapter for managing OpenList mounts through its official API.

MovieSync owns the workflow; OpenList remains the source of truth for native
storage-driver configuration and credentials.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from threading import Lock
from typing import Any

import requests

DEFAULT_OPENLIST_URL = "http://127.0.0.1:5244"
_SENSITIVE_MARKERS = (
    "cookie",
    "token",
    "password",
    "secret",
    "api_key",
    "api-key",
    "authorization",
    "credential",
    "private_key",
    "access_key",
    "refresh_key",
    "access_token",
    "refresh_token",
    "client_secret",
)
_STORAGE_FIELDS = {
    "id",
    "mount_path",
    "order",
    "driver",
    "cache_expiration",
    "custom_cache_policies",
    "status",
    "addition",
    "remark",
    "modified",
    "disabled",
    "disable_index",
    "enable_sign",
    "order_by",
    "order_direction",
    "extract_folder",
    "web_proxy",
    "webdav_policy",
    "proxy_range",
    "down_proxy_url",
    "disable_proxy_sign",
}


def is_sensitive_parameter(name: object) -> bool:
    """Return whether an OpenList driver parameter should be treated as secret."""
    key = str(name or "").lower()
    return any(marker in key for marker in _SENSITIVE_MARKERS)


def normalize_mount_path(value: object) -> str:
    """Normalize mount paths and reject path traversal before they reach OpenList."""
    path = str(value or "").strip().replace("\\", "/")
    if not path:
        raise ValueError("请填写挂载路径")
    if len(path) > 240:
        raise ValueError("挂载路径不能超过 240 个字符")
    if not path.startswith("/"):
        path = "/" + path
    parts = [part for part in path.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("挂载路径不能包含 ..")
    normalized = "/" + "/".join(parts)
    if normalized == "/":
        raise ValueError("挂载路径不能使用 /；请为网盘指定独立目录")
    return normalized


def normalize_root_path(value: object) -> str:
    """Normalize the driver-visible root; '/' means the root of the remote drive."""
    path = str(value or "/").strip().replace("\\", "/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    parts = [part for part in path.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError("根目录不能包含 ..")
    normalized = "/" + "/".join(parts)
    if len(normalized) > 1024:
        raise ValueError("根目录不能超过 1024 个字符")
    return normalized


def _parse_default(field: dict[str, Any]) -> Any:
    value = field.get("default", "")
    field_type = str(field.get("type") or "string").lower()
    if field_type in {"bool", "boolean"}:
        return str(value).lower() in {"true", "1", "yes"}
    if field_type in {"number", "integer", "int", "int64"} and str(value).strip():
        try:
            numeric = float(value)
            return int(numeric) if numeric.is_integer() else numeric
        except (TypeError, ValueError):
            return value
    return value


class OpenListAdminClient:
    """Small, token-private client for OpenList's official admin API."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        binary_path: str | Path | None = None,
        session: requests.Session | None = None,
    ):
        self.base_url = str(
            base_url or os.getenv("MOVIESYNC_OPENLIST_URL", DEFAULT_OPENLIST_URL)
        ).strip().rstrip("/")
        self.data_dir = Path(
            data_dir
            or os.getenv("MOVIESYNC_DATA_DIR", "/app/data")
        ).expanduser().resolve()
        self.openlist_data_dir = Path(
            os.getenv("MOVIESYNC_OPENLIST_DATA_DIR", str(self.data_dir / "openlist"))
        ).expanduser().resolve()
        self.binary_path = str(
            binary_path
            or os.getenv("MOVIESYNC_OPENLIST_BINARY", "")
            or shutil.which("openlist")
            or "/usr/local/bin/openlist"
        )
        self._token = str(token or os.getenv("MOVIESYNC_OPENLIST_ADMIN_TOKEN", "")).strip()
        self._token_lock = Lock()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "MovieSync-OpenList-StorageCard/1.0"})

    def close(self) -> None:
        self.session.close()

    def _admin_token(self) -> str:
        if self._token:
            return self._token
        with self._token_lock:
            if self._token:
                return self._token
            binary = Path(self.binary_path)
            if not binary.is_file():
                raise RuntimeError(
                    "未找到内置 OpenList 引擎；请确认 MovieSync 镜像包含 OpenList，且未关闭内置服务"
                )
            try:
                result = subprocess.run(
                    [
                        str(binary),
                        "--data",
                        str(self.openlist_data_dir),
                        "admin",
                        "token",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("读取内置 OpenList 管理凭据失败") from exc
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            match = re.search(r"Admin token:\s*(\S+)", output)
            if result.returncode != 0 or not match:
                raise RuntimeError("无法读取内置 OpenList 管理凭据；请检查 OpenList 数据目录和启动日志")
            self._token = match.group(1).strip()
            if not self._token:
                raise RuntimeError("内置 OpenList 尚未生成管理 API Token")
            return self._token

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + "/" + endpoint.lstrip("/")
        headers = {"Authorization": self._admin_token()}
        response = None
        try:
            request_method = getattr(self.session, method.lower())
            kwargs: dict[str, Any] = {
                "headers": headers,
                "timeout": (4, 20),
            }
            if method.upper() == "GET":
                kwargs["params"] = params or {}
            else:
                kwargs["params"] = params or {}
                kwargs["json"] = payload or {}
            response = request_method(url, **kwargs)
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            status_code = getattr(response, "status_code", None)
            if status_code in (401, 403):
                raise RuntimeError("内置 OpenList 管理 API 鉴权失败，请检查 OpenList 运行状态") from exc
            raise RuntimeError(f"连接内置 OpenList 失败（{type(exc).__name__}）") from exc
        except (ValueError, TypeError) as exc:
            raise RuntimeError("内置 OpenList 返回了无效 JSON") from exc

        if not isinstance(body, dict):
            raise RuntimeError("内置 OpenList 返回的数据格式无效")
        code = body.get("code")
        if code not in (None, 0, 200, "0", "200"):
            message = str(body.get("message") or body.get("msg") or "OpenList 接口请求失败")
            raise RuntimeError(f"OpenList API 错误（{code}）：{message[:180]}")
        return body

    @staticmethod
    def _response_data(body: dict[str, Any]) -> Any:
        return body.get("data")

    def driver_map(self) -> dict[str, dict[str, Any]]:
        """Read driver names and parameter schemas directly from OpenList."""
        body = self._request("GET", "/api/admin/driver/list")
        data = self._response_data(body)
        if not isinstance(data, dict) or not data:
            raise RuntimeError("OpenList 没有返回可用的官方存储驱动列表")
        return {
            str(name): info
            for name, info in data.items()
            if str(name).strip() and isinstance(info, dict)
        }

    def driver_schema(self, driver_name: str) -> dict[str, Any]:
        name = str(driver_name or "").strip()
        if not name or len(name) > 120:
            raise ValueError("请选择有效的 OpenList 存储类型")
        drivers = self.driver_map()
        info = drivers.get(name)
        if info is None:
            # Match the official driver names case-insensitively without inventing aliases.
            match = next((key for key in drivers if key.casefold() == name.casefold()), None)
            if match is None:
                raise ValueError(f"OpenList 官方驱动列表中没有「{name}」")
            name, info = match, drivers[match]
        additions = info.get("additional")
        if not isinstance(additions, list):
            additions = []
        fields = []
        for item in additions:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or "").strip()
            if not key:
                continue
            if key in {"root_folder_id", "root_folder_path"}:
                continue
            fields.append({
                "name": key,
                "type": str(item.get("type") or "string").lower(),
                "default": item.get("default", ""),
                "options": item.get("options") or "",
                "required": bool(item.get("required")),
                "help": str(item.get("help") or ""),
                "secret": is_sensitive_parameter(key),
            })
        return {
            "name": name,
            "label": str((info.get("config") or {}).get("name") or name),
            "fields": fields,
            "root_mode": (
                "id" if any(
                    isinstance(item, dict) and item.get("name") == "root_folder_id"
                    for item in additions
                ) else
                "path" if any(
                    isinstance(item, dict) and item.get("name") == "root_folder_path"
                    for item in additions
                ) else "driver"
            ),
        }

    def list_storages(self) -> list[dict[str, Any]]:
        storages: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while page <= 100:
            body = self._request(
                "GET",
                "/api/admin/storage/list",
                params={"page": page, "per_page": per_page},
            )
            data = self._response_data(body)
            content = data.get("content", []) if isinstance(data, dict) else []
            if not isinstance(content, list):
                raise RuntimeError("OpenList 返回的挂载列表格式无效")
            storages.extend(item for item in content if isinstance(item, dict))
            try:
                total = int(data.get("total", len(storages))) if isinstance(data, dict) else len(storages)
            except (TypeError, ValueError):
                total = len(storages)
            if len(storages) >= total or len(content) < per_page:
                break
            page += 1
        return storages

    @staticmethod
    def _read_addition(storage: dict[str, Any]) -> dict[str, Any]:
        raw = storage.get("addition", {})
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def list_public_storages(self) -> list[dict[str, Any]]:
        """Return mount metadata, with provider credentials removed before serialization."""
        drivers = self.driver_map()
        result = []
        for storage in self.list_storages():
            driver = str(storage.get("driver") or "")
            addition = self._read_addition(storage)
            info = drivers.get(driver, {})
            official_fields = info.get("additional", []) if isinstance(info, dict) else []
            secret_names = {
                str(item.get("name"))
                for item in official_fields
                if isinstance(item, dict) and is_sensitive_parameter(item.get("name"))
            }
            # Treat common credential names as sensitive even if an older OpenList
            # driver schema does not flag them in its Additional metadata.
            secret_names.update(key for key in addition if is_sensitive_parameter(key))
            has_value = {
                key: bool(str(addition.get(key) or "").strip())
                for key in secret_names
            }
            public_addition = dict(addition)
            for key in secret_names:
                if key in public_addition:
                    public_addition[key] = ""
            if "root_folder_path" in addition:
                root_path = str(addition.get("root_folder_path") or "/")
            elif str(addition.get("root_folder_id") or "0") in {"", "0"}:
                root_path = "/"
            else:
                # For ID-based drivers, the value represents a folder ID, not a path.
                root_path = str(addition.get("root_folder_id") or "/")
            mount_path = str(storage.get("mount_path") or "/")
            result.append({
                "id": storage.get("id"),
                "driver": driver,
                "display_name": str(storage.get("remark") or mount_path),
                "mount_path": mount_path,
                "root_path": root_path,
                "authorization_parameters": public_addition,
                "has_value": has_value,
                "status": str(storage.get("status") or ""),
                "disabled": bool(storage.get("disabled", False)),
            })
        return result

    @staticmethod
    def _required_missing(field: dict[str, Any], value: Any) -> bool:
        if not field.get("required"):
            return False
        if isinstance(value, bool):
            return False
        if value is None:
            return True
        return isinstance(value, str) and not value.strip()

    def save_storage(
        self,
        *,
        driver: object,
        display_name: object,
        mount_path: object,
        root_path: object = "/",
        authorization_parameters: object = None,
        storage_id: object = None,
    ) -> dict[str, Any]:
        driver_name = str(driver or "").strip()
        display = str(display_name or "").strip()
        if not display or len(display) > 160:
            raise ValueError("请填写显示名称（最多 160 个字符）")
        path = normalize_mount_path(mount_path)
        root = normalize_root_path(root_path)
        if not isinstance(authorization_parameters, dict):
            raise ValueError("授权参数格式无效；请重新选择存储类型后填写驱动参数")
        params = dict(authorization_parameters)
        if len(params) > 120:
            raise ValueError("驱动参数数量过多")

        schema = self.driver_schema(driver_name)
        driver_name = schema["name"]
        driver_info = self.driver_map()[driver_name]
        official_fields = driver_info.get("additional", [])
        if not isinstance(official_fields, list):
            official_fields = []

        # Read existing data first so empty password inputs can preserve credentials
        # that were saved in OpenList, while a new mount with empty credentials fails closed.
        storages = self.list_storages()
        requested_id = None
        if storage_id not in (None, ""):
            try:
                requested_id = int(storage_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("存储挂载 ID 无效") from exc
            if requested_id < 1:
                raise ValueError("存储挂载 ID 无效")
        by_id = next((item for item in storages if item.get("id") == requested_id), None) if requested_id else None
        by_path = next(
            (item for item in storages if str(item.get("mount_path") or "").rstrip("/") == path.rstrip("/")),
            None,
        )
        existing = by_id or by_path
        if requested_id and by_id is None:
            raise ValueError("要编辑的挂载已不存在，请刷新列表后重试")
        if by_id and str(by_id.get("mount_path") or "").rstrip("/") != path.rstrip("/"):
            conflict = next(
                (item for item in storages if item.get("id") != requested_id and str(item.get("mount_path") or "").rstrip("/") == path.rstrip("/")),
                None,
            )
            if conflict:
                raise ValueError(f"挂载路径「{path}」已被另一张网盘占用")
        if existing and str(existing.get("driver") or "") != driver_name:
            raise ValueError(
                "OpenList 不支持直接修改已有挂载的驱动类型；请先添加到新的挂载路径，验证成功后再删除旧挂载"
            )

        old_addition = self._read_addition(existing or {})
        merged_addition: dict[str, Any] = {}
        unknown = set(params) - {
            str(item.get("name"))
            for item in official_fields
            if isinstance(item, dict) and item.get("name") not in {"root_folder_id", "root_folder_path"}
        }
        if unknown:
            raise ValueError("包含该驱动未声明的参数：" + ", ".join(sorted(str(key) for key in unknown)[:10]))

        for field in official_fields:
            if not isinstance(field, dict):
                continue
            key = str(field.get("name") or "").strip()
            if not key:
                continue
            if key == "root_folder_id":
                value = "0" if root == "/" else root
            elif key == "root_folder_path":
                value = root
            else:
                value = params.get(key, _parse_default(field))
                if is_sensitive_parameter(key) and (value is None or (isinstance(value, str) and not value.strip())):
                    if old_addition.get(key) not in (None, ""):
                        value = old_addition[key]
            if self._required_missing(field, value):
                label = key.replace("_", " ")
                raise ValueError(f"请填写 OpenList 官方驱动必填参数：{label}")
            merged_addition[key] = value

        # If the selected driver does not expose a root selector, its official
        # implementation determines the root. Keep root_path in the UI for a
        # consistent workflow without inventing driver-specific JSON properties.
        addition_json = json.dumps(merged_addition, ensure_ascii=False, separators=(",", ":"))
        if len(addition_json) > 128 * 1024:
            raise ValueError("授权参数过大，不能超过 128 KiB")

        payload = {
            "mount_path": path,
            "order": 0,
            "driver": driver_name,
            "cache_expiration": 30,
            "custom_cache_policies": "",
            "remark": display,
            "addition": addition_json,
            "disabled": False,
            "disable_index": False,
            "enable_sign": False,
            "order_by": "",
            "order_direction": "asc",
            "extract_folder": "front",
            "web_proxy": False,
            "webdav_policy": "native_proxy",
            "proxy_range": False,
            "down_proxy_url": "",
            "disable_proxy_sign": False,
        }
        if existing:
            if bool(existing.get("disabled")):
                raise ValueError("该挂载已停用；请先启用后再编辑，或直接删除并重新创建")
            existing_payload = {
                key: value for key, value in existing.items()
                if key in _STORAGE_FIELDS and key != "status"
            }
            existing_payload.update(payload)
            existing_payload["id"] = existing.get("id")
            # Preserve OpenList's driver-independent tuning values while updating
            # only the mount/name/root/driver parameters owned by this form.
            for key in ("order", "cache_expiration", "custom_cache_policies", "disable_index",
                        "enable_sign", "order_by", "order_direction", "extract_folder",
                        "web_proxy", "webdav_policy", "proxy_range", "down_proxy_url",
                        "disable_proxy_sign"):
                if key in existing:
                    existing_payload[key] = existing[key]
            self._request("POST", "/api/admin/storage/update", payload=existing_payload)
            return {"id": existing.get("id"), "created": False, "driver": driver_name, "mount_path": path, "display_name": display}

        if by_path:
            raise ValueError(f"挂载路径「{path}」已存在，请点击该挂载的编辑按钮进行修改")
        body = self._request("POST", "/api/admin/storage/create", payload=payload)
        created_data = self._response_data(body)
        created_id = created_data.get("id") if isinstance(created_data, dict) else None
        return {"id": created_id, "created": True, "driver": driver_name, "mount_path": path, "display_name": display}

    def delete_storage(self, storage_id: object) -> None:
        try:
            id_value = int(storage_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("存储挂载 ID 无效") from exc
        if id_value < 1:
            raise ValueError("存储挂载 ID 无效")
        self._request("POST", "/api/admin/storage/delete", params={"id": id_value})

    def set_storage_enabled(self, storage_id: object, enabled: object) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须是布尔值")
        try:
            id_value = int(storage_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("存储挂载 ID 无效") from exc
        if id_value < 1:
            raise ValueError("存储挂载 ID 无效")
        endpoint = "/api/admin/storage/enable" if enabled else "/api/admin/storage/disable"
        self._request("POST", endpoint, params={"id": id_value})
