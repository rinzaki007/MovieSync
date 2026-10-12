"""Shared media-category normalization for metadata search results.

The display category (movie/series/variety/anime/documentary) is distinct from
the underlying TMDB media type (movie/tv). An animated movie is still a movie
for resource handling; an animated TV show is still episodic.
"""
from __future__ import annotations

from typing import Any

MEDIA_CATEGORIES = frozenset({"电影", "电视剧", "综艺", "动漫", "纪录片", "短剧"})
SERIES_CATEGORIES = frozenset({"电视剧", "综艺", "动漫", "纪录片", "短剧"})

_TMDB_GENRE_CATEGORY = {
    16: "动漫",
    99: "纪录片",
    10763: "纪录片",
    10764: "综艺",
    10767: "综艺",
}
_GENRE_NAME_CATEGORY = {
    "animation": "动漫",
    "动画": "动漫",
    "reality": "综艺",
    "talk": "综艺",
    "真人秀": "综艺",
    "脱口秀": "综艺",
    "documentary": "纪录片",
    "纪录片": "纪录片",
}


def normalize_media_category(value: object) -> str | None:
    """Return a known user-facing category, never a raw genre string."""
    text = str(value or "").strip()
    return text if text in MEDIA_CATEGORIES else None


def classify_metadata_item(
    item: dict[str, Any],
    fallback_category: str | None = None,
) -> dict[str, Any]:
    """Annotate a metadata item with a stable category and movie/tv format.

    Explicit provider media types win over the current UI tab. The fallback is
    only used for providers (such as legacy title suggestions) that do not
    expose type information.
    """
    result = dict(item)
    media_type = str(result.get("media_type") or result.get("type") or "").strip().lower()
    if media_type not in {"movie", "tv"}:
        if result.get("first_air_date") or result.get("original_name") or result.get("name"):
            media_type = "tv"
        elif result.get("release_date") or result.get("original_title"):
            media_type = "movie"
        else:
            media_type = ""

    category = normalize_media_category(result.get("category")) or normalize_media_category(result.get("tag"))
    genres = result.get("genres")
    genre_names = [str(value).strip().lower() for value in genres] if isinstance(genres, list) else []
    genre_ids = result.get("genre_ids")
    if not isinstance(genre_ids, list):
        genre_ids = []
    genre_category = next(
        (_TMDB_GENRE_CATEGORY.get(value) for value in genre_ids if isinstance(value, int) and value in _TMDB_GENRE_CATEGORY),
        None,
    ) or next((_GENRE_NAME_CATEGORY.get(value) for value in genre_names if value in _GENRE_NAME_CATEGORY), None)

    # Genre-derived categories apply to TV titles. Films remain films even when
    # animated; their movie/tv format is what controls resource handling.
    if media_type == "movie":
        category = category or "电影"
    elif media_type == "tv":
        category = genre_category or category or "电视剧"
    else:
        category = category or normalize_media_category(fallback_category) or "未知"

    if media_type:
        result["media_type"] = media_type
    result["tag"] = category
    return result
