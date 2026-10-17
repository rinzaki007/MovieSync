"""Stage A recovery regression tests for durable Smart Follow diagnostics."""

import json

from moviesync.services.subscriptions import SubscriptionManager


class RecoveryLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def test_restart_preserves_unfinished_diagnostics_and_marks_run_interrupted(tmp_path):
    path = tmp_path / "subscriptions.json"
    checkpoint = {
        "phase": "transfer",
        "started_at": 100,
        "updated_at": 120,
        "files_scanned": 12,
        "video_files": 9,
        "observed_episodes": [
            {"season": 1, "episode": 1},
            {"season": 1, "episode": 3},
        ],
        "shares": [
            {
                "resource_id": "share-1",
                "files_scanned": 12,
                "episodes": [
                    {"season": 1, "episode": 1},
                    {"season": 1, "episode": 3},
                ],
            }
        ],
    }
    path.write_text(
        json.dumps(
            [
                {
                    "id": "interrupted-diagnostics",
                    "title": "测试剧集",
                    "pwd_id": "share-1",
                    "status": "running",
                    "pending_save_keys": ["share-1:file-3"],
                    "pending_save_uncertain": True,
                    "last_diagnostics": checkpoint,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = SubscriptionManager(path, None, None, RecoveryLogger())
    try:
        recovered = manager.get_subscriptions()[0]

        assert recovered["status"] == "failed"
        assert recovered["pending_save_uncertain"] is True
        assert "核对网盘" in recovered["last_error"]

        diagnostics = recovered["last_diagnostics"]
        assert diagnostics["success"] is False
        assert diagnostics["interrupted"] is True
        assert diagnostics["phase"] == "interrupted"
        assert diagnostics["started_at"] == 100
        assert diagnostics["files_scanned"] == 12
        assert diagnostics["video_files"] == 9
        assert diagnostics["observed_episodes"] == checkpoint["observed_episodes"]
        assert diagnostics["shares"] == checkpoint["shares"]
        assert diagnostics["finished_at"] is not None
        assert diagnostics["updated_at"] == diagnostics["finished_at"]

        # A restart must retain the uncertain-transfer guard; it must not
        # silently launch another remote transfer while the outcome is unknown.
        ok, message = manager.check_subscription_now("interrupted-diagnostics")
        assert ok is False
        assert "待核实" in message
    finally:
        manager.stop_scheduler()
