from moviesync.services.subscriptions import SubscriptionManager


class FakeStorageTargets:
    def resolve_resource(self, resource, target_id=None):
        return {
            "target_id": target_id or "demo-storage",
            "files": [
                {"fid": "ep1", "file_name": "Show.S01E01.1080p.mkv"},
                {"fid": "ep2", "file_name": "Show.S01E02.1080p.mkv"},
            ],
            "token": "token",
            "error": None,
        }

    def create_folder(self, name, parent_id="0", target_id=None):
        assert target_id == "demo-storage"
        return "folder-1"

    def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
        assert storage_target_id == "demo-storage"
        assert token == "token"
        assert target_id == "folder-1"
        assert [item["fid"] for item in files] == ["ep1", "ep2"]
        return True, "ok"


class FakeResourceSources:
    def __init__(self):
        self.config_store = None
        self.shares = [
            {"channel": "demo", "pwd_id": "share1"},
            {"channel": "demo", "pwd_id": "share2"},
        ]

    def search_channel(self, source_id, channel, title):
        return list(self.shares)


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def test_subscription_uses_storage_target_card(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share123",
        target_fid="0",
        storage_target_id="demo-storage",
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 2 项" in message
    saved = manager.get_subscriptions()[0]
    assert saved["storage_target_id"] == "demo-storage"
    assert saved["saved_episodes"] == [1, 2]
    diagnostics = saved["last_diagnostics"]
    assert diagnostics["source_candidates"] == 1
    assert diagnostics["unique_shares"] == 1
    assert diagnostics["resolved_shares"] == 1
    assert diagnostics["files_scanned"] == 2
    assert diagnostics["video_files"] == 2
    assert diagnostics["queued_files"] == 2
    assert diagnostics["transferred_files"] == 2
    assert diagnostics["success"] is True


class DynamicStorageTargets(FakeStorageTargets):
    def __init__(self):
        self.files = [
            {"fid": "ep1", "file_name": "Show.S01E01.1080p.mkv"},
            {"fid": "ep2", "file_name": "Show.S01E02.1080p.mkv"},
        ]
        self.transfers = []

    def resolve_resource(self, resource, target_id=None):
        return {
            "target_id": target_id or "demo-storage",
            "files": list(self.files),
            "token": "token",
            "error": None,
        }

    def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
        self.transfers.append([item["fid"] for item in files])
        return True, "ok"


def test_subscription_transfers_selected_initial_files_and_tracks_new_files(tmp_path):
    storage = DynamicStorageTargets()
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share123",
        target_fid="0",
        storage_target_id="demo-storage",
        files=[{"fid": "ep1"}, {"fid": "ep2"}],
    )

    # Explicitly selected initial files must be included in the first transfer,
    # even though task creation also stores them as tracking markers.
    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is True
    assert "成功追更 2 项" in message
    assert storage.transfers == [["ep1", "ep2"]]

    storage.files.append(
        {"fid": "ep3", "file_name": "Show.S01E03.1080p.mkv"}
    )

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is True
    assert "成功追更 1 项" in message
    assert storage.transfers == [["ep1", "ep2"], ["ep3"]]

    saved = manager.get_subscriptions()[0]
    assert "ep3" in saved["tracked_file_keys"]


def test_legacy_subscription_uses_selected_source_episode_rule(tmp_path):
    class CustomEpisodeResourceSources(FakeResourceSources):
        def parse_tv_episode(self, source_id, file_name):
            assert source_id == "custom-source"
            if file_name == "Show - Part 05.mkv":
                return 1, 5
            return None, None

    storage = DynamicStorageTargets()
    storage.files = [{"fid": "custom5", "file_name": "Show - Part 05.mkv"}]
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        storage,
        CustomEpisodeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share123",
        storage_target_id="demo-storage",
        source_id="custom-source",
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 1 项" in message
    assert storage.transfers == [["custom5"]]
    assert manager.get_subscriptions()[0]["saved_episodes"] == [5]


