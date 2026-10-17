"""Task center API resilience regression tests."""

from moviesync.app import create_app


def test_task_center_handles_malformed_persisted_timestamps(tmp_path):
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    try:
        setup = client.post(
            "/api/setup",
            json={"username": "admin", "password": "password123"},
        )
        assert setup.status_code == 200

        services["tasks"].store.write([
            {
                "id": "malformed-time",
                "type": "transfer",
                "status": "success",
                "title": "旧任务",
                "updated_at": "not-a-timestamp",
                "created_at": None,
                "events": [],
            },
            {
                "id": "recent-task",
                "type": "transfer",
                "status": "success",
                "title": "新任务",
                "updated_at": 100,
                "created_at": 100,
                "events": [],
            },
            {
                "id": "non-finite-time",
                "type": "transfer",
                "status": "success",
                "title": "异常时间任务",
                "updated_at": float("nan"),
                "created_at": float("inf"),
                "events": [],
            },
        ])

        response = client.get("/api/tasks")
        assert response.status_code == 200
        tasks = response.get_json()["tasks"]
        assert [task["id"] for task in tasks] == [
            "recent-task",
            "malformed-time",
            "non-finite-time",
        ]
        non_finite = next(task for task in tasks if task["id"] == "non-finite-time")
        assert non_finite["updated_at"] == 0
        assert non_finite["created_at"] == 0

        detail = client.get("/api/tasks/non-finite-time")
        assert detail.status_code == 200
        assert detail.get_json()["task"]["updated_at"] == 0
        assert detail.get_json()["task"]["created_at"] == 0
    finally:
        services["tasks"].shutdown()
        services["subscriptions"].stop_scheduler()


def test_subscription_task_detail_preserves_media_context_and_diagnostics(tmp_path):
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    try:
        setup = client.post(
            "/api/setup",
            json={"username": "admin", "password": "password123"},
        )
        assert setup.status_code == 200

        sub = services["subscriptions"].add_subscription(
            title="兰香如故",
            pwd_id="share-1",
            media_type="tv",
            tag="电视剧",
            source_id="example-resource",
            channel="channel-1",
            channel_name="示例频道",
        )
        sub["last_diagnostics"] = {
            "schema_version": 1,
            "files_scanned": 12,
            "video_files": 10,
            "unrecognized": 2,
            "examples": [{"file": "unknown.mkv", "reason": "无法识别集数"}],
        }
        services["subscriptions"].store.write([sub])

        response = client.get(f"/api/tasks/subscription:{sub['id']}")
        assert response.status_code == 200
        task = response.get_json()["task"]
        assert task["media_type"] == "tv"
        assert task["tag"] == "电视剧"
        assert task["source_id"] == "example-resource"
        assert task["last_diagnostics"]["files_scanned"] == 12
        assert task["last_diagnostics"]["examples"][0]["file"] == "unknown.mkv"
    finally:
        services["tasks"].shutdown()
        services["subscriptions"].stop_scheduler()
