"""影视资源检索与转存业务服务。

用途：并发搜索已启用资源源、解析存储目标资源、去重候选资源，并执行用户选择的文件白名单转存。
维护说明：具体平台解析与转存由 StorageTargetCard 负责；核心业务不直接依赖具体网盘平台。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .filename_rules import parse_tv_episode
from .resource_sources import ResourceSourceManager
from .storage_targets import StorageTargetManager
from .transfer_outcome import is_uncertain_transfer_message

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts", ".m2ts", ".iso")
MAX_VIDEO_FILES_PER_CANDIDATE = 200
MAX_CANDIDATES_PER_MOVIE = 20
SHARE_CACHE_TTL_SECONDS = 30
MAX_SHARE_CACHE_ENTRIES = 128


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


def _detect_resolution(name: str) -> str:
    text = str(name or "").lower()
    patterns = [
        (r"\b(2160p|4k|uhd)\b", "4K"),
        (r"\b(1440p|2k)\b", "2K"),
        (r"\b1080[pi]?\b", "1080P"),
        (r"\b(720[pi]?)\b", "720P"),
        (r"\b(480[pi]?)\b", "480P"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text):
            return label
    return "未知"


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
        # 缓存仅用于搜索结果展示，不缓存可用于转存的临时 token。
        # TTL 和容量上限避免分享文件列表长期过期或随搜索次数无限增长。
        self._share_cache: dict[
            str,
            tuple[float, tuple[list[dict[str, Any]], str | None, str]],
        ] = {}
        self._share_lock = threading.Lock()

    @staticmethod
    def _title(movie: object) -> tuple[str, str]:
        title = str(movie.get("title", movie.get("name", "")) if isinstance(movie, dict) else movie).strip()
        cleaned = re.sub(r"[\(（]\s*(19\d{2}|20\d{2})\s*[\)）]", "", title).strip()
        return title, cleaned

    @staticmethod
    def _simplify(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", value or "")

    def _get_resource_files(self, resource: dict, *, refresh: bool = False):
        """解析分享文件。

        普通搜索允许短时复用文件列表；转存前必须 refresh，重新获取文件状态和
        临时 token。缓存键只保留提取码摘要，缓存内容不保存 token。
        """
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
        resource_type = str(resource.get("resource_type") or "").strip().lower()
        source_id = str(resource.get("source_id") or "").strip()

        password = str(resource.get("password") or resource.get("passcode") or "")
        password_digest = hashlib.sha256(password.encode("utf-8")).hexdigest()
        # Resource IDs are scoped to their provider; don't share cache entries
        # between clouds that happen to use the same ID.
        cache_key = f"{storage_target_id}:{resource_type}:{source_id}:{resource_id}:{password_digest}"
        now = time.monotonic()

        if not refresh:
            with self._share_lock:
                cached = self._share_cache.get(cache_key)
                if cached is not None:
                    cached_at, cached_result = cached
                    if now - cached_at < SHARE_CACHE_TTL_SECONDS:
                        files, error, target_id = cached_result
                        return files, None, error, target_id
                    self._share_cache.pop(cache_key, None)

        resolved = self.storage_targets.resolve_resource(resource, storage_target_id or None)
        files = resolved.get("files") or []
        token = resolved.get("token")
        error = resolved.get("error")
        target_id = resolved.get("target_id") or storage_target_id

        # 只缓存不含临时 token 的解析结果，并限制缓存总量。
        with self._share_lock:
            self._share_cache[cache_key] = (now, (files, error, target_id))
            while len(self._share_cache) > MAX_SHARE_CACHE_ENTRIES:
                oldest_key = min(
                    self._share_cache,
                    key=lambda key: self._share_cache[key][0],
                )
                self._share_cache.pop(oldest_key, None)

        return files, token, error, target_id

    @staticmethod
    def _candidate_priority(source: dict, movie_title: str) -> int:
        """稳定地把名称/剧集信息较明确的资源排在前面；未知格式仍保留在后续页。"""
        name_fields = (
            "title", "name", "file_name", "filename", "resource_name",
            "share_name", "description",
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
        simplified_title = re.sub(r"[^\w\u4e00-\u9fa5]", "", movie_title).lower()
        simplified_text = re.sub(r"[^\w\u4e00-\u9fa5]", "", text)
        if simplified_title and simplified_title in simplified_text:
            score += 3
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

        search_context = {"title": cleaned_title}
        if selected_target_id:
            search_context.update({
                "storage_target_id": selected_target_id,
                "resource_types": resource_types,
            })
        discovered = self.resource_sources.search(search_context, config)
        total_discovered = len(discovered)
        # 排序稳定：高置信度资源优先，无法识别命名的资源仍保留在后续页。
        discovered = sorted(
            discovered,
            key=lambda source: self._candidate_priority(source, title),
            reverse=True,
        )
        safe_offset = max(0, int(offset or 0))
        if scan_limit is not None:
            safe_limit = max(1, min(20, int(scan_limit)))
            page_sources = discovered[safe_offset : safe_offset + safe_limit]
        else:
            page_sources = discovered[safe_offset:]
        candidates: list[dict] = []
        seen_resource_keys: set[tuple[str, str]] = set()

        # 分享解析涉及网盘网络请求，按小批次并发执行。批次内仍按原始顺序
        # 消费结果，保证候选排序稳定，并在达到上限后立即停止后续请求。
        batch_size = 10
        with ThreadPoolExecutor(max_workers=batch_size) as executor:
            for batch_start in range(0, len(page_sources), batch_size):
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
                            "size": f.get("size", 0),
                            "size_text": _format_size(f.get("size", 0)),
                            "resolution": _detect_resolution(f.get("file_name", "")),
                        }
                        for f in files
                        if isinstance(f, dict)
                        and f.get("fid")
                        and str(f.get("file_name", "")).lower().endswith(VIDEO_EXTENSIONS)
                    ]
                    total_video_count = len(all_videos)
                    videos = all_videos[:MAX_VIDEO_FILES_PER_CANDIDATE]
                    files_truncated = total_video_count > len(videos)

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
                    seen_resource_keys.add(resource_key)
                    candidates.append({
                        "source_id": source.get("source_id", "unknown"),
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
                if len(candidates) >= MAX_CANDIDATES_PER_MOVIE:
                    break
        if page_info is not None:
            consumed = min(len(page_sources), max(0, total_discovered - safe_offset))
            page_info.update({
                "next_offset": safe_offset + consumed,
                "has_more": safe_offset + consumed < total_discovered,
                "total_discovered": total_discovered,
            })
        self.logger.info(
            "《%s》检索完成，有效候选=%s（已扫描 %s/%s 条）",
            title,
            len(candidates),
            min(total_discovered, safe_offset + len(page_sources)),
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

        try:
            destination_files = list_destination_files(folder_fid)
            if not isinstance(destination_files, list) or any(
                not isinstance(item, dict) for item in destination_files
            ):
                raise TypeError("目标目录文件列表格式无效")
        except Exception:
            self.logger.warning(
                "转存请求已返回成功，但无法读取目标目录进行结果核实",
                exc_info=True,
            )
            return False

        expected = Counter(name.casefold() for name in expected_names)
        observed = Counter(
            str(item.get("file_name") or "").strip().casefold()
            for item in destination_files
            if not item.get("is_dir") and str(item.get("file_name") or "").strip()
        )
        return all(observed[name] >= count for name, count in expected.items())

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
                    "网盘接口已接受转存请求，但暂未能核实所选文件已出现在目标目录。"
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

