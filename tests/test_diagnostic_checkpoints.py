"""回归测试：智能追剧诊断在执行中与重启后都应可读。"""

import json
import logging
import time

from moviesync.services.subscriptions import SubscriptionManager


def test_running_subscription_persists_diagnostic_checkpoint(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        object(),
        logger=logging.getLogger("test"),
    )
    try:
        sub = manager.add_subscription(title="诊断快照测试", pwd_id="share-1")
        sub["_run_diagnostics"] = manager._new_run_diagnostics()
        sub["_run_diagnostics"]["started_at"] = time.time()
        sub["_run_diagnostics"]["files_scanned"] = 25
        sub["_run_diagnostics"]["unrecognized"] = 2
        sub["_run_diagnostics"]["examples"] = [
            {"file": "episode-unknown.mkv", "reason": "无法识别集数"}
        ]

        manager._set_status(sub, "running", "scan", "正在扫描文件")

        saved = manager.get_subscriptions()[0]
        diagnostics = saved["last_diagnostics"]
        assert diagnostics["files_scanned"] == 25
        assert diagnostics["unrecognized"] == 2
        assert diagnostics["phase"] == "scan"
        assert diagnostics["success"] is None
        assert "finished_at" not in diagnostics
        assert diagnostics["updated_at"] >= diagnostics["started_at"]
        assert diagnostics["examples"][0]["file"] == "episode-unknown.mkv"
    finally:
        manager.shutdown()


def test_restart_marks_incomplete_diagnostic_snapshot_as_interrupted(tmp_path):
    path = tmp_path / "subscriptions.json"
    now = time.time()
    path.write_text(
        json.dumps(
            [
                {
                    "id": "interrupted-run",
                    "title": "中断诊断测试",
                    "pwd_id": "share-1",
                    "status": "running",
                    "pending_save_keys": [],
                    "pending_save_uncertain": False,
                    "last_diagnostics": {
                        "schema_version": 1,
                        "started_at": now - 60,
                        "updated_at": now - 10,
                        "phase": "scan",
                        "files_scanned": 25,
                        "success": None,
                        "message": "检查进行中：扫描并识别文件",
                    },
                    "tracked_file_keys": [],
                    "saved_episodes": [],
                    "run_history": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = SubscriptionManager(path, object(), logger=logging.getLogger("test"))
    try:
        saved = manager.get_subscriptions()[0]
        diagnostics = saved["last_diagnostics"]
        assert saved["status"] == "failed"
        assert diagnostics["interrupted"] is True
        assert diagnostics["success"] is False
        assert diagnostics["phase"] == "interrupted"
        assert diagnostics["message"] == saved["last_error"]
        assert diagnostics["files_scanned"] == 25
    finally:
        manager.shutdown()