class ChannelStorageTargets:
    def __init__(self):
        self.shares = {
            "share1": [
                {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            ],
            "share2": [
                {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
            ],
        }
        self.transfers = []

    def resolve_resource(self, resource, target_id=None):
        pwd_id = resource["pwd_id"]
        return {
            "target_id": target_id or "demo-storage",
            "files": list(self.shares.get(pwd_id, [])),
            "token": "token-" + pwd_id,
            "error": None,
        }

    def create_folder(self, name, parent_id="0", target_id=None):
        return "folder-1"

    def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
        self.transfers.append((resource["pwd_id"], [item["fid"] for item in files]))
        return True, "ok"


def test_subscription_monitors_selected_channel_shares(tmp_path):
    storage = ChannelStorageTargets()
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is True
    assert "成功追更 2 项" in message
    # The selected file is part of the initial transfer request even when the
    # API supplied only its FID and the filename is resolved during the scan.
    assert storage.transfers == [("share1", ["ep1"]), ("share2", ["ep2"])]

    storage.shares["share3"] = [
        {"fid": "ep3", "file_name": "Show.S01E03.mkv"},
    ]
    manager.resource_sources.shares.append(
        {"channel": "demo", "pwd_id": "share3"}
    )

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is True
    assert "成功追更 1 项" in message
    assert storage.transfers[-1] == ("share3", ["ep3"])


def test_channel_subscription_diagnostics_record_non_video_missing_ids_and_unrecognized(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-diagnostics": [
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            {"fid": "poster", "file_name": "poster.jpg"},
            {"file_name": "Show.S01E02.mkv"},
            {"fid": "unknown", "file_name": "unclassified-video.mkv"},
            "malformed-file-entry",
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-diagnostics"}]
    manager = SubscriptionManager(
        tmp_path / "diagnostics-complete.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-diagnostics",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, _message = manager.check_subscription_now(sub["id"])

    assert ok is True
    saved = manager.get_subscriptions()[0]
    diagnostics = saved["last_diagnostics"]
    assert diagnostics["source_candidates"] == 1
    assert diagnostics["unique_shares"] == 1
    assert diagnostics["resolved_shares"] == 1
    assert diagnostics["files_scanned"] == 5
    assert diagnostics["video_files"] == 3
    assert diagnostics["non_video_files"] == 1
    assert diagnostics["missing_file_id"] == 1
    assert diagnostics["invalid_file_entries"] == 1
    assert diagnostics["unrecognized"] == 1
    assert diagnostics["queued_files"] == 1
    assert diagnostics["transferred_files"] == 1
    assert len(diagnostics["shares"]) == 1
    assert diagnostics["shares"][0]["resource_id"] == "share-diagnostics"
    assert diagnostics["shares"][0]["files_scanned"] == 5
    assert diagnostics["shares"][0]["video_files"] == 3
    assert diagnostics["shares"][0]["queued_files"] == 1
    reasons = {item["reason"] for item in diagnostics["examples"]}
    assert "非视频文件，未进入追剧识别" in reasons
    assert "文件条目缺少 fid，无法追踪或转存" in reasons
    assert "无法识别季集，未自动转存" in reasons
    assert any("忽略非字典文件条目" in item["reason"] for item in diagnostics["examples"])
    manager.stop_scheduler()


def test_channel_subscription_uses_selected_episode_as_baseline(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share44": [
            {"fid": "ep44", "file_name": "Show.S01E44.mkv"},
        ],
        "share45": [
            {"fid": "ep45", "file_name": "Show.S01E45.mkv"},
        ],
        "share10": [
            {"fid": "ep10", "file_name": "Show.S01E10.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share10"},
        {"channel": "demo", "pwd_id": "share44"},
        {"channel": "demo", "pwd_id": "share45"},
    ]
    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share44",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep44", "file_name": "Show.S01E44.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 2 项" in message
    assert storage.transfers == [("share44", ["ep44"]), ("share45", ["ep45"])]


def test_channel_subscription_uses_season_and_episode_baseline(tmp_path):
    cases = [
        (
            "season-one",
            "Show.S01E08.mkv",
            ["ep108", "ep201", "ep202"],
        ),
        (
            "season-two",
            "Show.S02E01.mkv",
            ["ep201", "ep202"],
        ),
    ]

    for case_name, selected_name, expected_fids in cases:
        storage = ChannelStorageTargets()
        storage.shares = {
            "share-seasons": [
                {"fid": "ep101", "file_name": "Show.S01E01.mkv"},
                {"fid": "ep108", "file_name": "Show.S01E08.mkv"},
                {"fid": "ep201", "file_name": "Show.S02E01.mkv"},
                {"fid": "ep202", "file_name": "Show.S02E02.mkv"},
            ],
        }
        sources = FakeResourceSources()
        sources.shares = [
            {"channel": "demo", "pwd_id": "share-seasons"},
        ]
        manager = SubscriptionManager(
            tmp_path / f"{case_name}.json",
            storage,
            sources,
            FakeLogger(),
        )
        selected_fid = "ep108" if "S01E08" in selected_name else "ep201"
        sub = manager.add_subscription(
            title="Show",
            pwd_id="share-seasons",
            target_fid="0",
            storage_target_id="demo-storage",
            source_id="telegram",
            channel="demo",
            files=[{"fid": selected_fid, "file_name": selected_name}],
        )

        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更" in message
        assert [fid for _, fids in storage.transfers for fid in fids] == expected_fids



def test_channel_subscription_tracks_selected_episode_and_later_seasons_in_one_share(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-seasons": [
            {"fid": "ep207", "file_name": "Show.S02E07.mkv"},
            {"fid": "ep208", "file_name": "Show.S02E08.mkv"},
            {"fid": "ep301", "file_name": "Show.S03E01.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-seasons"}]
    manager = SubscriptionManager(
        tmp_path / "same-share.json",
        storage,
        sources,
        FakeLogger(),
    )

    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-seasons",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep207", "file_name": "Show.S02E07.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 3 项" in message
    assert storage.transfers == [("share-seasons", ["ep207", "ep208", "ep301"])]


def test_channel_subscription_tracks_later_episodes_across_separate_shares(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep207", "file_name": "Show.S02E07.mkv"},
        ],
        "share-next": [
            {"fid": "ep208", "file_name": "Show.S02E08.mkv"},
        ],
        "share-next-season": [
            {"fid": "ep301", "file_name": "Show.S03E01.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share-selected"},
        {"channel": "demo", "pwd_id": "share-next"},
        {"channel": "demo", "pwd_id": "share-next-season"},
    ]
    manager = SubscriptionManager(
        tmp_path / "multi-share.json",
        storage,
        sources,
        FakeLogger(),
    )

    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep207", "file_name": "Show.S02E07.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 3 项" in message
    assert storage.transfers == [
        ("share-selected", ["ep207"]),
        ("share-next", ["ep208"]),
        ("share-next-season", ["ep301"]),
    ]


def test_channel_subscription_checks_selected_share_when_channel_search_is_empty(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep108", "file_name": "Show.S01E08.mkv"},
            {"fid": "ep109", "file_name": "Show.S01E09.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = []
    manager = SubscriptionManager(
        tmp_path / "empty-channel-search.json",
        storage,
        sources,
        FakeLogger(),
    )

    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep108", "file_name": "Show.S01E08.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 2 项" in message
    assert storage.transfers == [("share-selected", ["ep108", "ep109"])]



def test_channel_subscription_checkpoints_successful_share_before_later_share_fails(tmp_path):
    class FailsOnceOnSecondShare(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.failed = False

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            if resource["pwd_id"] == "share2" and not self.failed:
                self.failed = True
                self.transfers.append((resource["pwd_id"], [item["fid"] for item in files]))
                return False, "模拟第二个分享转存失败"
            return super().transfer(resource, files, target_id, storage_target_id, token)

    storage = FailsOnceOnSecondShare()
    storage.shares = {
        "share1": [{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
        "share2": [{"fid": "ep2", "file_name": "Show.S01E02.mkv"}],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share1"}, {"channel": "demo", "pwd_id": "share2"}]
    manager = SubscriptionManager(tmp_path / "partial-success.json", storage, sources, FakeLogger())
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is False
    assert "转存失败" in message
    saved = manager.get_subscriptions()[0]
    assert "share1:ep1" in saved["tracked_file_keys"]
    assert "share1:ep1" in saved["saved_episodes"]

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is True
    assert "成功追更 1 项" in message
    assert storage.transfers.count(("share1", ["ep1"])) == 1
    assert storage.transfers.count(("share2", ["ep2"])) == 2


def test_uncertain_subscription_transfer_is_held_until_user_confirms(tmp_path):
    class UncertainStorage(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.calls += 1
            return False, "转存结果不确定：请求可能已到达网盘"

    storage = UncertainStorage()
    storage.shares = {
        "share-selected": [{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-selected"}]
    manager = SubscriptionManager(
        tmp_path / "uncertain-transfer.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is False
    assert "转存结果不确定" in message
    saved = manager.get_subscriptions()[0]
    assert saved["pending_save_uncertain"] is True
    assert saved["pending_save_keys"] == ["share-selected:ep1"]

    ok, message = manager.check_subscription_now(sub["id"])
    assert ok is False
    assert "待核实" in message
    assert storage.calls == 1

    ok, message = manager.resolve_pending_save(sub["id"], "saved")
    assert ok is True
    saved = manager.get_subscriptions()[0]
    assert saved["pending_save_keys"] == []
    assert saved["pending_save_uncertain"] is False
    assert "share-selected:ep1" in saved["tracked_file_keys"]
    assert "share-selected:ep1" in saved["saved_episodes"]
    assert storage.calls == 1


def test_interrupted_subscription_with_pending_files_recovers_as_uncertain(tmp_path):
    import json

    path = tmp_path / "interrupted-subscriptions.json"
    path.write_text(
        json.dumps([{
            "id": "interrupted",
            "title": "Show",
            "pwd_id": "share",
            "status": "running",
            "pending_save_keys": ["share:file1"],
            "tracked_file_keys": [],
            "saved_episodes": [],
            "run_history": [],
        }]),
        encoding="utf-8",
    )
    manager = SubscriptionManager(path, ChannelStorageTargets(), FakeResourceSources(), FakeLogger())
    recovered = manager.get_subscriptions()[0]
    assert recovered["status"] == "failed"
    assert recovered["pending_save_uncertain"] is True
    ok, message = manager.check_subscription_now("interrupted")
    assert ok is False
    assert "待核实" in message



class SelectiveResolutionStorageTargets(ChannelStorageTargets):
    def __init__(self, broken_shares):
        super().__init__()
        self.broken_shares = set(broken_shares)

    def resolve_resource(self, resource, target_id=None):
        if resource["pwd_id"] in self.broken_shares:
            return {
                "target_id": target_id or "demo-storage",
                "files": [],
                "token": None,
                "error": "临时解析失败",
            }
        return super().resolve_resource(resource, target_id)


def test_channel_subscription_does_not_report_no_updates_when_all_shares_fail_resolution(tmp_path):
    storage = SelectiveResolutionStorageTargets({"share1", "share2"})
    sources = FakeResourceSources()
    manager = SubscriptionManager(
        tmp_path / "all-shares-failed.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is False
    assert "频道资源解析失败" in message
    assert "暂无新更新" not in message
    saved = manager.get_subscriptions()[0]
    assert saved["status"] == "failed"
    assert saved["retry_count"] == 1
    assert storage.transfers == []


def test_channel_subscription_reports_partial_resolution_failure_without_resaving_successes(tmp_path):
    storage = SelectiveResolutionStorageTargets({"share2"})
    sources = FakeResourceSources()
    manager = SubscriptionManager(
        tmp_path / "partial-share-failure.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is False
    assert "已转存 1 项" in message
    assert "部分频道资源解析失败" in message
    assert storage.transfers == [("share1", ["ep1"])]
    saved = manager.get_subscriptions()[0]
    assert "share1:ep1" in saved["tracked_file_keys"]
    assert saved["status"] == "failed"

    # A later retry still sees the already-recorded success and never submits it twice.
    ok, _ = manager.check_subscription_now(sub["id"])
    assert ok is False
    assert storage.transfers == [("share1", ["ep1"])]



def test_stop_scheduler_keeps_live_worker_reference(tmp_path):
    from threading import Event, Thread

    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    release = Event()
    worker = Thread(target=release.wait, name="test-scheduler")
    manager.worker = worker
    worker.start()

    try:
        manager.stop_scheduler()
        assert worker.is_alive()
        assert manager.worker is worker

        # A second start must not create a competing scheduler while the old
        # worker is still finishing a blocking subscription check.
        manager.start_scheduler()
        assert manager.worker is worker
    finally:
        release.set()
        worker.join(timeout=2)
        manager.stop_scheduler()

    assert manager.worker is None


def test_subscription_worker_does_not_expose_exception_details(tmp_path, monkeypatch):
    import logging

    manager = SubscriptionManager(
        tmp_path / "subscriptions.json",
        lambda: "cookie",
        logging.getLogger("test"),
    )
    try:
        sub = manager.add_subscription(title="异常脱敏测试", pwd_id="share-1")

        def fail_with_sensitive_details(_subscription):
            raise RuntimeError("upstream cookie=SECRET_COOKIE_VALUE")

        monkeypatch.setattr(manager, "_check", fail_with_sensitive_details)
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is False
        assert message == "任务执行异常，请查看服务日志"
        current = next(item for item in manager.get_subscriptions() if item["id"] == sub["id"])
        assert "SECRET_COOKIE_VALUE" not in current["last_error"]
        assert all("SECRET_COOKIE_VALUE" not in event["message"] for event in current["run_history"])
    finally:
        manager.stop_scheduler()



def test_subscription_transfer_exception_with_pending_keys_requires_confirmation(tmp_path):
    class RaisesDuringTransfer(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.calls += 1
            raise RuntimeError("upstream worker failed unexpectedly")

    storage = RaisesDuringTransfer()
    storage.shares = {
        "share-selected": [
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-selected"}]
    manager = SubscriptionManager(
        tmp_path / "transfer-exception-pending.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "转存结果不确定" in message

        saved = manager.get_subscriptions()[0]
        assert saved["status"] == "failed"
        assert saved["pending_save_uncertain"] is True
        assert saved["pending_save_keys"] == ["share-selected:ep1"]

        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "待核实" in message
        assert storage.calls == 1
    finally:
        manager.stop_scheduler()


def test_channel_subscription_deduplicates_repeated_file_ids_before_transfer(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-selected"}]
    manager = SubscriptionManager(
        tmp_path / "duplicate-file-ids.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 2 项" in message
    assert storage.transfers == [("share-selected", ["ep1", "ep2"])]
    saved = manager.get_subscriptions()[0]
    assert saved["saved_episodes"].count("share-selected:ep1") == 1
    manager.stop_scheduler()


def test_record_success_keys_atomically_clears_pending_transfer(tmp_path):
    storage = ChannelStorageTargets()
    sources = FakeResourceSources()
    manager = SubscriptionManager(
        tmp_path / "atomic-success-checkpoint.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        manager._set_pending_save_keys(sub, ["share-selected:ep1"])
        before = manager.get_subscriptions()[0]
        assert before["pending_save_keys"] == ["share-selected:ep1"]

        manager._record_success_keys(sub, ["share-selected:ep1"])
        saved = manager.get_subscriptions()[0]

        assert "share-selected:ep1" in saved["tracked_file_keys"]
        assert "share-selected:ep1" in saved["saved_episodes"]
        assert saved["pending_save_keys"] == []
        assert saved["pending_save_uncertain"] is False
    finally:
        manager.stop_scheduler()


def test_subscription_running_guard_is_released_when_initial_status_write_fails(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "status-write-failure.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="状态写入异常", pwd_id="share-status")
    original_set_status = manager._set_status
    calls = 0

    def fail_once(subscription, status, phase, phase_label):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated subscription store failure")
        return original_set_status(subscription, status, phase, phase_label)

    monkeypatch.setattr(manager, "_set_status", fail_once)
    monkeypatch.setattr(manager, "_check", lambda subscription: (True, "检查成功"))

    try:
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "任务执行异常" in message
        assert sub["id"] not in manager.running_ids

        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert message == "检查成功"
        assert sub["id"] not in manager.running_ids
    finally:
        manager.stop_scheduler()


def test_concurrent_scheduler_start_waits_for_stop_to_finish(tmp_path, monkeypatch):
    from threading import Event, Thread

    manager = SubscriptionManager(
        tmp_path / "scheduler-lifecycle-race.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    calls = 0

    def controlled_loop():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            release_first.wait(timeout=5)
        else:
            second_entered.set()
            manager.stop_event.wait(timeout=5)

    monkeypatch.setattr(manager, "_scheduler_loop", controlled_loop)
    manager.start_scheduler()
    assert first_entered.wait(timeout=1)
    old_worker = manager.worker

    stopper = Thread(target=manager.stop_scheduler)
    starter = Thread(target=manager.start_scheduler)
    try:
        stopper.start()
        # Ensure stop has signalled the worker before racing a new start.
        assert manager.stop_event.wait(timeout=1)
        starter.start()
        release_first.set()

        stopper.join(timeout=2)
        starter.join(timeout=2)

        assert not stopper.is_alive()
        assert not starter.is_alive()
        assert calls == 2
        assert manager.worker is not old_worker
        assert second_entered.wait(timeout=1)
    finally:
        release_first.set()
        manager.stop_scheduler()


def test_running_subscription_cannot_be_deleted_during_check(tmp_path, monkeypatch):
    from threading import Event, Thread

    manager = SubscriptionManager(
        tmp_path / "atomic-check-claim.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="并发删除测试", pwd_id="share-race")
    status_entered = Event()
    release_status = Event()
    results = []

    def blocked_status(subscription, status, phase, phase_label):
        status_entered.set()
        assert release_status.wait(timeout=2)

    monkeypatch.setattr(manager, "_set_status", blocked_status)
    monkeypatch.setattr(manager, "_check", lambda subscription: (True, "checked"))

    worker = Thread(target=lambda: results.append(manager.check_subscription_now(sub["id"])))
    try:
        worker.start()
        assert status_entered.wait(timeout=1)
        assert sub["id"] in manager.running_ids
        assert manager.delete_subscription(sub["id"]) is False
        assert any(item["id"] == sub["id"] for item in manager.get_subscriptions())
        release_status.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert results == [(True, "checked")]
    finally:
        release_status.set()
        worker.join(timeout=2)
        manager.stop_scheduler()


def test_scheduler_start_requested_during_stop_timeout_restarts_after_old_worker_exits(tmp_path, monkeypatch):
    from threading import Event

    manager = SubscriptionManager(
        tmp_path / "scheduler-deferred-restart.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    first_entered = Event()
    release_first = Event()
    second_entered = Event()
    calls = 0

    def controlled_loop():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            assert release_first.wait(timeout=3)
        else:
            second_entered.set()
            manager.stop_event.wait(timeout=3)

    monkeypatch.setattr(manager, "_scheduler_loop", controlled_loop)
    manager.start_scheduler()
    assert first_entered.wait(timeout=1)
    old_worker = manager.worker

    try:
        # Model stop_scheduler timing out while the old worker is still in a
        # remote check; a concurrent start must be remembered, not discarded.
        manager.stop_event.set()
        manager.start_scheduler()
        assert manager.worker is old_worker
        release_first.set()

        assert second_entered.wait(timeout=2)
        assert calls == 2
        assert manager.worker is not old_worker
    finally:
        release_first.set()
        manager.stop_scheduler()


def test_channel_subscription_creates_separate_folders_for_each_storage_target(tmp_path):
    class MultiTargetStorage(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.folder_targets = []
            self.transfer_targets = []

        def resolve_resource(self, resource, target_id=None):
            resolved = super().resolve_resource(resource, target_id)
            resolved["target_id"] = target_id or "target-a"
            return resolved

        def create_folder(self, name, parent_id="0", target_id=None):
            self.folder_targets.append(target_id)
            return f"folder-{target_id}"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.transfer_targets.append((storage_target_id, target_id))
            return True, "ok"

    storage = MultiTargetStorage()
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share1", "storage_target_id": "target-a"},
        {"channel": "demo", "pwd_id": "share2", "storage_target_id": "target-b"},
    ]
    manager = SubscriptionManager(
        tmp_path / "multi-target-folders.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.folder_targets == ["target-a", "target-b"]
        assert storage.transfer_targets == [
            ("target-a", "folder-target-a"),
            ("target-b", "folder-target-b"),
        ]
    finally:
        manager.stop_scheduler()


def test_legacy_subscription_deduplicates_repeated_file_ids_before_transfer(tmp_path):
    storage = DynamicStorageTargets()
    storage.files = [
        {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
        {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
        {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
    ]
    manager = SubscriptionManager(
        tmp_path / "legacy-duplicate-file-ids.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        storage_target_id="demo-storage",
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.transfers == [["ep1", "ep2"]]
        saved = manager.get_subscriptions()[0]
        assert saved["saved_episodes"] == [1, 2]
    finally:
        manager.stop_scheduler()


def test_scheduled_subscription_rechecks_due_time_when_claiming_run(tmp_path, monkeypatch):
    import time

    manager = SubscriptionManager(
        tmp_path / "scheduled-due-recheck.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="计划时间竞态", pwd_id="share-due")
    calls = []

    def finish_success(subscription):
        calls.append(subscription["id"])
        return manager._finish(subscription, True, "检查完成")

    monkeypatch.setattr(manager, "_check", finish_success)

    try:
        # A manual check remains immediate and moves next_run_at into the future.
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert message == "检查完成"
        assert calls == [sub["id"]]

        saved = manager.get_subscriptions()[0]
        assert saved["next_run_at"] > time.time()

        # A scheduler using an earlier due snapshot must not launch a stale run.
        ok, message = manager.check_subscription_now(sub["id"], only_if_due=True)
        assert ok is False
        assert "计划检查时间" in message
        assert calls == [sub["id"]]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_keeps_same_share_from_distinct_storage_targets(tmp_path):
    class SameShareAcrossTargetsStorage(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.folder_targets = []
            self.transfer_targets = []

        def resolve_resource(self, resource, target_id=None):
            files = {
                "target-a": [{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
                "target-b": [{"fid": "ep2", "file_name": "Show.S01E02.mkv"}],
            }.get(target_id, [])
            return {
                "target_id": target_id,
                "files": files,
                "token": f"token-{target_id}",
                "error": None,
            }

        def create_folder(self, name, parent_id="0", target_id=None):
            self.folder_targets.append(target_id)
            return f"folder-{target_id}"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.transfer_targets.append(
                (resource["pwd_id"], storage_target_id, [item["fid"] for item in files])
            )
            return True, "ok"

    storage = SameShareAcrossTargetsStorage()
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "same-share", "storage_target_id": "target-a"},
        {"channel": "demo", "pwd_id": "same-share", "storage_target_id": "target-b"},
    ]
    manager = SubscriptionManager(
        tmp_path / "same-share-distinct-targets.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="same-share",
        target_fid="0",
        storage_target_id="target-a",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.folder_targets == ["target-a", "target-b"]
        assert storage.transfer_targets == [
            ("same-share", "target-a", ["ep1"]),
            ("same-share", "target-b", ["ep2"]),
        ]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_uses_saved_history_after_tracked_keys_are_bounded(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-selected"}]
    manager = SubscriptionManager(
        tmp_path / "bounded-tracking-history.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[],
    )

    try:
        subscriptions = manager.get_subscriptions()
        current = next(item for item in subscriptions if item["id"] == sub["id"])
        # Simulate a long-running subscription where the bounded index no
        # longer contains ep1, but the durable success history still does.
        current["tracked_file_keys"] = [f"old-share:old-{index}" for index in range(1000)]
        current["saved_episodes"] = ["share-selected:ep1"]
        manager.store.write(subscriptions)

        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 1 项" in message
        assert storage.transfers == [("share-selected", ["ep2"])]
        saved = manager.get_subscriptions()[0]
        assert "share-selected:ep1" in saved["saved_episodes"]
        assert "share-selected:ep2" in saved["saved_episodes"]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_tracks_same_file_separately_per_storage_target(tmp_path):
    class SameFileAcrossTargetsStorage(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.transfers = []
            self.fail_target_b_once = True

        def resolve_resource(self, resource, target_id=None):
            return {
                "target_id": target_id,
                "files": [{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
                "token": f"token-{target_id}",
                "error": None,
            }

        def create_folder(self, name, parent_id="0", target_id=None):
            return f"folder-{target_id}"

        def transfer(self, resource, files, target_id="0", storage_target_id=None, token=None):
            self.transfers.append(storage_target_id)
            if storage_target_id == "target-b" and self.fail_target_b_once:
                self.fail_target_b_once = False
                return False, "permission denied"
            return True, "ok"

    storage = SameFileAcrossTargetsStorage()
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "same-share", "storage_target_id": "target-a"},
        {"channel": "demo", "pwd_id": "same-share", "storage_target_id": "target-b"},
    ]
    manager = SubscriptionManager(
        tmp_path / "same-file-per-target.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="same-share",
        target_fid="0",
        storage_target_id="target-a",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, _ = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert storage.transfers == ["target-a", "target-b"]

        after_first = manager.get_subscriptions()[0]
        assert "same-share:ep1" in after_first["tracked_file_keys"]
        assert "target-b:same-share:ep1" not in after_first["tracked_file_keys"]

        # The next check must retry only target-b; target-a was already saved.
        ok, _ = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert storage.transfers == ["target-a", "target-b", "target-b"]

        saved = manager.get_subscriptions()[0]
        assert "same-share:ep1" in saved["tracked_file_keys"]
        assert "target-b:same-share:ep1" in saved["tracked_file_keys"]

        ok, _ = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert storage.transfers == ["target-a", "target-b", "target-b"]
    finally:
        manager.stop_scheduler()


def test_subscription_saved_history_normalizes_malformed_entries_before_finish(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "malformed-saved-history.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="历史兼容测试", pwd_id="share-history")

    try:
        subscriptions = manager.get_subscriptions()
        current = next(item for item in subscriptions if item["id"] == sub["id"])
        current["saved_episodes"] = [
            {"unexpected": "object"},
            ["unexpected", "list"],
            "1",
            1,
            "",
            None,
            True,
            2,
        ]
        manager.store.write(subscriptions)

        # A successful legacy completion used to build set(saved_episodes),
        # which raises TypeError if a malformed JSON entry is a list/dict.
        ok, message = manager._finish(
            sub,
            True,
            "检查完成",
            success_keys=[3],
        )

        assert ok is True
        assert message == "检查完成"
        saved = manager.get_subscriptions()[0]
        assert saved["saved_episodes"] == [2, 3, "1"]
        assert saved["status"] == "success"
    finally:
        manager.stop_scheduler()


def test_malformed_pending_save_keys_fail_closed(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "malformed-pending-keys.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="待确认记录异常", pwd_id="share-pending")
    calls = []
    try:
        items = manager.get_subscriptions()
        current = next(item for item in items if item["id"] == sub["id"])
        current["pending_save_keys"] = 123
        current["pending_save_uncertain"] = True
        current["status"] = "failed"
        manager.store.write(items)

        loaded = manager.get_subscriptions()[0]
        assert loaded["pending_save_keys"] == []
        assert loaded["pending_save_uncertain"] is True
        assert loaded["pending_save_keys_invalid"] is True

        def unexpected_check(subscription):
            calls.append(subscription["id"])
            return True, "不应执行"

        monkeypatch.setattr(manager, "_check", unexpected_check)
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "格式异常" in message
        assert calls == []

        ok, message = manager.resolve_pending_save(sub["id"], "not_saved")
        assert ok is False
        assert "格式异常" in message
    finally:
        manager.stop_scheduler()


def test_invalid_only_pending_keys_do_not_clear_fail_closed_marker(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "invalid-only-pending-keys.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="无效待确认键", pwd_id="share-invalid-pending")
    calls = []

    try:
        items = manager.get_subscriptions()
        current = next(item for item in items if item["id"] == sub["id"])
        current["pending_save_keys"] = [None, True, {}, "   "]
        current["pending_save_uncertain"] = True
        current["pending_save_keys_invalid"] = True
        current["status"] = "failed"
        manager.store.write(items)

        loaded = manager.get_subscriptions()[0]
        assert loaded["pending_save_keys"] == []
        assert loaded["pending_save_uncertain"] is True
        assert loaded["pending_save_keys_invalid"] is True

        def unexpected_check(subscription):
            calls.append(subscription["id"])
            return True, "不应执行"

        monkeypatch.setattr(manager, "_check", unexpected_check)
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "格式异常" in message
        assert calls == []
    finally:
        manager.stop_scheduler()


def test_invalid_only_pending_keys_without_marker_fail_closed(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "invalid-pending-without-marker.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="无效待确认键无标记", pwd_id="share-invalid-no-marker")
    calls = []

    try:
        items = manager.get_subscriptions()
        current = next(item for item in items if item["id"] == sub["id"])
        current["pending_save_keys"] = [None, True, {}, "   "]
        current["pending_save_uncertain"] = True
        current.pop("pending_save_keys_invalid", None)
        current["status"] = "failed"
        manager.store.write(items)

        loaded = manager.get_subscriptions()[0]
        assert loaded["pending_save_keys"] == []
        assert loaded["pending_save_uncertain"] is True
        assert loaded["pending_save_keys_invalid"] is True

        def unexpected_check(subscription):
            calls.append(subscription["id"])
            return True, "不应执行"

        monkeypatch.setattr(manager, "_check", unexpected_check)
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "格式异常" in message
        assert calls == []

        ok, message = manager.resolve_pending_save(sub["id"], "not_saved")
        assert ok is False
        assert "格式异常" in message
    finally:
        manager.stop_scheduler()


def test_recovery_keeps_invalid_pending_transfer_fail_closed(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "recovery-invalid-pending.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="重启恢复待确认记录异常", pwd_id="share-recovery-invalid")
    calls = []

    try:
        items = manager.get_subscriptions()
        current = next(item for item in items if item["id"] == sub["id"])
        current["status"] = "running"
        current["pending_save_keys"] = [None, True, {}, "   "]
        current["pending_save_uncertain"] = True
        current.pop("pending_save_keys_invalid", None)
        manager.store.write(items)

        # Simulate startup recovery after the pending list has become corrupt.
        manager._recover_interrupted_subscriptions()
        recovered = next(item for item in manager.get_subscriptions() if item["id"] == sub["id"])
        assert recovered["status"] == "failed"
        assert recovered["pending_save_keys"] == []
        assert recovered["pending_save_keys_invalid"] is True
        assert recovered["pending_save_uncertain"] is True
        assert "格式异常" in recovered["last_error"]

        def unexpected_check(subscription):
            calls.append(subscription["id"])
            return True, "不应执行"

        monkeypatch.setattr(manager, "_check", unexpected_check)
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "格式异常" in message
        assert calls == []

        ok, message = manager.resolve_pending_save(sub["id"], "not_saved")
        assert ok is False
        assert "格式异常" in message
    finally:
        manager.stop_scheduler()


def test_uncertain_subscription_without_pending_keys_fails_closed_until_user_confirms_not_saved(tmp_path, monkeypatch):
    manager = SubscriptionManager(
        tmp_path / "uncertain-without-pending-keys.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="缺少待确认文件记录", pwd_id="share-empty-pending")
    calls = []

    try:
        # Model an ambiguous result whose pending file list was lost or cleared.
        items = manager.get_subscriptions()
        current = next(item for item in items if item["id"] == sub["id"])
        current["pending_save_keys"] = []
        current["pending_save_uncertain"] = True
        current["pending_save_keys_invalid"] = False
        current["status"] = "failed"
        manager.store.write(items)

        def successful_check(subscription):
            calls.append(subscription["id"])
            return manager._finish(subscription, True, "检查完成")

        monkeypatch.setattr(manager, "_check", successful_check)

        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is False
        assert "缺少文件记录" in message
        assert calls == []

        ok, message = manager.resolve_pending_save(sub["id"], "saved")
        assert ok is False
        assert "缺少待确认文件记录" in message
        assert calls == []

        ok, message = manager.resolve_pending_save(sub["id"], "not_saved")
        assert ok is True
        assert message == "检查完成"
        assert calls == [sub["id"]]
        saved = manager.get_subscriptions()[0]
        assert saved["pending_save_uncertain"] is False
        assert saved["pending_save_keys"] == []
    finally:
        manager.stop_scheduler()


def test_subscription_finish_deduplicates_success_history_keys(tmp_path):
    manager = SubscriptionManager(
        tmp_path / "duplicate-success-history.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="重复成功记录", pwd_id="share-duplicate-history")

    try:
        ok, message = manager._finish(
            sub,
            True,
            "检查完成",
            success_keys=[3, 3, 4, 4],
        )

        assert ok is True
        assert message == "检查完成"
        saved = manager.get_subscriptions()[0]
        assert saved["saved_episodes"] == [3, 4]
        assert saved["tracked_file_keys"] == ["3", "4"]
        # The run counter still reflects the executioner's reported successes.
        assert saved["run_history"][-1]["success_count"] == 4
    finally:
        manager.stop_scheduler()


def test_concurrent_manual_and_scheduled_checks_do_not_run_same_subscription_twice(tmp_path, monkeypatch):
    from threading import Event, Thread

    manager = SubscriptionManager(
        tmp_path / "subscription-concurrent-check.json",
        FakeStorageTargets(),
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(title="并发检查保护", pwd_id="share-concurrent")
    check_entered = Event()
    release_check = Event()
    calls = []
    first_result = []

    def controlled_check(subscription):
        calls.append(subscription["id"])
        check_entered.set()
        assert release_check.wait(timeout=3)
        return manager._finish(subscription, True, "检查完成")

    monkeypatch.setattr(manager, "_check", controlled_check)
    worker = Thread(
        target=lambda: first_result.append(manager.check_subscription_now(sub["id"]))
    )

    try:
        worker.start()
        assert check_entered.wait(timeout=1)

        # A scheduler tick can use a stale due snapshot while a manual check
        # is already in flight. The same subscription must still be single-run.
        second_ok, second_message = manager.check_subscription_now(
            sub["id"],
            only_if_due=True,
        )
        assert second_ok is False
        assert "正在执行" in second_message
        assert calls == [sub["id"]]

        release_check.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert first_result == [(True, "检查完成")]
        assert calls == [sub["id"]]
        assert sub["id"] not in manager.running_ids
    finally:
        release_check.set()
        worker.join(timeout=2)
        manager.stop_scheduler()


def test_channel_subscription_deduplicates_overlapping_episode_ranges_across_shares(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep101", "file_name": "Show.S01E01.mkv"},
        ],
        "share-range-a": [
            {"fid": "ep102-a", "file_name": "Show.S01E02.mkv"},
        ],
        "share-range-b": [
            {"fid": "ep102-b", "file_name": "Show.S01E02.mkv"},
            {"fid": "ep202", "file_name": "Show.S02E02.mkv"},
            {"fid": "ep103", "file_name": "Show.S01E03.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share-range-a"},
        {"channel": "demo", "pwd_id": "share-range-b"},
        {"channel": "demo", "pwd_id": "share-selected"},
    ]
    manager = SubscriptionManager(
        tmp_path / "overlapping-ranges.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep101", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 4 项" in message
    assert storage.transfers == [
        ("share-selected", ["ep101"]),
        ("share-range-a", ["ep102-a"]),
        ("share-range-b", ["ep202", "ep103"]),
    ]


def test_channel_subscription_reuses_existing_task_when_followed_again(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep108", "file_name": "Show.S01E08.mkv"},
        ],
        "share-next": [
            {"fid": "ep109", "file_name": "Show.S01E09.mkv"},
            {"fid": "ep110", "file_name": "Show.S01E10.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share-selected"},
        {"channel": "demo", "pwd_id": "share-next"},
    ]
    manager = SubscriptionManager(
        tmp_path / "repeat-follow-click.json",
        storage,
        sources,
        FakeLogger(),
    )

    try:
        first = manager.add_subscription(
            title="Show",
            pwd_id="share-selected",
            target_fid="0",
            storage_target_id="demo-storage",
            source_id="telegram",
            channel="demo",
            files=[{"fid": "ep108", "file_name": "Show.S01E08.mkv"}],
        )
        ok, message = manager.check_subscription_now(first["id"])
        assert ok is True
        assert "成功追更 3 项" in message
        assert storage.transfers == [
            ("share-selected", ["ep108"]),
            ("share-next", ["ep109", "ep110"]),
        ]

        second = manager.add_subscription(
            title="show",
            pwd_id="share-selected",
            target_fid="0",
            storage_target_id="demo-storage",
            source_id="telegram",
            channel="demo",
            files=[{"fid": "ep108", "file_name": "Show.S01E08.mkv"}],
        )
        assert second["id"] == first["id"]
        assert len(manager.get_subscriptions()) == 1

        ok, message = manager.check_subscription_now(second["id"])
        assert ok is True
        assert "暂无新更新" in message
        assert storage.transfers == [
            ("share-selected", ["ep108"]),
            ("share-next", ["ep109", "ep110"]),
        ]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_persists_episode_deduplication_across_new_shares(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep108", "file_name": "Show.S01E08.mkv"},
        ],
        "share-next": [
            {"fid": "ep109", "file_name": "Show.S01E09.mkv"},
            {"fid": "ep110", "file_name": "Show.S01E10.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share-selected"},
        {"channel": "demo", "pwd_id": "share-next"},
    ]
    manager = SubscriptionManager(
        tmp_path / "persistent-episode-dedup.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep108", "file_name": "Show.S01E08.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert "成功追更 3 项" in message
        assert storage.transfers == [
            ("share-selected", ["ep108"]),
            ("share-next", ["ep109", "ep110"]),
        ]

        # The channel search now exposes another share with different file IDs
        # for episodes already transferred from the original shares.
        storage.shares["share-reposted"] = [
            {"fid": "copy108", "file_name": "Show.S01E08.mkv"},
            {"fid": "copy109", "file_name": "Show.S01E09.mkv"},
            {"fid": "copy110", "file_name": "Show.S01E10.mkv"},
        ]
        sources.shares.append({"channel": "demo", "pwd_id": "share-reposted"})

        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert "暂无新更新" in message
        assert storage.transfers == [
            ("share-selected", ["ep108"]),
            ("share-next", ["ep109", "ep110"]),
        ]
        saved = manager.get_subscriptions()[0]
        assert saved["saved_episode_sources"]["demo-storage|1|9"] == "share-next"
        assert saved["saved_episode_sources"]["demo-storage|1|10"] == "share-next"
    finally:
        manager.stop_scheduler()


def test_channel_subscription_resolves_multiple_shares_concurrently(tmp_path):
    from threading import Barrier

    class ConcurrentStorageTargets(ChannelStorageTargets):
        def __init__(self):
            super().__init__()
            self.resolve_barrier = Barrier(2)

        def resolve_resource(self, resource, target_id=None):
            # Sequential resolution times out here; two concurrent calls pass.
            self.resolve_barrier.wait(timeout=2)
            return super().resolve_resource(resource, target_id)

    storage = ConcurrentStorageTargets()
    sources = FakeResourceSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share1"},
        {"channel": "demo", "pwd_id": "share2"},
    ]
    manager = SubscriptionManager(
        tmp_path / "concurrent-channel.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share1",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 2 项" in message
    assert storage.transfers == [("share1", ["ep1"]), ("share2", ["ep2"])]


def test_channel_subscription_with_legacy_all_mode_still_applies_episode_baseline(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-seasons": [
            {"fid": "ep101", "file_name": "Show.S01E01.mkv"},
            {"fid": "ep108", "file_name": "Show.S01E08.mkv"},
            {"fid": "ep109", "file_name": "Show.S01E09.mkv"},
            {"fid": "ep201", "file_name": "Show.S02E01.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-seasons"}]
    manager = SubscriptionManager(
        tmp_path / "legacy-all-channel.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-seasons",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep108", "file_name": "Show.S01E08.mkv"}],
    )

    # Reproduce a legacy/migrated record whose old mode would otherwise
    # bypass the channel-specific season/episode guard.
    persisted = manager.get_subscriptions()
    current = next(item for item in persisted if item["id"] == sub["id"])
    current["tracking_mode"] = "all"
    manager.store.write(persisted)

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 3 项" in message
    assert storage.transfers == [
        ("share-seasons", ["ep108", "ep109", "ep201"]),
    ]
    assert all(
        fid != "ep101"
        for _share, fids in storage.transfers
        for fid in fids
    )



def test_channel_subscription_falls_back_to_generic_parser_for_numeric_episode(tmp_path):
    class NumericEpisodeFallbackSources(FakeResourceSources):
        def parse_tv_episode(self, source_id, file_name):
            # Simulate a card parser that cannot recognize plain "45.mkv".
            return None, None

    storage = ChannelStorageTargets()
    storage.shares = {
        "share-numeric": [
            {"fid": "ep44", "file_name": "44.mkv"},
            {"fid": "ep45", "file_name": "45.mkv"},
            {"fid": "ep46", "file_name": "46.mkv"},
        ],
    }
    sources = NumericEpisodeFallbackSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-numeric"}]
    manager = SubscriptionManager(
        tmp_path / "numeric-episode-fallback.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-numeric",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep45", "file_name": "45.mkv"}],
    )

    try:
        assert sub["start_ep"] == 45
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.transfers == [("share-numeric", ["ep45", "ep46"])]
    finally:
        manager.stop_scheduler()



def test_channel_subscription_preserves_season_when_card_parser_omits_it(tmp_path):
    class SeasonDroppingSources(FakeResourceSources):
        def parse_tv_episode(self, source_id, file_name):
            # Simulate a card rule that recognizes E06 but drops season 03.
            if file_name == "Show.S03E06.mkv":
                return None, 6
            return None, None

    storage = ChannelStorageTargets()
    storage.shares = {
        "share-season-baseline": [
            {"fid": "s2e8", "file_name": "Show.S02E08.mkv"},
            {"fid": "s3e5", "file_name": "Show.S03E05.mkv"},
            {"fid": "s3e6", "file_name": "Show.S03E06.mkv"},
            {"fid": "s3e7", "file_name": "Show.S03E07.mkv"},
            {"fid": "s4e1", "file_name": "Show.S04E01.mkv"},
        ],
    }
    sources = SeasonDroppingSources()
    sources.shares = [
        {"channel": "demo", "pwd_id": "share-season-baseline"},
    ]
    manager = SubscriptionManager(
        tmp_path / "season-baseline.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-season-baseline",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "s3e6", "file_name": "Show.S03E06.mkv"}],
    )

    try:
        assert sub["start_season"] == 3
        assert sub["start_ep"] == 6
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 3 项" in message
        assert storage.transfers == [
            ("share-season-baseline", ["s3e6", "s3e7", "s4e1"]),
        ]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_transfers_selected_fid_when_creation_payload_omits_filename(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-14": [
            {"fid": f"ep{episode}", "file_name": f"Show.S01E{episode:02d}.mkv"}
            for episode in range(1, 15)
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-14"}]
    manager = SubscriptionManager(
        tmp_path / "selected-fid-without-name.json",
        storage,
        sources,
        FakeLogger(),
    )
    # Some callers send selected files as FID strings, not {fid, file_name}
    # objects. The selected episode is the baseline but must still transfer once.
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-14",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        start_ep=1,
        files=["ep1"],
    )

    try:
        assert sub["initial_file_keys"] == ["ep1"]
        assert "share-14:ep1" not in sub["tracked_file_keys"]
        ok, message = manager.check_subscription_now(sub["id"])
        assert ok is True
        assert "成功追更 14 项" in message
        assert storage.transfers == [
            ("share-14", [f"ep{episode}" for episode in range(1, 15)])
        ]
    finally:
        manager.stop_scheduler()



def test_channel_subscription_recovers_initial_selection_pretracked_by_legacy_version(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-45": [
            {"fid": f"ep{episode}", "file_name": f"Show.S01E{episode:02d}.mkv"}
            for episode in range(45, 59)
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-45"}]
    manager = SubscriptionManager(
        tmp_path / "legacy-pretracked-selected-file.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-45",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep45", "file_name": "Show.S01E45.mkv"}],
    )

    try:
        # Reproduce the persisted state written by older releases when a selected
        # file was not parseable at creation time: tracked marker exists, but no
        # confirmed-success record exists. Upgrade must not lose the selected file.
        persisted = manager.get_subscriptions()
        current = next(item for item in persisted if item["id"] == sub["id"])
        current["tracked_file_keys"] = ["share-45:ep45"]
        current["saved_episodes"] = []
        manager.store.write(persisted)

        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 14 项" in message
        assert storage.transfers == [
            ("share-45", [f"ep{episode}" for episode in range(45, 59)])
        ]
        saved = manager.get_subscriptions()[0]
        assert "share-45:ep45" in saved["tracked_file_keys"]
        assert "share-45:ep45" in saved["saved_episodes"]
    finally:
        manager.stop_scheduler()


def test_channel_subscription_transfers_explicitly_selected_file_with_unrecognized_episode(tmp_path):
    storage = ChannelStorageTargets()
    storage.shares = {
        "share-unrecognized": [
            {"fid": "selected-cut", "file_name": "Show.special-cut.mkv"},
            {"fid": "new-episode", "file_name": "Show.S01E46.mkv"},
        ],
    }
    sources = FakeResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-unrecognized"}]
    manager = SubscriptionManager(
        tmp_path / "selected-unrecognized-episode.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-unrecognized",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        start_ep=45,
        files=[{"fid": "selected-cut", "file_name": "Show.special-cut.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.transfers == [
            ("share-unrecognized", ["selected-cut", "new-episode"])
        ]
    finally:
        manager.stop_scheduler()



def test_generic_all_mode_transfers_selected_episode_and_later_episodes_only(tmp_path):
    storage = DynamicStorageTargets()
    storage.files = [
        {"fid": f"ep{episode}", "file_name": f"Show.S01E{episode:02d}.mkv"}
        for episode in range(15, 25)
    ]
    manager = SubscriptionManager(
        tmp_path / "generic-all-initial-selection.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    # Without channel/source metadata this task takes the generic "all" path.
    # The selected FID is pre-marked tracked by legacy task creation logic, but
    # that marker alone must not suppress its first transfer. Episodes before
    # the selected baseline must remain excluded.
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-20",
        target_fid="0",
        storage_target_id="demo-storage",
        files=[{"fid": "ep20", "file_name": "Show.S01E20.mkv"}],
    )

    try:
        assert sub["tracking_mode"] == "all"
        assert sub["start_ep"] == 20
        assert "ep20" in sub["tracked_file_keys"]
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 5 项" in message
        assert storage.transfers == [
            [f"ep{episode}" for episode in range(20, 25)]
        ]
        saved = manager.get_subscriptions()[0]
        assert "ep20" in saved["saved_episodes"]
    finally:
        manager.stop_scheduler()


def test_generic_all_mode_does_not_retransfer_initial_fid_with_confirmed_success(tmp_path):
    storage = DynamicStorageTargets()
    storage.files = [
        {"fid": "ep45", "file_name": "Show.S01E45.mkv"},
        {"fid": "ep46", "file_name": "Show.S01E46.mkv"},
    ]
    manager = SubscriptionManager(
        tmp_path / "generic-all-initial-already-saved.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-45",
        target_fid="0",
        storage_target_id="demo-storage",
        files=[{"fid": "ep45", "file_name": "Show.S01E45.mkv"}],
    )

    try:
        persisted = manager.get_subscriptions()
        current = next(item for item in persisted if item["id"] == sub["id"])
        current["saved_episodes"] = ["ep45"]
        manager.store.write(persisted)

        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 1 项" in message
        assert storage.transfers == [["ep46"]]
    finally:
        manager.stop_scheduler()



def test_smart_follow_uses_global_filename_rules_for_channel_scan_and_season_baseline(tmp_path):
    from card_templates.filename_recognition import FilenameRecognitionCard
    from moviesync.cards import CardRegistry
    from moviesync.services.resource_sources import ResourceSourceManager

    class RuleConfigStore:
        def load(self):
            return {
                "cards": {
                    "filename_recognition": {
                        "enabled": True,
                        "config": {
                            "rules": [
                                {
                                    "pattern": r"^Raw_S(\d+)_E(\d+)[.]mkv$",
                                    "replace": r"Show.S\1E\2.mkv",
                                }
                            ]
                        },
                    }
                }
            }

    registry = CardRegistry()
    registry.register(FilenameRecognitionCard())
    parser = ResourceSourceManager(RuleConfigStore(), FakeLogger(), registry=registry)

    class SharedRuleResourceSources(FakeResourceSources):
        def parse_tv_episode(self, source_id, file_name):
            return parser.parse_tv_episode(source_id, file_name)

    storage = ChannelStorageTargets()
    storage.shares = {
        "share-seasons": [
            {"fid": "ep206", "file_name": "Raw_S02_E06.mkv"},
            {"fid": "ep207", "file_name": "Raw_S02_E07.mkv"},
            {"fid": "ep208", "file_name": "Raw_S02_E08.mkv"},
            {"fid": "ep301", "file_name": "Raw_S03_E01.mkv"},
        ],
    }
    sources = SharedRuleResourceSources()
    sources.shares = [{"channel": "demo", "pwd_id": "share-seasons"}]
    manager = SubscriptionManager(
        tmp_path / "global-rules-smart-follow.json",
        storage,
        sources,
        FakeLogger(),
    )

    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-seasons",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep207", "file_name": "Raw_S02_E07.mkv"}],
    )

    ok, message = manager.check_subscription_now(sub["id"])

    assert ok is True
    assert "成功追更 3 项" in message
    assert storage.transfers == [("share-seasons", ["ep207", "ep208", "ep301"])]



def test_channel_search_failure_is_persisted_and_not_reported_as_full_success(tmp_path):
    class FailingResourceSources(FakeResourceSources):
        def search_channel(self, source_id, channel, title):
            raise RuntimeError("temporary search outage")

    storage = ChannelStorageTargets()
    storage.shares = {
        "share-selected": [
            {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
            {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
        ],
    }
    sources = FailingResourceSources()
    manager = SubscriptionManager(
        tmp_path / "channel-search-failure-diagnostics.json",
        storage,
        sources,
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-selected",
        target_fid="0",
        storage_target_id="demo-storage",
        source_id="telegram",
        channel="demo",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        # The selected share remains usable as a fallback, but the channel scan
        # is incomplete and must not be presented as a fully successful check.
        assert ok is False
        assert "频道搜索存在 1 项异常" in message
        assert storage.transfers == [("share-selected", ["ep1", "ep2"])]
        saved = manager.get_subscriptions()[0]
        diagnostics = saved["last_diagnostics"]
        assert diagnostics["source_search_errors"] == 1
        assert diagnostics["transferred_files"] == 2
        assert diagnostics["success"] is False
        assert any("temporary search outage" in item["reason"] for item in diagnostics["examples"])
    finally:
        manager.stop_scheduler()



def test_direct_subscription_diagnostics_skip_malformed_and_non_video_entries(tmp_path):
    storage = DynamicStorageTargets()
    storage.files = [
        {"fid": "ep1", "file_name": "Show.S01E01.mkv"},
        {"fid": "poster", "file_name": "poster.jpg"},
        {"file_name": "Show.S01E02.mkv"},
        "malformed-file-entry",
        {"fid": "ep2", "file_name": "Show.S01E02.mkv"},
    ]
    manager = SubscriptionManager(
        tmp_path / "direct-diagnostics-malformed-files.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-direct",
        target_fid="0",
        storage_target_id="demo-storage",
        files=[{"fid": "ep1", "file_name": "Show.S01E01.mkv"}],
    )

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 2 项" in message
        assert storage.transfers == [["ep1", "ep2"]]
        diagnostics = manager.get_subscriptions()[0]["last_diagnostics"]
        assert diagnostics["files_scanned"] == 5
        assert diagnostics["video_files"] == 3
        assert diagnostics["non_video_files"] == 1
        assert diagnostics["missing_file_id"] == 1
        assert diagnostics["invalid_file_entries"] == 1
        assert diagnostics["queued_files"] == 2
        assert diagnostics["transferred_files"] == 2
    finally:
        manager.stop_scheduler()


def test_legacy_direct_subscription_respects_season_baseline(tmp_path):
    storage = DynamicStorageTargets()
    storage.files = [
        {"fid": "s1e99", "file_name": "Show.S01E99.mkv"},
        {"fid": "s2e1", "file_name": "Show.S02E01.mkv"},
        {"fid": "s2e2", "file_name": "Show.S02E02.mkv"},
    ]
    manager = SubscriptionManager(
        tmp_path / "legacy-direct-season-baseline.json",
        storage,
        FakeResourceSources(),
        FakeLogger(),
    )
    sub = manager.add_subscription(
        title="Show",
        pwd_id="share-direct-season",
        target_fid="0",
        storage_target_id="demo-storage",
        start_ep=1,
    )
    # Exercise the legacy path, where no explicit file list or channel is stored.
    persisted = manager.get_subscriptions()
    current = next(item for item in persisted if item["id"] == sub["id"])
    current["tracking_mode"] = "legacy"
    current["start_season"] = 2
    current["start_ep"] = 1
    manager.store.write(persisted)

    try:
        ok, message = manager.check_subscription_now(sub["id"])

        assert ok is True
        assert "成功追更 1 项" in message
        assert storage.transfers == [["s2e2"]]
    finally:
        manager.stop_scheduler()
