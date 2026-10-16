"""配置层单元测试。

覆盖：敏感 Cookie 保留、公共配置脱敏、频道规范化/去重以及非法频道拒绝。
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from moviesync.config_store import ConfigStore, ConfigValidationError


def test_config_preserves_secret_when_not_replaced(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save({"quark_cookie": "secret-cookie", "channels": [{"name": "测试", "id": "test_channel"}]})
    store.save({"default_fid": "123"})
    assert store.get_cookie() == "secret-cookie"
    public = store.public()
    assert "quark_cookie" not in public
    assert public["cards"]["quark"]["config"]["has_cookie"] is True


def test_telegram_rejects_nested_unbounded_regex_without_overwriting_config(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    original = store.get_telegram_config()["magic_regex"]

    with pytest.raises(ConfigValidationError, match="高风险的嵌套无限量词"):
        store.save_telegram_config({
            "magic_regex": {"pattern": r"(.+)+$", "replace": r"\1"},
        })

    assert store.get_telegram_config()["magic_regex"] == original


def test_redacted_quark_cookie_import_does_not_clear_existing_cookie(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save_quark_config({"cookie": "keep-this-quark-cookie"})

    # The public/exported representation redacts the cookie to an empty string.
    exported = store.public()
    assert exported["cards"]["quark"]["config"]["cookie"] == ""
    assert exported["cards"]["quark"]["config"]["has_cookie"] is True

    store.save(exported)

    assert store.get_cookie() == "keep-this-quark-cookie"


def test_channels_are_normalized_and_deduplicated(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    channels = store.save_channels([
        {"name": "A", "id": "@channel_a"},
        {"name": "B", "id": "channel_a"},
    ])
    assert channels == [{"id": "channel_a", "name": "A"}]


def test_invalid_channel_is_rejected(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    try:
        store.save_channels([{"name": "bad", "id": "https://evil.example/a"}])
    except ConfigValidationError:
        pass
    else:
        raise AssertionError("invalid channel should be rejected")


def test_config_schema_version_is_present(tmp_path):
    from moviesync.config_store import CONFIG_SCHEMA_VERSION, ConfigStore

    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    assert store.load()["schema_version"] == CONFIG_SCHEMA_VERSION


def test_resource_source_defaults_and_health_persist(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    sources = store.get_resource_sources()
    assert sources[0]["id"] == "telegram"
    assert sources[0]["enabled"] is True

    store.update_resource_source_health("telegram", "healthy", "所有已配置频道正常")
    health = store.get_resource_sources()[0]["health"]
    assert health["status"] == "healthy"
    assert health["message"] == "所有已配置频道正常"
    assert health["last_checked_at"] is not None



def test_quark_settings_are_stored_inside_card_config(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save({
        "quark_cookie": "secret-cookie",
        "default_fid": "123",
        "category_fids": {"电影": "456"},
    })

    config = store.load()
    assert config["cards"]["quark"]["config"]["cookie"] == "secret-cookie"
    assert config["cards"]["quark"]["config"]["default_fid"] == "123"
    assert config["cards"]["quark"]["config"]["category_fids"]["电影"] == "456"
    assert "quark_cookie" not in config
    assert "default_fid" not in config
    assert "category_fids" not in config


def test_legacy_quark_config_is_migrated_on_load(tmp_path):
    import json

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "quark_cookie": "legacy-cookie",
            "default_fid": "789",
            "category_fids": {"电影": "101112"},
            "schema_version": 3,
        }),
        encoding="utf-8",
    )

    store = ConfigStore(config_file, tmp_path / "legacy")
    quark = store.get_quark_config()

    assert quark["cookie"] == "legacy-cookie"
    assert quark["default_fid"] == "789"
    assert quark["category_fids"]["电影"] == "101112"
    persisted = __import__("json").loads(config_file.read_text(encoding="utf-8"))
    assert persisted["schema_version"] >= 5
    assert "quark_cookie" not in persisted
    assert persisted["cards"]["quark"]["config"]["cookie"] == "legacy-cookie"


def test_telegram_settings_are_stored_inside_card_config(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    saved = store.save_telegram_config({
        "channels": [{"name": "Telegram", "id": "@movie_channel"}],
    })
    assert saved["config"]["channels"] == [
        {"id": "movie_channel", "name": "Telegram"}
    ]
    config = store.load()
    assert config["cards"]["telegram"]["config"]["channels"] == [
        {"id": "movie_channel", "name": "Telegram"}
    ]
    assert "channels" not in config
    assert "resource_sources" not in config


def test_legacy_telegram_config_is_migrated_on_load(tmp_path):
    import json

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "channels": [{"name": "旧频道", "id": "@legacy_channel"}],
            "resource_sources": [{
                "id": "telegram",
                "enabled": False,
                "health": {"status": "healthy", "message": "旧状态"},
            }],
            "schema_version": 3,
        }),
        encoding="utf-8",
    )

    store = ConfigStore(config_file, tmp_path / "legacy")
    config = store.load()
    telegram = config["cards"]["telegram"]

    assert telegram["enabled"] is False
    assert telegram["config"]["channels"] == [
        {"id": "legacy_channel", "name": "旧频道"}
    ]
    assert telegram["config"]["health"]["status"] == "healthy"
    assert "channels" not in config
    assert "resource_sources" not in config


def test_default_storage_target_is_persisted(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    assert store.get_default_storage_target_id() == ""
    assert store.set_default_storage_target_id("quark") == "quark"
    assert store.get_default_storage_target_id() == "quark"


def test_openlist_url_can_be_empty(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    saved = store.save({"openlist_url": ""})
    assert saved["openlist_url"] == ""


def test_full_config_save_preserves_telegram_magic_regex(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    channels = [{"id": "movie_channel", "name": "电影频道"}]
    magic_regex = {
        "pattern": r"(?i)\bS\d{2}E\d{2}\b",
        "replace": r"\g<0>",
    }

    store.save({
        "cards": {
            "telegram": {
                "enabled": True,
                "config": {
                    "channels": channels,
                    "magic_regex": magic_regex,
                },
            },
        },
    })

    config = store.get_telegram_config()
    assert config["channels"] == channels
    assert config["magic_regex"] == magic_regex

def test_generic_card_config_persists_after_store_recreation(tmp_path):
    config_file = tmp_path / "config.json"
    store = ConfigStore(config_file, tmp_path / "legacy")

    store.save_card_config(
        "third-party.storage",
        {"endpoint": "https://storage.example", "cookie": "private-cookie"},
        enabled=False,
    )

    reopened = ConfigStore(config_file, tmp_path / "legacy")
    saved = reopened.load()["cards"]["third-party.storage"]

    assert saved["enabled"] is False
    assert saved["config"]["endpoint"] == "https://storage.example"
    assert saved["config"]["cookie"] == "private-cookie"


def test_public_config_redacts_secrets_from_all_cards(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save_card_config(
        "third-party.storage",
        {
            "cookie": "private-cookie",
            "endpoint": "https://storage.example",
            "auth": {"refresh_token": "private-token", "region": "us-east"},
        },
    )

    public = store.public()
    public_config = public["cards"]["third-party.storage"]["config"]

    assert public_config["cookie"] == ""
    assert public_config["has_cookie"] is True
    assert public_config["endpoint"] == "https://storage.example"
    assert public_config["auth"]["refresh_token"] == ""
    assert public_config["auth"]["has_refresh_token"] is True
    assert public_config["auth"]["region"] == "us-east"

    # Public responses are sanitized copies; persisted credentials remain intact.
    stored_config = store.load()["cards"]["third-party.storage"]["config"]
    assert stored_config["cookie"] == "private-cookie"
    assert stored_config["auth"]["refresh_token"] == "private-token"


def test_default_storage_target_persists_after_store_recreation(tmp_path):
    config_file = tmp_path / "config.json"
    store = ConfigStore(config_file, tmp_path / "legacy")
    store.set_default_storage_target_id("quark")

    reopened = ConfigStore(config_file, tmp_path / "legacy")

    assert reopened.get_default_storage_target_id() == "quark"


def test_config_backup_restores_third_party_card_settings_and_secrets(tmp_path):
    backup_dir = tmp_path / "backup"
    restore_dir = tmp_path / "restore"
    backup = ConfigStore(backup_dir / "config.json", backup_dir / "legacy")
    backup.save_card_config(
        "third-party.storage",
        {
            "endpoint": "https://storage.example",
            "cookie": "backup-cookie",
            "auth": {"refresh_token": "backup-token", "region": "us-east"},
        },
        enabled=False,
    )

    exported = backup.load()
    restored = ConfigStore(restore_dir / "config.json", restore_dir / "legacy")
    restored.save(exported)

    card = restored.load()["cards"]["third-party.storage"]
    assert card["enabled"] is False
    assert card["config"]["endpoint"] == "https://storage.example"
    assert card["config"]["cookie"] == "backup-cookie"
    assert card["config"]["auth"] == {
        "refresh_token": "backup-token",
        "region": "us-east",
    }


def test_redacted_config_import_does_not_clear_existing_third_party_secrets(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save_card_config(
        "third-party.storage",
        {
            "endpoint": "https://old.example",
            "cookie": "keep-this-cookie",
            "auth": {"refresh_token": "keep-this-token", "region": "us-east"},
        },
    )

    store.save({
        "cards": {
            "third-party.storage": {
                "enabled": True,
                "config": {
                    "endpoint": "https://new.example",
                    "cookie": "",
                    "auth": {"refresh_token": "", "region": "eu-west"},
                },
            },
        },
    })

    card = store.load()["cards"]["third-party.storage"]
    assert card["config"]["endpoint"] == "https://new.example"
    assert card["config"]["cookie"] == "keep-this-cookie"
    assert card["config"]["auth"] == {"refresh_token": "keep-this-token", "region": "eu-west"}



def test_public_config_does_not_require_legacy_cards(monkeypatch, tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    monkeypatch.setattr(
        store,
        "load",
        lambda: {
            "cards": {
                "custom.storage": {
                    "enabled": True,
                    "config": {"cookie": "private-value", "endpoint": "https://storage.example"},
                },
            },
        },
    )

    public = store.public()

    assert "quark" not in public["cards"]
    assert "telegram" not in public["cards"]
    assert public["cards"]["custom.storage"]["config"]["cookie"] == ""
    assert public["cards"]["custom.storage"]["config"]["has_cookie"] is True
    assert public["cards"]["custom.storage"]["config"]["endpoint"] == "https://storage.example"



def test_concurrent_config_updates_do_not_overwrite_each_other(tmp_path):
    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    card_ids = [f"test-storage-{index:02d}" for index in range(24)]

    def save_card(index):
        store.save_card_config(
            card_ids[index],
            {"endpoint": f"https://storage-{index}.example"},
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(save_card, range(len(card_ids))))
        store.set_default_storage_target_id("test-storage-00")

    config = store.load()
    saved_cards = config["cards"]
    for index, card_id in enumerate(card_ids):
        assert saved_cards[card_id]["config"]["endpoint"] == (
            f"https://storage-{index}.example"
        )
    assert config["default_storage_target_id"] == "test-storage-00"
