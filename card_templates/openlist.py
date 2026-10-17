"""Optional OpenList playback-provider card.

This card talks to an existing OpenList instance; MovieSync does not install or
manage the OpenList service. The core playback page/API can consume this card's
normalized file and playback URL methods.
"""
from __future__ import annotations

import requests
from typing import Any
from urllib.parse import urljoin, urlparse

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
        version="0.1.0",
        type="playback_provider",
        description="连接已有的 OpenList，浏览媒体目录并解析视频播放地址；OpenList 服务需自行部署。",
        capabilities=("playback.check", "playback.list_files", "playback.resolve"),
        config_fields=(
            {
                "key": "base_url",
                "label": "OpenList 服务地址",
                "type": "string",
                "format": "url",
                "required": True,
                "placeholder": "http://192.168.1.2:5244",
                "description": "填写 MovieSync 容器可访问的 OpenList 地址，不要填写 /api 路径。",
            },
            {
                "key": "token",
                "label": "OpenList API Token",
                "type": "password",
                "secret": True,
                "required": False,
                "placeholder": "粘贴 OpenList Token；留空表示尝试匿名访问",
                "description": "仅保存在服务端，不会回显。",
            },
            {
                "key": "root_path",
                "label": "媒体根目录",
                "type": "string",
                "required": True,
                "default": "/",
                "placeholder": "/",
                "description": "OpenList 中媒体目录的路径，例如 /夸克/电影。",
            },
            {
                "key": "playback_base_url",
                "label": "浏览器播放地址前缀（可选）",
                "type": "string",
                "format": "url",
                "required": False,
                "placeholder": "https://alist.example.com",
                "description": "仅当 OpenList 返回的播放链接无法被浏览器访问时填写；用于替换链接的主机前缀。",
            },
            {
                "key": "verify_tls",
                "label": "验证 HTTPS 证书",
                "type": "boolean",
                "required": False,
                "default": True,
                "description": "仅对 HTTPS 地址生效；不建议关闭。",
            },
        ),
    )

    def __init__(self, context):
        self.config_store = context["config_store"]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MovieSync-OpenList-Card/0.1"})
    
    def _config(self) -> dict[str, Any]:
        getter = getattr(self.config_store, "get_card_config", None)
        if not callable(getter):
            return {}
        config = getter(self.card_id)
        return config if isinstance(config, dict) else {}

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = super().validate_config(config)
        normalized["base_url"] = _clean_base_url(normalized.get("base_url"))
        normalized["root_path"] = _clean_path(normalized.get("root_path", "/"))
        playback_base = str(normalized.get("playback_base_url") or "").strip()
        normalized["playback_base_url"] = _clean_base_url(playback_base) if playback_base else ""
        normalized["token"] = str(normalized.get("token") or "").strip()
        normalized["verify_tls"] = normalized.get("verify_tls", True) is not False
        return normalized

    def is_configured(self, config: dict[str, Any]) -> bool:
        return bool(str(config.get("base_url") or "").strip())

    def _request(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        config = self._config()
        base_url = _clean_base_url(config.get("base_url"))
        headers = {}
        token = str(config.get("token") or "").strip()
        if token:
            headers["Authorization"] = token
        verify_tls = config.get("verify_tls", True) is not False
        url = urljoin(base_url + "/", endpoint.lstrip("/"))
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
            raise RuntimeError(f"连接 OpenList 失败：{type(exc).__name__}") from exc
        except ValueError as exc:
            raise RuntimeError("OpenList 返回的不是有效 JSON") from exc
        if not isinstance(body, dict):
            raise RuntimeError("OpenList 返回的数据格式无效")
        code = body.get("code")
        if code not in (None, 200):
            message = str(body.get("message") or body.get("msg") or "接口请求失败")
            raise RuntimeError(f"OpenList API 错误（{code}）：{message[:180]}")
        return body

    def check(self, config: dict | None = None) -> dict[str, Any]:
        if isinstance(config, dict):
            # Config is normally persisted before the card's check method is called.
            base_url = str(config.get("base_url") or "").strip()
            if not base_url:
                return {"status": "unconfigured", "message": "请先填写 OpenList 服务地址"}
        saved = self._config()
        if not str(saved.get("base_url") or "").strip():
            return {"status": "unconfigured", "message": "请先填写 OpenList 服务地址"}
        try:
            items = self.list_files()
        except Exception as exc:
            return {"status": "unavailable", "message": str(exc)[:240]}
        return {
            "status": "healthy",
            "message": f"OpenList 连接成功，媒体根目录可读取（{len(items)} 个条目）",
            "details": {"root_path": _clean_path(saved.get("root_path", "/")), "items": len(items)},
        }

    def list_files(self, parent_fid: str = "") -> list[dict[str, Any]]:
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
            {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": False},
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
