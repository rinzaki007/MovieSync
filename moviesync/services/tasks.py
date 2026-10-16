"""统一任务中心服务：持久化一次性转存任务与执行历史。"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from ..storage import JsonStore
from .transfer_outcome import is_uncertain_transfer_message

MAX_TASKS = 300
TASK_SCHEMA_VERSION = 2
MAX_EVENTS = 60


class TaskManager:
    def __init__(self, path, logger):
        self.store = JsonStore(path, lambda: [])
        self.logger = logger
        self.lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="moviesync-task")
        # Track submissions in this process so a queued task whose worker exited
        # before persisting its running state can be safely resubmitted later.
        self.submitted_task_ids: set[str] = set()
        self._recover_interrupted_tasks()

    def _recover_interrupted_tasks(self):
        with self.lock:
            items = self.store.read()
            if not isinstance(items, list):
                return
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("status") == "queued":
                    # queued means the worker had not marked the task as running;
                    # it is safe to retry because the transfer runner had not started.
                    item["status"] = "failed"
                    item["phase"] = "failed"
                    item["phase_label"] = "任务中断"
                    item["message"] = "服务重启时任务仍在排队，未开始执行，可安全重试"
                    item["recovery_uncertain"] = False
                    item["failed_count"] = self._recovered_failed_count(item)
                    item["updated_at"] = time.time()
                    self._append_event(item, "error", item["message"])
                    changed = True
                elif item.get("status") == "running":
                    # A running transfer may already have reached the remote drive.
                    # Never offer a blind retry after a process crash.
                    item["status"] = "failed"
                    item["phase"] = "failed"
                    item["phase_label"] = "结果待核实"
                    item["message"] = (
                        "服务在任务执行中重启，网盘端可能已完成转存。"
                        "请先检查目标网盘；确认未转存后再手动重新创建任务。"
                        "为避免重复转存，系统已禁用此任务的直接重试。"
                    )
                    item["recovery_uncertain"] = True
                    item["failed_count"] = self._recovered_failed_count(item)
                    item["updated_at"] = time.time()
                    self._append_event(item, "error", item["message"])
                    changed = True
                if item.get("schema_version") != TASK_SCHEMA_VERSION:
                    item["schema_version"] = TASK_SCHEMA_VERSION
                    item.setdefault("phase", "waiting")
                    item.setdefault("phase_label", "等待执行")
                    item.setdefault("events", [])
                    changed = True
            if changed:
                self._save(items)

    def list_tasks(self):
        with self.lock:
            items = self.store.read()
            if not isinstance(items, list):
                return []
            return [dict(item) for item in items if isinstance(item, dict)]

    def _save(self, items):
        # Never evict queued/running tasks: their workers still need a durable
        # record to claim and report the final outcome. Bound only terminal history.
        active = [item for item in items if item.get("status") in {"queued", "running"}]
        terminal = [item for item in items if item.get("status") not in {"queued", "running"}]
        remaining = max(0, MAX_TASKS - len(active))
        keep_ids = {id(item) for item in active}
        if remaining:
            keep_ids.update(id(item) for item in terminal[-remaining:])
        kept = [item for item in items if id(item) in keep_ids]
        self.store.write(kept)

    @staticmethod
    def _payload_key(payload):
        """基于实际转存目标与文件生成指纹，忽略封面等展示字段和文件顺序。"""
        if isinstance(payload, dict):
            candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
            files = candidate.get("files") if isinstance(candidate.get("files"), list) else []
            file_ids = sorted({
                str(item.get("fid") or "").strip()
                for item in files
                if isinstance(item, dict) and str(item.get("fid") or "").strip()
            })
            resource_id = str(
                candidate.get("resource_id") or candidate.get("share_id") or candidate.get("pwd_id") or ""
            ).strip()
            if resource_id and file_ids:
                fingerprint = {
                    "type": "transfer",
                    # Display titles can differ for the same underlying share/file.
                    # Keep deduplication anchored to resource identity and destination.
                    "resource_id": resource_id,
                    "resource_type": str(candidate.get("resource_type") or "").strip().lower(),
                    "source_id": str(candidate.get("source_id") or "").strip(),
                    "storage_target_id": str(
                        candidate.get("storage_target_id")
                        or candidate.get("target_id")
                        or payload.get("storage_target_id")
                        or ""
                    ).strip(),
                    "target_fid": str(payload.get("target_fid") or "0").strip(),
                    "file_ids": file_ids,
                }
            else:
                # 保留旧式/不完整 payload 的稳定指纹行为。
                fingerprint = payload
        else:
            fingerprint = payload
        serialized = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _phase_for_progress(progress):
        value = int(progress or 0)
        if value <= 10:
            return "validate", "校验资源"
        if value <= 30:
            return "list_files", "获取文件列表"
        if value <= 60:
            return "create_folder", "准备目标文件夹"
        if value < 100:
            return "transfer", "提交转存"
        return "completed", "转存完成"

    @staticmethod
    def _recovered_failed_count(item):
        try:
            return max(1, int(item.get("failed_count", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            # Invalid legacy metadata must not prevent the service from starting.
            return 1

    @staticmethod
    def _append_event(item, level, message):
        raw_events = item.get("events")
        events = (
            [event for event in raw_events if isinstance(event, dict)]
            if isinstance(raw_events, list)
            else []
        )
        if events != raw_events:
            item["events"] = events

        text = str(message or "").strip()
        if not text:
            return
        if events and events[-1].get("message") == text:
            return
        events.append({
            "at": time.time(),
            "level": str(level or "info"),
            "message": text[:300],
        })
        item["events"] = events[-MAX_EVENTS:]

    def create_transfer_task(self, payload, runner):
        movie = payload.get("movie") if isinstance(payload.get("movie"), dict) else {}
        title = str(movie.get("title") or movie.get("name") or payload.get("title") or "未命名资源")[:200]
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        files = candidate.get("files") if isinstance(candidate.get("files"), list) else []
        now = time.time()
        task = {
            "id": uuid.uuid4().hex,
            "schema_version": TASK_SCHEMA_VERSION,
            "type": "transfer",
            "kind": "普通转存",
            "title": title,
            "cover": str(movie.get("cover") or ""),
            "status": "queued",
            "phase": "waiting",
            "phase_label": "等待执行",
            "progress": 0,
            "total": max(0, len(files)),
            "success_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "message": "任务已创建，等待执行",
            "source_channel": str(candidate.get("channel") or ""),
            "share_code": str(
                candidate.get("resource_id")
                or candidate.get("share_id")
                or candidate.get("pwd_id")
                or ""
            ),
            "target_fid": str(payload.get("target_fid") or "0"),
            "storage_target_id": str(payload.get("storage_target_id") or candidate.get("storage_target_id") or ""),
            "destination_listing_supported": bool(payload.get("destination_listing_supported", False)),
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "updated_at": now,
            "events": [],
            "retry_payload": payload,
        }
        task["dedupe_key"] = self._payload_key(payload)
        self._append_event(task, "info", "任务已创建")
        with self.lock:
            items = self.list_tasks()
            active = next((
                item for item in items
                if item.get("type") == "transfer"
                and item.get("status") in {"queued", "running"}
                and item.get("dedupe_key") == task["dedupe_key"]
            ), None)
            if active:
                # A worker may have exited before it could persist the running
                # state (for example, during a transient disk error). If no
                # worker currently owns this queued record, reclaim it.
                if active.get("status") == "queued":
                    return self._submit_transfer(active, runner)
                return active
            items.append(task)
            self._save(items)
        return self._submit_transfer(task, runner)

    def _submit_transfer(self, task, runner):
        """Submit a persisted task once; allow reclaiming it after a worker exits."""
        task_id = str(task["id"])
        with self.lock:
            if task_id in self.submitted_task_ids:
                return self._get(task_id) or task
            self.submitted_task_ids.add(task_id)
        try:
            self.executor.submit(self._run_transfer_guarded, task_id, runner)
        except Exception:
            with self.lock:
                self.submitted_task_ids.discard(task_id)
            self.logger.exception("提交转存任务 %s 到后台执行器失败", task_id)
            self._update(
                task_id,
                status="failed",
                phase="failed",
                phase_label="提交失败",
                progress=0,
                recovery_uncertain=False,
                message="任务未能提交到后台执行器，尚未开始转存，可以安全重试",
                failed_count=1,
                finished_at=time.time(),
            )
            return self.get_task(task_id) or task
        # Preserve the queue snapshot returned by the previous implementation;
        # the worker may already have moved the persisted task to running.
        return task

    def _run_transfer_guarded(self, task_id, runner):
        try:
            self._run_transfer(task_id, runner)
        finally:
            with self.lock:
                self.submitted_task_ids.discard(str(task_id))

    def _update(self, task_id, **changes):
        with self.lock:
            items = self.list_tasks()
            current = next((x for x in items if x.get("id") == task_id), None)
            if not current:
                return
            if current.get("status") in {"success", "failed", "cancelled"} and (
                "status" not in changes or changes["status"] != current.get("status")
            ):
                # Ignore late progress/status updates after a terminal outcome.
                return
            old_message = current.get("message")
            current.update(changes)
            current["updated_at"] = time.time()
            message = current.get("message")
            if message and message != old_message:
                level = "error" if current.get("status") == "failed" else (
                    "success" if current.get("status") == "success" else "info"
                )
                self._append_event(current, level, message)
            self._save(items)

    def _progress_update(self, task_id, progress, message):
        value = max(0, min(100, int(progress)))
        phase, phase_label = self._phase_for_progress(value)
        self._update(
            task_id,
            progress=value,
            message=str(message)[:300],
            phase=phase,
            phase_label=phase_label,
        )

    def _run_transfer(self, task_id, runner):
        # Claim the queued task atomically with deletion/clear-history operations.
        # A task deleted before its worker starts must never execute a remote transfer.
        with self.lock:
            current = self._get(task_id)
            if not current or current.get("status") != "queued":
                return
            try:
                self._update(
                    task_id,
                    status="running",
                    progress=10,
                    phase="validate",
                    phase_label="校验资源",
                    message="正在重新验证分享资源…",
                    started_at=time.time(),
                )
            except Exception:
                # The remote runner has not started, so this failure is safe to
                # retry. Do not leave a durable queued task that dedupe will
                # keep returning without ever submitting again.
                self.logger.exception("转存任务 %s 启动状态写入失败，远程转存尚未开始", task_id)
                try:
                    self._update(
                        task_id,
                        status="failed",
                        phase="failed",
                        phase_label="启动失败",
                        progress=0,
                        recovery_uncertain=False,
                        message="任务状态写入失败，尚未开始转存，可以安全重试",
                        failed_count=1,
                        finished_at=time.time(),
                    )
                except Exception:
                    self.logger.exception("无法保存转存任务 %s 的启动失败状态", task_id)
                return
        try:
            result = runner(lambda progress, message: self._progress_update(task_id, progress, message))
            ok, message, counts = result
            if not isinstance(ok, bool) or not isinstance(message, str) or not isinstance(counts, dict):
                raise ValueError("invalid transfer runner result contract")
            uncertain = bool(counts.get("uncertain")) or (not ok and is_uncertain_transfer_message(message))
            now = time.time()
            current = self._get(task_id)
            final_progress = 100 if ok else max(10, int(current.get("progress", 10)))
            self._update(
                task_id,
                status="success" if ok else "failed",
                progress=final_progress,
                phase="completed" if ok else "failed",
                phase_label="转存完成" if ok else ("结果待核实" if uncertain else "执行失败"),
                recovery_uncertain=uncertain,
                message=str(message)[:500],
                success_count=int(counts.get("success", 0)),
                skipped_count=int(counts.get("skipped", 0)),
                failed_count=int(counts.get("failed", 0)),
                finished_at=now,
            )
        except Exception:
            self.logger.exception("一次性转存任务 %s 执行异常", task_id)
            # Once the runner has started, an exception alone cannot prove that
            # no remote side effect occurred. Treat the outcome as ambiguous to
            # prevent a blind retry from duplicating a transfer.
            uncertain = True
            safe_message = "转存结果待核实，请检查目标网盘后再决定是否重试"
            self._update(
                task_id,
                status="failed",
                phase="failed",
                phase_label="结果待核实" if uncertain else "执行失败",
                recovery_uncertain=uncertain,
                message=safe_message,
                failed_count=1,
                finished_at=time.time(),
            )

    def _get(self, task_id):
        return next((x for x in self.list_tasks() if x.get("id") == task_id), {})

    def get_task(self, task_id):
        return self._get(str(task_id))

    def retry_transfer(self, task_id, runner):
        with self.lock:
            items = self.list_tasks()
            old = next((item for item in items if str(item.get("id")) == str(task_id)), None)
            if (
                not old
                or old.get("type") != "transfer"
                or old.get("status") != "failed"
                or old.get("recovery_uncertain")
            ):
                return None
            payload = old.get("retry_payload") or {}
            dedupe_key = old.get("dedupe_key") or self._payload_key(payload)
            active = next((
                item for item in items
                if item.get("type") == "transfer"
                and item.get("status") in {"queued", "running"}
                and item.get("dedupe_key") == dedupe_key
            ), None)
            if active:
                if active.get("status") == "queued":
                    return self._submit_transfer(active, runner)
                return active
            new = dict(old)
            new["dedupe_key"] = dedupe_key
            new["id"] = uuid.uuid4().hex
            new["status"] = "queued"
            new["phase"] = "waiting"
            new["phase_label"] = "等待重试"
            new["progress"] = 0
            new["message"] = "已创建重试任务，等待执行"
            new["success_count"] = new["skipped_count"] = new["failed_count"] = 0
            new["created_at"] = time.time()
            new["started_at"] = None
            new["finished_at"] = None
            new["updated_at"] = time.time()
            new["events"] = []
            self._append_event(new, "info", "已创建重试任务")
            items.append(new)
            self._save(items)
        return self._submit_transfer(new, runner)

    def delete_task(self, task_id):
        task_id = str(task_id)
        with self.lock:
            items = self.list_tasks()
            target = next((x for x in items if str(x.get("id")) == task_id), None)
            if not target:
                return False, "任务不存在"
            if target.get("status") == "running":
                return False, "执行中的任务不能删除，请等待任务结束"
            items = [x for x in items if str(x.get("id")) != task_id]
            self._save(items)
        return True, "任务已删除"

    def clear_history(self):
        with self.lock:
            items = self.list_tasks()
            kept = [x for x in items if x.get("status") in {"queued", "running"}]
            removed = len(items) - len(kept)
            self._save(kept)
        return removed

    def shutdown(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
