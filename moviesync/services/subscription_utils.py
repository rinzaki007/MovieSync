"""Pure normalization and episode-parsing helpers for automatic subscriptions."""
from __future__ import annotations

from .filename_rules import parse_tv_episode


def _normalize_resource_id(value: object) -> str:
    value = str(value or "").strip()
    if len(value) > 128 or (value and not all(char.isalnum() or char in "_-" for char in value)):
        raise ValueError("分享资源 ID 无效")
    return value

def _file_relative_folder(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    value = item.get("relative_path") or item.get("relative_dir") or item.get("folder_path") or ""
    return str(value).replace("\\", "/").strip("/")

def _parse_tv_episode(file_name: str) -> tuple[int | None, int | None]:
    """兼容旧调用；新订阅优先使用对应资源卡片的解析规则。"""
    return parse_tv_episode(file_name)


def _parse_source_episode(resource_sources, source_id: str, file_name: str) -> tuple[int | None, int | None]:
    """Use canonical season/episode parsing before card-specific fallbacks.

    A card parser may return a valid episode number while losing the season
    (for example, (None, 6) for S03E06). Treating that as valid would collapse
    every season into one episode sequence and make the follow-up baseline wrong.
    """
    generic = _parse_tv_episode(file_name)
    if generic[0] is not None and generic[1] is not None:
        return generic

    parser = getattr(resource_sources, "parse_tv_episode", None)
    parsed = None
    if source_id and callable(parser):
        try:
            parsed = parser(source_id, file_name)
        except Exception:
            parsed = None

    if isinstance(parsed, (tuple, list)) and len(parsed) == 2:
        season, episode = parsed
        valid = all(
            value is None
            or (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 1
            )
            for value in (season, episode)
        )
        if valid and episode is not None:
            # Preserve a season supplied by a card only when it agrees with
            # the episode number recognized by the generic parser.
            if generic[1] is not None:
                if episode == generic[1] and season is not None:
                    return season, episode
                return generic
            return season, episode

    return generic


def _clean_tv_filename(file_name: str, title: str = "") -> tuple[int | None, str]:
    _, episode = _parse_tv_episode(file_name)
    return episode, file_name

def _normalize_fid(value: object) -> str:
    fid = str(
        value or ""
    ).strip()

    if not fid:
        return "0"

    if len(fid) > 128:
        raise ValueError(
            "目标存储目录 FID 不能超过 128 个字符"
        )

    if not all(
        char.isalnum() or char in "_-"
        for char in fid
    ):
        raise ValueError(
            "目标存储目录 FID 格式无效"
        )

    return fid
