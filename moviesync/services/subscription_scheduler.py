"""Scheduler lifecycle mixin for automatic subscriptions."""
from __future__ import annotations

import time
from threading import Thread, current_thread


class SubscriptionSchedulerMixin:
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
