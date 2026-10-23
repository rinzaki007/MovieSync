"""影视资源检索与转存业务服务。

用途：并发搜索已启用资源源、解析存储目标资源、去重候选资源，并执行用户选择的文件白名单转存。
维护说明：具体平台解析与转存由 StorageTargetCard 负责；核心业务不直接依赖具体网盘平台。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .filename_rules import parse_media_filename, parse_tv_episode
from .resource_sources import ResourceSourceManager
from .storage_targets import StorageTargetManager
from .transfer_outcome import is_uncertain_transfer_message

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts", ".m2ts", ".iso")
MAX_VIDEO_FILES_PER_CANDIDATE = 200
MAX_CANDIDATES_PER_MOVIE = 20


def _format_size(value: object) -> str:
    try:
        size = int(float(value or 0))
    except (TypeError, ValueError):
        return "未知大小"
    if size <= 0:
        return "未知大小"
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    number = float(size)
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def _detect_resolution(name: str, fallback: str = "") -> str:
    """Detect quality from the filename first, then fall back to the source title."""
    patterns = [
        (r"(?<![a-z0-9])(2160p|4k|uhd)(?![a-z0-9])", "4K"),
        (r"(?<![a-z0-9])(1440p|2k)(?![a-z0-9])", "2K"),
        (r"(?<![a-z0-9])1080[pi]?(?![a-z0-9])", "1080P"),
        (r"(?<![a-z0-9])720[pi]?(?![a-z0-9])", "720P"),
        (r"(?<![a-z0-9])480[pi]?(?![a-z0-9])", "480P"),
    ]
    for value in (name, fallback):
        text = str(value or "").lower()
        for pattern, label in patterns:
            if re.search(pattern, text):
                return label
    return "未知"


def _declared_episode_count(source_title: str) -> int | None:
    """Extract an explicitly advertised total episode count, not a current update number."""
    text = str(source_title or "")
    patterns = (
        r"(?:全|共)\s*(\d{1,3})\s*[集话期]",
        r"(\d{1,3})\s*[集话期]\s*全",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            count = int(match.group(1))
            if 1 <= count <= 999:
                return count
    return None


def _candidate_match_explanation(
    movie: dict,
    source_title: str,
    recognized_episode_files: int,
    total_video_count: int,
    declared_episode_count: int | None,
    scan_complete: bool,
) -> str:
    """Explain the observable evidence behind a candidate without pretending it is a score."""
    target_title = str(movie.get("title") or "").strip()
    original_title = str(movie.get("original_title") or "").strip()
    source = str(source_title or "").strip()
    def normalize(value: object) -> str:
        return re.sub(r"[\W_]+", "", str(value or "").casefold())

    target_norm, source_norm = normalize(target_title), normalize(source)
    original_norm = normalize(original_title)
    if target_norm and target_norm in source_norm:
        title_reason = "分享标题包含目标名称"
    elif original_norm and original_norm in source_norm:
        title_reason = "分享标题匹配影视原名"
    else:
        aliases = movie.get("aliases", [])
        alias_norms = [normalize(alias) for alias in aliases] if isinstance(aliases, list) else []
        title_reason = (
            "分享标题匹配已知别名"
            if any(alias and alias in source_norm for alias in alias_norms)
            else "标题未直接匹配，建议人工核对"
        )

    reasons = [title_reason]
    media_type = str(movie.get("media_type") or "").strip().lower()
    if media_type in {"tv", "tv_show", "series", "anime", "variety", "documentary", "short_drama"}:
        recognized = max(0, int(recognized_episode_files or 0))
        total = max(0, int(total_video_count or 0))
        reasons.append(f"已识别 {recognized}/{total} 个视频文件的集数")
        if declared_episode_count:
            reasons.append(f"分享标题标注共 {int(declared_episode_count)} 集（不代表实际文件齐全）")
    elif media_type == "movie":
        reasons.append("按电影候选检查")
    if not scan_complete:
        reasons.append("分享扫描不完整，文件数量与集数统计可能不完整")
    return "；".join(reasons)


def _episode_statistics(
    files: list[dict[str, Any]],
    source_id: str,
    resource_sources,
) -> tuple[str, int, int, int, int]:
    """Summarize parsed episodes and file quality without inferring gaps or deleting versions."""
    by_season: dict[int, set[int]] = {}
    unseasoned: set[int] = set()
    episode_file_count = 0
    unrecognized_file_count = 0
    episode_occurrences: dict[tuple[int | None, int], int] = {}
    parser = getattr(resource_sources, "parse_tv_episode", None)

    for item in files:
        name = str(item.get("file_name") or "")
        try:
            season, episode = parser(source_id, name) if callable(parser) else parse_tv_episode(name)
        except Exception:
            season, episode = parse_tv_episode(name)
        if episode is None:
            unrecognized_file_count += 1
            continue

        episode_file_count += 1
        season_key = int(season) if season is not None else None
        episode_number = int(episode)
        episode_occurrences[(season_key, episode_number)] = (
            episode_occurrences.get((season_key, episode_number), 0) + 1
        )
        if season_key is None:
            unseasoned.add(episode_number)
        else:
            by_season.setdefault(season_key, set()).add(episode_number)

    def format_numbers(numbers: set[int]) -> str:
        ordered = sorted(numbers)
        if not ordered:
            return ""
        parts: list[str] = []
        start = previous = ordered[0]
        for number in ordered[1:]:
            if number == previous + 1:
                previous = number
                continue
            parts.append(f"E{start:02d}" if start == previous else f"E{start:02d}-E{previous:02d}")
            start = previous = number
        parts.append(f"E{start:02d}" if start == previous else f"E{start:02d}-E{previous:02d}")
        return "、".join(parts)

    unique_count = len(unseasoned) + sum(len(numbers) for numbers in by_season.values())
    additional_versions = sum(max(0, count - 1) for count in episode_occurrences.values())
    if not unique_count:
        return "集数暂未识别", 0, episode_file_count, unrecognized_file_count, additional_versions

    summary = [
        f"S{season:02d} {format_numbers(numbers)}"
        for season, numbers in sorted(by_season.items())
    ]
    if unseasoned:
        summary.append(f"未标季 {format_numbers(unseasoned)}")
    if len(summary) > 3:
        extra_seasons = max(0, len(summary) - 3)
        summary = summary[:3] + [f"另有 {extra_seasons} 组"]
    return (
        " · ".join(summary) + f"（共 {unique_count} 集）",
        unique_count,
        episode_file_count,
        unrecognized_file_count,
        additional_versions,
    )


class SearchService:
    def __init__(
        self,
        resource_sources: ResourceSourceManager,
        storage_targets: StorageTargetManager,
        logger,
    ):
        self.resource_sources = resource_sources
        self.storage_targets = storage_targets
        self.logger = logger

    @staticmethod
    def _title(movie: object) -> tuple[str, str]:
        title = str(movie.get("title", movie.get("name", "")) if isinstance(movie, dict) else movie).strip()
        cleaned = re.sub(r"[\(（]\s*(19\d{2}|20\d{2})\s*[\)）]", "", title).strip()
        return title, cleaned

    @staticmethod
    def _simplify(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", value or "")

    def _get_resource_files(self, resource: dict, *, refresh: bool = False):
        """解析分享文件。每次搜索均读取最新文件列表，不复用旧缓存。"""
        storage_target_id = str(
            resource.get("storage_target_id") or resource.get("target_id") or ""
        ).strip()
        if not storage_target_id:
            selector = getattr(self.storage_targets, "select_target_id", None)
            if callable(selector):
                storage_target_id = str(selector(resource) or "").strip()
            else:
                getter = getattr(self.storage_targets, "get", None)
                target = getter() if callable(getter) else None
                storage_target_id = target.card_id if target else ""
        if resource.get("resource_type") and not storage_target_id:
            return (
                [],
                None,
                "没有已启用的存储卡片支持此资源类型，请检查存储卡片配置",
                "",
            )
        resource_id = str(
            resource.get("resource_id") or resource.get("share_id") or resource.get("pwd_id") or ""
        ).strip()
        if not resource_id:
            return [], None, "资源标识无效", storage_target_id
        # 搜索阶段不复用短时分享文件缓存：每次都读取最新目录，
        # 避免旧文件清单影响候选相关性与集数判断。转存仍会独立刷新 token。
        resolved = self.storage_targets.resolve_resource(resource, storage_target_id or None)
        files = resolved.get("files") or []
        token = resolved.get("token")
        error = resolved.get("error")
        target_id = resolved.get("target_id") or storage_target_id

        return files, token, error, target_id

    @staticmethod
    def _pansou_title_for_share(source_title: str, share_url: str) -> str:
        """Extract the title segment associated with one link in a PanSou aggregate.

        PanSou may return one display title containing several title/URL pairs.
        Treating that entire aggregate as one title either leaks unrelated shares
        into the result set or rejects every link. When the current share URL is
        present, use the text immediately preceding that URL instead.
        """
        title = str(source_title or "").strip()
        url = str(share_url or "").strip()
        if not title or not url:
            return ""
        matches = list(re.finditer(r"https?://[^\s]+", title, re.IGNORECASE))
        for index, match in enumerate(matches):
            candidate_url = match.group(0).rstrip(".,;，；。)")
            if candidate_url != url.rstrip(".,;，；。)"):
                continue
            start = matches[index - 1].end() if index > 0 else 0
            segment = title[start:match.start()].strip(" \t|｜;；,，")
            return segment[:500]
        return ""

    @classmethod
    def _has_conflicting_media_title(
        cls,
        movie: object,
        source_title: str,
        videos: list[dict[str, Any]],
        *,
        display_title: str | None = None,
    ) -> bool:
        """过滤明显错误的影视候选，同时尽量保留别名、简称和标题模糊的合集。

        当来源标题明确指向另一部中文作品时，即使分享内文件只按集数命名也会过滤；
        来源标题不明确时，仍要求分享元数据与视频文件相互佐证，降低误杀风险。
        """
        if not isinstance(movie, dict):
            return False

        media_type = str(movie.get("media_type") or "").strip().lower()
        tag = str(movie.get("tag") or "").strip().lower()
        supported_types = {
            "movie", "film", "tv", "tv_show", "series", "anime", "variety",
            "电影", "电视剧", "剧集", "综艺", "动画", "动漫",
        }
        category_markers = ("电影", "电视剧", "剧集", "综艺", "动漫", "动画")
        known_media = media_type in supported_types or any(marker in tag for marker in category_markers)
        if not known_media or not source_title or not videos:
            return False

        target_titles = [
            str(movie.get(key) or "").strip()
            for key in ("title", "name", "original_title")
            if str(movie.get(key) or "").strip()
        ]
        has_known_alternate_title = bool(
            movie.get("original_title")
            or movie.get("aliases")
            or movie.get("alternative_titles")
            or movie.get("aka")
        )
        target_is_chinese = any(re.search(r"[\u4e00-\u9fff]", value) for value in target_titles)
        for alias_key in ("aliases", "alternative_titles", "aka"):
            aliases = movie.get(alias_key)
            if isinstance(aliases, str):
                target_titles.extend(part.strip() for part in re.split(r"[,;；、|]", aliases) if part.strip())
            elif isinstance(aliases, (list, tuple, set)):
                target_titles.extend(str(part).strip() for part in aliases if str(part).strip())
        target_titles = [cls._simplify(value).replace("_", "").lower() for value in target_titles]
        target_titles = [value for value in target_titles if len(value) >= 2]
        if not target_titles:
            return False

        source_name = cls._simplify(source_title).replace("_", "").lower()
        file_names = [
            cls._simplify(str(video.get("file_name") or "")).replace("_", "").lower()
            for video in videos
        ]
        # 明确的其他作品名优先于来源描述中偶然出现的目标名称，避免混合帖/频道文案误放行。
        # 有些网盘分享的实际文件只叫“第 287 集.mp4”，无法从文件名交叉验证作品。
        # 若用户看到的来源标题本身明确写了另一部中文作品，也应过滤；纯“更新至第几集”
        # 或“古装仙侠合集”一类缺少具体作品名的模糊标题仍然保留。
        if display_title:
            title_noise = (
                "晚间更新", "晚上更新", "晚上更", "每日更新", "持续更新", "更新至", "更新中",
                "更新", "连载", "热播", "全网", "最新", "热门", "精品", "精选", "高分",
                "必看", "推荐", "影视资源", "影视", "资源", "分享", "合集", "全集", "全套",
                "全季", "全系列", "完整版", "未删减", "高清", "超清", "蓝光", "国语", "中字",
                "字幕", "电视剧", "连续剧", "剧集", "动漫", "动画", "国漫", "综艺", "纪录片",
                "电影", "年番", "番剧", "新番", "片源", "网盘", "转存", "修复", "收藏",
                "解说", "混剪", "预告", "片花", "短剧", "短视频", "国产", "日韩", "美剧",
                "韩剧", "日剧", "古装", "仙侠", "悬疑", "都市", "言情", "甜宠", "历史",
                "现代", "爱情", "情感", "战争", "科幻", "剧情", "喜剧", "奇幻", "武侠",
                "犯罪", "动作", "恐怖", "青春", "校园", "家庭", "冒险", "传记", "搞笑",
                "真人秀", "脱口秀", "动画片", "电视剧集",
                # Release/codec/audio-video metadata must not be mistaken for another work title.
                "内封官中", "内封", "官中", "杜比视界", "杜比", "视界", "高码率",
                "HDR10", "HDR", "SDR", "DV", "iT", "HEVC", "H265", "H264",
                "DDP", "DTS", "Atmos", "蓝光原盘", "原盘",
            )
            for term in re.findall(r"[\u4e00-\u9fff]{3,}", str(display_title)):
                normalized_term = cls._simplify(term).replace("_", "").lower()
                for marker in sorted(title_noise, key=len, reverse=True):
                    normalized_marker = cls._simplify(marker).replace("_", "").lower()
                    if normalized_marker:
                        normalized_term = normalized_term.replace(normalized_marker, "")
                # 目标作品名本身不是冲突证据，但标题中并列的另一部作品仍是。
                # A numbered title may legitimately be followed by a subtitle, e.g.
                # "潜伏6：血域". Keep sequel identity strict in the other direction:
                # searching "潜伏" must not make "潜伏6" look like the same work.
                def matches_target_or_numbered_subtitle(title: str) -> bool:
                    if normalized_term == title or normalized_term in title:
                        return True
                    if not title or not title[-1:].isdigit() or not normalized_term.startswith(title):
                        return False
                    suffix = normalized_term[len(title):]
                    # Punctuation is already normalized away. Chinese suffixes are
                    # common official subtitles; a parenthesized release year is metadata.
                    return bool(
                        re.match(r"[\u4e00-\u9fff]", suffix)
                        or re.match(r"(?:19|20)\d{2}", suffix)
                    )

                if any(matches_target_or_numbered_subtitle(title) for title in target_titles):
                    continue
                if len(normalized_term) >= 3:
                    return True

        # 目标标题或实际文件路径有正向证据时保留；它不能覆盖上面已识别的冲突标题。
        if any(title in source_name for title in target_titles) or any(
            title in name for title in target_titles for name in file_names
        ):
            return False

        # 同时检查中文作品名片段与清理后的文件标题，兼顾中文剧集和英文原名电影。
        generic = {
            "全集", "高清", "超清", "国语", "中字", "合集", "更新", "资源", "分享",
            "动画", "动漫", "电影", "电视剧", "综艺", "WEB-DL", "BluRay",
        }
        occurrences: Counter[str] = Counter()
        for video in videos:
            file_name = str(video.get("file_name") or "")
            file_candidates = set(re.findall(r"[\u4e00-\u9fff]{3,}", file_name))
            try:
                parsed_name = str(parse_media_filename(file_name).get("name") or "").strip()
            except Exception:
                parsed_name = ""
            if len(parsed_name) >= 3:
                file_candidates.add(parsed_name)

            # 每个文件对同一候选标题最多计数一次，避免标题中的重复片段抬高置信度。
            normalized_candidates = set()
            for candidate in file_candidates:
                normalized = cls._simplify(candidate).replace("_", "").lower()
                # A filename such as "第02集.mp4" is an episode marker, not a work title.
                # Otherwise "更新至第02集" can falsely corroborate the unrelated source title.
                episode_label_only = bool(
                    re.fullmatch(r"第?\d{1,4}[集话期](?:国语|中字|字幕|高清|超清)?", normalized)
                )
                # Chinese title searches often use a translated name while shares use
                # an English original title. Without metadata aliases, keep such candidates.
                looks_english = bool(re.search(r"[A-Za-z]", candidate))
                if (
                    not episode_label_only
                    and len(normalized) >= 3
                    and normalized not in {cls._simplify(item).replace("_", "").lower() for item in generic}
                    and not any(title in normalized for title in target_titles)
                    and not (target_is_chinese and looks_english and not has_known_alternate_title)
                ):
                    normalized_candidates.add(normalized)
            for normalized in normalized_candidates:
                occurrences[normalized] += 1

        if not occurrences:
            return False

        # 过滤需同时满足：候选标题在分享元数据中出现，且在多数视频文件中重复出现。
        minimum = max(1, (len(videos) + 1) // 2)
        for normalized_foreign, count in occurrences.items():
            if count < minimum or normalized_foreign not in source_name:
                continue
            # 单文件分享也可判断，但必须有分享标题与文件名两处证据。
            if len(videos) == 1 or count * 2 >= len(videos):
                return True
        return False

    @classmethod
    def _has_target_title_evidence(
        cls,
        movie: object,
        source_title: str,
        videos: list[dict[str, Any]],
    ) -> bool:
        """Require positive title evidence before accepting a PanSou series result.

        PanSou performs broad searches and may return unrelated channel posts. A source
        title that is missing must not inherit the query title, and an unrelated result
        without a recognizable foreign title must not be accepted merely because its
        share resolves to playable video files.
        """
        if not isinstance(movie, dict):
            return False

        target_titles = [
            str(movie.get(key) or "").strip()
            for key in ("title", "name", "original_title")
            if str(movie.get(key) or "").strip()
        ]
        for alias_key in ("aliases", "alternative_titles", "aka"):
            aliases = movie.get(alias_key)
            if isinstance(aliases, str):
                target_titles.extend(
                    part.strip() for part in re.split(r"[,;；、|]", aliases) if part.strip()
                )
            elif isinstance(aliases, (list, tuple, set)):
                target_titles.extend(str(part).strip() for part in aliases if str(part).strip())

        normalized_titles = {
            cls._simplify(value).replace("_", "").lower()
            for value in target_titles
        }
        normalized_titles = {value for value in normalized_titles if len(value) >= 2}
        if not normalized_titles:
            return False

        source_name = cls._simplify(str(source_title or "")).replace("_", "").lower()
        if any(title in source_name for title in normalized_titles):
            return True

        for video in videos:
            # Folder names often contain the series title even when the leaf is just E01.mp4.
            file_evidence = " ".join(
                str(video.get(key) or "")
                for key in ("file_name", "relative_path", "path", "parent_name")
            )
            normalized_file = cls._simplify(file_evidence).replace("_", "").lower()
            if any(title in normalized_file for title in normalized_titles):
                return True
        return False

    @staticmethod
    def _candidate_priority(
        source: dict,
        movie_title: str,
        media_type: str = "",
        metadata: dict | None = None,
    ) -> int:
        """稳定地把名称/剧集信息较明确的资源排在前面；未知格式仍保留在后续页。"""
        name_fields = (
            "title", "name", "file_name", "filename", "resource_name",
            "share_name", "description", "content", "relative_path", "path",
        )
        text = " ".join(str(source.get(key) or "") for key in name_fields).lower()
        score = 0
        # 多个集数标记通常意味着资源标题包含更明确的剧集范围；
        # 只用于排序，不过滤候选，也不把分辨率差异当作重复资源。
        episode_markers = re.findall(
            r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
            text,
            re.I,
        )
        score += min(len(episode_markers), 4) * 2
        if re.search(r"s\d{1,2}", text, re.I):
            score += 1
        if re.search(r"\.(?:mp4|mkv|avi|mov|flv|wmv|m4v|ts|m2ts|iso)(?:\b|$)", text, re.I):
            score += 2
        if re.search(r"\b(?:2160p|1080p|720p|4k|web[- .]?dl|bluray|bdrip|remux)\b", text, re.I):
            score += 1
        # Release years are metadata, not part of title identity. Strip a
        # trailing parenthesized year before sequel-number checks; otherwise
        # "潜伏6 (2025)" can be mistaken for a title numbered 2025.
        normalized_movie_title = re.sub(
            r"[（(]\s*(19\d{2}|20\d{2})\s*[）)]", "", movie_title
        ).strip()
        simplified_title = SearchService._simplify(normalized_movie_title).lower()
        simplified_text = SearchService._simplify(text)
        primary_title_text = " ".join(
            str(source.get(key) or "")
            for key in ("title", "name", "resource_name", "share_name")
        )
        simplified_primary_title = SearchService._simplify(primary_title_text)
        if simplified_title and simplified_title in simplified_primary_title:
            # An explicit work-name match in the source title outranks generic episode,
            # resolution, and codec markers. Quality is useful only after identity.
            score += 80
        elif simplified_title and simplified_title in simplified_text:
            # A mention in descriptions/paths is weaker evidence than the source title.
            score += 5

        # Metadata is soft evidence for ordering, never a hard filter. Share titles
        # often omit credits or years, so absent evidence must not hide a valid result.
        metadata = metadata if isinstance(metadata, dict) else {}

        def metadata_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [part.strip() for part in re.split(r"[,;；、|]", value) if part.strip()]
            if isinstance(value, (list, tuple, set)):
                return [str(part).strip() for part in value if str(part).strip()]
            return []

        for field in ("original_title", "aliases", "alternative_titles", "aka"):
            alias_hit = any(
                len(normalized := SearchService._simplify(alias).lower()) >= 2
                and normalized in simplified_text
                for alias in metadata_values(metadata.get(field))
            )
            if alias_hit:
                score += 3
                break

        year = str(metadata.get("year") or metadata.get("release_year") or "").strip()
        year_match = re.search(r"(?:19|20)\d{2}", year)
        if year_match and re.search(rf"(?<!\d){year_match.group(0)}(?!\d)", text):
            score += 2

        credit_hits = 0
        for field in ("directors", "writers", "casts", "actors"):
            if any(
                len(normalized := SearchService._simplify(person).lower()) >= 2
                and normalized in simplified_text
                for person in metadata_values(metadata.get(field))
            ):
                credit_hits += 1
            if credit_hits >= 2:
                break
        score += min(credit_hits, 2)

        # A sequel number is part of the title, not disposable punctuation:
        # "潜伏6" must not rank "潜伏" TV-season shares above matching film shares.
        sequel_match = re.search(r"(\d+)$", simplified_title)
        if sequel_match:
            base_title = simplified_title[: -len(sequel_match.group(1))]
            if base_title and base_title in simplified_text and simplified_title not in simplified_text:
                score -= 8
        # For a selected movie, explicit episode markers are strong contrary
        # evidence. Keep these candidates visible, but place them lower.
        if media_type == "movie" and re.search(
            r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
            text,
            re.I,
        ):
            score -= 6
        # 标题过于简短或主要由分享说明组成时，降低优先级但仍保留候选。
        meaningful_text = re.sub(
            r"(?:分享|资源|链接|下载|转存|合集|全集|更新|推荐)",
            "",
            simplified_text,
        )
        if len(meaningful_text) < 3:
            score -= 2
        return score

    def search_movie_candidates(
        self,
        movie: object,
        config: dict,
        storage_target_id: str | None = None,
        *,
        offset: int = 0,
        scan_limit: int | None = None,
        page_info: dict | None = None,
        source_ids: list[str] | None = None,
    ) -> list[dict]:
        title, cleaned_title = self._title(movie)
        if not title:
            return []
        movie_target_id = (
            movie.get("storage_target_id")
            if isinstance(movie, dict)
            else ""
        )
        selected_target_id = str(storage_target_id or movie_target_id or "").strip()
        resource_types: list[str] = []
        if selected_target_id:
            getter = getattr(self.storage_targets, "get", None)
            target = getter(selected_target_id) if callable(getter) else None
            if target is None:
                self.logger.warning(
                    "资源搜索已取消：指定的存储目标 %s 不存在或已停用",
                    selected_target_id,
                )
                return []
            capabilities = getattr(target, "capabilities", None)
            if capabilities is None:
                manifest = getattr(target, "manifest", None)
                capabilities = getattr(manifest, "capabilities", ())
            resource_types = sorted({
                capability.removeprefix("storage.accepts.")
                for capability in (capabilities or ())
                if isinstance(capability, str)
                and capability.startswith("storage.accepts.")
                and capability != "storage.accepts.*"
            })
            if not resource_types:
                self.logger.warning(
                    "资源搜索已取消：存储目标 %s 未声明可搜索的资源类型",
                    selected_target_id,
                )
                return []

        self.logger.info(
            "开始检索《%s》%s",
            title,
            f"（存储目标：{selected_target_id}）" if selected_target_id else "",
        )

        # Preserve the selected work's identity for resource cards that can use it.
        # Legacy cards may continue to read only "title"; media-aware cards can use
        # category, media_type, year and alternate/original titles without core coupling.
        search_context = {"title": cleaned_title}
        if isinstance(movie, dict):
            for key in (
                "tag", "media_type", "year", "release_year", "original_title",
                "aliases", "alternative_titles", "aka", "genres", "directors",
                "writers", "casts", "actors", "countries", "release_dates",
                "runtime", "summary", "provider_id", "id", "url",
            ):
                value = movie.get(key)
                if value not in (None, "", [], {}):
                    search_context[key] = value
        if selected_target_id:
            search_context.update({
                "storage_target_id": selected_target_id,
                "resource_types": resource_types,
            })
        source_search_diagnostics: list[dict[str, Any]] = []
        search_with_diagnostics = getattr(self.resource_sources, "search_with_diagnostics", None)
        if callable(search_with_diagnostics):
            if source_ids is None:
                discovered, source_search_diagnostics = search_with_diagnostics(search_context, config)
            else:
                discovered, source_search_diagnostics = search_with_diagnostics(
                    search_context,
                    config,
                    source_ids=source_ids,
                )
        elif source_ids is None:
            discovered = self.resource_sources.search(search_context, config)
        else:
            discovered = self.resource_sources.search(search_context, config, source_ids=source_ids)
        total_discovered = len(discovered)
        # 排序稳定：高置信度资源优先，无法识别命名的资源仍保留在后续页。
        discovered = sorted(
            discovered,
            key=lambda source: self._candidate_priority(
                source,
                title,
                str(movie.get("media_type") or "") if isinstance(movie, dict) else "",
                movie if isinstance(movie, dict) else None,
            ),
            reverse=True,
        )
        safe_offset = max(0, int(offset or 0))
        safe_limit: int | None = None
        if scan_limit is not None:
            safe_limit = max(1, min(20, int(scan_limit)))
            page_sources = discovered[safe_offset : safe_offset + safe_limit]
        else:
            page_sources = discovered[safe_offset:]

        media_type_for_scan = (
            str(movie.get("media_type") or "").strip().lower()
            if isinstance(movie, dict)
            else ""
        )
        media_tag_for_scan = str(movie.get("tag") or "").strip() if isinstance(movie, dict) else ""
        is_series_query_for_scan = (
            media_type_for_scan in {
                "tv", "tv_show", "series", "anime", "variety", "documentary", "short_drama"
            }
            or media_tag_for_scan in {"电视剧", "剧集", "综艺", "动漫", "动画", "纪录片", "短剧"}
        )
        # PanSou's first results can be broad: if the initial page consists only of
        # unrelated or unresolvable shares, an empty page must not prevent the user
        # from ever reaching a valid match. Expand only empty first pages for series
        # searches, and keep a hard cap so ordinary searches retain their fast path.
        allow_empty_page_expansion = bool(
            safe_limit is not None
            and safe_offset == 0
            and is_series_query_for_scan
            and any(
                str(source.get("source_id") or "").strip().lower() == "pansou"
                for source in discovered
            )
        )
        auto_scan_end = (
            min(total_discovered, safe_offset + min(50, safe_limit * 5))
            if allow_empty_page_expansion and safe_limit is not None
            else safe_offset + len(page_sources)
        )
        candidates: list[dict] = []
        seen_resource_keys: set[tuple[str, str]] = set()
        direct_title_match_found = False

        # 分享解析涉及网盘网络请求，按小批次并发执行。批次内仍按原始顺序
        # 消费结果，保证候选排序稳定，并在达到上限后立即停止后续请求。
        batch_size = 10
        scanned_count = 0
        batch_start = 0
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            while batch_start < len(page_sources):
                batch = page_sources[batch_start : batch_start + batch_size]

                def resolve_source(source):
                    resource_id = str(
                        source.get("resource_id") or source.get("share_id") or source.get("pwd_id") or ""
                    ).strip()
                    if not resource_id:
                        return None
                    resource = {
                        **source,
                        "resource_id": resource_id,
                        **({"storage_target_id": selected_target_id} if selected_target_id else {}),
                    }
                    if source.get("pwd_id"):
                        resource["pwd_id"] = str(source.get("pwd_id") or "").strip()
                    try:
                        return self._get_resource_files(resource)
                    except Exception as exc:
                        self.logger.warning(
                            "资源源 %s 分享解析失败，跳过此候选: %s",
                            source.get("source_name") or source.get("source_id") or "未知来源",
                            exc,
                        )
                        return ([], None, "分享解析失败", selected_target_id)

                resolved_batch = list(executor.map(resolve_source, batch))
                resolved_by_source = {
                    id(source): resolved
                    for source, resolved in zip(batch, resolved_batch, strict=True)
                    if resolved is not None
                }

                for source in batch:
                    resource_id = str(
                        source.get("resource_id") or source.get("share_id") or source.get("pwd_id") or ""
                    ).strip()
                    resource_type = str(source.get("resource_type") or "").strip().lower()
                    resource_key = (resource_type, resource_id)
                    if not resource_id or resource_key in seen_resource_keys:
                        continue
                    resource = {
                        **source,
                        "resource_id": resource_id,
                        **({"storage_target_id": selected_target_id} if selected_target_id else {}),
                    }
                    if source.get("pwd_id"):
                        resource["pwd_id"] = str(source.get("pwd_id") or "").strip()
                    resolved = resolved_by_source.get(id(source))
                    if resolved is None:
                        files, _stoken, err, storage_target_id = [], None, "资源标识无效", selected_target_id
                    else:
                        files, _stoken, err, storage_target_id = resolved
                    if err or not files:
                        self.logger.debug(
                            "资源源 %s 命中 %s 但解析失败: %s",
                            source.get("source_name") or source.get("source_id"),
                            resource_id,
                            err,
                        )
                        continue
        
                    all_videos = [
                        {
                            "fid": f.get("fid"),
                            "file_name": f.get("file_name", ""),
                            "relative_path": str(f.get("relative_path") or ""),
                            "size": f.get("size", 0),
                            "size_text": _format_size(f.get("size", 0)),
                            "resolution": _detect_resolution(f.get("file_name", "")),
                        }
                        for f in files
                        if isinstance(f, dict)
                        and f.get("fid")
                        and str(f.get("file_name", "")).lower().endswith(VIDEO_EXTENSIONS)
                    ]
                    source_title = next(
                        (
                            str(source.get(key) or "").strip()[:200]
                            for key in ("title", "name", "resource_name", "share_name", "content")
                            if str(source.get(key) or "").strip()
                        ),
                        "",
                    )
                    # 网盘文件名经常只有“第01集.mp4”等通用名称；若文件名没有
                    # 分辨率标记，使用来源标题补足版本信息，但绝不覆盖文件名中的明确标记。
                    title_resolution = _detect_resolution("", source_title)
                    if title_resolution != "未知":
                        for video in all_videos:
                            if video["resolution"] == "未知":
                                video["resolution"] = title_resolution
                    source_title_evidence = " ".join(
                        str(source.get(key) or "").strip()
                        for key in ("title", "name", "resource_name", "share_name", "content", "description")
                        if str(source.get(key) or "").strip()
                    )
                    source_id = str(source.get("source_id") or "").strip().lower()
                    # PanSou may attach several different titles and share URLs to one
                    # aggregated result. Its title is then a description of a bundle,
                    # not reliable identity evidence for each individual Quark link.
                    # In that case, don't reject the whole share based on any unrelated
                    # title mentioned in the aggregate. Clear contrary evidence in the
                    # actual video filenames still goes through the mismatch filter.
                    is_pansou_aggregate = (
                        source_id == "pansou"
                        and len(re.findall(r"https?://", source_title_evidence, re.IGNORECASE)) >= 2
                    )
                    # Ordinary resource cards can put the real work title in description/content
                    # while the first title field only says "updated to episode N". Check all
                    # available text for conflicts; aggregate PanSou results remain URL-scoped.
                    display_title_for_filter = source_title_evidence
                    candidate_title_evidence = source_title_evidence
                    if is_pansou_aggregate:
                        # Use the title paired with this specific share URL rather than
                        # the whole multi-work aggregate. If no pairing is available,
                        # retain the legacy conservative behavior instead of guessing.
                        display_title_for_filter = self._pansou_title_for_share(
                            source_title_evidence,
                            str(source.get("url") or ""),
                        ) or None
                        if display_title_for_filter:
                            candidate_title_evidence = display_title_for_filter
                    if self._has_conflicting_media_title(
                        movie,
                        source_title_evidence,
                        all_videos,
                        display_title=display_title_for_filter,
                    ):
                        self.logger.info(
                            "过滤疑似错误影视候选：目标《%s》，来源标题/文件名证据指向其他作品《%s》",
                            title,
                            source_title,
                        )
                        seen_resource_keys.add(resource_key)
                        continue

                    # Match the broad "More download links" flow: for PanSou, missing
                    # positive title evidence is not enough on its own to discard a
                    # resolved share. Keep clear foreign-title filtering above and rely
                    # on the user's candidate selection for ambiguous episode-only files.

                    total_video_count = len(all_videos)
                    videos = all_videos[:MAX_VIDEO_FILES_PER_CANDIDATE]
                    files_truncated = total_video_count > len(videos)
                    scan_complete = bool(getattr(files, "scan_complete", True))
                    scan_warnings = list(getattr(files, "scan_warnings", []) or [])

                    if not videos:
                        continue

                    # 统计信息尽量反映完整分享；只有文件选择列表受上限限制。
                    resolutions = {}
                    for video in all_videos:
                        resolution = video["resolution"]
                        resolutions[resolution] = resolutions.get(resolution, 0) + 1
                    (
                        episode_coverage,
                        episode_count,
                        recognized_episode_files,
                        unrecognized_video_files,
                        additional_episode_versions,
                    ) = _episode_statistics(
                        all_videos,
                        str(source.get("source_id") or ""),
                        self.resource_sources,
                    )

                    total_size = sum(int(v.get("size") or 0) for v in all_videos)
                    declared_episode_count = _declared_episode_count(source_title_evidence)
                    media_type = str(movie.get("media_type") or "").strip().lower() if isinstance(movie, dict) else ""
                    media_tag = str(movie.get("tag") or "").strip()
                    is_series_media = (
                        media_type in {"tv", "tv_show", "series", "anime", "variety", "documentary", "short_drama"}
                        or media_tag in {"电视剧", "剧集", "综艺", "动漫", "动画", "纪录片", "短剧"}
                    )
                    episode_count_warning = bool(
                        is_series_media
                        and declared_episode_count
                        and episode_count < declared_episode_count
                        and recognized_episode_files > 0
                    )
                    simplified_movie_title = self._simplify(title).lower()
                    simplified_source_title = self._simplify(source_title).lower()
                    sequel_match = re.search(r"(\d+)$", simplified_movie_title)
                    title_mismatch = bool(
                        sequel_match
                        and simplified_movie_title not in simplified_source_title
                        and simplified_movie_title[: -len(sequel_match.group(1))]
                        and simplified_movie_title[: -len(sequel_match.group(1))] in simplified_source_title
                    )
                    strong_episode_markers = any(
                        re.search(
                            r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
                            str(video.get("file_name") or ""),
                            re.I,
                        )
                        for video in all_videos
                    )
                    format_mismatch = bool(
                        isinstance(movie, dict)
                        and movie.get("media_type") == "movie"
                        and strong_episode_markers
                    )
                    match_explanation = _candidate_match_explanation(
                        movie if isinstance(movie, dict) else {"title": title},
                        candidate_title_evidence,
                        recognized_episode_files,
                        total_video_count,
                        declared_episode_count if is_series_media else None,
                        scan_complete,
                    )
                    # Only title metadata should end the expanded first scan. A folder
                    # path can be useful evidence for a candidate, but must not stop us
                    # before a later share whose actual source title directly matches.
                    if self._has_target_title_evidence(
                        movie,
                        candidate_title_evidence,
                        [],
                    ):
                        direct_title_match_found = True
                    seen_resource_keys.add(resource_key)
                    candidates.append({
                        "match_explanation": match_explanation,
                        "source_id": source.get("source_id", "unknown"),
                        "resource_title": source_title,
                        "title_mismatch": title_mismatch,
                        "format_mismatch": format_mismatch,
                        "media_type": str(movie.get("media_type") or "") if isinstance(movie, dict) else "",
                        "tag": str(movie.get("tag") or "") if isinstance(movie, dict) else "",
                        "declared_episode_count": declared_episode_count if is_series_media else None,
                        "episode_count_warning": episode_count_warning,
                        "scan_complete": scan_complete,
                        "scan_warnings": scan_warnings,
                        "source_name": source.get("source_name", "未知来源"),
                        "cover": str(movie.get("cover", "") if isinstance(movie, dict) else "").strip()[:1000],
                        "storage_target_id": storage_target_id,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "channel": source.get("channel", ""),
                        "channel_id": source.get("channel_id", ""),
                        **({"pwd_id": str(source.get("pwd_id") or "").strip()} if source.get("pwd_id") else {}),
                        "password": str(source.get("password") or source.get("passcode") or ""),
                        "url": str(source.get("url") or ""),
                        "files": videos,
                        "file_count": len(videos),
                        "total_video_count": total_video_count,
                        "files_truncated": files_truncated,
                        "episode_coverage": episode_coverage,
                        "episode_count": episode_count,
                        "recognized_episode_files": recognized_episode_files,
                        "unrecognized_video_files": unrecognized_video_files,
                        "additional_episode_versions": additional_episode_versions,
                        "total_size": total_size,
                        "total_size_text": _format_size(total_size),
                        "resolutions": resolutions,
                        "summary": (
                            f"来源: [{source.get('source_name', '未知来源')}] | "
                            f"频道: [{source.get('channel', '')}] | "
                            f"{len(videos)} 个可选视频"
                            + (
                                f"（实际共 {total_video_count} 个，仅展示前 {len(videos)} 个）"
                                if files_truncated
                                else ""
                            )
                            + f" | {_format_size(total_size)}"
                        ),
                    })
                batch_start += len(batch)
                scanned_count += len(batch)
                if len(candidates) >= MAX_CANDIDATES_PER_MOVIE:
                    break
                if (
                    allow_empty_page_expansion
                    and not direct_title_match_found
                    and batch_start >= len(page_sources)
                    and safe_offset + len(page_sources) < auto_scan_end
                ):
                    if batch_start == safe_limit:
                        self.logger.info(
                            "PanSou 剧集首批扫描 %s 条未形成候选，继续扩展检索（最多扫描 %s 条）",
                            safe_limit,
                            auto_scan_end - safe_offset,
                        )
                    expanded_end = min(
                        auto_scan_end,
                        safe_offset + len(page_sources) + safe_limit,
                    )
                    page_sources = discovered[safe_offset:expanded_end]
        if page_info is not None:
            consumed = min(scanned_count, max(0, total_discovered - safe_offset))
            page_info.update({
                "next_offset": safe_offset + consumed,
                "has_more": safe_offset + consumed < total_discovered,
                "total_discovered": total_discovered,
            })
            # Preserve the exact legacy page-info shape when integrating an
            # older/custom source manager that doesn't implement diagnostics.
            if callable(search_with_diagnostics):
                page_info.update({
                    "source_search_diagnostics": source_search_diagnostics,
                    "source_search_failed": sum(
                        item.get("status") == "error"
                        for item in source_search_diagnostics
                        if isinstance(item, dict)
                    ),
                })
        self.logger.info(
            "《%s》检索完成，有效候选=%s（已扫描 %s/%s 条）",
            title,
            len(candidates),
            min(total_discovered, safe_offset + scanned_count),
            total_discovered,
        )
        return candidates

    def transfer_selected_resource(
        self,
        movie: object,
        candidate: dict,
        target_fid: str = "0",
        category_fids: dict | None = None,
    ) -> tuple[bool, str]:
        ok, message, _ = self.transfer_selected_resource_with_progress(
            movie, candidate, target_fid, category_fids
        )
        return ok, message

    def _verify_destination_transfer(
        self,
        storage_target_id: str,
        folder_fid: str,
        selected_fids: list[str],
        source_files: list[dict],
    ) -> bool | None:
        """Verify files for cards that advertise destination listing.

        None means the storage card does not support verification. False means
        verification was advertised but could not confirm every selected file.
        """
        get_card = getattr(self.storage_targets, "get", None)
        if not callable(get_card):
            return None
        card = get_card(storage_target_id or None)
        capabilities = set(getattr(card, "capabilities", ()) or ())
        list_destination_files = getattr(card, "list_destination_files", None)
        if (
            "storage.list_destination_files" not in capabilities
            or not callable(list_destination_files)
        ):
            return None

        selected = set(selected_fids)
        expected_names = [
            str(item.get("file_name") or "").strip()
            for item in source_files
            if isinstance(item, dict)
            and str(item.get("fid") or "") in selected
        ]
        if len(expected_names) != len(selected) or any(not name for name in expected_names):
            return False

        expected = Counter(name.casefold() for name in expected_names)

        # Quark may acknowledge a save before the destination listing catches up.
        # Poll for up to ~5 seconds; never resubmit the transfer just because the
        # listing is delayed, since that could create duplicate files.
        last_error = None
        retry_delays = (0.5, 1.0, 1.5, 2.0)
        for attempt in range(len(retry_delays) + 1):
            try:
                destination_files = list_destination_files(folder_fid)
                if not isinstance(destination_files, list) or any(
                    not isinstance(item, dict) for item in destination_files
                ):
                    raise TypeError("目标目录文件列表格式无效")
                observed = Counter(
                    str(item.get("file_name") or "").strip().casefold()
                    for item in destination_files
                    if not item.get("is_dir") and str(item.get("file_name") or "").strip()
                )
                if all(observed[name] >= count for name, count in expected.items()):
                    return True
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    "转存请求已返回成功，读取目标目录核实失败（第 %s/%s 次）",
                    attempt + 1,
                    len(retry_delays) + 1,
                    exc_info=True,
                )

            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])

        if last_error is not None:
            self.logger.warning(
                "转存请求已返回成功，但等待重试后仍无法读取目标目录进行结果核实"
            )
        return False

    def transfer_selected_resource_with_progress(
        self,
        movie: object,
        candidate: dict,
        target_fid: str = "0",
        category_fids: dict | None = None,
        progress=None,
    ) -> tuple[bool, str, dict]:
        progress = progress or (lambda *_args: None)
        title, _ = self._title(movie)
        tag = str(movie.get("tag", "电影")) if isinstance(movie, dict) else "电影"
        # 前端传入 target_fid 即表示用户明确选择了目标目录；分类目录仅作为资源选择页的默认值。
        # 不再在服务层强制覆盖用户选择，避免“下拉框看似可选但实际始终按分类目录转存”。
        parent_fid = str(target_fid or (category_fids or {}).get(tag) or "0").strip() or "0"
        resource_id = str(
            candidate.get("resource_id") or candidate.get("share_id") or candidate.get("pwd_id") or ""
        ).strip()
        selected_fids = [
            str(item.get("fid"))
            for item in candidate.get("files", [])
            if isinstance(item, dict) and item.get("fid")
        ]
        selected_fids = list(dict.fromkeys(selected_fids))
        total = len(selected_fids)
        if not resource_id or not selected_fids:
            return False, "候选资源参数无效", {"success": 0, "skipped": 0, "failed": total}

        storage_target_id = str(candidate.get("storage_target_id") or "").strip()
        resource = {
            **candidate,
            "resource_id": resource_id,
            "storage_target_id": storage_target_id,
        }
        progress(20, "正在重新验证分享资源…")
        files, fresh_stoken, err, storage_target_id = self._get_resource_files(resource, refresh=True)
        if err or not files or not fresh_stoken:
            if err:
                self.logger.warning("分享资源解析失败: %s", err)
            return False, "分享资源解析失败，请检查分享链接或查看服务日志", {
                "success": 0,
                "skipped": 0,
                "failed": total,
            }
        available_fids = {str(item.get("fid")) for item in files if item.get("fid")}
        if not set(selected_fids).issubset(available_fids):
            return False, "所选文件已不存在或不属于该分享资源", {"success": 0, "skipped": 0, "failed": total}

        progress(45, "正在准备目标文件夹…")
        try:
            folder_fid = self.storage_targets.create_folder(
                title,
                parent_fid,
                storage_target_id or None,
            )
            create_err = None
        except Exception:
            self.logger.exception("创建专属文件夹失败: %s", title)
            folder_fid = None
            create_err = "请检查存储目标配置或查看服务日志"
        if not folder_fid:
            return False, f"创建专属文件夹失败: {create_err}", {"success": 0, "skipped": 0, "failed": total}

        progress(70, f"正在转存 {total} 个文件…")
        ok, msg = self.storage_targets.transfer(
            resource,
            [{"fid": fid} for fid in selected_fids],
            folder_fid,
            storage_target_id or None,
            fresh_stoken,
        )
        if ok:
            verified = self._verify_destination_transfer(
                storage_target_id,
                str(folder_fid),
                selected_fids,
                files,
            )
            if verified is False:
                message = (
                    "网盘接口已接受转存请求，但结果待核实：暂未能确认所选文件已出现在目标目录。"
                    "请检查目标网盘后再决定是否重试"
                )
                self.logger.warning("《%s》转存结果待核实，resource_id=%s", title, resource_id)
                return False, message, {
                    "success": 0,
                    "skipped": 0,
                    "failed": total,
                    "uncertain": True,
                }
            self.logger.info("《%s》转存成功，resource_id=%s", title, resource_id)
            progress(100, "转存完成")
            if verified is True:
                message = f"《{title}》转存成功！已在目标目录核实 {total} 个文件"
            else:
                message = f"《{title}》转存成功！已精准归档至专属文件夹"
            return True, message, {"success": total, "skipped": 0, "failed": 0}
        self.logger.warning("《%s》转存失败: %s", title, msg)
        uncertain = is_uncertain_transfer_message(msg)
        safe_message = (
            "转存结果待核实，请检查目标网盘后再决定是否重试"
            if uncertain
            else "转存失败，请检查存储目标配置或查看服务日志"
        )
        return False, safe_message, {
            "success": 0,
            "skipped": 0,
            "failed": total,
            "uncertain": uncertain,
        }

