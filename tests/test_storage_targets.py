from moviesync.cards import CardManifest, CardRegistry, StorageTargetCard
from moviesync.services.storage_targets import StorageTargetManager


class FakeStore:
    def __init__(self, default_target_id=""):
        self.default_target_id = default_target_id

    def load(self):
        return {
            "cards": {
                "demo-storage": {"enabled": True, "config": {}},
                "disabled-storage": {"enabled": False, "config": {}},
            }
        }

    def get_default_storage_target_id(self):
        return self.default_target_id


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


class DemoStorage(StorageTargetCard):
    manifest = CardManifest(
        id="demo-storage",
        name="Demo Storage",
        type="storage_target",
        capabilities=(
            "storage.check",
            "storage.resolve_resource",
            "storage.list_files",
            "storage.create_folder",
            "storage.transfer",
        ),
    )

    def resolve_resource(self, resource):
        return {
            "files": [{"fid": "1"}],
            "token": "demo-token",
            "error": None,
        }

    def list_files(self, resource):
        return [{"fid": "1"}]

    def create_folder(self, name, parent_id="0"):
        return "folder-1"

    def transfer(self, resource, files, target_id="0"):
        return True, "ok"


class DisabledStorage(DemoStorage):
    manifest = CardManifest(
        id="disabled-storage",
        name="Disabled Storage",
        type="storage_target",
        capabilities=DemoStorage.manifest.capabilities,
    )



class QuarkCompatibleStorage(DemoStorage):
    manifest = CardManifest(
        id="quark-compatible",
        name="Quark Compatible",
        type="storage_target",
        capabilities=(
            "storage.check",
            "storage.accepts.quark_share",
            "storage.resolve_resource",
            "storage.list_files",
            "storage.create_folder",
            "storage.transfer",
        ),
    )


def test_storage_target_selection_uses_resource_capabilities_not_card_ids():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(QuarkCompatibleStorage())
    manager = StorageTargetManager(registry, FakeStore(default_target_id="demo-storage"), FakeLogger())

    assert manager.select_target_id({"resource_type": "quark_share"}) == "quark-compatible"
    result = manager.resolve_resource({"pwd_id": "abc", "resource_type": "quark_share"})
    assert result["target_id"] == "quark-compatible"
    assert result["token"] == "demo-token"



def test_capability_matching_fails_closed_when_default_target_lookup_fails():
    class BrokenDefaultStore(FakeStore):
        def get_default_storage_target_id(self):
            raise OSError("simulated default target lookup failure")

    registry = CardRegistry()
    registry.register(QuarkCompatibleStorage())
    manager = StorageTargetManager(registry, BrokenDefaultStore(), FakeLogger())

    assert manager.select_target_id({"resource_type": "quark_share"}) == ""
    result = manager.resolve_resource({"pwd_id": "abc", "resource_type": "quark_share"})
    assert result["files"] == []
    assert "没有已启用的存储卡片支持此资源类型" in result["error"]



def test_resource_type_without_compatible_target_does_not_fall_back_to_default():
    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, FakeStore(default_target_id="demo-storage"), FakeLogger())

    assert manager.select_target_id({"resource_type": "quark_share"}) == ""
    result = manager.resolve_resource({"pwd_id": "abc", "resource_type": "quark_share"})
    assert result["target_id"] == ""
    assert result["files"] == []
    assert "没有已启用的存储卡片支持此资源类型" in result["error"]



