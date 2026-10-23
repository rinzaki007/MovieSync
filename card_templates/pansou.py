"""Bundled single-file PanSou resource-source card.

The file can be copied to the persistent data card directory when bundled-card
installation is explicitly enabled. The runtime loads that copy, so uninstalling it
does not require changing core code.
"""
from __future__ import annotations

import re
import threading
from typing import Any

from moviesync.cards import CardManifest, ResourceSourceCard
from moviesync.config_store import ConfigValidationError


class PanSouResourceSource(ResourceSourceCard):
    """PanSou Web 搜索适配器。

    根据所选存储卡片声明的 storage.accepts.* 能力筛选 PanSou cloud_types，
    并仅规范化所选网盘的分享链接；不把其他网盘的资源交给错误的存储卡片。
    """

    source_id = "pansou"
    name = "PanSou"
    source_type = "pansou"
    manifest = CardManifest(
        id="pansou",
        name="PanSou",
        version="1.0.0",
        type="resource_source",
        description="根据所选存储卡片搜索对应网盘分享资源；可使用内置 PanSou 或外部服务",
        capabilities=("resource.search", "resource.health_check"),
        config_fields=(
            {
                "key": "base_url",
                "label": "PanSou 地址（默认内置）",
                "type": "string",
                "format": "url",
                "required": False,
                "default": "http://127.0.0.1:8888",
                "max_length": 500,
                "placeholder": "http://127.0.0.1:8888",
                "description": "默认使用 MovieSync 内置 PanSou，无需单独部署。仅在接入外部 PanSou 时修改此地址。",
            },
            {
                "key": "username",
                "label": "PanSou 用户名",
                "type": "string",
                "required": False,
                "max_length": 128,
                "description": "仅在 PanSou 启用了认证时填写。",
            },
            {
                "key": "password",
                "label": "PanSou 密码",
                "type": "password",
                "secret": True,
                "required": False,
                "max_length": 256,
                "description": "仅在 PanSou 启用了认证时填写；不会回显到页面。",
            },
            {
                "key": "timeout",
                "label": "请求超时（秒）",
                "type": "number",
                "default": 45,
                "description": "PanSou 搜索可能较慢，建议 30–60 秒。",
            },
        ),
    )

    def __init__(self, http_client=None):
        from moviesync.clients.http import HttpClient

        self.http = http_client or HttpClient()
        self._token = ""
        self._token_expires_at = 0.0
        self._auth_lock = threading.RLock()

    def close(self) -> None:
        self.http.close()

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        from urllib.parse import urlparse

        normalized = super().validate_config(config)
        base_url = str(normalized.get("base_url") or "").strip().rstrip("/")
        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigValidationError("PanSou 地址必须是有效的 HTTP 或 HTTPS URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ConfigValidationError("PanSou 地址不能包含账号密码、查询参数或片段")
        normalized["base_url"] = base_url
        normalized["username"] = str(normalized.get("username") or "").strip()
        normalized["password"] = str(normalized.get("password") or "")
        timeout = normalized.get("timeout", 45)
        if isinstance(timeout, bool):
            raise ConfigValidationError("PanSou 请求超时必须是 5–120 秒之间的数字")
        try:
            timeout = int(timeout)
        except (TypeError, ValueError) as exc:
            raise ConfigValidationError("PanSou 请求超时必须是 5–120 秒之间的数字") from exc
        if not 5 <= timeout <= 120:
            raise ConfigValidationError("PanSou 请求超时必须是 5–120 秒之间的数字")
        normalized["timeout"] = timeout
        return normalized

    def _base_url(self, config: dict[str, Any]) -> str:
        import os

        return str(
            config.get("base_url") or os.environ.get("MOVIESYNC_PANSOU_URL") or ""
        ).strip().rstrip("/")

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        wrapped = data.get("data")
        return wrapped if isinstance(wrapped, dict) else data

    def _login(self, base_url: str, config: dict[str, Any], timeout: int) -> str:
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        if not username or not password:
            raise RuntimeError("PanSou 已启用认证，请在 PanSou 卡片中填写用户名和密码")
        response, data = self.http.request_json(
            "POST",
            f"{base_url}/api/auth/login",
            timeout=timeout,
            retries=0,
            json={"username": username, "password": password},
        )
        if response.status_code != 200:
            raise RuntimeError("PanSou 登录失败，请检查用户名、密码及服务状态")
        payload = self._payload(data)
        token = str(payload.get("token") or "").strip()
        if not token:
            raise RuntimeError("PanSou 登录响应中没有 Token")
        expires_at = payload.get("expires_at")
        try:
            expiry = float(expires_at) if expires_at else 0.0
        except (TypeError, ValueError):
            expiry = 0.0
        import time

        with self._auth_lock:
            self._token = token
            self._token_expires_at = expiry or (time.time() + 3600)
        return token

    def _request(
        self,
        method: str,
        url: str,
        config: dict[str, Any],
        *,
        timeout: int,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        import time

        base_url = self._base_url(config)
        headers: dict[str, str] = {}
        with self._auth_lock:
            token = self._token
            expires_at = self._token_expires_at
        if token and expires_at > time.time() + 30:
            headers["Authorization"] = f"Bearer {token}"

        response, data = self.http.request_json(
            method, url, timeout=timeout, retries=0, params=params, headers=headers
        )
        if response.status_code == 401:
            token = self._login(base_url, config, timeout)
            response, data = self.http.request_json(
                method,
                url,
                timeout=timeout,
                retries=0,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"PanSou 请求失败（HTTP {response.status_code}）")
        return response, self._payload(data)

    @staticmethod
    def _cloud_type_for_resource_type(resource_type: object) -> str:
        value = str(resource_type or "").strip().lower()
        aliases = {
            "quark": "quark",
            "quark_share": "quark",
            "aliyun": "aliyun",
            "aliyun_share": "aliyun",
            "baidu": "baidu",
            "baidu_share": "baidu",
            "uc": "uc", "uc_share": "uc",
            "tianyi": "tianyi", "tianyi_share": "tianyi",
            "mobile": "mobile", "mobile_share": "mobile",
            "115": "115", "115_share": "115",
            "pikpak": "pikpak", "pikpak_share": "pikpak",
            "xunlei": "xunlei", "xunlei_share": "xunlei",
            "123": "123", "123_share": "123",
            "magnet": "magnet", "magnet_link": "magnet",
            "ed2k": "ed2k", "ed2k_link": "ed2k",
        }
        return aliases.get(value, "")

    @staticmethod
    def _share_id(url: object, cloud_type: str) -> str:
        from urllib.parse import urlparse

        value = str(url or "").strip()
        try:
            parsed = urlparse(value)
        except ValueError:
            return ""
        host = (parsed.hostname or "").lower()
        host_matches = {
            "quark": host == "quark.cn" or host.endswith(".quark.cn"),
            "aliyun": (
                host == "alipan.com" or host.endswith(".alipan.com")
                or host == "aliyundrive.com" or host.endswith(".aliyundrive.com")
            ),
            "baidu": host == "baidu.com" or host.endswith(".baidu.com"),
        }
        if not host_matches.get(cloud_type, False):
            return ""
        match = re.match(r"^/s/([A-Za-z0-9_-]{1,128})(?:/|$)", parsed.path)
        return match.group(1) if match else ""

    @staticmethod
    def _quark_share_id(url: object) -> str:
        """兼容旧测试及第三方调用；新代码统一使用按网盘类型解析的 _share_id。"""
        return PanSouResourceSource._share_id(url, "quark")

    @classmethod
    def _normalize_link(
        cls,
        item: dict[str, Any],
        *,
        cloud_type: str,
        title: str = "",
        channel: str = "",
    ) -> dict[str, Any] | None:
        link_url = str(item.get("url") or "").strip()
        share_id = cls._share_id(link_url, cloud_type)
        if not share_id:
            # Display-only links for providers without a compatible storage card.
            from urllib.parse import urlparse
            try:
                parsed = urlparse(link_url)
            except ValueError:
                return None
            if cloud_type in {"magnet", "ed2k"}:
                if parsed.scheme.lower() != cloud_type:
                    return None
                share_id = link_url
            elif cloud_type not in {"quark", "aliyun", "baidu"}:
                if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                    return None
                share_id = link_url
            else:
                return None
        resource_type = cloud_type if cloud_type in {"magnet", "ed2k"} else f"{cloud_type}_share"
        normalized = {
            "resource_id": share_id,
            "url": link_url,
            "password": str(item.get("password") or item.get("passcode") or "").strip(),
            "resource_type": resource_type,
            "title": title[:500],
            "channel": channel[:200],
        }
        # Keep the old alias only for Quark data that predates provider-neutral IDs.
        if cloud_type == "quark":
            normalized["pwd_id"] = share_id
        return normalized

    @staticmethod
    def _snapshot_link_urls(payload: dict[str, Any]) -> set[str]:
        """Return link URLs present in one progressive-search snapshot."""
        urls: set[str] = set()
        raw_results = payload.get("results")
        if isinstance(raw_results, list):
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                links = result.get("links")
                if not isinstance(links, list):
                    continue
                for item in links:
                    if isinstance(item, dict):
                        url = str(item.get("url") or item.get("link") or "").strip()
                        if url:
                            urls.add(url)

        merged = payload.get("merged_by_type")
        if isinstance(merged, dict):
            for items in merged.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        url = str(item.get("url") or item.get("link") or "").strip()
                        if url:
                            urls.add(url)
                    elif isinstance(item, str) and item.strip():
                        urls.add(item.strip())
        return urls

    def search(self, movie: object, config: dict) -> list[dict[str, Any]]:
        title = str(
            movie.get("title", movie.get("name", ""))
            if isinstance(movie, dict)
            else movie
        ).strip()
        base_url = self._base_url(config)
        if not title or not base_url:
            return []
        all_resource_types = bool(
            isinstance(movie, dict) and movie.get("all_resource_types")
        )
        has_selected_target = bool(
            isinstance(movie, dict) and "storage_target_id" in movie
        )
        requested_types = (
            ["quark_share", "aliyun_share", "baidu_share", "uc_share", "tianyi_share",
             "mobile_share", "115_share", "pikpak_share", "xunlei_share", "123_share",
             "magnet_link"]
            if all_resource_types
            else (
                movie.get("resource_types")
                if has_selected_target
                else ["quark_share"]
            )
        )
        if not isinstance(requested_types, (list, tuple, set)):
            requested_types = []

        # Separate the providers queried upstream from the provider types returned
        # to MovieSync. The "More download links" flow searches all providers at once,
        # which often surfaces good Quark shares that a Quark-only PanSou query misses.
        # Reuse that broad discovery strategy for selected storage targets, but still
        # return only links the selected storage card explicitly accepts.
        allowed_cloud_types = sorted({
            cloud_type
            for resource_type in requested_types
            if (cloud_type := self._cloud_type_for_resource_type(resource_type))
        })
        all_provider_cloud_types = sorted({
            "quark", "aliyun", "baidu", "uc", "tianyi", "mobile",
            "115", "pikpak", "xunlei", "123", "magnet",
        })
        query_cloud_types = (
            allowed_cloud_types
            if all_resource_types or not has_selected_target
            else all_provider_cloud_types
        )
        # An explicit target without a matching PanSou cloud type must not silently
        # fall back to Quark or search every provider without filtering the results.
        if not allowed_cloud_types or not query_cloud_types:
            return []
        try:
            timeout = int(config.get("timeout", 45))
        except (TypeError, ValueError):
            timeout = 45
        search_params = {
            "kw": title,
            "res": "all",
            "src": "plugin",
            "cloud_types": ",".join(query_cloud_types),
            "wait_ms": 6000,
        }
        try:
            # The progressive endpoint may return cached/partial plugin results while
            # the background search continues. Poll briefly and combine snapshots so
            # a user does not need to close and reopen the dialog to see later results.
            _, payload = self._request(
                "GET",
                f"{base_url}/api/search/first",
                config,
                timeout=timeout,
                params=search_params,
            )
        except RuntimeError as exc:
            # Preserve compatibility with external PanSou instances that do not yet
            # provide the progressive endpoint.
            if "HTTP 404" not in str(exc):
                raise
            search_params.pop("wait_ms", None)
            _, payload = self._request(
                "GET",
                f"{base_url}/api/search",
                config,
                timeout=timeout,
                params=search_params,
            )
        else:
            import time

            snapshots = [payload]
            seen_urls = self._snapshot_link_urls(payload)
            stale_polls = 0
            poll_params = {**search_params, "wait_ms": 1200}
            for _ in range(3):
                time.sleep(1)
                try:
                    _, next_payload = self._request(
                        "GET",
                        f"{base_url}/api/search/first",
                        config,
                        timeout=timeout,
                        params=poll_params,
                    )
                except RuntimeError:
                    break
                snapshots.append(next_payload)

                next_urls = self._snapshot_link_urls(next_payload)
                new_urls = next_urls - seen_urls
                if new_urls:
                    seen_urls.update(new_urls)
                    stale_polls = 0
                else:
                    stale_polls += 1
                    # A single unchanged snapshot can be transient while PanSou
                    # plugins are still running. Stop only after two stale polls.
                    if stale_polls >= 2:
                        break

            # Merge progressive snapshots before normalizing links. The same link
            # may appear repeatedly; the existing deduplication below handles that.
            payload = dict(snapshots[0])
            payload["results"] = [
                result
                for snapshot in snapshots
                for result in (snapshot.get("results") or [])
                if isinstance(result, dict)
            ]
            merged_by_type: dict[str, list[Any]] = {}
            for snapshot in snapshots:
                merged = snapshot.get("merged_by_type")
                if not isinstance(merged, dict):
                    continue
                for raw_type, items in merged.items():
                    if isinstance(items, list):
                        merged_by_type.setdefault(str(raw_type), []).extend(items)
            if merged_by_type:
                payload["merged_by_type"] = merged_by_type
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        raw_results = payload.get("results")
        if isinstance(raw_results, list):
            for result in raw_results:
                if not isinstance(result, dict):
                    continue
                links = result.get("links")
                if not isinstance(links, list):
                    continue
                # A missing provider title is unknown, not the searched query. Reusing the query
                # here makes every link in a title-less result look like an exact match.
                result_title = str(result.get("title") or result.get("content") or "").strip()[:500]
                channel = str(result.get("channel") or result.get("source") or "")
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    link_type = str(link.get("type") or "").strip().lower()
                    aliases = {
                        "quark": "quark", "夸克": "quark",
                        "aliyun": "aliyun", "alipan": "aliyun", "阿里云盘": "aliyun",
                        "baidu": "baidu", "百度": "baidu", "百度网盘": "baidu",
                        "uc": "uc", "uc网盘": "uc",
                        "tianyi": "tianyi", "天翼": "tianyi", "天翼云盘": "tianyi",
                        "mobile": "mobile", "移动": "mobile", "移动云盘": "mobile",
                        "115": "115", "115网盘": "115",
                        "pikpak": "pikpak", "pikpak网盘": "pikpak",
                        "xunlei": "xunlei", "迅雷": "xunlei", "迅雷云盘": "xunlei",
                        "123": "123", "123云盘": "123",
                        "magnet": "magnet", "magnet_link": "magnet", "磁力": "magnet", "磁力链接": "magnet",
                        "ed2k": "ed2k", "电驴": "ed2k", "ed2k链接": "ed2k",
                    }
                    cloud_type = aliases.get(link_type)
                    if not cloud_type:
                        cloud_type = next(
                            (
                                candidate_type
                                for candidate_type in query_cloud_types
                                if self._share_id(link.get("url"), candidate_type)
                            ),
                            "",
                        )
                    if cloud_type not in allowed_cloud_types:
                        continue
                    normalized = self._normalize_link(
                        link, cloud_type=cloud_type, title=result_title, channel=channel
                    )
                    key = (normalized["resource_type"], normalized["resource_id"]) if normalized else None
                    if normalized and key not in seen:
                        seen.add(key)
                        results.append(normalized)

        # 兼容只返回按网盘类型聚合结果的 PanSou 部署版本。
        merged = payload.get("merged_by_type")
        if isinstance(merged, dict):
            aliases = {
                "quark": "quark", "夸克": "quark",
                "aliyun": "aliyun", "alipan": "aliyun", "阿里云盘": "aliyun",
                "baidu": "baidu", "百度": "baidu", "百度网盘": "baidu",
                "uc": "uc", "uc网盘": "uc",
                "tianyi": "tianyi", "天翼": "tianyi", "天翼云盘": "tianyi",
                "mobile": "mobile", "移动": "mobile", "移动云盘": "mobile",
                "115": "115", "115网盘": "115",
                "pikpak": "pikpak", "pikpak网盘": "pikpak",
                "xunlei": "xunlei", "迅雷": "xunlei", "迅雷云盘": "xunlei",
                "123": "123", "123云盘": "123",
                "magnet": "magnet", "magnet_link": "magnet", "磁力": "magnet", "磁力链接": "magnet",
                "ed2k": "ed2k", "电驴": "ed2k", "ed2k链接": "ed2k",
            }
            for raw_cloud_type, items in merged.items():
                cloud_type = aliases.get(str(raw_cloud_type).strip().lower())
                if cloud_type not in allowed_cloud_types or not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    normalized = self._normalize_link(
                        item,
                        cloud_type=cloud_type,
                        # Do not substitute the search query for missing source metadata.
                        title=str(item.get("note") or item.get("title") or "").strip()[:500],
                        channel=str(item.get("source") or ""),
                    )
                    key = (normalized["resource_type"], normalized["resource_id"]) if normalized else None
                    if normalized and key not in seen:
                        seen.add(key)
                        results.append(normalized)
        # If the primary title yields no links, retry a small number of metadata
        # aliases before giving up. This is a fallback only (not extra requests on
        # every successful search), so the common path keeps its existing latency.
        if not results and isinstance(movie, dict) and not movie.get("_metadata_alias_fallback"):
            def values(value: object) -> list[str]:
                if isinstance(value, str):
                    return [part.strip() for part in re.split(r"[,;；、|]", value) if part.strip()]
                if isinstance(value, (list, tuple, set)):
                    return [str(part).strip() for part in value if str(part).strip()]
                return []

            normalize = lambda value: re.sub(r"[^\w\u4e00-\u9fff]", "", str(value or "")).casefold()
            primary = normalize(title)
            alternate_terms: list[str] = []
            for field in ("original_title", "aliases", "alternative_titles", "aka"):
                for alternate in values(movie.get(field)):
                    if len(normalize(alternate)) >= 2 and normalize(alternate) != primary:
                        if alternate not in alternate_terms:
                            alternate_terms.append(alternate)
                    if len(alternate_terms) >= 2:
                        break
                if len(alternate_terms) >= 2:
                    break
            if not alternate_terms:
                year = str(movie.get("year") or movie.get("release_year") or "").strip()
                if re.fullmatch(r"(?:19|20)\d{2}", year):
                    alternate_terms.append(f"{title} {year}")

            for alternate in alternate_terms[:2]:
                fallback_movie = {**movie, "title": alternate, "_metadata_alias_fallback": True}
                fallback_results = self.search(fallback_movie, config)
                if fallback_results:
                    return fallback_results
        return results

    def check(self, config: dict) -> dict[str, Any]:
        base_url = self._base_url(config)
        if not base_url:
            return {
                "status": "unconfigured",
                "message": "尚未配置 PanSou 地址",
                "total": 0,
                "valid_count": 0,
            }
        try:
            timeout = int(config.get("timeout", 45))
            response, payload = self._request(
                "GET",
                f"{base_url}/api/health",
                config,
                timeout=min(timeout, 15),
            )
            if response.status_code != 200:
                raise RuntimeError("PanSou 健康检查失败")
            enabled_plugins = payload.get("plugins_enabled")
            return {
                "status": "healthy",
                "message": (
                    f"PanSou 连接正常，已启用 {payload.get('plugin_count', 0)} 个搜索插件"
                    if enabled_plugins is not False
                    else "PanSou 可连接，但当前没有启用搜索插件"
                ),
                "total": int(payload.get("plugin_count") or 0),
                "valid_count": int(payload.get("plugin_count") or 0),
            }
        except Exception:
            return {
                "status": "unavailable",
                "message": "PanSou 连接失败，请检查地址、认证配置和服务日志",
                "total": 0,
                "valid_count": 0,
            }


def create_card(context):
    """Factory entry point required by MovieSync's single-file card loader."""
    return PanSouResourceSource()
