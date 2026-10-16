"""任务中心与自动追剧状态回归测试。"""

import logging

from moviesync.services.subscriptions import SubscriptionManager
from moviesync.services.tasks import TaskManager


def test_transfer_retry_only_accepts_failed_task(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    failed = {
        "id": "failed-task",
        "type": "transfer",
        "status": "failed",
        "title": "测试电影",
        "events": [],
        "retry_payload": {"movie": {"title": "测试电影"}, "candidate": {"files": []}},
    }
    queued = dict(failed, id="queued-task", status="queued")
    manager.store.write([queued, failed])

    assert manager.retry_transfer(queued["id"], lambda progress: None) is None
    retry = manager.retry_transfer(failed["id"], lambda progress: (True, "ok", {"success": 0}))
    assert retry is not None
    assert retry["status"] == "queued"
    manager.shutdown()


def test_subscription_success_clears_pending_save_keys(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        lambda: "cookie",
        logging.getLogger("test"),
    )
    sub = manager.add_subscription(
        title="测试追剧",
        pwd_id="abc123",
        files=[{"fid": "file1"}],
    )

    with manager.lock:
        items = manager._load_subscriptions()
        items[0]["pending_save_keys"] = ["file1"]
        manager.store.write(items)

    ok, _ = manager._finish(
        sub,
        True,
        "转存成功",
        success_keys=["file1"],
    )

    assert ok is True
    current = manager.get_subscriptions()[0]
    assert current["pending_save_keys"] == []
    assert "file1" in current["saved_episodes"]


def test_running_subscription_cannot_be_deleted(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        lambda: "cookie",
        logging.getLogger("test"),
    )
    sub = manager.add_subscription(title="测试追剧", pwd_id="abc123")

    with manager.lock:
        manager.running_ids.add(sub["id"])

    assert manager.delete_subscription(sub["id"]) is False
    assert manager.get_subscriptions()



def test_duplicate_transfer_submission_reuses_active_task(tmp_path):
    from threading import Event

    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    started = Event()
    release = Event()
    calls = []
    payload = {
        "movie": {"title": "同一部电影"},
        "candidate": {"pwd_id": "share-1", "files": [{"fid": "file-1"}]},
        "target_fid": "target-1",
    }

    def runner(progress):
        calls.append(1)
        started.set()
        assert release.wait(3)
        return True, "ok", {"success": 1, "skipped": 0, "failed": 0}

    try:
        first = manager.create_transfer_task(payload, runner)
        assert started.wait(2)
        second = manager.create_transfer_task(payload, runner)
        assert second["id"] == first["id"]
        assert len(manager.list_tasks()) == 1
        assert len(calls) == 1
    finally:
        release.set()
        manager.executor.shutdown(wait=True)


def test_stale_queued_task_is_resubmitted_after_state_write_failures(tmp_path, monkeypatch):
    from threading import Event

    manager = TaskManager(tmp_path / "stale-queued.json", logging.getLogger("test"))
    payload = {
        "movie": {"title": "磁盘短暂故障恢复"},
        "candidate": {"pwd_id": "share-stale", "files": [{"fid": "file-stale"}]},
        "target_fid": "target-stale",
    }
    original_update = manager._update
    failed_writes = 0
    runner_started = Event()

    def fail_start_and_fallback_once(task_id, **changes):
        nonlocal failed_writes
        if changes.get("status") in {"running", "failed"} and failed_writes < 2:
            failed_writes += 1
            raise OSError("simulated transient state persistence failure")
        return original_update(task_id, **changes)

    monkeypatch.setattr(manager, "_update", fail_start_and_fallback_once)
    try:
        first = manager.create_transfer_task(payload, lambda progress: (True, "不应在首次运行", {"success": 1}))
        # The first worker cannot persist either running or fallback failed state.
        # It exits without remote transfer and releases its in-process submission claim.
        for _ in range(100):
            if first["id"] not in manager.submitted_task_ids:
                break
            import time
            time.sleep(0.01)
        stale = manager.get_task(first["id"])
        assert stale["status"] == "queued"

        def successful_runner(progress):
            runner_started.set()
            return True, "恢复后成功", {"success": 1, "skipped": 0, "failed": 0}

        second = manager.create_transfer_task(payload, successful_runner)
        assert second["id"] == first["id"]
        assert runner_started.wait(2)
        manager.executor.shutdown(wait=True)
        recovered = manager.get_task(first["id"])
        assert recovered["status"] == "success"
        assert failed_writes == 2
    finally:
        manager.shutdown()


def test_duplicate_retry_reuses_existing_active_retry(tmp_path):
    from threading import Event

    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    started = Event()
    release = Event()
    payload = {"movie": {"title": "重试电影"}, "candidate": {"pwd_id": "share-2", "files": []}}
    manager.store.write([{
        "id": "failed-task",
        "type": "transfer",
        "status": "failed",
        "title": "重试电影",
        "events": [],
        "retry_payload": payload,
    }])

    def runner(progress):
        started.set()
        assert release.wait(3)
        return True, "ok", {"success": 0}

    try:
        first = manager.retry_transfer("failed-task", runner)
        assert first is not None
        assert started.wait(2)
        second = manager.retry_transfer("failed-task", runner)
        assert second is not None
        assert second["id"] == first["id"]
        assert len(manager.list_tasks()) == 2
    finally:
        release.set()
        manager.executor.shutdown(wait=True)


def test_restart_recovery_only_allows_retry_when_task_never_started(tmp_path):
    import json

    path = tmp_path / "tasks.json"
    payload = {
        "movie": {"title": "恢复测试"},
        "candidate": {"pwd_id": "share-1", "files": [{"fid": "file-1"}]},
        "target_fid": "target-1",
    }
    path.write_text(
        json.dumps([
            {
                "id": "queued-before-restart",
                "type": "transfer",
                "status": "queued",
                "title": "排队任务",
                "events": [],
                "retry_payload": payload,
            },
            {
                "id": "running-before-restart",
                "type": "transfer",
                "status": "running",
                "title": "执行中任务",
                "events": [],
                "retry_payload": payload,
            },
        ]),
        encoding="utf-8",
    )

    manager = TaskManager(path, logging.getLogger("test"))
    try:
        recovered = {task["id"]: task for task in manager.list_tasks()}
        assert recovered["queued-before-restart"]["status"] == "failed"
        assert recovered["queued-before-restart"]["recovery_uncertain"] is False
        assert recovered["running-before-restart"]["status"] == "failed"
        assert recovered["running-before-restart"]["recovery_uncertain"] is True
        assert "检查目标网盘" in recovered["running-before-restart"]["message"]

        assert manager.retry_transfer(
            "running-before-restart",
            lambda progress: (True, "不应执行", {"success": 1}),
        ) is None

        retry = manager.retry_transfer(
            "queued-before-restart",
            lambda progress: (True, "安全重试成功", {"success": 1}),
        )
        assert retry is not None
        assert retry["status"] == "queued"
        manager.executor.shutdown(wait=True)
        assert manager.get_task(retry["id"])["status"] == "success"
    finally:
        manager.shutdown()


def test_uncertain_transfer_failure_disables_direct_retry(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    payload = {"movie": {"title": "不确定结果"}, "candidate": {"pwd_id": "share", "files": [{"fid": "f1"}]}}
    try:
        task = manager.create_transfer_task(
            payload,
            lambda progress: (
                False,
                "转存结果不确定：请求可能已到达网盘",
                {"success": 0, "skipped": 0, "failed": 1, "uncertain": True},
            ),
        )
        manager.executor.shutdown(wait=True)
        failed = manager.get_task(task["id"])
        assert failed["status"] == "failed"
        assert failed["recovery_uncertain"] is True
        assert failed["phase_label"] == "结果待核实"
        assert manager.retry_transfer(task["id"], lambda progress: (True, "不应重试", {"success": 1})) is None
    finally:
        manager.shutdown()


def test_deleted_queued_task_never_starts_remote_runner(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    calls = []
    try:
        manager.executor.shutdown(wait=True)
        manager.store.write([{
            "id": "queued-task",
            "type": "transfer",
            "status": "queued",
            "title": "待执行任务",
            "events": [],
        }])

        ok, message = manager.delete_task("queued-task")
        assert ok is True
        manager._run_transfer("queued-task", lambda progress: calls.append("ran"))

        assert calls == []
        assert manager.get_task("queued-task") == {}
    finally:
        manager.shutdown()


def test_late_progress_cannot_overwrite_terminal_task_state(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    try:
        manager.store.write([{
            "id": "done-task",
            "type": "transfer",
            "status": "success",
            "phase": "completed",
            "phase_label": "转存完成",
            "progress": 100,
            "message": "ok",
            "events": [],
        }])

        manager._progress_update("done-task", 45, "迟到的进度事件")

        current = manager.get_task("done-task")
        assert current["status"] == "success"
        assert current["phase"] == "completed"
        assert current["phase_label"] == "转存完成"
        assert current["progress"] == 100
        assert current["message"] == "ok"
    finally:
        manager.shutdown()


def test_task_history_limit_never_discards_active_tasks(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    try:
        items = [
            {"id": f"terminal-{index}", "status": "success"}
            for index in range(305)
        ]
        items.extend([
            {"id": f"active-{index}", "status": "running"}
            for index in range(3)
        ])
        manager._save(items)

        saved = manager.list_tasks()
        saved_ids = {item["id"] for item in saved}
        assert {"active-0", "active-1", "active-2"} <= saved_ids
        assert len(saved) == 300
        assert "terminal-0" not in saved_ids
        assert "terminal-304" in saved_ids
    finally:
        manager.shutdown()


def test_executor_submit_failure_is_recorded_and_retry_remains_safe(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    calls = []
    payload = {
        "movie": {"title": "执行器关闭测试"},
        "candidate": {"pwd_id": "share-submit-failure", "files": [{"fid": "file-1"}]},
        "target_fid": "target-1",
    }

    def runner(progress):
        calls.append("ran")
        return True, "ok", {"success": 1}

    try:
        manager.shutdown()

        task = manager.create_transfer_task(payload, runner)
        assert task["status"] == "failed"
        assert task["recovery_uncertain"] is False
        assert "尚未开始转存" in task["message"]
        assert calls == []

        retry = manager.retry_transfer(task["id"], runner)
        assert retry is not None
        assert retry["status"] == "failed"
        assert retry["recovery_uncertain"] is False
        assert "尚未开始转存" in retry["message"]
        assert calls == []
        assert len(manager.list_tasks()) == 2
    finally:
        manager.shutdown()


def test_transfer_worker_does_not_expose_exception_details(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    payload = {
        "movie": {"title": "异常信息脱敏测试"},
        "candidate": {"pwd_id": "share-secret-test", "files": [{"fid": "file-1"}]},
        "target_fid": "target-1",
    }

    def runner(_progress):
        raise RuntimeError("upstream cookie=SECRET_COOKIE_VALUE")

    try:
        task = manager.create_transfer_task(payload, runner)
        manager.executor.shutdown(wait=True)
        failed = manager.get_task(task["id"])

        assert failed["status"] == "failed"
        assert failed["recovery_uncertain"] is True
        assert failed["phase_label"] == "结果待核实"
        assert failed["message"] == "转存结果待核实，请检查目标网盘后再决定是否重试"
        assert "SECRET_COOKIE_VALUE" not in failed["message"]
        assert all("SECRET_COOKIE_VALUE" not in event["message"] for event in failed["events"])
        assert manager.retry_transfer(
            task["id"],
            lambda progress: (True, "不应重试", {"success": 1}),
        ) is None
    finally:
        manager.shutdown()


def test_malformed_transfer_runner_result_is_not_marked_success(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    payload = {
        "movie": {"title": "返回值契约测试"},
        "candidate": {"pwd_id": "share-contract", "files": [{"fid": "file-1"}]},
    }
    try:
        task = manager.create_transfer_task(
            payload,
            lambda progress: ("false", "插件返回了非布尔成功标志", {"success": 1}),
        )
        manager.executor.shutdown(wait=True)
        failed = manager.get_task(task["id"])

        assert failed["status"] == "failed"
        assert failed["recovery_uncertain"] is True
        assert failed["phase_label"] == "结果待核实"
        assert failed["message"] == "转存结果待核实，请检查目标网盘后再决定是否重试"
        assert manager.retry_transfer(
            task["id"],
            lambda progress: (True, "不应重试", {"success": 1}),
        ) is None
    finally:
        manager.shutdown()


def test_malformed_transfer_runner_counts_are_not_silently_ignored(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", logging.getLogger("test"))
    payload = {
        "movie": {"title": "统计契约测试"},
        "candidate": {"pwd_id": "share-counts", "files": [{"fid": "file-1"}]},
    }
    try:
        task = manager.create_transfer_task(
            payload,
            lambda progress: (True, "看似成功", None),
        )
        manager.executor.shutdown(wait=True)
        failed = manager.get_task(task["id"])

        assert failed["status"] == "failed"
        assert failed["recovery_uncertain"] is True
        assert failed["message"] == "转存结果待核实，请检查目标网盘后再决定是否重试"
    finally:
        manager.shutdown()


def test_transfer_start_status_write_failure_is_safe_and_does_not_run_remote_runner(tmp_path, monkeypatch):
    manager = TaskManager(tmp_path / "startup-status-write-failure.json", logging.getLogger("test"))
    calls = []
    task = {
        "id": "queued-startup-failure",
        "type": "transfer",
        "status": "queued",
        "phase": "waiting",
        "phase_label": "等待执行",
        "progress": 0,
        "message": "任务已创建，等待执行",
        "events": [],
        "created_at": 1,
        "updated_at": 1,
    }
    manager.shutdown()
    manager.store.write([task])
    original_update = manager._update
    failed_once = False

    def fail_running_update(task_id, **changes):
        nonlocal failed_once
        if changes.get("status") == "running" and not failed_once:
            failed_once = True
            raise OSError("simulated task status persistence failure")
        return original_update(task_id, **changes)

    monkeypatch.setattr(manager, "_update", fail_running_update)
    try:
        manager._run_transfer(task["id"], lambda progress: calls.append("ran"))

        current = manager.get_task(task["id"])
        assert calls == []
        assert current["status"] == "failed"
        assert current["recovery_uncertain"] is False
        assert current["phase_label"] == "启动失败"
        assert "尚未开始转存" in current["message"]
    finally:
        manager.shutdown()


def test_task_recovery_survives_malformed_counts_and_event_history(tmp_path):
    import json

    path = tmp_path / "malformed-task-history.json"
    path.write_text(
        json.dumps([
            {
                "id": "queued-corrupt-history",
                "type": "transfer",
                "status": "queued",
                "failed_count": "not-a-number",
                "events": "legacy-invalid-value",
            },
            {
                "id": "running-corrupt-history",
                "type": "transfer",
                "status": "running",
                "failed_count": None,
                "events": [None, "invalid event", {"message": "older event"}],
            },
        ]),
        encoding="utf-8",
    )

    manager = TaskManager(path, logging.getLogger("test"))
    try:
        recovered = {task["id"]: task for task in manager.list_tasks()}

        queued = recovered["queued-corrupt-history"]
        assert queued["status"] == "failed"
        assert queued["failed_count"] == 1
        assert isinstance(queued["events"], list)
        assert all(isinstance(event, dict) for event in queued["events"])
        assert "可安全重试" in queued["message"]

        running = recovered["running-corrupt-history"]
        assert running["status"] == "failed"
        assert running["failed_count"] == 1
        assert running["recovery_uncertain"] is True
        assert isinstance(running["events"], list)
        assert all(isinstance(event, dict) for event in running["events"])
        assert "检查目标网盘" in running["message"]
    finally:
        manager.shutdown()


def test_transfer_task_fingerprint_is_namespaced_by_resource_provider():
    base = {
        "movie": {"title": "Same title"},
        "candidate": {
            "resource_id": "same-share-id",
            "files": [{"fid": "same-file"}],
            "storage_target_id": "cloud-a",
        },
        "target_fid": "0",
    }
    other_provider = {
        "movie": {"title": "Same title"},
        "candidate": {
            "resource_id": "same-share-id",
            "resource_type": "cloud_b_share",
            "files": [{"fid": "same-file"}],
            "storage_target_id": "cloud-a",
        },
        "target_fid": "0",
    }
    assert TaskManager._payload_key(base) != TaskManager._payload_key(other_provider)

    # Different display metadata must not permit a duplicate transfer of the
    # same provider resource, file set, and destination.
    renamed = {
        **base,
        "movie": {"title": "A completely different display title", "cover": "other-cover"},
        "title": "Another alias",
    }
    assert TaskManager._payload_key(base) == TaskManager._payload_key(renamed)

    other_destination = {**base, "target_fid": "another-folder"}
    assert TaskManager._payload_key(base) != TaskManager._payload_key(other_destination)

    other_resource = {
        **base,
        "candidate": {**base["candidate"], "resource_id": "different-share-id"},
    }
    assert TaskManager._payload_key(base) != TaskManager._payload_key(other_resource)
    assert TaskManager._phase_for_progress(70)[1] == "提交转存"


def test_subscription_accepts_provider_neutral_resource_identity(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        lambda: "unused",
        logging.getLogger("test"),
    )
    try:
        sub = manager.add_subscription(
            title="跨网盘测试",
            resource_id="share-789",
            resource_type="cloud_b_share",
            storage_target_id="cloud-b",
        )
        assert sub["resource_id"] == "share-789"
        assert sub["resource_type"] == "cloud_b_share"
        assert sub["pwd_id"] == ""
    finally:
        manager.stop_scheduler()
