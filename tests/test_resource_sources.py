import pytest

from card_templates.telegram import TelegramResourceSource
from moviesync.cards import (
    Card,
    CardManifest,
    CardRegistry,
    FilenameProcessorCard,
    ResourceSourceCard,
)
from moviesync.services import resource_sources as resource_sources_module
from moviesync.services.resource_sources import ResourceSourceManager


class FakeTelegramClient:
    def __init__(self):
        self.last_scan_all = None

    def search_channel(self, channel, title, scan_all=False):
        self.last_scan_all = scan_all
        return [{
            "pwd_id": "abc123",
            "channel": channel["name"],
        }]

    def check_channel_detail(self, channel):
        return {"status": "healthy", "message": "正常"}


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def test_resource_source_uses_telegram_card_config():
    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {
                        "enabled": True,
                        "config": {
                            "channels": [{"id": "movie_channel", "name": "电影频道"}],
                        },
                    }
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

        def get_resource_sources(self):
            return [{
                "id": "telegram",
                "name": "Telegram",
                "type": "telegram",
                "enabled": True,
                "health": {},
            }]

    manager = ResourceSourceManager(
        Store(), FakeLogger(), resource_cards=[TelegramResourceSource(FakeTelegramClient())]
    )
    results = manager.search(
        {"title": "测试电影"},
        Store().load(),
    )

    assert len(results) == 1
    assert results[0]["source_id"] == "telegram"
    assert results[0]["channel"] == "电影频道"



def test_resource_source_parses_episode_using_card_magic_regex():
    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {
                        "enabled": True,
                        "config": {
                            "channels": [],
                            "magic_regex": {
                                "pattern": r".*?(?<!\\d)([Ss]\\d{1,2})?([Ee]?[Pp]?[Xx]?\\d{1,3})(?!\\d).*?\\.(mp4|mkv)",
                                "replace": r"\\1\\2.\\3",
                            },
                        },
                    }
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

        def get_resource_sources(self):
            return []

    manager = ResourceSourceManager(
        Store(), FakeLogger(), resource_cards=[TelegramResourceSource(FakeTelegramClient())]
    )
    assert manager.parse_tv_episode(
        "telegram",
        "Stranger.Things.S04E01.2022.NF.WEB-DL.2160p.HEVC.HDR.DDP.mkv",
    ) == (4, 1)
    assert manager.parse_tv_episode(
        "telegram",
        "Stranger.Things.S02E09.2160p.BluRay.x265.10bit.DTS.mkv",
    ) == (2, 9)


def test_resource_source_health_uses_telegram_card_config():
    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {
                        "enabled": True,
                        "config": {
                            "channels": [{"id": "movie_channel", "name": "电影频道"}],
                        },
                    }
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            self.last = args

        def get_resource_sources(self):
            return []

    store = Store()
    manager = ResourceSourceManager(store, FakeLogger(), resource_cards=[TelegramResourceSource(FakeTelegramClient())])
    results = manager.check_all()

    assert results[0]["id"] == "telegram"
    assert results[0]["status"] == "healthy"
    assert results[0]["total"] == 1
    assert results[0]["valid_count"] == 1



def test_channel_search_scans_recent_messages_without_telegram_title_query():
    class Store:
        def load(self):
            return {
                "cards": {
                    "telegram": {
                        "enabled": True,
                        "config": {
                            "channels": [{"id": "movie_channel", "name": "电影频道"}],
                        },
                    }
                }
            }

        def update_resource_source_health(self, *args, **kwargs):
            pass

        def get_resource_sources(self):
            return []

    client = FakeTelegramClient()
    manager = ResourceSourceManager(Store(), FakeLogger(), resource_cards=[TelegramResourceSource(client)])

    results = manager.search_channel(
        "telegram",
        {"id": "movie_channel", "name": "电影频道"},
        "测试剧",
    )

    assert len(results) == 1
    assert client.last_scan_all is True


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
    registry.register(TelegramResourceSource(FakeTelegramClient()))
    registry.register(OtherCard())

    manifests = manager.get_cards()
    assert [item["id"] for item in manifests] == ["telegram"]
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
    }]



def test_telegram_card_does_not_search_removed_or_unconfigured_channel():
    client = FakeTelegramClient()
    card = TelegramResourceSource(client)

    assert card.search_channel(
        {"id": "my_test_channel", "name": "旧测试频道"},
        "测试剧",
        {"channels": [{"id": "movie_channel", "name": "正式频道"}]},
    ) == []
    assert client.last_scan_all is None


def test_resource_manager_isolates_malformed_channel_search_results():
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

