"""OpenList playback-provider card backed by MovieSync's bundled OpenList runtime.

Connection details and administrator credentials are managed internally. The only
optional setting is a browser-reachable playback URL prefix for reverse-proxy setups.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from moviesync.cards import CardManifest, PlaybackProviderCard
from moviesync.errors import ConfigValidationError

_VIDEO_EXTENSIONS = {
    ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".vob", ".webm", ".wmv",
}


def _clean_base_url(value: object) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise ConfigValidationError("OpenList 地址格式无效") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(char.isspace() for char in parsed.hostname or "")
        or (port is not None and not 1 <= port <= 65535)
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigValidationError("OpenList 地址必须是有效的 HTTP/HTTPS 根地址")
    return raw


def _clean_path(value: object, default: str = "/") -> str:
    path = str(value or default).strip().replace("\\", "/")
    if not path:
        path = default
    if not path.startswith("/"):
        path = "/" + path
    parts = [part for part in path.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ConfigValidationError("目录路径不能包含 ..")
    return "/" + "/".join(parts)


def _is_video(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in _VIDEO_EXTENSIONS)


class OpenListPlaybackCard(PlaybackProviderCard):
    manifest = CardManifest(
        id="openlist",
        name="OpenList 播放卡片",
        version="0.2.0",
        type="playback_provider",
        description="自动连接 MovieSync 内置 OpenList；无需填写服务地址或 Token。",
        capabilities=(
            "playback.check",
            "playback.list_files",
            "playback.resolve",
            "playback.delete_files",
            "playback.delete_items",
        ),
        config_fields=(
            {
                "key": "playback_base_url",
                "label": "浏览器播放地址前缀（可选）",
                "type": "string",
                "format": "url",
                "required": False,
                "placeholder": "例如 http://192.168.1.2:5244",
                "description": (
                    "通常可留空。仅当播放链接中的容器内部地址无法被浏览器访问时，"
                    "填写浏览器可访问的 OpenList 地址；不需要填写 /api 路径。"
                ),
            },
        ),
    )

    def __init__(self, context):
        self.config_store = context["config_store"]
        self.openlist_admin = context.get("openlist_admin")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MovieSync-OpenList-Card/0.1"})
    
    def _config(self) -> dict[str, Any]:
        getter = getattr(self.config_store, "get_card_config", None)
        if not callable(getter):
            return {}
        config = getter(self.card_id)
        config = dict(config) if isinstance(config, dict) else {}
        # Ignore legacy editable connection values: this card now always targets
        # the bundled engine, whose auth token is resolved server-side.
        config["base_url"] = str(
            getattr(self.openlist_admin, "base_url", "") or "http://127.0.0.1:5244"
        )
        config["root_path"] = "/"
        config.setdefault("playback_base_url", "")
        config["verify_tls"] = True
        return config

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        # The generic validator checks URL format whenever a field is present.
        # Omit an empty optional prefix during that pass, then restore its empty
        # normalized value so the form can save without a browser-facing prefix.
        incoming = dict(config)
        playback_base = str(incoming.get("playback_base_url") or "").strip()
        if not playback_base:
            incoming.pop("playback_base_url", None)
        normalized = super().validate_config(incoming)
        normalized["base_url"] = _clean_base_url(
            getattr(self.openlist_admin, "base_url", "") or "http://127.0.0.1:5244"
        )
        normalized["root_path"] = "/"
        normalized["playback_base_url"] = _clean_base_url(playback_base) if playback_base else ""
        normalized["token"] = str(normalized.get("token") or "").strip()
        normalized["verify_tls"] = True
        return normalized

    def is_configured(self, config: dict[str, Any]) -> bool:
        # The bundled engine is part of MovieSync, so the card needs no user-entered
        # connection settings to be considered configured.
        return True

    def _auth_token(self, config: dict[str, Any]) -> str:
        base_url = _clean_base_url(
            config.get("base_url")
            or getattr(self.openlist_admin, "base_url", "")
            or "http://127.0.0.1:5244"
        )
        internal_url = _clean_base_url(
            getattr(self.openlist_admin, "base_url", "") or "http://127.0.0.1:5244"
        )
        if self.openlist_admin is not None and base_url == internal_url:
            token_getter = getattr(self.openlist_admin, "_admin_token", None)
            if callable(token_getter):
                return str(token_getter() or "").strip()
        return str(config.get("token") or "").strip()

    def _request(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self._config()
        base_url = _clean_base_url(
            config.get("base_url")
            or getattr(self.openlist_admin, "base_url", "")
            or "http://127.0.0.1:5244"
        )
        headers = {}
        token = self._auth_token(config)
        if token:
            headers["Authorization"] = token
        verify_tls = config.get("verify_tls", True) is not False
        url = urljoin(base_url + "/", endpoint.lstrip("/"))
        response = None
        try:
            response = self.session.post(
                url,
                json=payload or {},
                headers=headers,
                timeout=(4, 15),
                verify=verify_tls,
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as exc:
            status_code = getattr(response, "status_code", None)
            if status_code in (401, 403):
                raise RuntimeError(
                    "OpenList 权限不足或 Token 无效；请检查账号权限及映射存储的删除权限"
                ) from exc
            raise RuntimeError(f"连接 OpenList 失败：{type(exc).__name__}") from exc
        except ValueError as exc:
            raise RuntimeError("OpenList 返回的不是有效 JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("OpenList 返回的数据格式无效")
        code = body.get("code")
        if code in (401, 403):
            raise RuntimeError(
                "OpenList 权限不足或 Token 无效；请检查账号权限及映射存储的删除权限"
            )
        if code not in (None, 200):
            message = str(body.get("message") or body.get("msg") or "接口请求失败")
            raise RuntimeError(f"OpenList API 错误（{code}）：{message[:180]}")
        return body

    def check(self, config: dict | None = None) -> dict[str, Any]:
        saved = self._config()

        try:
            items = self.list_files()
        except Exception as exc:
            return {"status": "unavailable", "message": str(exc)[:240]}
        return {
            "status": "healthy",
            "message": f"OpenList 连接成功，媒体根目录可读取（{len(items)} 个条目）",
            "details": {"root_path": _clean_path(saved.get("root_path", "/")), "items": len(items)},
        }

    def list_files(self, parent_fid: str = "", refresh: bool = False) -> list[dict[str, Any]]:
        config = self._config()
        root_path = _clean_path(config.get("root_path", "/"))
        if parent_fid:
            requested_path = _clean_path(parent_fid)
            root_prefix = root_path.rstrip("/")
            if root_prefix and requested_path != root_path and not requested_path.startswith(root_prefix + "/"):
                raise ValueError("只能浏览已配置媒体根目录内的路径")
            path = requested_path
        else:
            path = root_path
        body = self._request(
            "/api/fs/list",
            {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": bool(refresh)},
        )
        data = body.get("data") or {}
        content = data.get("content") or []
        if not isinstance(content, list):
            raise RuntimeError("OpenList 目录列表格式无效")
        items = []
        for item in content:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            is_dir = bool(item.get("is_dir", item.get("isDir", False)))
            item_path = path.rstrip("/") + "/" + name if path != "/" else "/" + name
            items.append({
                "id": item_path,
                "fid": item_path,
                "name": name,
                "path": item_path,
                "is_dir": is_dir,
                "size": item.get("size"),
                "modified": item.get("modified") or item.get("updated_at"),
                "is_video": (not is_dir) and _is_video(name),
                "type": "directory" if is_dir else ("video" if _is_video(name) else "file"),
            })
        items.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
        return items

    def delete_item(self, item_path: str, expected_is_dir: bool | None = None) -> dict[str, Any]:
        """Delete one explicitly selected file or folder inside the media root."""
        path = _clean_path(item_path)
        config = self._config()
        root_path = _clean_path(config.get("root_path", "/"))
        root_prefix = root_path.rstrip("/")
        if path == root_path:
            raise ValueError("不能删除媒体根目录")
        if root_prefix and not path.startswith(root_prefix + "/"):
            raise ValueError("只能删除已配置媒体根目录内的路径")
        name = path.rsplit("/", 1)[-1]
        parent_path = path.rsplit("/", 1)[0] or "/"
        if not name or name in {".", ".."}:
            raise ValueError("路径无效")

        if not self._auth_token(config):
            raise RuntimeError(
                "无法获取 OpenList 管理凭据；请确认内置 OpenList 已启动"
            )

        # Re-read the parent listing and verify both path and type to prevent
        # deleting stale entries or a different item that reused the same name.
        entries = self.list_files(parent_path, refresh=True)
        target = next(
            (item for item in entries if item.get("name") == name and item.get("path") == path),
            None,
        )
        if target is None:
            raise ValueError("文件或文件夹不存在或已从当前 OpenList 目录移除，请刷新后重试")
        is_dir = bool(target.get("is_dir"))
        if expected_is_dir is not None and is_dir is not expected_is_dir:
            raise ValueError("目标类型已变化，请刷新目录后重试")
        self._request("/api/fs/remove", {"dir": parent_path, "names": [name]})
        return {"path": path, "name": name, "is_dir": is_dir}

    def delete_file(self, file_path: str) -> dict[str, Any]:
        """Backward-compatible file-only delete API."""
        result = self.delete_item(file_path, expected_is_dir=False)
        return {"path": result["path"], "name": result["name"]}

    def resolve_playback(self, fid: str) -> dict[str, Any]:
        path = _clean_path(fid)
        config = self._config()
        root_path = _clean_path(config.get("root_path", "/"))
        root_prefix = root_path.rstrip("/")
        if root_prefix and path != root_path and not path.startswith(root_prefix + "/"):
            raise ValueError("只能播放已配置媒体根目录内的文件")
        if not _is_video(path):
            raise ValueError("所选文件不是支持的视频格式")
        body = self._request(
            "/api/fs/get",
            {"path": path, "password": ""},
        )
        data = body.get("data") or {}
        play_url = str(data.get("raw_url") or data.get("url") or "").strip()
        if not play_url:
            raise RuntimeError("OpenList 未返回可用的原始播放地址")
        playback_base = str(config.get("playback_base_url") or "").strip()
        if playback_base:
            original = urlparse(play_url)
            replacement = urlparse(playback_base)
            if original.scheme not in {"http", "https"} or not original.hostname:
                raise RuntimeError("OpenList 返回的播放地址无效")
            original_prefix = original.scheme + "://" + original.netloc
            play_url = replacement.scheme + "://" + replacement.netloc + play_url[len(original_prefix):]
        return {
            "fid": path,
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "url": play_url,
            "play_url": play_url,
            "headers": data.get("header") or data.get("headers") or {},
            "mime_type": "video/mp4" if path.lower().endswith(".mp4") else "",
        }

    def close(self) -> None:
        self.session.close()


def create_card(context):
    """Card factory used by MovieSync's local card loader."""
    return OpenListPlaybackCard(context)
