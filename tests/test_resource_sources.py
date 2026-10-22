import pytest

from moviesync.cards import (
    Card,
    CardManifest,
    CardRegistry,
    FilenameProcessorCard,
    ResourceSourceCard,
)
from moviesync.services import resource_sources as resource_sources_module
from moviesync.services.resource_sources import ResourceSourceManager


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def test_manager_registers_direct_resource_source_card():
    from moviesync.cards import CardManifest, ResourceSourceCard

    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {"enabled": True, "config": {"channels": []}},
                    "sample-source": {"enabled": True, "config": {"prefix": "test"}},
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

    class DirectCard(ResourceSourceCard):
        manifest = CardManifest(
            id="sample-source",
            name="Sample Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return [
                {"pwd_id": "sample123", "title": movie["title"] + config["prefix"]},
                None,
            ]

        def check(self, config):
            return {"status": "healthy", "message": "sample ok"}

    manager = ResourceSourceManager(
        Store(), FakeLogger(),
        resource_cards=[DirectCard()],
    )
    results = manager.search({"title": "Film"}, Store().load())

    assert any(item["source_id"] == "sample-source" for item in results)
    sample = next(item for item in results if item["source_id"] == "sample-source")
    assert sample["source_name"] == "Sample Source"
    assert sample["title"] == "Filmtest"
    assert manager.check_all()[-1]["id"] == "sample-source"


def test_manager_rejects_non_resource_card_registration():
    class OtherCard(Card):
        pass

    manager = ResourceSourceManager(object(), FakeLogger())
    with pytest.raises(TypeError):
        manager.register(OtherCard())


def test_manager_loads_installed_resource_card_entry_points(monkeypatch):
    class PluginCard(ResourceSourceCard):
        manifest = CardManifest(
            id="entrypoint-source",
            name="Entry Point Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy", "message": "ok"}

    class FakeEntryPoint:
        name = "entrypoint-source"

        def load(self):
            return lambda context: PluginCard()

    monkeypatch.setattr(resource_sources_module, "entry_points", lambda **kwargs: [FakeEntryPoint()])
    manager = ResourceSourceManager(object(), FakeLogger())

    assert manager.load_plugins({"example": True}) == ["entrypoint-source"]
    assert manager.registry.get("entrypoint-source") is not None


def test_resource_source_health_does_not_expose_plugin_exception():
    class Store:
        def load(self):
            return {
                "cards": {
                    "broken-source": {"enabled": True, "config": {}},
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

    class BrokenCard(ResourceSourceCard):
        manifest = CardManifest(
            id="broken-source",
            name="Broken Source",
            type="resource_source",
            capabilities=("resource.health_check",),
        )

        def check(self, config):
            raise RuntimeError("upstream cookie=SECRET_COOKIE_VALUE")

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[BrokenCard()],
    )

    result = next(item for item in manager.check_all() if item["id"] == "broken-source")

    assert result["status"] == "unavailable"
    assert result["message"] == "资源来源连接检查失败，请查看服务日志"
    assert "SECRET_COOKIE_VALUE" not in result["message"]


def test_resource_health_result_cannot_override_card_identity():
    from moviesync.cards import CardManifest, ResourceSourceCard

    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {"enabled": True, "config": {}},
                    "identity-source": {"enabled": True, "config": {}},
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

    class IdentitySpoofCard(ResourceSourceCard):
        manifest = CardManifest(
            id="identity-source",
            name="Identity Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {
                "id": "another-card",
                "name": "Spoofed Name",
                "type": "storage_target",
                "enabled": False,
                "status": "healthy",
                "message": "ok",
            }

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[IdentitySpoofCard()],
    )

    result = next(item for item in manager.check_all() if item["id"] == "identity-source")

    assert result["name"] == "Identity Source"
    assert result["type"] == "resource_source"
    assert result["enabled"] is True
    assert result["status"] == "healthy"