def test_explicit_incompatible_storage_target_is_rejected_without_fallback():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(QuarkCompatibleStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    result = manager.resolve_resource({
        "pwd_id": "abc",
        "resource_type": "quark_share",
        "storage_target_id": "demo-storage",
    })

    assert result["target_id"] == "demo-storage"
    assert result["files"] == []
    assert "不支持此资源类型" in result["error"]



def test_explicit_storage_target_is_never_replaced_by_compatibility_matching():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(QuarkCompatibleStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert manager.select_target_id({
        "resource_type": "quark_share",
        "storage_target_id": "demo-storage",
    }) == "demo-storage"


def test_storage_target_manager_discovers_enabled_cards():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(DisabledStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert [item["id"] for item in manager.list_targets()] == [
        "demo-storage",
        "disabled-storage",
    ]
    assert manager.get().card_id == "demo-storage"
    assert manager.get("disabled-storage") is None


def test_storage_target_manager_routes_resource_and_transfer():
    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    resolved = manager.resolve_resource({"pwd_id": "abc"})
    assert resolved["target_id"] == "demo-storage"
    assert resolved["token"] == "demo-token"

    assert manager.create_folder("Show") == "folder-1"
    ok, message = manager.transfer(
        {"pwd_id": "abc"},
        [{"fid": "1"}],
        "folder-1",
        "demo-storage",
        "demo-token",
    )
    assert ok is True
    assert message == "ok"


def test_storage_target_destination_options():
    from moviesync.cards import StorageTargetCard

    class DestinationCard(StorageTargetCard):
        card_id = "demo"

        def destination_options(self):
            return [{"id": "root", "name": "默认目录", "is_default": True}]

    from moviesync.services.storage_targets import StorageTargetManager

    class Registry:
        def get(self, target_id):
            return DestinationCard() if target_id == "demo" else None

        def find_by_type(self, card_type):
            return [DestinationCard()] if card_type == "storage_target" else []

    class Config:
        def load(self):
            return {"cards": {"demo": {"enabled": True}}}

        def get_default_storage_target_id(self):
            return "demo"

    manager = StorageTargetManager(Registry(), Config(), None)
    assert manager.destination_options("demo") == [{"id": "root", "name": "默认目录", "is_default": True}]



def test_storage_target_manager_loads_installed_plugins(monkeypatch):
    from moviesync.services import storage_targets as module

    received_context = {}

    class EntryPoint:
        name = "demo-storage"

        def load(self):
            def factory(context):
                received_context.update(context)
                return DemoStorage()

            return factory

    monkeypatch.setattr(module, "entry_points", lambda group: [EntryPoint()])
    registry = CardRegistry()
    store = FakeStore()
    logger = FakeLogger()
    manager = StorageTargetManager(registry, store, logger)

    assert manager.load_plugins({"config_store": store, "logger": logger}) == ["demo-storage"]
    assert manager.get("demo-storage").card_id == "demo-storage"
    assert received_context == {"config_store": store, "logger": logger}


def test_storage_target_manager_isolates_invalid_plugin(monkeypatch):
    from moviesync.services import storage_targets as module

    class EntryPoint:
        name = "invalid-storage"

        def load(self):
            return lambda context: object()

    monkeypatch.setattr(module, "entry_points", lambda group: [EntryPoint()])
    registry = CardRegistry()
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert manager.load_plugins() == []
    assert registry.list() == []



def test_configured_default_storage_target_is_used():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(DisabledStorage())
    manager = StorageTargetManager(
        registry, FakeStore(default_target_id="demo-storage"), FakeLogger()
    )

    assert manager.get().card_id == "demo-storage"


def test_disabled_default_storage_target_does_not_silently_fallback():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(DisabledStorage())
    manager = StorageTargetManager(
        registry, FakeStore(default_target_id="disabled-storage"), FakeLogger()
    )

    assert manager.get() is None
    result = manager.resolve_resource({"pwd_id": "abc"})
    assert result["error"] == "存储目标卡片「Disabled Storage」已停用，请重新启用后重试"


def test_missing_default_storage_target_does_not_silently_fallback():
    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(
        registry, FakeStore(default_target_id="removed-plugin"), FakeLogger()
    )

    assert manager.get() is None
    result = manager.resolve_resource({"pwd_id": "abc"})
    assert "removed-plugin" in result["error"]
    assert "重新选择默认存储目标" in result["error"]


def test_no_default_storage_target_uses_first_enabled_card():
    registry = CardRegistry()
    registry.register(DemoStorage())
    registry.register(DisabledStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert manager.get().card_id == "demo-storage"



def test_storage_manager_normalizes_malformed_plugin_results():
    class MalformedStorage(DemoStorage):
        manifest = CardManifest(
            id="malformed-storage",
            name="Malformed Storage",
            type="storage_target",
            capabilities=DemoStorage.manifest.capabilities,
        )

        def resolve_resource(self, resource):
            return ["not", "a", "mapping"]

        def list_files(self, resource):
            return {"fid": "not-a-list"}

        def destination_options(self):
            return [None]

        def create_folder(self, name, parent_id="0"):
            return None

        def transfer(self, resource, files, target_id="0"):
            return {"success": True}

    registry = CardRegistry()
    registry.register(MalformedStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    resolved = manager.resolve_resource({"pwd_id": "abc"}, "malformed-storage")
    assert resolved == {
        "target_id": "malformed-storage",
        "files": [],
        "token": None,
        "error": "存储目标资源解析失败，请检查插件实现或查看服务日志",
    }
    assert manager.list_files({"pwd_id": "abc"}, "malformed-storage") == []
    assert manager.destination_options("malformed-storage") == []
    import pytest
    with pytest.raises(RuntimeError, match="创建目录失败"):
        manager.create_folder("Show", target_id="malformed-storage")
    ok, message = manager.transfer(
        {"pwd_id": "abc"}, [{"fid": "1"}], storage_target_id="malformed-storage"
    )
    assert ok is False
    assert "转存失败" in message


def test_storage_manager_catches_plugin_exceptions_without_leaking_details():
    class ExplodingStorage(DemoStorage):
        manifest = CardManifest(
            id="exploding-storage",
            name="Exploding Storage",
            type="storage_target",
            capabilities=DemoStorage.manifest.capabilities,
        )

        def resolve_resource(self, resource):
            raise RuntimeError("cookie=SECRET_COOKIE")

        def list_files(self, resource):
            raise RuntimeError("cookie=SECRET_COOKIE")

        def destination_options(self):
            raise RuntimeError("cookie=SECRET_COOKIE")

        def create_folder(self, name, parent_id="0"):
            raise RuntimeError("cookie=SECRET_COOKIE")

        def transfer(self, resource, files, target_id="0"):
            raise RuntimeError("cookie=SECRET_COOKIE")

    registry = CardRegistry()
    registry.register(ExplodingStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert "SECRET_COOKIE" not in str(manager.resolve_resource({}, "exploding-storage"))
    assert manager.list_files({}, "exploding-storage") == []
    assert manager.destination_options("exploding-storage") == []
    import pytest
    with pytest.raises(RuntimeError, match="创建目录失败") as error:
        manager.create_folder("Show", target_id="exploding-storage")
    assert "SECRET_COOKIE" not in str(error.value)
    ok, message = manager.transfer({}, [], storage_target_id="exploding-storage")
    assert ok is False
    assert "SECRET_COOKIE" not in message
    assert "转存结果不确定" in message
    from moviesync.services.transfer_outcome import is_uncertain_transfer_message
    assert is_uncertain_transfer_message(message) is True


def test_storage_plugin_factory_closes_card_when_registration_fails(monkeypatch):
    from moviesync.services import storage_targets as module

    class CandidateStorage(DemoStorage):
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    candidate = CandidateStorage()

    class EntryPoint:
        name = "duplicate-storage"

        def load(self):
            return lambda context: candidate

    monkeypatch.setattr(module, "entry_points", lambda group: [EntryPoint()])
    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, FakeStore(), FakeLogger())

    assert manager.load_plugins() == []
    assert candidate.closed is True
    assert registry.get("demo-storage") is not candidate



def test_storage_config_read_failure_does_not_crash_and_never_guesses_target():
    class BrokenStore:
        def load(self):
            raise OSError("simulated config read failure")

        def get_default_storage_target_id(self):
            return ""

    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, BrokenStore(), FakeLogger())

    assert manager.get() is None
    assert manager._enabled_cards() == []
    targets = manager.list_targets()
    assert len(targets) == 1
    assert targets[0]["enabled"] is False
    assert targets[0]["config_error"] == "无法读取存储目标配置"
    result = manager.resolve_resource({"pwd_id": "abc"})
    assert result["files"] == []
    assert "无法读取存储目标配置" in result["error"]


def test_explicit_default_target_is_not_used_when_config_cannot_be_read():
    class BrokenDefaultStore:
        def load(self):
            raise OSError("simulated config read failure")

        def get_default_storage_target_id(self):
            return "demo-storage"

    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, BrokenDefaultStore(), FakeLogger())

    assert manager.get() is None
    result = manager.resolve_resource({"pwd_id": "abc"})
    assert result["files"] == []
    assert "无法读取存储目标配置" in result["error"]

def test_default_target_lookup_failure_is_isolated_without_fallback():
    class BrokenDefaultLookupStore:
        def load(self):
            return {"cards": {"demo-storage": {"enabled": True}}}

        def get_default_storage_target_id(self):
            raise OSError("simulated default target read failure")

    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(
        registry, BrokenDefaultLookupStore(), FakeLogger()
    )

    # A failed default lookup must not escape into the API or silently select
    # the first available storage card.
    assert manager.get() is None
    result = manager.resolve_resource({"pwd_id": "abc"})
    assert result["files"] == []
    assert "无法读取默认存储目标配置" in result["error"]


def test_transfer_rejects_resource_type_not_supported_by_target():
    registry = CardRegistry()
    registry.register(DemoStorage())
    manager = StorageTargetManager(registry, FakeStore(default_target_id="demo-storage"), FakeLogger())

    ok, message = manager.transfer(
        {"resource_id": "share-123", "resource_type": "cloud_b_share"},
        [{"fid": "file-1"}],
        "folder-1",
        "demo-storage",
        "fresh-token",
    )

    assert ok is False
    assert "不支持此资源类型" in message
