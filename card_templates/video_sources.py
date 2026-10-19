"""Manage configured online video collection sources using the MacCMS JSON API.

The module filename is intentionally generic; the stable card ID and integration
capabilities remain MacCMS-specific for backward compatibility.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlsplit

import requests

from moviesync.cards import Card, CardManifest
from moviesync.errors import ConfigValidationError

_MAX_SOURCES = 50
_TIMEOUT = (3.05, 8)
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _public_http_url(value: object, *, resolve_dns: bool = False) -> str:
    """Validate an externally reachable HTTP(S) URL before making an outbound request."""
    url = str(value or "").strip()
    if len(url) > 2048:
        raise ConfigValidationError("采集接口地址不能超过 2048 个字符")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigValidationError("采集接口地址格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ConfigValidationError("采集接口只支持 HTTP 或 HTTPS 地址")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ConfigValidationError("请填写完整接口地址，不要在 URL 中嵌入用户名或密码")
    if parsed.fragment:
        raise ConfigValidationError("接口地址不能包含 # 片段")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigValidationError("接口端口无效")

    hostname = parsed.hostname.rstrip(".").lower()
    try:
        literal_address = ipaddress.ip_address(hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise ConfigValidationError("为避免服务器端请求伪造，暂不允许访问本机、内网或非公网地址")
    elif resolve_dns:
        try:
            addresses = {
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    hostname,
                    port or (443 if parsed.scheme.lower() == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError) as exc:
            raise ConfigValidationError("接口域名无法解析") from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise ConfigValidationError("为避免服务器端请求伪造，暂不允许访问本机、内网或非公网地址")
    return url


def _normalize_sources(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigValidationError("采集站点必须是数组")
    if len(value) > _MAX_SOURCES:
        raise ConfigValidationError(f"采集站点数量不能超过 {_MAX_SOURCES} 个")

    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ConfigValidationError(f"第 {index} 个采集站点格式无效")
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or "").strip()
        enabled = raw.get("enabled", True)
        if not name:
            raise ConfigValidationError(f"第 {index} 个采集站点需要填写名称")
        if len(name) > 100:
            raise ConfigValidationError(f"第 {index} 个采集站点名称不能超过 100 个字符")
        if not isinstance(enabled, bool):
            raise ConfigValidationError(f"第 {index} 个采集站点的启用状态无效")
        url = _public_http_url(url)
        normalized_url = url.rstrip("/")
        if normalized_url in seen_urls:
            raise ConfigValidationError(f"采集接口重复：{name}")
        seen_urls.add(normalized_url)

        source_id = str(raw.get("id") or "").strip().lower()
        if not source_id:
            slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:32]
            if len(slug) < 2:
                slug = "source"
            suffix = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
            source_id = f"{slug}-{suffix}"[:64]
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise ConfigValidationError(f"第 {index} 个采集站点 ID 无效")
        if source_id in seen_ids:
            raise ConfigValidationError(f"采集站点 ID 重复：{source_id}")
        seen_ids.add(source_id)
        result.append({"id": source_id, "name": name, "url": url, "enabled": enabled})
    return result


def _check_source(source: dict[str, Any]) -> dict[str, Any]:
    """Check reachability and a JSON response without following redirects."""
    try:
        url = _public_http_url(source.get("url"), resolve_dns=True)
        response = requests.get(
            url,
            params={"ac": "list", "pg": 1},
            timeout=_TIMEOUT,
            allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "MovieSync-MacCMS-Checker/1.0"},
        )
        if 300 <= response.status_code < 400:
            return {"id": source["id"], "name": source["name"], "status": "unavailable",
                    "message": "接口返回重定向；请填写最终的 API 地址"}
        if response.status_code != 200:
            return {"id": source["id"], "name": source["name"], "status": "unavailable",
                    "message": f"HTTP {response.status_code}"}
        try:
            payload = response.json()
        except ValueError:
            return {"id": source["id"], "name": source["name"], "status": "invalid",
                    "message": "接口可访问，但响应不是有效 JSON"}
        if not isinstance(payload, dict):
            return {"id": source["id"], "name": source["name"], "status": "invalid",
                    "message": "接口返回了 JSON，但顶层不是对象"}

        code = payload.get("code")
        looks_like_maccms = (
            "list" in payload or "vod" in payload or "data" in payload
            or (code is not None and str(code) in {"1", "200"})
        )
        if not looks_like_maccms:
            return {"id": source["id"], "name": source["name"], "status": "invalid",
                    "message": "接口返回 JSON，但未识别到常见 MacCMS 数据字段"}
        return {"id": source["id"], "name": source["name"], "status": "healthy",
                "message": "连接成功，已收到 JSON 接口响应"}
    except ConfigValidationError as exc:
        return {"id": source.get("id", ""), "name": source.get("name", "未命名站点"),
                "status": "invalid", "message": str(exc)}
    except requests.Timeout:
        return {"id": source.get("id", ""), "name": source.get("name", "未命名站点"),
                "status": "unavailable", "message": "连接超时"}
    except requests.RequestException:
        return {"id": source.get("id", ""), "name": source.get("name", "未命名站点"),
                "status": "unavailable", "message": "请求失败，请确认接口地址和站点状态"}
    except Exception:
        return {"id": source.get("id", ""), "name": source.get("name", "未命名站点"),
                "status": "unavailable", "message": "检查失败，请查看服务日志"}


class MacCMSSourceManagerCard(Card):
    manifest = CardManifest(
        id="maccms",
        name="影视采集源",
        version="0.1.0",
        type="custom",
        description="集中管理多个 MacCMS JSON 采集接口，并检查接口连接状态。",
        capabilities=("maccms.sources.manage", "maccms.check"),
        config_fields=(
            {
                "key": "sources",
                "label": "采集站点",
                "type": "json",
                "editor": "maccms_sources",
                "default": [],
                "required": False,
                "item_fields": (
                    {
                        "key": "name",
                        "label": "站点名称",
                        "placeholder": "例如：影视站 A",
                        "required": True,
                    },
                    {
                        "key": "url",
                        "label": "MacCMS JSON 接口地址",
                        "placeholder": "https://example.com/api.php/provide/vod/",
                        "required": True,
                    },
                ),
                "description": (
                    "在这里集中添加多个站点。添加后先保存并检查连接；"
                    "只有启用的站点会在后续点播搜索中使用。"
                ),
            },
        ),
    )

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = super().validate_config(config)
        normalized["sources"] = _normalize_sources(normalized.get("sources", []))
        return normalized

    def is_configured(self, config: dict[str, Any]) -> bool:
        try:
            return bool(_normalize_sources((config or {}).get("sources", [])))
        except (ConfigValidationError, TypeError, AttributeError):
            return False

    def check(self, config: dict[str, Any]) -> dict[str, Any]:
        try:
            sources = _normalize_sources((config or {}).get("sources", []))
        except (ConfigValidationError, TypeError, AttributeError) as exc:
            return {"status": "unavailable", "message": str(exc), "sources": []}

        if not sources:
            return {"status": "idle", "message": "还没有添加采集站点，请先添加接口地址。", "sources": []}

        results = [_check_source(source) for source in sources]
        healthy = sum(item["status"] == "healthy" for item in results)
        enabled = sum(source["enabled"] for source in sources)
        if healthy == len(sources):
            status = "healthy"
        elif healthy:
            status = "degraded"
        else:
            status = "unavailable"
        message = f"已检查 {len(results)} 个站点，{healthy} 个接口响应正常；其中 {enabled} 个已启用。"
        return {"status": status, "message": message, "sources": results}


def create_card(context):
    """Factory used by MovieSync's bundled single-file card loader."""
    return MacCMSSourceManagerCard()
