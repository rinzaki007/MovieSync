"""自动追剧任务管理器。

用途：周期性检查资源源中的新内容，并通过选定的存储目标完成归档。

用途：创建/删除/立即执行订阅任务，并周期性检查资源分享中的新集数或指定文件。
维护说明：订阅会持久化到 data/subscriptions.json；每次执行都会重新获取分享 Token，并再次校验目标 FID。
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from threading import Event, RLock, Thread, current_thread

from ..storage import JsonStore
from .filename_rules import parse_tv_episode
from .transfer_outcome import is_uncertain_transfer_message

SUBSCRIPTION_SCHEMA_VERSION = 5
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts", ".m2ts", ".iso")


def _normalize_resource_id(value: object) -> str:
    value = str(value or "").strip()
    if len(value) > 128 or (value and not all(char.isalnum() or char in "_-" for char in value)):
        raise ValueError("分享资源 ID 无效")
    return value

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


class SubscriptionManager:
    def __init__(
        self,
        subscriptions_file,
        storage_targets,
        resource_sources=None,
        logger=None,
        max_interval_hours: int = 168,
    ):
        # 兼容旧调用方式：SubscriptionManager(file, storage_target, logger)
        if logger is None:
            logger = resource_sources
            resource_sources = None
        self.store = JsonStore(
            subscriptions_file,
            lambda: [],
        )

        self.storage_targets = storage_targets
        self.resource_sources = resource_sources
        self.config_store = getattr(resource_sources, "config_store", None)
        self.logger = logger
        self.max_interval_hours = (
            max_interval_hours
        )

        self.lock = RLock()
        # Serialize start/stop callers without holding the data lock while
        # joining the worker, since the worker may need that lock to finish.
        self._scheduler_lifecycle_lock = RLock()
        self._shutdown_requested = False
        self.running_ids: set[str] = set()
        self.stop_event = Event()
        self.worker: Thread | None = None
        self.schema_version = SUBSCRIPTION_SCHEMA_VERSION
        self._recover_interrupted_subscriptions()

    @staticmethod
    def _now_string() -> str:
        return datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        )

    def _load_subscriptions(self) -> list[dict]:
        data = self.store.read()

        if not isinstance(data, list):
            data = []

        changed = False
        normalized = []

        for item in data:
            if not isinstance(item, dict):
                changed = True
                continue

            item = dict(item)

            try:
                version = int(
                    item.get(
                        "schema_version",
                        1,
                    )
                )
            except (TypeError, ValueError):
                version = 1

            if version < 2:
                item.setdefault("retry_count", 0)
                item.setdefault("pending_save_keys", [])
                changed = True

            item["schema_version"] = SUBSCRIPTION_SCHEMA_VERSION
            item.setdefault("storage_target_id", "")
            if "resource_id" not in item:
                item["resource_id"] = item.get("pwd_id", "")
                changed = True
            if "resource_type" not in item:
                item["resource_type"] = ""
                changed = True
            item.setdefault("cover", "")
            item.setdefault("source_id", "")
            item.setdefault("initial_file_keys", [])
            item.setdefault("saved_episode_sources", {})
            if not isinstance(item.get("saved_episode_sources"), dict):
                item["saved_episode_sources"] = {}
                changed = True
            else:
                item["saved_episode_sources"] = {
                    str(key): str(value)
                    for key, value in item["saved_episode_sources"].items()
                    if str(key).strip() and str(value).strip()
                }
            item.setdefault("tracking_mode", "legacy")
            item.setdefault("status", "waiting")
            item.setdefault("phase", "waiting")
            item.setdefault("phase_label", "等待下次检查")
            item.setdefault("pending_save_uncertain", False)
            tracked = item.get("tracked_file_keys")
            if not isinstance(tracked, list):
                item["tracked_file_keys"] = []
                changed = True
            item["tracked_file_keys"] = list(dict.fromkeys(
                str(key).strip()
                for key in item.get("tracked_file_keys", [])
                if str(key).strip()
            ))[:1000]
            item.setdefault("run_history", [])
            if not isinstance(item["run_history"], list):
                item["run_history"] = []
                changed = True
            item["run_history"] = item["run_history"][-50:]

            # Older or manually edited JSON may contain non-scalar history
            # entries. Keep only supported episode identifiers so completion
            # bookkeeping can safely deduplicate saved history with a set.
            raw_saved = item.get("saved_episodes", [])
            if not isinstance(raw_saved, list):
                raw_saved = []
                changed = True
            saved_episodes = []
            seen_saved = set()
            for value in raw_saved:
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    changed = True
                    continue
                if isinstance(value, str):
                    value = value.strip()
                    if not value:
                        changed = True
                        continue
                key = str(value)
                if key in seen_saved:
                    changed = True
                    continue
                seen_saved.add(key)
                saved_episodes.append(value)
            if saved_episodes != item.get("saved_episodes", []):
                changed = True
            item["saved_episodes"] = saved_episodes

            raw_pending_keys = item.get("pending_save_keys", [])
            if not isinstance(raw_pending_keys, list):
                # Corrupt pending state must not crash every subscription
                # read. If the previous run was uncertain, fail closed rather
                # than silently allowing a potentially duplicate transfer.
                item["pending_save_keys"] = []
                item["pending_save_keys_invalid"] = bool(
                    item.get("pending_save_uncertain")
                )
                changed = True
            else:
                item["pending_save_keys"] = [
                    str(key)
                    for key in raw_pending_keys
                    if isinstance(key, (str, int))
                    and not isinstance(key, bool)
                    and str(key).strip()
                ][:200]
                # A non-empty raw list that normalizes to no usable keys is
                # corrupt. If the remote outcome was uncertain, do not let
                # normalization erase the only evidence that retry is unsafe.
                if (
                    item.get("pending_save_uncertain")
                    and raw_pending_keys
                    and not item["pending_save_keys"]
                    and not item.get("pending_save_keys_invalid")
                ):
                    item["pending_save_keys_invalid"] = True
                    changed = True
                if item.get("pending_save_keys_invalid") and item["pending_save_keys"]:
                    # Keep the fail-closed marker until an operator supplies
                    # a repaired, non-empty list of pending keys.
                    item["pending_save_keys_invalid"] = False
                    changed = True
                if item["pending_save_keys"] != raw_pending_keys:
                    changed = True

            normalized.append(item)

        if changed or normalized != data:
            self.store.write(normalized)

        return normalized


    def _recover_interrupted_subscriptions(self) -> None:
        """Recover persisted running subscriptions conservatively after process restart."""
        with self.lock:
            subscriptions = self._load_subscriptions()
            changed = False
            for item in subscriptions:
                if item.get("status") != "running":
                    continue
                pending = list(item.get("pending_save_keys", []) or [])
                invalid_pending = bool(item.get("pending_save_keys_invalid"))
                uncertain_pending = bool(pending) or invalid_pending
                item["status"] = "failed"
                item["phase"] = "failed"
                item["last_check_at"] = time.time()
                item["last_error"] = (
                    "服务重启时待确认转存记录格式异常，无法安全重试；请检查网盘并修复订阅记录。"
                    if invalid_pending
                    else (
                        "服务重启时转存请求结果不明确，请核对网盘后手动确认。"
                        if pending
                        else "服务重启导致检查中断，可安全重新检查。"
                    )
                )
                item["phase_label"] = "转存结果待核实" if uncertain_pending else "检查中断"
                # A malformed pending-key list is still evidence of an
                # ambiguous remote side effect. Do not clear the guard merely
                # because normalization removed every unusable key.
                item["pending_save_uncertain"] = uncertain_pending
                item["last_check"] = self._now_string() + " (中断)"
                diagnostics = item.get("last_diagnostics")
                # Retain an unfinished run's latest checkpoint after restart.
                if isinstance(diagnostics, dict) and not diagnostics.get("finished_at"):
                    diagnostics = deepcopy(diagnostics)
                    diagnostics["success"] = False
                    diagnostics["interrupted"] = True
                    diagnostics["phase"] = "interrupted"
                    diagnostics["finished_at"] = time.time()
                    diagnostics["updated_at"] = diagnostics["finished_at"]
                    diagnostics["message"] = str(item["last_error"])[:500]
                    item["last_diagnostics"] = diagnostics
                changed = True
            if changed:
                self.store.write(subscriptions)

    def get_subscriptions(
        self,
    ) -> list[dict]:
        with self.lock:
            return list(self._load_subscriptions())

    def add_subscription(
        self,
        *,
        title: str,
        pwd_id: str = "",
        resource_id: str = "",
        resource_type: str = "",
        target_fid: str = "0",
        interval_hours: int = 6,
        start_ep: int = 0,
        channel: str = "",
        channel_name: str = "",
        files=None,
        storage_target_id: str = "",
        cover: str = "",
        source_id: str = "",
        media_type: str = "",
        tag: str = "",
        source_folder_path: str = "",
    ) -> dict:
        title = str(
            title or ""
        ).strip()

        if not 1 <= len(title) <= 200:
            raise ValueError(
                "追剧名称长度必须在 1-200 个字符之间"
            )

        legacy_pwd_id = _normalize_resource_id(pwd_id)
        resource_id = _normalize_resource_id(resource_id or legacy_pwd_id)
        resource_type = str(resource_type or "").strip().lower()
        if not resource_id:
            raise ValueError("资源 ID 无效")

        target_fid = _normalize_fid(
            target_fid
        )

        interval_hours = int(
            interval_hours
        )

        start_ep = max(
            0,
            int(start_ep),
        )

        if not (
            1
            <= interval_hours
            <= self.max_interval_hours
        ):
            raise ValueError(
                "检测周期必须在 "
                f"1-{self.max_interval_hours} "
                "小时之间"
            )

        target_fids = []
        selected_episode_numbers: list[int] = []
        selected_season_episodes: list[tuple[int, int]] = []
        seen = set()

        for item in (files or [])[:200]:
            fid = str(
                item.get("fid")
                if isinstance(
                    item,
                    dict,
                )
                else item
                or ""
            ).strip()

            if (
                fid
                and len(fid) <= 128
                and all(
                    char.isalnum() or char in "_-"
                    for char in fid
                )
                and fid not in seen
            ):
                seen.add(fid)
                target_fids.append(fid)
                file_name = (
                    str(item.get("file_name") or "")
                    if isinstance(item, dict)
                    else ""
                )
                season_num, ep_num = _parse_source_episode(
                    self.resource_sources,
                    source_id,
                    file_name,
                )
                if ep_num is not None:
                    selected_episode_numbers.append(ep_num)
                    if season_num is not None:
                        selected_season_episodes.append((season_num, ep_num))
                # Files supplied to add_subscription are the user's explicit
                # initial selection. Do not pre-mark them as tracked merely because
                # the request omitted file_name: the channel scan resolves the full
                # filename later, and pre-tracking here would skip the selected file
                # before is_selected_initial can allow its first transfer.

        selected_baseline = max(selected_season_episodes, default=(0, 0))
        baseline_episode = (
            selected_baseline[1]
            if selected_baseline[0] > 0
            else max([start_ep, *selected_episode_numbers], default=start_ep)
        )

        subscription = {
            "id": uuid.uuid4().hex,
            "title": title,
            "media_type": str(media_type or "").strip().lower()[:40],
            "tag": str(tag or "").strip()[:40],
            "pwd_id": legacy_pwd_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
            "source_folder_path": "/".join(part for part in str(source_folder_path or "").replace("\\", "/").split("/") if part and part not in {".", ".."})[:1000],
            "target_fid": target_fid,
            "storage_target_id": str(storage_target_id or "").strip(),
            "interval_hours": interval_hours,
            "start_ep": baseline_episode,
            "start_season": selected_baseline[0],
            "channel": str(
                channel or ""
            ).strip()[:100],
            "channel_name": str(
                channel_name or channel or ""
            ).strip()[:100],
            "source_id": str(source_id or "").strip()[:100],
            "cover": str(cover or "").strip()[:1000],
            "tracking_mode": (
                "channel"
                if source_id and channel
                else ("all" if target_fids else "legacy")
            ),
            "status": "waiting",
            "phase": "waiting",
            "phase_label": "等待下次检查",
            "tracked_file_keys": (
                []
                if source_id and channel
                else target_fids
            ),
            "initial_file_keys": target_fids,
            "files": [],
            "saved_episodes": [],
            "saved_episode_sources": {},
            "last_check": "从未检测",
            "last_check_at": None,
            "next_run_at": time.time(),
            "last_error": "",
            "retry_count": 0,
            "pending_save_keys": [],
            "run_history": [],
            "schema_version": SUBSCRIPTION_SCHEMA_VERSION,
        }

        with self.lock:
            subscriptions = self._load_subscriptions()

            subscription["schema_version"] = SUBSCRIPTION_SCHEMA_VERSION
            subscription["pending_save_keys"] = []

            # Repeated clicks on the same channel/show/destination should reuse
            # the existing long-running subscription instead of creating a fresh
            # history that can transfer the same episodes again.
            if subscription["source_id"] and subscription["channel"]:
                existing = next(
                    (
                        item for item in subscriptions
                        if str(item.get("source_id") or "").strip()
                        == subscription["source_id"]
                        and str(item.get("channel") or "").strip().casefold()
                        == subscription["channel"].casefold()
                        and str(item.get("title") or "").strip().casefold()
                        == subscription["title"].casefold()
                        and str(item.get("storage_target_id") or "").strip()
                        == subscription["storage_target_id"]
                        and str(item.get("target_fid") or "0").strip()
                        == subscription["target_fid"]
                    ),
                    None,
                )
                if existing is not None:
                    return dict(existing)

            subscriptions.append(
                subscription
            )

            self.store.write(
                subscriptions
            )

        return subscription

    def delete_subscription(
        self,
        sub_id: str,
    ) -> bool:
        sub_id = str(sub_id)
        with self.lock:
            if sub_id in self.running_ids:
                return False
            subscriptions = self._load_subscriptions()

            new_subscriptions = [
                item
                for item in subscriptions
                if item.get("id")
                != str(sub_id)
            ]

            changed = (
                len(new_subscriptions)
                != len(subscriptions)
            )

            if changed:
                self.store.write(
                    new_subscriptions
                )

            return changed

    def clear_run_history(self) -> int:
        with self.lock:
            subscriptions = self._load_subscriptions()
            removed = 0
            changed = False
            for item in subscriptions:
                history = item.get("run_history") or []
                if history:
                    removed += len(history)
                    item["run_history"] = []
                    changed = True
            if changed:
                self.store.write(subscriptions)
            return removed

    def check_subscription_now(
        self,
        sub_id: str,
        *,
        only_if_due: bool = False,
    ) -> tuple[bool, str]:
        sub_id = str(sub_id)

        # Claim the subscription atomically with reading its persisted state.
        # Otherwise delete_subscription() can remove it between lookup and
        # running_ids registration while this worker continues with stale data.
        with self.lock:
            subscriptions = self._load_subscriptions()
            subscription = next(
                (
                    item
                    for item in subscriptions
                    if item.get("id") == sub_id
                ),
                None,
            )

            if not subscription:
                return False, "未找到订阅任务"

            if subscription.get("pending_save_uncertain"):
                if subscription.get("pending_save_keys_invalid"):
                    return (
                        False,
                        "待确认转存记录格式异常，请检查目标网盘并修复订阅记录后再重试。",
                    )
                if subscription.get("pending_save_keys"):
                    return (
                        False,
                        "转存结果待核实：请先在任务中心确认已转存，或确认未转存后再允许重试。",
                    )
                return (
                    False,
                    "转存结果待核实但缺少文件记录；请检查目标网盘，确认未转存后再选择“确认未转存”以解除保护。",
                )

            # The scheduler iterates a snapshot that can become stale while
            # another manual check or confirmation updates next_run_at.
            # Recheck the persisted deadline atomically when claiming a run.
            if only_if_due:
                try:
                    next_run_at = float(subscription.get("next_run_at", 0) or 0)
                except (TypeError, ValueError):
                    next_run_at = 0
                if next_run_at > time.time():
                    return False, "订阅尚未到计划检查时间"

            if sub_id in self.running_ids:
                return False, "该订阅正在执行，请稍后再试"

            self.running_ids.add(sub_id)

        try:
            # Keep the initial status write inside the guarded lifecycle so
            # running_ids is always cleared, even if persistence fails here.
            self._set_status(
                subscription,
                "running",
                "searching",
                "正在搜刮频道资源…",
            )
            return self._check(
                subscription
            )

        except Exception as exc:
            self.logger.exception(
                "订阅任务 %s 执行异常",
                sub_id,
            )
            # Pending keys are persisted before remote transfer calls, so an
            # exception while keys remain pending has an ambiguous remote outcome.
            current = next(
                (item for item in self.get_subscriptions() if item.get("id") == sub_id),
                {},
            )
            has_pending_transfer = bool(current.get("pending_save_keys"))
            # The normalized key list can be empty when its persisted form is
            # corrupt. Preserve either uncertainty marker so _finish cannot
            # accidentally clear the retry guard after an unrelated exception.
            pending_state_uncertain = bool(
                current.get("pending_save_uncertain")
                or current.get("pending_save_keys_invalid")
            )
            uncertain = (
                has_pending_transfer
                or pending_state_uncertain
                or is_uncertain_transfer_message(exc)
            )
            safe_message = (
                "转存结果不确定，请检查目标网盘后再决定是否重试"
                if uncertain
                else "任务执行异常，请查看服务日志"
            )
            return self._finish(
                subscription,
                False,
                safe_message,
                outcome_uncertain=uncertain,
            )

        finally:
            with self.lock:
                self.running_ids.discard(
                    sub_id
                )

    def _set_status(self, sub: dict, status: str, phase: str, phase_label: str) -> None:
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next(
                (item for item in subscriptions if item.get("id") == sub.get("id")),
                None,
            )
            if current is None:
                return
            current["status"] = status
            current["phase"] = phase
            current["phase_label"] = phase_label
            if status == "running":
                current["last_error"] = ""
            self.store.write(subscriptions)
        self._persist_diagnostics_snapshot(sub, phase=phase)

    def _persist_diagnostics_snapshot(self, sub: dict, *, phase: str | None = None) -> None:
        """Persist bounded diagnostics while a run is active."""
        diagnostics = sub.get("_run_diagnostics")
        if not isinstance(diagnostics, dict):
            return

        now = time.time()
        diagnostics.setdefault("started_at", now)
        diagnostics["updated_at"] = now
        if phase:
            diagnostics["phase"] = str(phase)[:40]

        snapshot = deepcopy(diagnostics)
        snapshot.pop("finished_at", None)
        snapshot["success"] = None
        snapshot["interrupted"] = False
        current_phase = str(phase or snapshot.get("phase") or "running")
        phase_labels = {
            "starting": "准备检查",
            "searching": "搜刮资源",
            "resolve": "解析资源与文件列表",
            "scan": "扫描并识别文件",
            "transfer": "转存新内容",
        }
        snapshot["message"] = "检查进行中：" + phase_labels.get(current_phase, current_phase)

        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next(
                (item for item in subscriptions if item.get("id") == sub.get("id")),
                None,
            )
            if current is None:
                return
            current["last_diagnostics"] = snapshot
            self.store.write(subscriptions)

    def _parse_episode_for_subscription(self, sub: dict, file_name: str) -> int | None:
        """优先使用订阅所属资源卡片的集数规则，避免忽略卡片独立配置。"""
        source_id = str(sub.get("source_id") or "").strip()
        parser = getattr(self.resource_sources, "parse_tv_episode", None)
        if source_id and callable(parser):
            try:
                parsed = parser(source_id, file_name)
                if isinstance(parsed, (tuple, list)) and len(parsed) == 2:
                    episode = parsed[1]
                    if episode is None or (
                        isinstance(episode, int) and not isinstance(episode, bool)
                    ):
                        return episode
            except Exception:
                if self.logger is not None:
                    self.logger.exception(
                        "资源卡片 %s 的集数解析失败，改用通用规则",
                        source_id,
                    )
        return _parse_tv_episode(file_name)[1]

    @staticmethod
    def _new_run_diagnostics() -> dict:
        """Create a consistent diagnostic shape for every subscription mode."""
        return {
            "schema_version": 1,
            "source_candidates": 0,
            "unique_shares": 0,
            "resolved_shares": 0,
            "resolution_failures": 0,
            "files_scanned": 0,
            "video_files": 0,
            "non_video_files": 0,
            "missing_file_id": 0,
            "invalid_file_entries": 0,
            "invalid_source_entries": 0,
            "duplicate_source_entries": 0,
            "source_search_errors": 0,
            "initial_files_missing": 0,
            "already_saved": 0,
            "already_tracked": 0,
            "unrecognized": 0,
            "filtered_old": 0,
            "duplicate_episodes": 0,
            "queued_files": 0,
            "transferred_files": 0,
            "shares": [],
            "observed_episodes": [],
            "episode_observations_truncated": False,
            "source_scan_complete": False,
            "examples": [],
        }

    def _check(
        self,
        sub: dict,
    ) -> tuple[bool, str]:
        # Every subscription path must initialize diagnostics before doing work.
        # The channel-based path adds detailed counters; legacy/direct-share
        # subscriptions still need a persisted summary instead of an empty panel.
        sub["_run_diagnostics"] = self._new_run_diagnostics()
        self._persist_diagnostics_snapshot(sub, phase="starting")
        # Channel/source metadata is the durable signal for channel-based Smart Follow.
        # Older or migrated records may still carry tracking_mode="all"; letting those
        # records fall through to the generic branch bypasses season/episode filtering
        # and can queue every video in a share. Always use the guarded channel path when
        # both channel identity fields are present, regardless of the legacy mode value.
        if sub.get("source_id") and sub.get("channel"):
            return self._check_channel_subscription(sub)

        resource = {
            **({"pwd_id": str(sub.get("pwd_id") or "").strip()} if sub.get("pwd_id") else {}),
            "resource_id": str(sub.get("resource_id") or sub.get("pwd_id") or "").strip(),
            "resource_type": str(sub.get("resource_type") or "").strip().lower(),
            "storage_target_id": str(sub.get("storage_target_id") or "").strip(),
        }
        diagnostics = sub["_run_diagnostics"]
        diagnostics["source_candidates"] = 1
        diagnostics["unique_shares"] = 1
        self._set_status(sub, "running", "resolve", "正在解析分享与文件列表…")
        try:
            resolved = self.storage_targets.resolve_resource(
                resource,
                resource.get("storage_target_id") or None,
            )
        except Exception as exc:
            diagnostics["resolution_failures"] = 1
            diagnostics["examples"].append({
                "file": resource.get("resource_id") or resource.get("pwd_id") or "当前分享",
                "reason": f"资源解析异常：{type(exc).__name__}: {str(exc)[:160]}",
            })
            self.logger.exception("自动追剧资源解析异常")
            return self._finish(sub, False, "解析链接失败：资源卡片执行异常，请查看服务日志")

        if not isinstance(resolved, dict):
            diagnostics["resolution_failures"] = 1
            diagnostics["examples"].append({
                "file": resource.get("resource_id") or resource.get("pwd_id") or "当前分享",
                "reason": f"资源卡片返回 {type(resolved).__name__}，预期为对象",
            })
            return self._finish(sub, False, "解析链接失败：资源卡片返回格式异常")

        files = resolved.get("files")
        if not isinstance(files, list):
            diagnostics["resolution_failures"] = 1
            diagnostics["examples"].append({
                "file": resource.get("resource_id") or resource.get("pwd_id") or "当前分享",
                "reason": f"文件列表返回 {type(files).__name__}，预期为列表",
            })
            return self._finish(sub, False, "解析链接失败：文件列表格式异常")
        source_folder_path = str(sub.get("source_folder_path") or "").strip().strip("/")
        if source_folder_path:
            files = [
                item for item in files
                if isinstance(item, dict)
                and (
                    str(item.get("relative_path") or item.get("relative_dir") or item.get("folder_path") or "").replace("\\", "/").strip("/") == source_folder_path
                    or str(item.get("relative_path") or item.get("relative_dir") or item.get("folder_path") or "").replace("\\", "/").strip("/").startswith(source_folder_path + "/")
                )
            ]
            if not files:
                diagnostics["examples"].append({
                    "file": source_folder_path,
                    "reason": "所选来源文件夹中没有可扫描的文件；已阻止回退到其他文件夹",
                })
        fetched_stoken = resolved.get("token")
        target_id = str(
            resolved.get("target_id")
            or resource.get("storage_target_id")
            or ""
        ).strip()
        err = resolved.get("error")

        if err or not files or not fetched_stoken:
            diagnostics["resolution_failures"] = 1
            if err:
                diagnostics["examples"].append({
                    "file": resource.get("resource_id") or resource.get("pwd_id") or "当前分享",
                    "reason": str(err)[:200],
                })
            return self._finish(sub, False, f"解析链接失败: {err or '未找到文件'}")

        diagnostics["resolved_shares"] = 1
        diagnostics["files_scanned"] = 0
        for item in files:
            diagnostics["files_scanned"] += 1
            if diagnostics["files_scanned"] % 25 == 0:
                self._persist_diagnostics_snapshot(sub, phase="scan")
            if not isinstance(item, dict):
                diagnostics["invalid_file_entries"] += 1
                if len(diagnostics["examples"]) < 20:
                    diagnostics["examples"].append({
                        "file": resource.get("resource_id") or "当前分享",
                        "reason": f"忽略非字典文件条目：{type(item).__name__}",
                    })
                continue
            file_name = str(item.get("file_name") or item.get("name") or "").strip()
            fid = str(item.get("fid") or "").strip()
            if file_name.lower().endswith(VIDEO_EXTENSIONS):
                parsed_season, parsed_episode = _parse_source_episode(
                    self.resource_sources, str(sub.get("source_id") or ""), file_name
                )
                if parsed_episode is not None:
                    observed = diagnostics.setdefault("observed_episodes", [])
                    marker = {"season": parsed_season, "episode": parsed_episode}
                    if marker not in observed:
                        if len(observed) < 1000:
                            observed.append(marker)
                        else:
                            diagnostics["episode_observations_truncated"] = True
                diagnostics["video_files"] += 1
            else:
                diagnostics["non_video_files"] += 1
                if len(diagnostics["examples"]) < 20:
                    diagnostics["examples"].append({
                        "file": file_name or "(无文件名)",
                        "reason": "非视频文件，未进入追剧识别",
                    })
            if not fid:
                diagnostics["missing_file_id"] += 1
                if len(diagnostics["examples"]) < 20:
                    diagnostics["examples"].append({
                        "file": file_name or "(无文件名)",
                        "reason": "文件条目缺少 fid，无法追踪或转存",
                    })

        diagnostics["source_scan_complete"] = bool(getattr(files, "scan_complete", True))
        self._persist_diagnostics_snapshot(sub, phase="scan")
        tracked_keys = {
            str(key).strip()
            for key in (sub.get("tracked_file_keys", []) or [])
            if str(key).strip()
        }
        selected = []
        found_keys = []
        selected_fids = set()
        if sub.get("tracking_mode") == "all":
            # An explicitly selected initial file is a transfer request, not
            # merely a tracking watermark. Older records may already contain its
            # FID in tracked_file_keys before the first confirmed transfer.
            initial_file_keys = {
                str(key).strip()
                for key in (sub.get("initial_file_keys", []) or [])
                if str(key).strip()
            }
            saved_keys = {
                str(key).strip()
                for key in (sub.get("saved_episodes", []) or [])
                if str(key).strip()
            }
            baseline_episode = max(0, int(sub.get("start_ep") or 0))
            baseline_season = max(0, int(sub.get("start_season") or 0))
            source_id = str(sub.get("source_id") or "").strip()

            # Older callers may submit selected FIDs without filenames. Resolve
            # the selected files from the current listing before deciding which
            # later episodes are eligible; otherwise a missing baseline would
            # either dump the share or suppress every later episode.
            selected_baselines = []
            for initial_item in files:
                if not isinstance(initial_item, dict):
                    continue
                initial_fid = str(initial_item.get("fid") or "").strip()
                if initial_fid not in initial_file_keys:
                    continue
                initial_name = str(
                    initial_item.get("file_name") or initial_item.get("name") or ""
                ).strip()
                initial_season, initial_episode = _parse_source_episode(
                    self.resource_sources,
                    source_id,
                    initial_name,
                )
                if initial_episode is not None:
                    selected_baselines.append((
                        initial_season if initial_season is not None else 0,
                        initial_episode,
                    ))
            known_season_baselines = [
                item for item in selected_baselines if item[0] > 0
            ]
            if known_season_baselines:
                baseline_season, baseline_episode = max(known_season_baselines)
            elif selected_baselines and baseline_episode <= 0:
                baseline_episode = max(item[1] for item in selected_baselines)

            for item in files:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("fid") or "").strip()
                file_name = str(item.get("file_name") or item.get("name") or "").strip()
                is_initial = fid in initial_file_keys
                # Auto-follow only queues identifiable video files. An explicitly
                # selected FID may omit its filename, but known non-video files are
                # never transferred by the automatic scan.
                if not fid or (file_name and not file_name.lower().endswith(VIDEO_EXTENSIONS)):
                    continue
                if not file_name and not is_initial:
                    continue
                already_saved = fid in saved_keys
                if not fid or already_saved or fid in selected_fids:
                    if already_saved:
                        diagnostics["already_saved"] += 1
                    if fid in selected_fids:
                        diagnostics["already_tracked"] += 1
                    continue

                # The selected initial file is the inclusive baseline: transfer
                # it once, then only episodes after it. Generic/all-mode tasks
                # must not turn a single selected episode into a whole-share dump.
                if not is_initial:
                    season, episode = _parse_source_episode(
                        self.resource_sources,
                        source_id,
                        file_name,
                    )
                    if (
                        episode is None
                        or baseline_episode <= 0
                        or (baseline_season > 0 and season is None)
                    ):
                        diagnostics["unrecognized"] += 1
                        if len(diagnostics["examples"]) < 20:
                            diagnostics["examples"].append({
                                "file": file_name or fid,
                                "reason": "无法可靠识别季集或追剧起点，未自动转存",
                            })
                        continue
                    if baseline_season > 0:
                        if (season, episode) <= (baseline_season, baseline_episode):
                            diagnostics["filtered_old"] += 1
                            continue
                    elif episode <= baseline_episode:
                        diagnostics["filtered_old"] += 1
                        continue

                if fid not in tracked_keys or is_initial:
                    selected.append({"fid": fid})
                    found_keys.append(fid)
                    selected_fids.add(fid)
                else:
                    diagnostics["already_tracked"] += 1
        elif sub.get("files"):
            saved = {
                item for item in (sub.get("saved_episodes", []) or [])
                if isinstance(item, (str, int))
            }
            target_fids = {
                str(item.get("fid"))
                for item in (sub.get("files") or [])
                if isinstance(item, dict) and item.get("fid")
            }
            for item in files:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("fid") or "").strip()
                file_name = str(item.get("file_name") or item.get("name") or "").strip()
                if file_name and not file_name.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                if fid and fid in target_fids and fid not in saved and fid not in selected_fids:
                    selected.append({"fid": fid})
                    found_keys.append(fid)
                    selected_fids.add(fid)
        else:
            saved = {
                item for item in (sub.get("saved_episodes", []) or [])
                if isinstance(item, (str, int))
            }
            start_ep = int(sub.get("start_ep", 0) or 0)
            start_season = max(0, int(sub.get("start_season", 0) or 0))
            source_id = str(sub.get("source_id") or "").strip()
            for item in files:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("fid") or "").strip()
                file_name = str(item.get("file_name") or item.get("name") or "").strip()
                if not fid or not file_name.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                season, ep_num = _parse_source_episode(
                    self.resource_sources,
                    source_id,
                    file_name,
                )
                if start_season > 0:
                    is_after_baseline = (
                        season is not None
                        and ep_num is not None
                        and (season, ep_num) > (start_season, start_ep)
                    )
                    if ep_num is not None and season is None:
                        diagnostics["unrecognized"] += 1
                        if len(diagnostics["examples"]) < 20:
                            diagnostics["examples"].append({
                                "file": file_name,
                                "reason": "文件名缺少可确认的季数，无法安全判断追剧起点，未自动转存",
                            })
                        continue
                else:
                    is_after_baseline = ep_num is not None and ep_num > start_ep

                if ep_num is None:
                    diagnostics["unrecognized"] += 1
                    if len(diagnostics["examples"]) < 20:
                        diagnostics["examples"].append({
                            "file": file_name,
                            "reason": "无法识别集数，未自动转存",
                        })
                    continue
                if ep_num in saved:
                    diagnostics["already_saved"] += 1
                    continue
                if not is_after_baseline:
                    diagnostics["filtered_old"] += 1
                    continue
                if fid in selected_fids:
                    diagnostics["already_tracked"] += 1
                    continue

                selected.append({"fid": fid})
                found_keys.append(ep_num)
                selected_fids.add(fid)

        diagnostics["queued_files"] = len(selected)
        self._persist_diagnostics_snapshot(sub, phase="scan")
        if not selected:
            if diagnostics["invalid_file_entries"] or diagnostics["missing_file_id"]:
                return self._finish(
                    sub,
                    False,
                    "文件列表存在格式异常或缺少文件 ID，未能完整检查；请检查存储卡片后重试",
                    success_keys=[],
                )
            return self._finish(sub, True, f"《{sub.get('title', '')}》暂无新更新", success_keys=[])

        target_fid = _normalize_fid(sub.get("target_fid"))
        try:
            folder_fid = self.storage_targets.create_folder(
                str(sub.get("title", "")),
                target_fid,
                target_id or None,
            )
        except Exception:
            self.logger.exception("自动追剧创建专属文件夹失败: %s", sub.get("title", ""))
            return self._finish(sub, False, "创建专属文件夹失败，请检查存储目标配置或查看服务日志")

        pending_keys = list(dict.fromkeys(str(key) for key in found_keys))[:200]
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next((item for item in subscriptions if item.get("id") == sub.get("id")), None)
            if current is not None:
                current["pending_save_keys"] = pending_keys
                current["pending_save_uncertain"] = False
                current["storage_target_id"] = target_id
                self.store.write(subscriptions)

        self._set_status(sub, "running", "transfer", "正在转存新内容…")
        ok, msg = self.storage_targets.transfer(
            resource,
            selected,
            folder_fid,
            target_id or None,
            fetched_stoken,
        )
        if not ok:
            self.logger.warning("自动追剧转存失败: %s", msg)
            uncertain = is_uncertain_transfer_message(msg)
            safe_message = (
                "转存结果不确定，请检查目标网盘后再决定是否重试"
                if uncertain
                else "转存失败，请检查存储目标配置或查看服务日志"
            )
            return self._finish(
                sub,
                False,
                safe_message,
                outcome_uncertain=uncertain,
            )

        diagnostics["transferred_files"] = len(selected)
        return self._finish(
            sub,
            True,
            f"🎉 成功追更 {len(found_keys)} 项，已存入专属文件夹【{sub.get('title', '')}】！",
            success_keys=found_keys,
        )

    def _save_channel_scan_cursor(self, sub: dict, cursor: object) -> None:
        """Persist only a confirmed scan watermark, never a partial/error result."""
        try:
            value = int(cursor)
        except (TypeError, ValueError, OverflowError):
            return
        if value <= 0:
            return
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next(
                (item for item in subscriptions if item.get("id") == sub.get("id")),
                None,
            )
            if current is None:
                return
            previous = current.get("channel_scan_cursor")
            try:
                previous_value = int(previous or 0)
            except (TypeError, ValueError, OverflowError):
                previous_value = 0
            # Telegram post IDs increase over time; never move the watermark back.
            if value > previous_value:
                current["channel_scan_cursor"] = value
                self.store.write(subscriptions)

    def _save_channel_history_cursor(self, sub: dict, cursor: object, complete: bool) -> None:
        """Persist older-history progress only after the batch was handled safely."""
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next((item for item in subscriptions if item.get("id") == sub.get("id")), None)
            if current is None:
                return
            changed = False
            if cursor is not None:
                try:
                    value = int(cursor)
                except (TypeError, ValueError, OverflowError):
                    value = 0
                if value > 0:
                    previous = current.get("channel_history_before")
                    try:
                        previous_value = int(previous) if previous is not None else None
                    except (TypeError, ValueError, OverflowError):
                        previous_value = None
                    # Backfill moves toward older (smaller) Telegram post IDs.
                    if previous_value is None or value < previous_value:
                        current["channel_history_before"] = value
                        changed = True
            if complete and not current.get("channel_history_complete"):
                current["channel_history_complete"] = True
                changed = True
            if changed:
                self.store.write(subscriptions)

    def _check_channel_subscription(self, sub: dict) -> tuple[bool, str]:
        source_id = str(sub.get("source_id") or "").strip()
        channel = str(sub.get("channel") or "").strip()
        title = str(sub.get("title") or "").strip()
        diagnostics = sub.get("_run_diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = self._new_run_diagnostics()
            diagnostics["started_at"] = time.time()
            sub["_run_diagnostics"] = diagnostics
            self._persist_diagnostics_snapshot(sub, phase="starting")
        self._set_status(sub, "running", "searching", "正在搜刮频道资源…")
        scan_cursor = None
        scan_complete = False
        history_cursor = None
        history_complete = False
        history_state = {"items": [], "cursor": None, "complete": False}
        sources = []
        try:
            if self.resource_sources:
                incremental_search = getattr(self.resource_sources, "search_channel_updates", None)
                if callable(incremental_search):
                    scan_state = incremental_search(source_id, channel, title, sub.get("channel_scan_cursor"))
                    if isinstance(scan_state, dict):
                        sources = scan_state.get("items", [])
                        scan_cursor = scan_state.get("cursor")
                        scan_complete = bool(scan_state.get("complete"))
                    else:
                        diagnostics["source_search_errors"] += 1
                        diagnostics["examples"].append({
                            "file": "频道增量搜索",
                            "reason": "资源卡片返回了非预期的数据结构",
                        })
                else:
                    sources = self.resource_sources.search_channel(source_id, channel, title)
        except Exception as exc:
            diagnostics["source_search_errors"] += 1
            diagnostics["examples"].append({
                "file": "频道资源搜索",
                "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
            })
            self.logger.exception("智能追剧频道搜索失败，source_id=%s channel=%s", source_id, channel)
            scan_complete = False

        if self.resource_sources and not sub.get("channel_history_complete"):
            history_search = getattr(self.resource_sources, "search_channel_history", None)
            if callable(history_search):
                try:
                    history_before = sub.get("channel_history_before")
                    if history_before is None:
                        history_before = scan_cursor or sub.get("channel_scan_cursor")
                    history_state = history_search(source_id, channel, title, history_before)
                    if isinstance(history_state, dict):
                        history_cursor = history_state.get("cursor")
                        history_complete = bool(history_state.get("complete"))
                        history_items = history_state.get("items", [])
                        if isinstance(history_items, list):
                            sources = [*(sources if isinstance(sources, list) else []), *history_items]
                        else:
                            diagnostics["source_search_errors"] += 1
                            diagnostics["examples"].append({
                                "file": "频道历史搜索",
                                "reason": "资源卡片返回了非列表 items",
                            })
                    else:
                        diagnostics["source_search_errors"] += 1
                        diagnostics["examples"].append({
                            "file": "频道历史搜索",
                            "reason": "资源卡片返回了非预期的数据结构",
                        })
                except Exception as exc:
                    diagnostics["source_search_errors"] += 1
                    diagnostics["examples"].append({
                        "file": "频道历史搜索",
                        "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
                    })
                    self.logger.exception("智能追剧频道历史搜索失败，source_id=%s channel=%s", source_id, channel)
                    history_complete = False

        if not isinstance(sources, list):
            diagnostics["source_search_errors"] += 1
            diagnostics["examples"].append({
                "file": "频道资源搜索",
                "reason": f"资源卡片返回 {type(sources).__name__}，预期为列表",
            })
            sources = []
            scan_complete = False
        diagnostics["source_candidates"] = len(sources)

        # 标题搜索可能因频道帖子使用英文名、旧消息超出扫描范围等原因返回空列表。
        # 用户创建订阅时选中的分享是可靠的起点，至少先检查该分享中的后续集数。
        selected_pwd_id = str(sub.get("resource_id") or sub.get("pwd_id") or "").strip()
        selected_resource_type = str(sub.get("resource_type") or "").strip().lower()
        selected_target_id = str(sub.get("storage_target_id") or "").strip()
        if selected_pwd_id and not any(
            str(item.get("resource_id") or item.get("share_id") or item.get("pwd_id") or "").strip() == selected_pwd_id
            and str(item.get("resource_type") or selected_resource_type).strip().lower() == selected_resource_type
            and str(item.get("storage_target_id") or selected_target_id).strip() == selected_target_id
            for item in sources
            if isinstance(item, dict)
        ):
            sources = [
                {
                    **({"pwd_id": str(sub.get("pwd_id") or "").strip()} if sub.get("pwd_id") else {}),
                    "resource_id": selected_pwd_id,
                    "resource_type": str(sub.get("resource_type") or "").strip().lower(),
                    "channel": channel,
                    "channel_name": str(sub.get("channel_name") or channel),
                    "source_id": source_id,
                    "storage_target_id": selected_target_id,
                },
                *sources,
            ]
            diagnostics["examples"].append({
                "file": selected_pwd_id,
                "reason": "频道搜索结果未包含初选分享，已补入初选分享进行检查",
            })

        # A share ID can exist under multiple storage-target cards. Deduplicate
        # repeated scan results only when both the share and target identity match.
        unique_sources = []
        seen_source_keys = set()
        for item in sources:
            if not isinstance(item, dict):
                diagnostics["invalid_source_entries"] += 1
                diagnostics["examples"].append({
                    "file": "频道搜索结果",
                    "reason": f"忽略非字典资源条目：{type(item).__name__}",
                })
                continue
            item_resource_id = str(item.get("resource_id") or item.get("share_id") or item.get("pwd_id") or "").strip()
            item_resource_type = str(item.get("resource_type") or "").strip().lower()
            item_target_id = str(item.get("storage_target_id") or selected_target_id).strip()
            source_key = (item_resource_type, item_resource_id, item_target_id)
            if not item_resource_id:
                diagnostics["invalid_source_entries"] += 1
                diagnostics["examples"].append({"file": "频道搜索结果", "reason": "资源条目缺少分享 ID"})
                continue
            if source_key in seen_source_keys:
                diagnostics["duplicate_source_entries"] += 1
                continue
            seen_source_keys.add(source_key)
            unique_sources.append(item)
        # 优先检查用户最初选中的分享：当多个分享重复包含同一集时，
        # 以用户已选资源为优先，避免后续频道重复转存同一集。
        sources = sorted(
            unique_sources,
            key=lambda item: (
                str(item.get("resource_id") or item.get("share_id") or item.get("pwd_id") or "").strip()
                != selected_pwd_id,
            ),
        )
        diagnostics["unique_shares"] = len(sources)

        if not sources:
            if scan_complete:
                self._save_channel_scan_cursor(sub, scan_cursor)
            self._save_channel_history_cursor(sub, history_cursor, history_complete)
            return self._finish(sub, True, f"《{title}》频道暂未发现新资源", success_keys=[])

        tracked = {
            str(key).strip()
            for key in (sub.get("tracked_file_keys", []) or [])
            if str(key).strip()
        }
        # tracked_file_keys is intentionally bounded. Keep the durable success
        # history as a fallback so older confirmed files do not become eligible
        # for transfer again after they fall out of that bounded index.
        saved_episodes = sub.get("saved_episodes", [])
        saved_episode_keys = {
            str(key).strip()
            for key in (saved_episodes if isinstance(saved_episodes, list) else [])
            if str(key).strip()
        }
        tracked.update(saved_episode_keys)
        saved_episode_sources = sub.get("saved_episode_sources", {})
        if not isinstance(saved_episode_sources, dict):
            saved_episode_sources = {}
        # The user-selected files are the initial transfer request, not just a
        # tracking watermark. Transfer them once even when they equal the baseline.
        # Older versions incorrectly pre-marked selected files whose names could
        # not be parsed as tracked. Keep respecting that historical state for every
        # other file, but do not let an unconfirmed legacy marker suppress an
        # explicitly selected initial file. Confirmed-success history remains final.
        initial_file_keys = {
            str(fid).strip()
            for fid in (sub.get("initial_file_keys", []) or [])
            if str(fid).strip()
        }
        selected_pwd_id = str(sub.get("resource_id") or sub.get("pwd_id") or "").strip()
        if initial_file_keys:
            self.logger.info(
                "智能追剧初选诊断：标题=%s，初选分享=%s，初选文件数=%d，初选FID=%s，已保存记录数=%d，已追踪记录数=%d",
                title,
                selected_pwd_id,
                len(initial_file_keys),
                ",".join(sorted(initial_file_keys)[:20]),
                len(saved_episode_keys),
                len(tracked),
            )
        baseline_episode = int(sub.get("start_ep", 0) or 0)
        baseline_season = int(sub.get("start_season", 0) or 0)
        grouped: dict[tuple[str, str, str], dict] = {}
        # A file can legitimately be copied to multiple storage targets. Queue
        # identity therefore includes the destination, while persisted legacy
        # keys remain compatible for the subscription's originally selected target.
        queued_keys: set[tuple[str, str, str, str]] = set()
        # Episode identity is separate from share/file identity. Channels often
        # publish overlapping ranges (for example 1-10 and 8-20); only queue an
        # episode once across different shares, while preserving multi-part files
        # from the same share and same-number episodes from different seasons.
        queued_episode_sources: dict[tuple[str, int | None, int | None], tuple[str, str, str]] = {}
        resolution_errors: list[str] = []

        self._set_status(sub, "running", "resolve", "正在并发解析发现的资源…")

        def resolve_source(source):
            resource_id = str(
                source.get("resource_id") or source.get("share_id") or source.get("pwd_id") or ""
            ).strip()
            if not resource_id:
                return source, None, None

            resource_type = str(source.get("resource_type") or "").strip().lower()
            resource = {
                **source,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "storage_target_id": str(
                    source.get("storage_target_id")
                    or sub.get("storage_target_id")
                    or ""
                ).strip(),
            }
            try:
                resolved = self.storage_targets.resolve_resource(
                    resource,
                    resource.get("storage_target_id") or None,
                )
            except Exception as exc:
                self.logger.warning(
                    "自动追剧资源解析异常，资源 ID=%s，错误类型=%s",
                    resource_id,
                    type(exc).__name__,
                )
                resolved = {
                    "error": "资源解析异常",
                    "files": [],
                    "token": None,
                }
            return source, resource, resolved

        # 分享解析主要受网盘网络请求延迟影响。限制并发数，按原始顺序消费结果，
        # 保留后续集数去重、用户选中分享优先级与转存顺序的既有行为。
        with ThreadPoolExecutor(max_workers=min(4, len(sources))) as executor:
            resolved_sources = list(executor.map(resolve_source, sources))

        for _source, resource, resolved in resolved_sources:
            share_diag = None
            if resource is None:
                diagnostics["resolution_failures"] += 1
                diagnostics["examples"].append({
                    "file": "频道搜索结果",
                    "reason": "资源条目缺少分享 ID，无法解析",
                })
                diagnostics["shares"].append({
                    "resource_id": "",
                    "resource_type": "",
                    "status": "无效资源",
                    "files_scanned": 0,
                    "video_files": 0,
                    "queued_files": 0,
                    "skipped_files": 0,
                    "error": "资源条目缺少分享 ID",
                })
                continue
            resource_id = resource["resource_id"]
            resource_type = resource["resource_type"]
            if not isinstance(resolved, dict):
                resolved = {"error": "资源解析返回格式无效", "files": [], "token": None}
            files = resolved.get("files") or []
            source_folder_path = str(sub.get("source_folder_path") or "").strip().strip("/")
            if source_folder_path and isinstance(files, list):
                original_file_count = len(files)
                files = [
                    item for item in files
                    if isinstance(item, dict)
                    and (
                        str(item.get("relative_path") or item.get("relative_dir") or item.get("folder_path") or "").replace("\\", "/").strip("/") == source_folder_path
                        or str(item.get("relative_path") or item.get("relative_dir") or item.get("folder_path") or "").replace("\\", "/").strip("/").startswith(source_folder_path + "/")
                    )
                ]
                if not files:
                    diagnostics["examples"].append({
                        "file": f"{resource_id}/{source_folder_path}",
                        "reason": f"所选来源文件夹未匹配到文件（原分享文件数 {original_file_count}）；已阻止扫描其他文件夹",
                    })
            token = resolved.get("token")
            target_id = str(
                resolved.get("target_id")
                or resource.get("storage_target_id")
                or ""
            ).strip()
            if resolved.get("error") or not files or not token:
                reason = str(resolved.get("error") or "未获取到文件列表或分享令牌").strip()
                self.logger.warning("自动追剧资源解析失败，资源 ID=%s，原因=%s", resource_id, reason)
                resolution_errors.append(f"{resource_id}: 解析失败")
                diagnostics["resolution_failures"] += 1
                diagnostics["examples"].append({"file": resource_id, "reason": reason[:200]})
                diagnostics["shares"].append({
                    "resource_id": resource_id[:120],
                    "resource_type": resource_type[:60],
                    "status": "解析失败",
                    "files_scanned": 0,
                    "video_files": 0,
                    "queued_files": 0,
                    "skipped_files": 0,
                    "error": reason[:200],
                })
                continue

            diagnostics["resolved_shares"] += 1
            share_diag = {
                "resource_id": resource_id[:120],
                "resource_type": resource_type[:60],
                "status": "已解析",
                "files_scanned": 0,
                "video_files": 0,
                "queued_files": 0,
                "skipped_files": 0,
                "episodes": [],
                "error": "",
            }
            if len(diagnostics["shares"]) < 50:
                diagnostics["shares"].append(share_diag)
            if not isinstance(files, list):
                diagnostics["resolution_failures"] += 1
                reason = "资源解析返回的文件列表不是列表"
                share_diag = {
                    "resource_id": resource_id[:120],
                    "resource_type": resource_type[:60],
                    "status": "文件列表格式异常",
                    "files_scanned": 0,
                    "video_files": 0,
                    "queued_files": 0,
                    "skipped_files": 0,
                    "error": reason,
                }
                if len(diagnostics["shares"]) < 50:
                    diagnostics["shares"].append(share_diag)
                diagnostics["examples"].append({"file": resource_id, "reason": reason})
                continue
            file_fids = {
                str(file_item.get("fid") or "").strip()
                for file_item in files
                if isinstance(file_item, dict) and str(file_item.get("fid") or "").strip()
            }
            if resource_id == selected_pwd_id and initial_file_keys:
                missing_selected = sorted(initial_file_keys - file_fids)
                if missing_selected:
                    diagnostics["initial_files_missing"] += len(missing_selected)
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": resource_id,
                            "reason": (
                                "初选文件 ID 未出现在分享解析返回的文件列表中："
                                + ", ".join(missing_selected[:10])
                            ),
                        })
                    self.logger.warning(
                        "智能追剧初选诊断：初选分享=%s，以下初选FID未出现在网盘返回的文件列表中：%s",
                        resource_id,
                        ",".join(missing_selected[:20]),
                    )
                else:
                    self.logger.info(
                        "智能追剧初选诊断：初选分享=%s，全部%d个初选FID均存在于网盘文件列表",
                        resource_id,
                        len(initial_file_keys),
                    )
            for item in files:
                diagnostics["files_scanned"] += 1
                if diagnostics["files_scanned"] % 25 == 0:
                    self._persist_diagnostics_snapshot(sub, phase="scan")
                if share_diag is not None:
                    share_diag["files_scanned"] += 1
                if not isinstance(item, dict):
                    diagnostics["invalid_file_entries"] += 1
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": resource_id,
                            "reason": f"忽略非字典文件条目：{type(item).__name__}",
                        })
                    continue
                fid = str(item.get("fid") or "").strip()
                file_name = str(item.get("file_name") or item.get("name") or "").strip()
                is_video = bool(file_name.lower().endswith(VIDEO_EXTENSIONS))
                parsed_season, parsed_episode = _parse_source_episode(
                    self.resource_sources, source_id, file_name
                ) if is_video and file_name else (None, None)
                if parsed_episode is not None:
                    marker = {"season": parsed_season, "episode": parsed_episode}
                    observed = diagnostics.setdefault("observed_episodes", [])
                    if marker not in observed:
                        if len(observed) < 1000:
                            observed.append(marker)
                        else:
                            diagnostics["episode_observations_truncated"] = True
                if (
                    share_diag is not None
                    and parsed_episode is not None
                    and not any(
                        isinstance(entry, dict)
                        and entry.get("season") == parsed_season
                        and entry.get("episode") == parsed_episode
                        for entry in share_diag.get("episodes", [])
                    )
                ):
                    episode_list = share_diag.setdefault("episodes", [])
                    if len(episode_list) < 100:
                        episode_list.append({
                            "season": parsed_season,
                            "episode": parsed_episode,
                        })
                    else:
                        diagnostics["episode_observations_truncated"] = True
                if is_video:
                    diagnostics["video_files"] += 1
                    if share_diag is not None:
                        share_diag["video_files"] += 1
                else:
                    diagnostics["non_video_files"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": file_name or "(无文件名)",
                            "reason": "非视频文件，未进入追剧识别",
                        })
                if not fid:
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    diagnostics["missing_file_id"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": file_name or "(无文件名)",
                            "reason": "文件条目缺少 fid，无法追踪或转存",
                        })
                    continue
                legacy_key = f"{resource_id}:{fid}"
                provider_key = (
                    f"{resource_type}:{legacy_key}"
                    if resource_type and resource_type != "quark_share"
                    else legacy_key
                )
                key_base = provider_key
                key = (
                    key_base
                    if target_id == selected_target_id
                    else f"{target_id}:{key_base}" if target_id else key_base
                )
                queue_key = (resource_type, resource_id, target_id, fid)
                is_selected_initial = (
                    resource_id == selected_pwd_id and fid in initial_file_keys
                )
                legacy_selected_key = target_id == selected_target_id and legacy_key in tracked
                confirmed_saved = key in saved_episode_keys or (
                    target_id == selected_target_id and legacy_key in saved_episode_keys
                )
                is_initial_fid = fid in initial_file_keys
                if is_initial_fid and not is_selected_initial:
                    self.logger.warning(
                        "智能追剧初选诊断：FID=%s 命中初选列表但分享不匹配，当前分享=%s，初选分享=%s，资源类型=%s",
                        fid,
                        resource_id,
                        selected_pwd_id,
                        resource_type,
                    )
                if is_selected_initial:
                    self.logger.info(
                        "智能追剧初选诊断：命中初选文件，FID=%s，文件名=%s，目标=%s，tracked=%s，saved=%s，key=%s",
                        fid,
                        str(item.get("file_name") or ""),
                        target_id,
                        key in tracked or legacy_selected_key,
                        confirmed_saved,
                        key,
                    )
                # Do not re-copy files with a durable success record. A tracked-only
                # marker on a selected initial file may be the false-positive written
                # by older versions before filename recognition was available.
                if confirmed_saved:
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    diagnostics["already_saved"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": str(item.get("file_name") or fid),
                            "reason": "已确认保存，跳过重复转存",
                        })
                    if is_selected_initial:
                        self.logger.warning(
                            "智能追剧初选诊断：初选文件被已保存记录拦截，FID=%s，key=%s",
                            fid,
                            key,
                        )
                    continue
                if ((key in tracked or legacy_selected_key) and not is_selected_initial) or queue_key in queued_keys:
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    diagnostics["already_tracked"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": str(item.get("file_name") or fid),
                            "reason": "已追踪或重复文件，跳过",
                        })
                    continue
                if not is_video:
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    if is_selected_initial:
                        self.logger.warning(
                            "智能追剧初选诊断：初选文件因扩展名被过滤，FID=%s，文件名=%s",
                            fid,
                            file_name,
                        )
                    continue
                season, episode = parsed_season, parsed_episode
                if episode is None:
                    if share_diag is not None:
                        share_diag["skipped_files"] += 1
                    diagnostics["unrecognized"] += 1
                    if len(diagnostics["examples"]) < 50:
                        diagnostics["examples"].append({
                            "file": file_name[:240],
                            "reason": "无法识别季集，未自动转存",
                        })
                    # Keep the channel watermark unchanged while a video file
                    # remains unclassified. Otherwise the incremental scan could
                    # permanently skip a share before recognition rules improve.
                    scan_complete = False
                    # Explicit user selection is authoritative for the first run:
                    # it must still transfer even if the global/card parser cannot
                    # identify the episode. Unselected unclassified files are skipped.
                    if not is_selected_initial:
                        continue
                if episode is not None:
                    completion_key = (
                        f"{target_id}|{season if season is not None else ''}|{episode}"
                    )
                    current_source_identity = (
                        f"{resource_type}:{resource_id}" if resource_type else resource_id
                    )
                    previously_saved_source = str(
                        saved_episode_sources.get(completion_key) or ""
                    ).strip()
                    if (
                        previously_saved_source
                        and previously_saved_source != current_source_identity
                    ):
                        if share_diag is not None:
                            share_diag["skipped_files"] += 1
                        diagnostics["already_saved"] += 1
                        if len(diagnostics["examples"]) < 50:
                            diagnostics["examples"].append({
                                "file": file_name[:240],
                                "reason": "该季集已从其他分享转存，跳过重复集",
                            })
                        continue
                if is_selected_initial:
                    self.logger.info(
                        "智能追剧初选诊断：初选文件已进入转存队列，FID=%s，解析集数=%s，季=%s",
                        fid,
                        episode,
                        season,
                    )
                if not is_selected_initial:
                    if episode is None:
                        continue
                    if baseline_season > 0 and season is None:
                        if share_diag is not None:
                            share_diag["skipped_files"] += 1
                        diagnostics["unrecognized"] += 1
                        scan_complete = False
                        if len(diagnostics["examples"]) < 50:
                            diagnostics["examples"].append({
                                "file": file_name[:240],
                                "reason": "文件名缺少可确认的季数，无法安全判断追剧起点，未自动转存",
                            })
                        continue
                    if season is not None and baseline_season > 0:
                        if (season, episode) <= (baseline_season, baseline_episode):
                            if share_diag is not None:
                                share_diag["skipped_files"] += 1
                            diagnostics["filtered_old"] += 1
                            if len(diagnostics["examples"]) < 50:
                                diagnostics["examples"].append({
                                    "file": file_name[:240],
                                    "reason": "早于追剧起始集，已过滤",
                                })
                            continue
                    elif episode <= baseline_episode:
                        # 只过滤未选中的基准集及更早集数；选中集需首次转存。
                        if share_diag is not None:
                            share_diag["skipped_files"] += 1
                        diagnostics["filtered_old"] += 1
                        if len(diagnostics["examples"]) < 50:
                            diagnostics["examples"].append({
                                "file": file_name[:240],
                                "reason": "早于追剧起始集，已过滤",
                            })
                        continue
                group_key = (resource_type, resource_id, target_id)
                episode_key = (target_id, season, episode)
                if episode is not None:
                    previous_group = queued_episode_sources.get(episode_key)
                    if previous_group is not None and previous_group != group_key:
                        # Another share already covers this episode. Do not transfer
                        # the overlap twice, but keep scanning for later episodes.
                        if is_selected_initial:
                            self.logger.warning(
                                "智能追剧初选诊断：初选文件被跨分享同集去重拦截，FID=%s，文件名=%s，集数=%s，当前分享=%s，优先分享=%s",
                                fid,
                                file_name,
                                episode,
                                resource_id,
                                previous_group[1],
                            )
                        if share_diag is not None:
                            share_diag["skipped_files"] += 1
                        diagnostics["duplicate_episodes"] += 1
                        if len(diagnostics["examples"]) < 50:
                            diagnostics["examples"].append({
                                "file": file_name[:240],
                                "reason": "与优先分享中的同集重复，已去重",
                            })
                        continue
                grouped.setdefault(group_key, {
                    "resource": resource,
                    "token": token,
                    "target_id": target_id,
                    "files": [],
                    "keys": [],
                    "episode_sources": {},
                })["files"].append({"fid": fid})
                grouped[group_key]["keys"].append(key)
                if share_diag is not None:
                    share_diag["queued_files"] += 1
                diagnostics["queued_files"] += 1
                queued_keys.add(queue_key)
                if episode is not None:
                    queued_episode_sources.setdefault(episode_key, group_key)
                    completion_key = (
                        f"{target_id}|{season if season is not None else ''}|{episode}"
                    )
                    source_identity = (
                        f"{resource_type}:{resource_id}" if resource_type else resource_id
                    )
                    grouped[group_key]["episode_sources"].setdefault(
                        completion_key, source_identity
                    )

        if not grouped:
            if resolution_errors:
                return self._finish(
                    sub,
                    False,
                    "频道资源解析失败，未能确认是否有新集；请检查资源来源状态或查看服务日志",
                    success_keys=[],
                )
            if diagnostics["source_search_errors"]:
                return self._finish(
                    sub,
                    False,
                    (
                        f"频道搜索存在 {diagnostics['source_search_errors']} 项异常，"
                        "未能完整确认是否有新集；请检查资源来源状态后重试"
                    ),
                    success_keys=[],
                )
            if scan_complete:
                self._save_channel_scan_cursor(sub, scan_cursor)
            self._save_channel_history_cursor(sub, history_cursor, history_complete)
            return self._finish(sub, True, f"《{title}》暂无新更新", success_keys=[])

        target_fid = _normalize_fid(sub.get("target_fid"))
        # Folder IDs belong to a specific storage target. A channel can contain
        # resources resolved through different target cards, so never reuse one
        # target's folder ID with another target.
        folder_fids: dict[str, str] = {}

        all_keys: list[str] = []
        total = 0
        self._set_status(sub, "running", "transfer", "正在转存新资源…")
        for group in grouped.values():
            target_id = str(group["target_id"] or "").strip()
            if target_id not in folder_fids:
                try:
                    folder_fids[target_id] = self.storage_targets.create_folder(
                        title,
                        target_fid,
                        target_id or None,
                    )
                except Exception:
                    self.logger.exception(
                        "频道追剧在存储目标 %s 创建专属文件夹失败: %s",
                        target_id or "(default)",
                        title,
                    )
                    return self._finish(
                        sub,
                        False,
                        "创建专属文件夹失败，请检查存储目标配置或查看服务日志",
                        success_keys=all_keys,
                    )

            self._set_pending_save_keys(sub, group["keys"])
            ok, msg = self.storage_targets.transfer(
                group["resource"],
                group["files"],
                folder_fids[target_id],
                target_id or None,
                group["token"],
            )
            if not ok:
                self.logger.warning("频道追剧转存失败: %s", msg)
                uncertain = is_uncertain_transfer_message(msg)
                safe_message = (
                    "转存结果不确定，请检查目标网盘后再决定是否重试"
                    if uncertain
                    else "转存失败，请检查存储目标配置或查看服务日志"
                )
                return self._finish(
                    sub,
                    False,
                    safe_message,
                    success_keys=all_keys,
                    outcome_uncertain=uncertain,
                )
            all_keys.extend(group["keys"])
            diagnostics["transferred_files"] += len(group["files"])
            # Commit confirmed success and clear pending state in one write.
            self._record_success_keys(
                sub,
                group["keys"],
                episode_sources=group.get("episode_sources"),
            )
            total += len(group["files"])

        if resolution_errors:
            return self._finish(
                sub,
                False,
                f"已转存 {total} 项，但部分频道资源解析失败，后续将继续检查",
                success_keys=all_keys,
            )

        if diagnostics["source_search_errors"]:
            return self._finish(
                sub,
                False,
                (
                    f"已转存 {total} 项，但频道搜索存在 "
                    f"{diagnostics['source_search_errors']} 项异常，未能完整确认新集；"
                    "后续将重试"
                ),
                success_keys=all_keys,
            )

        if scan_complete:
            self._save_channel_scan_cursor(sub, scan_cursor)
        self._save_channel_history_cursor(sub, history_cursor, history_complete)
        diagnostics["source_scan_complete"] = bool(
            scan_complete and (history_complete or sub.get("channel_history_complete"))
        )
        return self._finish(
            sub,
            True,
            f"🎉 成功追更 {total} 项，已存入专属文件夹【{title}】！",
            success_keys=all_keys,
        )

    def _set_pending_save_keys(self, sub: dict, pending_keys) -> None:
        keys = list(dict.fromkeys(str(key).strip() for key in (pending_keys or []) if str(key).strip()))[:200]
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next((item for item in subscriptions if item.get("id") == sub.get("id")), None)
            if current is None:
                return
            current["pending_save_keys"] = keys
            current["pending_save_uncertain"] = False
            self.store.write(subscriptions)

    def resolve_pending_save(self, sub_id: str, action: str) -> tuple[bool, str]:
        """Resolve an ambiguous transfer only after an explicit user confirmation."""
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next((item for item in subscriptions if str(item.get("id")) == str(sub_id)), None)
            if current is None:
                return False, "未找到订阅任务"
            if str(sub_id) in self.running_ids:
                return False, "该订阅正在执行，请稍后再确认"
            keys = list(current.get("pending_save_keys", []) or [])
            if action not in {"saved", "not_saved"}:
                return False, "不支持的确认操作"
            if current.get("pending_save_keys_invalid"):
                return False, "待确认转存记录格式异常，请先检查网盘并修复订阅记录"
            if not current.get("pending_save_uncertain"):
                return False, "该任务没有待确认的转存结果"
            if not keys and action == "saved":
                return False, "缺少待确认文件记录，无法安全登记转存成功；请修复订阅记录"
            # If the uncertainty flag survived but its file list is empty,
            # an explicit user confirmation that nothing was transferred can
            # safely release the guard. Never infer this from the empty list.
            current["pending_save_keys"] = []
            current["pending_save_uncertain"] = False
            current["last_error"] = ""
            current["next_run_at"] = time.time()
            if action == "saved":
                tracked = list(current.get("tracked_file_keys", []) or [])
                tracked_seen = {str(key) for key in tracked}
                tracked.extend(key for key in keys if str(key) not in tracked_seen)
                current["tracked_file_keys"] = tracked[-1000:]
                saved = list(current.get("saved_episodes", []) or [])
                saved_seen = {str(key) for key in saved}
                for key in keys:
                    # Channel-transfer keys include the share ID (share_id:file_id).
                    # Keep them in the durable history too, so a confirmed transfer
                    # remains deduplicated after tracked_file_keys is bounded.
                    if ":" in str(key):
                        value = str(key)
                    elif str(key).isdigit():
                        value = int(key)
                    else:
                        value = key
                    if str(value) not in saved_seen:
                        saved.append(value)
                        saved_seen.add(str(value))
                current["saved_episodes"] = sorted(saved, key=lambda value: (isinstance(value, str), str(value)))
                current["status"] = "success"
                current["phase"] = "completed"
                current["phase_label"] = "已人工确认转存"
                current["last_check"] = self._now_string() + " (已确认转存)"
                current["last_check_at"] = time.time()
                try:
                    interval = int(current.get("interval_hours", 6) or 6)
                except (TypeError, ValueError):
                    interval = 6
                interval = max(1, min(self.max_interval_hours, interval))
                current["next_run_at"] = time.time() + interval * 3600
                history = list(current.get("run_history", []) or [])
                history.append({
                    "at": time.time(),
                    "success": True,
                    "message": "用户已确认网盘端转存成功",
                    "success_count": len(keys),
                    "failed_count": 0,
                })
                current["run_history"] = history[-50:]
                self.store.write(subscriptions)
                return True, "已记录转存成功，后续检查不会重复提交这些文件"
            current["status"] = "waiting"
            current["phase"] = "waiting"
            current["phase_label"] = "用户确认未转存，正在重新检查"
            current["last_check"] = self._now_string() + " (确认未转存)"
            self.store.write(subscriptions)
        return self.check_subscription_now(str(sub_id))

    def _record_success_keys(self, sub: dict, success_keys, episode_sources=None) -> None:
        """逐组持久化已确认转存的文件及季集身份，避免跨分享重复转存。"""
        keys = list(dict.fromkeys(str(key).strip() for key in (success_keys or []) if str(key).strip()))
        if not keys:
            return
        with self.lock:
            subscriptions = self._load_subscriptions()
            current = next((item for item in subscriptions if item.get("id") == sub.get("id")), None)
            if current is None:
                return
            tracked = [str(key) for key in (current.get("tracked_file_keys", []) or [])]
            seen_tracked = set(tracked)
            tracked.extend(key for key in keys if key not in seen_tracked)
            current["tracked_file_keys"] = tracked[-1000:]
            saved = list(current.get("saved_episodes", []) or [])
            seen_saved = {str(key) for key in saved}
            saved.extend(key for key in keys if key not in seen_saved)
            current["saved_episodes"] = sorted(saved, key=lambda value: (isinstance(value, str), str(value)))
            saved_sources = current.get("saved_episode_sources", {})
            if not isinstance(saved_sources, dict):
                saved_sources = {}
            for episode_key, source_identity in (episode_sources or {}).items():
                episode_key = str(episode_key).strip()
                source_identity = str(source_identity).strip()
                if episode_key and source_identity:
                    saved_sources.setdefault(episode_key, source_identity)
            # Bound this auxiliary index for very long-running subscriptions while
            # keeping the durable file-ID history as the primary fallback.
            current["saved_episode_sources"] = dict(list(saved_sources.items())[-5000:])
            # The remote transfer is confirmed: persist its success checkpoint
            # and clear its pending marker atomically to avoid false uncertainty
            # if the process exits between two separate writes.
            current["pending_save_keys"] = []
            current["pending_save_uncertain"] = False
            self.store.write(subscriptions)

    def _finish(
        self,
        sub: dict,
        success: bool,
        message: str,
        success_keys=None,
        outcome_uncertain: bool = False,
    ) -> tuple[bool, str]:
        success_keys = (
            success_keys or []
        )

        with self.lock:
            subscriptions = self._load_subscriptions()

            current = next(
                (
                    item
                    for item in subscriptions
                    if item.get("id")
                    == sub.get("id")
                ),
                None,
            )

            if current is None:
                return (
                    success,
                    message,
                )

            current["status"] = "success" if success else "failed"
            current["phase"] = "completed" if success else "failed"
            current["phase_label"] = "检查完成" if success else "检查失败"
            current["last_check"] = (
                self._now_string()
                + (
                    " (成功)"
                    if success
                    else " (失败)"
                )
            )

            current["last_check_at"] = (
                time.time()
            )

            try:
                interval_hours = int(
                    current.get(
                        "interval_hours",
                        6,
                    )
                )
            except (TypeError, ValueError):
                interval_hours = 6

            interval_hours = max(
                1,
                min(
                    self.max_interval_hours,
                    interval_hours,
                ),
            )

            current["interval_hours"] = interval_hours

            if success:
                current["retry_count"] = 0
                current["pending_save_keys"] = []
                current["pending_save_uncertain"] = False
                delay_seconds = interval_hours * 3600
            else:
                if outcome_uncertain:
                    current["pending_save_uncertain"] = True
                    current["phase_label"] = "转存结果待核实"
                else:
                    current["pending_save_keys"] = []
                    current["pending_save_uncertain"] = False
                try:
                    retry_count = int(
                        current.get(
                            "retry_count",
                            0,
                        )
                    )
                except (TypeError, ValueError):
                    retry_count = 0

                retry_count = max(0, retry_count) + 1
                current["retry_count"] = retry_count

                # 临时故障采用短退避，连续失败再逐步拉长，
                # 但不会超过用户设置的正常检测周期。
                delay_seconds = min(
                    interval_hours * 3600,
                    300 * (2 ** min(retry_count - 1, 6)),
                )

            current["next_run_at"] = (
                time.time() + delay_seconds
            )

            current["last_error"] = (
                ""
                if success
                else message
            )
            diagnostics = sub.get("_run_diagnostics")
            if isinstance(diagnostics, dict):
                saved_diagnostics = deepcopy(diagnostics)
                saved_diagnostics["examples"] = deepcopy(list(diagnostics.get("examples") or [])[:50])
                saved_diagnostics["shares"] = deepcopy(list(diagnostics.get("shares") or [])[:50])
                finished_at = time.time()
                saved_diagnostics["finished_at"] = finished_at
                saved_diagnostics["updated_at"] = finished_at
                saved_diagnostics["phase"] = "completed" if success else "failed"
                saved_diagnostics["success"] = bool(success)
                saved_diagnostics["interrupted"] = False
                saved_diagnostics["message"] = str(message)[:500]
                current["last_diagnostics"] = saved_diagnostics
            history = current.get("run_history", [])
            if not isinstance(history, list):
                history = []
            history.append({
                "at": time.time(),
                "success": bool(success),
                "message": str(message)[:500],
                "success_count": len(success_keys) if success else 0,
                "failed_count": 0 if success else 1,
            })
            current["run_history"] = history[-50:]

            if success_keys:
                tracked = list(current.get("tracked_file_keys", []) or [])
                seen_tracked = set(str(key) for key in tracked)
                for key in success_keys:
                    normalized_key = str(key)
                    if normalized_key not in seen_tracked:
                        tracked.append(normalized_key)
                        seen_tracked.add(normalized_key)
                current["tracked_file_keys"] = tracked[-1000:]

                old = list(
                    current.get(
                        "saved_episodes",
                        [],
                    )
                )

                seen = set(old)

                for key in success_keys:
                    if key not in seen:
                        old.append(key)
                        seen.add(key)

                current[
                    "saved_episodes"
                ] = sorted(
                    old,
                    key=lambda value: (
                        isinstance(
                            value,
                            str,
                        ),
                        str(value),
                    ),
                )

            self.store.write(
                subscriptions
            )

        if success:
            self.logger.info(
                message
            )
        else:
            self.logger.warning(
                message
            )

        return (
            success,
            message,
        )

    def _run_scheduler_worker(self) -> None:
        worker = current_thread()
        try:
            self._scheduler_loop()
        finally:
            # Never let the worker wait on the lifecycle lock: stop_scheduler()
            # may be joining it while holding that lock.
            with self.lock:
                restart_requested = getattr(self, "_scheduler_restart_requested", False)
                if not restart_requested and self.worker is worker:
                    self.worker = None
            if restart_requested:
                Thread(
                    target=self._restart_scheduler_after_worker_exit,
                    args=(worker,),
                    name="moviesync-scheduler-restart",
                    daemon=True,
                ).start()

    def _restart_scheduler_after_worker_exit(self, old_worker: Thread) -> None:
        # Wait outside the lifecycle lock so the old worker can fully exit.
        old_worker.join()
        with self._scheduler_lifecycle_lock:
            with self.lock:
                if not getattr(self, "_scheduler_restart_requested", False):
                    if self.worker is old_worker:
                        self.worker = None
                    return
                self._scheduler_restart_requested = False
                if self.worker is old_worker:
                    self.worker = None
            self.start_scheduler()

    def start_scheduler(
        self,
    ) -> None:
        # If stop timed out while a check was in flight, remember the new
        # start request. The exiting worker will launch its replacement after
        # it has fully terminated, avoiding both a lost start and overlap.
        with self._scheduler_lifecycle_lock:
            with self.lock:
                if self._shutdown_requested:
                    return
                if self.worker and self.worker.is_alive():
                    if self.stop_event.is_set():
                        self._scheduler_restart_requested = True
                    return

                self._scheduler_restart_requested = False
                self.stop_event.clear()
                self.worker = Thread(
                    target=self._run_scheduler_worker,
                    name="moviesync-scheduler",
                    daemon=True,
                )
                worker = self.worker
                worker.start()

            self.logger.info("自动追剧调度器已启动")

    def stop_scheduler(
        self,
    ) -> None:
        with self._scheduler_lifecycle_lock:
            with self.lock:
                # An explicit stop cancels any deferred restart request.
                self._scheduler_restart_requested = False
            self.stop_event.set()
            with self.lock:
                worker = self.worker

            if worker and worker.is_alive():
                worker.join(timeout=2)

            # Keep the reference while a check is still running. Clearing it
            # would allow a second scheduler to overlap the existing worker.
            with self.lock:
                if self.worker is worker and (worker is None or not worker.is_alive()):
                    self.worker = None
                elif worker is not None and worker.is_alive():
                    self.logger.warning(
                        "自动追剧调度器尚未退出；保留线程引用以阻止重复启动"
                    )

    def shutdown(self) -> None:
        """停止调度器并等待正在执行的检查结束后再释放卡片资源。"""
        with self._scheduler_lifecycle_lock:
            with self.lock:
                self._shutdown_requested = True
                self._scheduler_restart_requested = False

        self.stop_scheduler()

        # stop_scheduler intentionally has a short timeout for normal lifecycle
        # operations. Process shutdown is different: wait for any in-flight check
        # to finish before the app closes the cards it may still be using.
        with self.lock:
            worker = self.worker
        if worker is not None and worker is not current_thread():
            worker.join()
        with self.lock:
            if self.worker is worker and (worker is None or not worker.is_alive()):
                self.worker = None

    def _scheduler_loop(
        self,
    ) -> None:
        while not self.stop_event.wait(30):
            try:
                subscriptions = self.get_subscriptions()
            except Exception as exc:
                self.logger.exception(
                    "读取自动追剧任务失败: %s",
                    exc,
                )
                continue

            now = time.time()

            for sub in subscriptions:
                if self.stop_event.is_set():
                    break

                try:
                    next_run_at = float(
                        sub.get(
                            "next_run_at",
                            0,
                        )
                        or 0
                    )
                except (TypeError, ValueError):
                    next_run_at = 0

                if next_run_at > now:
                    continue

                try:
                    self.check_subscription_now(
                        str(sub.get("id") or ""),
                        only_if_due=True,
                    )
                except Exception:
                    self.logger.exception(
                        "自动追剧任务调度异常: %s",
                        sub.get("id"),
                    )
