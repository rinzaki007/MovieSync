"""Standalone TMDB metadata-provider card."""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any
from urllib.parse import quote

from moviesync.cards import CardManifest, MetadataProviderCard
from moviesync.clients.http import ApiError, HttpClient
from moviesync.services.media_classification import classify_metadata_item

API_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"
CACHE_TTL_SECONDS = 6 * 60 * 60
MAX_CACHE_ENTRIES = 256


class TMDBMetadataCard(MetadataProviderCard):
    manifest = CardManifest(
        id="tmdb",
        name="TMDB",
        version="1.0.0",
        type="metadata_provider",
        description="The Movie Database 影视元数据与搜索",
        capabilities=(
            "metadata.list",
            "metadata.search",
            "metadata.detail",
            "metadata.health_check",
        ),
        config_fields=(
            {
                "key": "api_read_access_token",
                "label": "API Read Access Token（优先）",
                "type": "password",
                "required": False,
                "max_length": 4096,
            },
            {
                "key": "api_key",
                "label": "API Key（v3 密钥，可选）",
                "type": "password",
                "required": False,
                "max_length": 256,
            },
            {
                "key": "language",
                "label": "显示语言",
                "type": "string",
                "required": False,
                "max_length": 16,
                "default": "zh-CN",
            },
        ),
        image_hosts=("image.tmdb.org",),
        image_referer="https://www.themoviedb.org/",
    )

    def __init__(self, config_store, http_client: HttpClient | None = None):
        self.config_store = config_store
        self.http = http_client or HttpClient()
        self._cache: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], tuple[float, dict[str, Any]]] = {}
        self._cache_lock = threading.RLock()
        self._last_error = ""

    def close(self) -> None:
        self.http.close()

    def _config(self) -> dict[str, Any]:
        try:
            value = self.config_store.get_card_config("tmdb")
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _language(config: dict[str, Any]) -> str:
        language = str(config.get("language") or "zh-CN").strip()
        return language[:16] or "zh-CN"

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        api_key: str | None = None,
        language: str = "zh-CN",
    ) -> dict[str, Any] | None:
        config = self._config()
        token = str(token if token is not None else config.get("api_read_access_token") or "").strip()
        api_key = str(api_key if api_key is not None else config.get("api_key") or "").strip()
        if not token and not api_key:
            self._last_error = "请填写 API Read Access Token 或 API Key"
            return None

        query = {str(key): str(value) for key, value in (params or {}).items() if value is not None}
        query.setdefault("language", language or self._language(config))
        credential = token or api_key
        credential_type = "bearer" if token else "api_key"
        if not token:
            query["api_key"] = api_key
        cache_params = {key: value for key, value in query.items() if key != "api_key"}
        cache_key = (
            path,
            query.get("language", "zh-CN"),
            hashlib.sha256(f"{credential_type}:{credential}".encode()).hexdigest(),
            tuple(sorted(cache_params.items())),
        )
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                self._last_error = ""
                return dict(cached[1])
            if cached:
                self._cache.pop(cache_key, None)

        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response, payload = self.http.request_json(
                "GET",
                f"{API_BASE}{path}",
                timeout=8,
                retries=1,
                params=query,
                headers=headers,
            )
        except ApiError:
            self._last_error = "无法连接 TMDB API，请检查容器网络、DNS 或 HTTPS 连接"
            return None

        if response.status_code != 200 or not isinstance(payload, dict):
            if response.status_code in {401, 403}:
                self._last_error = f"TMDB 拒绝认证（HTTP {response.status_code}），请核对凭据类型与内容"
            elif response.status_code == 429:
                self._last_error = "TMDB API 请求频率受限（HTTP 429），请稍后重试"
            elif response.status_code >= 500:
                self._last_error = f"TMDB 服务暂时异常（HTTP {response.status_code}）"
            else:
                self._last_error = f"TMDB API 返回异常状态（HTTP {response.status_code}）"
            return None

        self._last_error = ""
        with self._cache_lock:
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)
            self._cache[cache_key] = (now + CACHE_TTL_SECONDS, dict(payload))
        return payload

    @staticmethod
    def _media_type(item: dict[str, Any]) -> str:
        value = str(item.get("media_type") or "").lower()
        if value in {"movie", "tv"}:
            return value
        return "tv" if item.get("name") or item.get("first_air_date") else "movie"

    @staticmethod
    def _normalize(item: dict[str, Any], language: str = "zh-CN") -> dict[str, Any] | None:
        raw_id = item.get("id")
        if raw_id is None:
            return None
        media_type = TMDBMetadataCard._media_type(item)
        item_id = str(raw_id)
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            return None
        poster_path = str(item.get("poster_path") or "").strip()
        backdrop_path = str(item.get("backdrop_path") or "").strip()
        release_date = str(item.get("release_date") or item.get("first_air_date") or "")
        vote = item.get("vote_average")
        try:
            rating = f"{float(vote):.1f}" if vote is not None and float(vote) > 0 else "暂无"
        except (TypeError, ValueError):
            rating = "暂无"
        genres = item.get("genres") or []
        if not isinstance(genres, list):
            genres = []
        normalized = {
            "id": f"{media_type}:{item_id}",
            "tmdb_id": item_id,
            "media_type": media_type,
            "title": title,
            "original_title": str(item.get("original_title") or item.get("original_name") or ""),
            "cover": f"{IMAGE_BASE}/w500{poster_path}" if poster_path else "",
            "backdrop": f"{IMAGE_BASE}/w1280{backdrop_path}" if backdrop_path else "",
            "rate": rating,
            "vote_count": item.get("vote_count", 0),
            "year": release_date[:4],
            "tag": "电影" if media_type == "movie" else "电视剧",
            "genres": [str(value) for value in genres if value],
            "genre_ids": [
                value for value in (item.get("genre_ids") or [])
                if isinstance(value, int) and not isinstance(value, bool)
            ] if isinstance(item.get("genre_ids"), list) else [],
            "summary": str(item.get("overview") or "").strip(),
            "overview": str(item.get("overview") or "").strip(),
            "url": f"https://www.themoviedb.org/{media_type}/{item_id}",
            "provider_id": "tmdb",
            "provider_name": "TMDB",
            "language": language,
        }
        return classify_metadata_item(normalized)

    def is_configured(self, config: dict[str, Any]) -> bool:
        config = config if isinstance(config, dict) else {}
        return bool(
            str(config.get("api_read_access_token") or "").strip()
            or str(config.get("api_key") or "").strip()
        )

    def validate_enabled_config(self, config: dict[str, Any]) -> None:
        if not self.is_configured(config):
            raise ValueError("启用 TMDB 前，请填写 API Read Access Token 或 API Key")

    def check(self, config: dict) -> dict[str, Any]:
        config = config if isinstance(config, dict) else {}
        token = str(config.get("api_read_access_token") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        if not token and not api_key:
            return {
                "status": "unconfigured",
                "message": "请填写 API Read Access Token 或 API Key",
            }
        payload = self._request(
            "/configuration",
            token=token,
            api_key=api_key,
            language=self._language(config),
        )
        if payload is None:
            return {
                "status": "unhealthy",
                "message": self._last_error or "TMDB API 请求失败",
            }
        return {"status": "healthy", "message": "TMDB API 连接正常"}

    def list_movies(self, tag: str, sort_type: str) -> list[dict[str, Any]]:
        config = self._config()
        language = self._language(config)
        media_type = "tv" if tag in {"电视剧", "综艺", "动漫"} else "movie"
        sort_map = {
            "U": "popularity.desc",
            "T": "primary_release_date.desc" if media_type == "movie" else "first_air_date.desc",
            "R": "vote_average.desc",
        }
        params: dict[str, Any] = {
            "sort_by": sort_map.get(sort_type, "popularity.desc"),
            "include_adult": "false",
            "page": 1,
        }
        if sort_type == "R":
            params["vote_count.gte"] = 100
        if tag == "动漫":
            params["with_genres"] = "16"
        elif tag == "综艺":
            params["with_genres"] = "10764|10767"
        payload = self._request(f"/discover/{media_type}", params, language=language)
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return [
            normalized
            for item in results[:40]
            if isinstance(item, dict)
            and (normalized := self._normalize({**item, "media_type": media_type}, language))
        ]

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search across movies and TV when no category was explicitly selected."""
        query = str(query or "").strip()[:80]
        if not query:
            return []
        config = self._config()
        language = self._language(config)
        payload = self._request(
            "/search/multi",
            {"query": query, "include_adult": "false", "page": 1},
            language=language,
        )
        results = payload.get("results", []) if isinstance(payload, dict) else []
        normalized = []
        for item in results:
            if not isinstance(item, dict) or item.get("media_type") not in {"movie", "tv"}:
                continue
            result = self._normalize(item, language)
            if result:
                normalized.append(result)
        return normalized[:40]

    def search_by_category(self, query: str, category: str) -> list[dict[str, Any]]:
        """Search only the selected media format to avoid movie/TV title collisions."""
        query = str(query or "").strip()[:80]
        category = str(category or "").strip()
        if not query:
            return []
        if category not in {"电影", "电视剧", "综艺", "动漫", "纪录片", "短剧"}:
            return self.search(query)

        media_type = "movie" if category == "电影" else "tv"
        config = self._config()
        language = self._language(config)
        payload = self._request(
            f"/search/{media_type}",
            {"query": query, "include_adult": "false", "page": 1},
            language=language,
        )
        results = payload.get("results", []) if isinstance(payload, dict) else []
        normalized = []
        special_tv_genres = {16, 99, 10763, 10764, 10767}
        wanted_genres = {
            "综艺": {10764, 10767},
            "动漫": {16},
            "纪录片": {99, 10763},
        }
        for item in results:
            if not isinstance(item, dict):
                continue
            raw_genres = item.get("genre_ids")
            genre_ids = {
                value for value in raw_genres
                if isinstance(value, int) and not isinstance(value, bool)
            } if isinstance(raw_genres, list) else set()
            if category in wanted_genres and not genre_ids.intersection(wanted_genres[category]):
                continue
            if category == "电视剧" and genre_ids.intersection(special_tv_genres):
                continue
            result = self._normalize({**item, "media_type": media_type}, language)
            if result:
                normalized.append(result)
        return normalized[:40]

    def get_detail(self, item_id: str) -> dict[str, Any] | None:
        value = str(item_id or "").strip()
        media_type, separator, raw_id = value.partition(":")
        if not separator:
            media_type, raw_id = "movie", value
        if media_type not in {"movie", "tv"} or not raw_id.isdigit():
            return None
        config = self._config()
        language = self._language(config)
        payload = self._request(
            f"/{media_type}/{quote(raw_id)}",
            {"append_to_response": "external_ids"},
            language=language,
        )
        if not isinstance(payload, dict):
            return None
        normalized = self._normalize({**payload, "media_type": media_type}, language)
        if normalized:
            external_ids = payload.get("external_ids")
            normalized["external_ids"] = external_ids if isinstance(external_ids, dict) else {}
        return normalized


def create_card(context):
    """Create the standalone TMDB card using the generic config store."""
    return TMDBMetadataCard(context["config_store"])