def test_resource_search_result_cannot_spoof_source_identity():
    class Store:
        def load(self):
            return {"cards": {"telegram": {"enabled": True, "config": {}},
                              "identity-source": {"enabled": True, "config": {}}}}

        def update_resource_source_health(self, *args, **kwargs):
            pass

    class SpoofCard(ResourceSourceCard):
        manifest = CardManifest(
            id="identity-source",
            name="Trusted Source Name",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return [{"pwd_id": "abc123", "source_id": "telegram", "source_name": "Impersonated Source"}]

        def check(self, config):
            return {"status": "healthy"}

    manager = ResourceSourceManager(
        Store(), FakeLogger(), resource_cards=[SpoofCard()]
    )
    results = manager.search({"title": "Film"}, Store().load())
    item = next(result for result in results if result["pwd_id"] == "abc123")

    assert item["source_id"] == "identity-source"
    assert item["source_name"] == "Trusted Source Name"



def test_resource_source_manager_reflects_registry_unregister():
    from moviesync.cards import CardRegistry

    class Store:
        def load(self):
            return {"cards": {"removable-source": {"enabled": True, "config": {}}}}

        def update_resource_source_health(self, *args, **kwargs):
            pass

        def get_resource_sources(self):
            return []

    class RemovableSource(ResourceSourceCard):
        manifest = CardManifest(
            id="removable-source",
            name="Removable Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return [{"title": "should not appear"}]

        def check(self, config):
            return {"status": "healthy"}

    registry = CardRegistry()
    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        registry=registry,
    )
    manager.register(RemovableSource())

    assert "removable-source" in manager.sources
    registry.unregister("removable-source")

    assert "removable-source" not in manager.sources
    assert manager.search({"title": "test"}, Store().load()) == []


def test_resource_plugin_factory_closes_card_when_registration_fails(monkeypatch):
    class ExistingCard(ResourceSourceCard):
        manifest = CardManifest(
            id="duplicate-source",
            name="Existing",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

    class CandidateCard(ExistingCard):
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    candidate = CandidateCard()

    class EntryPoint:
        name = "duplicate-source-plugin"

        def load(self):
            return lambda context: candidate

    monkeypatch.setattr(
        resource_sources_module, "entry_points", lambda **kwargs: [EntryPoint()]
    )
    registry = __import__("moviesync.cards", fromlist=["CardRegistry"]).CardRegistry()
    registry.register(ExistingCard())
    manager = ResourceSourceManager(
        object(), FakeLogger(), registry=registry
    )

    assert manager.load_plugins() == []
    assert candidate.closed is True
    assert registry.get("duplicate-source") is not candidate


def test_resource_manager_lists_only_resource_source_manifests():
    class OtherCard(Card):
        manifest = CardManifest(
            id="demo-storage-card",
            name="Demo Storage",
            type="storage_target",
            capabilities=("storage.transfer",),
        )

    from moviesync.cards import CardRegistry

    registry = CardRegistry()
    manager = ResourceSourceManager(
        object(), FakeLogger(), registry=registry
    )
    class GenericSource(ResourceSourceCard):
        manifest = CardManifest(
            id="generic-source",
            name="Generic Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

    registry.register(GenericSource())
    registry.register(OtherCard())

    manifests = manager.get_cards()
    assert [item["id"] for item in manifests] == ["generic-source"]
    assert all(item["type"] == "resource_source" for item in manifests)


def test_health_persistence_failure_does_not_stop_other_resource_checks():
    class HealthyCard(ResourceSourceCard):
        def __init__(self, card_id):
            self.manifest = CardManifest(
                id=card_id,
                name=card_id,
                type="resource_source",
                capabilities=("resource.health_check",),
            )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy", "message": "ok", "total": 1, "valid_count": 1}

    class Store:
        def load(self):
            return {
                "cards": {
                    "health-one": {"enabled": True, "config": {}},
                    "health-two": {"enabled": True, "config": {}},
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            raise RuntimeError("health persistence unavailable")

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[HealthyCard("health-one"), HealthyCard("health-two")],
    )

    results = manager.check_all()
    assert [item["id"] for item in results if item["id"].startswith("health-")] == [
        "health-one",
        "health-two",
    ]
    assert all(item["status"] == "healthy" for item in results if item["id"].startswith("health-"))



def test_resource_source_health_is_saved_and_reported_by_card_id():
    class Store:
        def __init__(self):
            self.data = {
                "cards": {
                    "custom-source": {
                        "enabled": True,
                        "config": {"endpoint": "https://source.example"},
                    },
                },
            }

        def load(self):
            from copy import deepcopy
            return deepcopy(self.data)

        def save_card_config(self, card_id, incoming):
            card = self.data["cards"].setdefault(card_id, {"enabled": True, "config": {}})
            card["config"].update(incoming)
            return card

    class CustomSource(ResourceSourceCard):
        manifest = CardManifest(
            id="custom-source",
            name="Custom Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy", "message": "custom source ok", "total": 2, "valid_count": 2}

    store = Store()
    manager = ResourceSourceManager(store, FakeLogger(), resource_cards=[CustomSource()])
    result = manager.check_all()[0]

    assert result["id"] == "custom-source"
    saved_health = store.data["cards"]["custom-source"]["config"]["health"]
    assert saved_health["status"] == "healthy"
    assert saved_health["message"] == "custom source ok"
    status = manager.get_status()
    assert status == [{
        "id": "custom-source",
        "name": "Custom Source",
        "type": "resource_source",
        "enabled": True,
        "health": saved_health,
    }]def test_resource_manager_isolates_malformed_channel_search_results():
    class Store:
        def load(self):
            return {
                "cards": {
                    "malformed-channel-source": {"enabled": True, "config": {}},
                }
            }

    class MalformedChannelCard(ResourceSourceCard):
        manifest = CardManifest(
            id="malformed-channel-source",
            name="Malformed Channel Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

        def search_channel(self, channel, title, config):
            return {"pwd_id": "not-a-list"}

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[MalformedChannelCard()],
    )

    assert manager.search_channel(
        "malformed-channel-source", {"id": "some_channel"}, "测试剧"
    ) == []


def test_resource_manager_owns_channel_search_result_identity():
    class Store:
        def load(self):
            return {
                "cards": {
                    "channel-identity-source": {"enabled": True, "config": {}},
                }
            }

    class SpoofedChannelCard(ResourceSourceCard):
        manifest = CardManifest(
            id="channel-identity-source",
            name="Channel Identity Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

        def search_channel(self, channel, title, config):
            return [{"pwd_id": "share123", "source_id": "telegram", "source_name": "Spoofed"}]

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[SpoofedChannelCard()],
    )

    result = manager.search_channel(
        "channel-identity-source", {"id": "source_channel"}, "测试剧"
    )

    assert result[0]["source_id"] == "channel-identity-source"
    assert result[0]["source_name"] == "Channel Identity Source"


@pytest.mark.parametrize(
    "malformed_result",
    [
        None,
        (),
        "1,2",
        (1,),
        (True, 2),
        ("1", 2),
        (1, 0),
        (1, 2, 3),
        (1.5, 2),
    ],
)
def test_filename_processor_invalid_output_does_not_block_next_processor(malformed_result):
    class Store:
        def load(self):
            return {
                "cards": {
                    "broken-processor": {"enabled": True, "config": {}},
                    "valid-processor": {"enabled": True, "config": {}},
                }
            }

    class BrokenProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="broken-processor",
            name="Broken Processor",
            type="filename_processor",
            capabilities=("filename.parse",),
        )

        def parse_episode(self, file_name, config):
            return malformed_result

    class ValidProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="valid-processor",
            name="Valid Processor",
            type="filename_processor",
            capabilities=("filename.parse",),
        )

        def parse_episode(self, file_name, config):
            return (2, 7)

    registry = CardRegistry()
    registry.register(BrokenProcessor())
    registry.register(ValidProcessor())
    manager = ResourceSourceManager(Store(), FakeLogger(), registry=registry)

    assert manager.parse_tv_episode("unknown-source", "example-video.mkv") == (2, 7)



def test_resource_health_config_read_failure_returns_isolated_unavailable_results():
    class BrokenStore:
        def load(self):
            raise OSError("simulated config disk error")

    class MustNotCheck(ResourceSourceCard):
        manifest = CardManifest(
            id="config-failure-source",
            name="Config Failure Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def __init__(self):
            self.check_calls = 0

        def search(self, movie, config):
            return []

        def check(self, config):
            self.check_calls += 1
            return {"status": "healthy"}

    card = MustNotCheck()
    manager = ResourceSourceManager(
        BrokenStore(),
        FakeLogger(),
        resource_cards=[card],
    )

    results = manager.check_all()

    assert len(results) == 1
    assert results[0]["id"] == "config-failure-source"
    assert results[0]["status"] == "unavailable"
    assert "未执行连接检查" in results[0]["message"]
    assert card.check_calls == 0


def test_channel_search_fails_safely_when_config_cannot_be_read():
    class BrokenStore:
        def load(self):
            raise OSError("simulated config disk error")

    class SearchCard(ResourceSourceCard):
        manifest = CardManifest(
            id="config-failure-search",
            name="Config Failure Search",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

        def search_channel(self, channel, title, config):
            raise AssertionError("upstream search must not run without trusted config")

    manager = ResourceSourceManager(
        BrokenStore(),
        FakeLogger(),
        resource_cards=[SearchCard()],
    )

    assert manager.search_channel("config-failure-search", {}, "test") == []
    assert manager.search_channel_updates("config-failure-search", {}, "test") == {
        "items": [],
        "cursor": None,
        "complete": False,
    }
    assert manager.search_channel_history("config-failure-search", {}, "test") == {
        "items": [],
        "cursor": None,
        "complete": False,
    }

def test_resource_source_search_runs_cards_concurrently_and_preserves_order():
    import threading

    barrier = threading.Barrier(2)

    class Store:
        def load(self):
            return {
                "cards": {
                    "first-source": {"enabled": True, "config": {}},
                    "second-source": {"enabled": True, "config": {}},
                }
            }

    class ConcurrentCard(ResourceSourceCard):
        def __init__(self, card_id):
            self.manifest = CardManifest(
                id=card_id,
                name=card_id,
                type="resource_source",
                capabilities=("resource.search",),
            )

        def search(self, movie, config):
            barrier.wait(timeout=2)
            return [{"title": self.card_id}]

        def check(self, config):
            return {"status": "healthy"}

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[ConcurrentCard("first-source"), ConcurrentCard("second-source")],
    )

    results = manager.search({"title": "Film"}, Store().load())

    assert [item["source_id"] for item in results] == ["first-source", "second-source"]



def test_resource_source_search_can_filter_to_selected_cards():
    class Store:
        def load(self):
            return {
                "cards": {
                    "source-a": {"enabled": True, "config": {}},
                    "source-b": {"enabled": True, "config": {}},
                }
            }

    class NamedCard(ResourceSourceCard):
        def __init__(self, card_id):
            self.manifest = CardManifest(
                id=card_id,
                name=card_id,
                type="resource_source",
                capabilities=("resource.search",),
            )

        def search(self, movie, config):
            return [{"title": self.card_id}]

        def check(self, config):
            return {"status": "healthy"}

    store = Store()
    manager = ResourceSourceManager(
        store,
        FakeLogger(),
        resource_cards=[NamedCard("source-a"), NamedCard("source-b")],
    )

    results = manager.search(
        {"title": "Film"},
        store.load(),
        source_ids=["source-b"],
    )

    assert [item["source_id"] for item in results] == ["source-b"]
    assert manager.search({"title": "Film"}, store.load(), source_ids=["missing"]) == []



def test_search_with_diagnostics_reports_empty_and_failed_sources_safely():
    class Store:
        def load(self):
            return {
                "cards": {
                    "good-source": {"enabled": True, "config": {}},
                    "empty-source": {"enabled": True, "config": {}},
                    "broken-source": {"enabled": True, "config": {}},
                }
            }

    class GoodSource(ResourceSourceCard):
        manifest = CardManifest(
            id="good-source",
            name="Good Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return [{"pwd_id": "share123", "title": movie["title"]}]

        def check(self, config):
            return {"status": "healthy", "message": "ok"}

    class EmptySource(ResourceSourceCard):
        manifest = CardManifest(
            id="empty-source",
            name="Empty Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy", "message": "ok"}

    class BrokenSource(ResourceSourceCard):
        manifest = CardManifest(
            id="broken-source",
            name="Broken Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            raise RuntimeError("cookie=DO_NOT_EXPOSE")

        def check(self, config):
            return {"status": "healthy", "message": "ok"}

    manager = ResourceSourceManager(
        Store(),
        FakeLogger(),
        resource_cards=[GoodSource(), EmptySource(), BrokenSource()],
    )

    items, diagnostics = manager.search_with_diagnostics({"title": "Film"}, Store().load())

    by_id = {item["id"]: item for item in diagnostics}
    assert [item["source_id"] for item in items] == ["good-source"]
    assert by_id["good-source"]["status"] == "success"
    assert by_id["good-source"]["result_count"] == 1
    assert by_id["empty-source"]["status"] == "empty"
    assert by_id["empty-source"]["result_count"] == 0
    assert by_id["broken-source"]["status"] == "error"
    assert by_id["broken-source"]["message"] == "资源来源请求失败，请检查卡片配置或查看服务日志"
    assert "DO_NOT_EXPOSE" not in str(diagnostics)
    assert all(item["duration_ms"] >= 0 for item in diagnostics)


def test_search_with_diagnostics_marks_all_invalid_records_as_partial():
    class Store:
        def load(self):
            return {"cards": {"invalid-source": {"enabled": True, "config": {}}}}

    class InvalidSource(ResourceSourceCard):
        manifest = CardManifest(
            id="invalid-source",
            name="Invalid Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return [None, "not-a-resource"]

        def check(self, config):
            return {"status": "healthy"}

    store = Store()
    manager = ResourceSourceManager(
        store,
        FakeLogger(),
        resource_cards=[InvalidSource()],
    )

    items, diagnostics = manager.search_with_diagnostics(
        {"title": "Film"},
        store.load(),
    )

    assert items == []
    assert len(diagnostics) == 1
    assert diagnostics[0]["status"] == "partial"
    assert diagnostics[0]["result_count"] == 0
    assert diagnostics[0]["invalid_items"] == 2
    assert diagnostics[0]["message"] == "已忽略 2 条格式无效的资源记录"


def test_filename_processor_invalid_priority_uses_safe_default():
    class Store:
        def load(self):
            return {
                "cards": {
                    "invalid-priority": {"enabled": True, "config": {}},
                }
            }

    class InvalidPriorityProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="invalid-priority",
            name="Invalid Priority",
            type="filename_processor",
            capabilities=("filename.parse",),
        )
        rule_priority = "not-a-number"

        def parse_episode(self, file_name, config):
            return (3, 8)

    registry = CardRegistry()
    registry.register(InvalidPriorityProcessor())
    manager = ResourceSourceManager(Store(), FakeLogger(), registry=registry)

    assert manager.parse_tv_episode("unknown-source", "sample.mkv") == (3, 8)

