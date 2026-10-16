"""Regression tests for trusted, persistent single-file card plugins."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

import moviesync.card_plugins as card_plugins
from moviesync.card_plugins import CardFilePluginManager
from moviesync.cards import CardRegistry


def _plugin_source(card_id: str = "upload-demo") -> bytes:
    return f'''from moviesync.cards import Card, CardManifest


class DemoCard(Card):
    manifest = CardManifest(id="{card_id}", name="Uploaded Demo", type="custom")


def create_card(context):
    return DemoCard()
'''.encode()


def test_file_plugin_loads_and_uninstall_removes_only_plugin_file(tmp_path):
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / "sample-plugin.py").write_bytes(_plugin_source("sample-plugin"))
    registry = CardRegistry()
    manager = CardFilePluginManager(plugin_dir, registry, logging.getLogger("test-card-plugins"))

    loaded = manager.load_all()

    assert loaded == ["sample-plugin"]
    assert registry.get("sample-plugin") is not None
    assert manager.list_plugins()[0]["loaded"] is True

    removed = manager.uninstall("sample-plugin.py")

    assert removed == {"filename": "sample-plugin.py", "card_id": "sample-plugin"}
    assert registry.get("sample-plugin") is None
    assert not (plugin_dir / "sample-plugin.py").exists()
    assert manager.list_plugins() == []


def test_file_plugin_requires_filename_to_match_manifest_id(tmp_path):
    manager = CardFilePluginManager(
        tmp_path / "cards", CardRegistry(), logging.getLogger("test-card-plugins")
    )
    with pytest.raises(ValueError, match="必须与文件名"):
        manager.install("sample-plugin.py", _plugin_source("different-id"))
    assert not (tmp_path / "cards" / "sample-plugin.py").exists()


@pytest.mark.parametrize("filename", ["../escape.py", "UPPER.py", "not-python.txt", ".hidden.py"])
def test_file_plugin_rejects_unsafe_filenames(tmp_path, filename):
    manager = CardFilePluginManager(
        tmp_path / "cards", CardRegistry(), logging.getLogger("test-card-plugins")
    )
    with pytest.raises(ValueError):
        manager.validate_filename(filename)


def test_file_plugin_install_rejects_oversized_source(tmp_path):
    manager = CardFilePluginManager(
        tmp_path / "cards", CardRegistry(), logging.getLogger("test-card-plugins")
    )
    with pytest.raises(ValueError, match="256 KiB"):
        manager.install("too-large.py", b"#" * (256 * 1024 + 1))


def test_admin_can_upload_load_and_uninstall_a_single_file_card(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    response = client.post(
        "/api/cards/plugins",
        data={"file": (io.BytesIO(_plugin_source()), "upload-demo.py")},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["card_id"] == "upload-demo"
    assert services["card_registry"].get("upload-demo") is not None

    listing = client.get("/api/cards").get_json()
    assert listing["dynamic_install_enabled"] is True
    assert any(item["filename"] == "upload-demo.py" and item["loaded"] for item in listing["file_plugins"])

    response = client.delete("/api/cards/plugins/upload-demo.py", headers=headers)
    assert response.status_code == 200
    assert services["card_registry"].get("upload-demo") is None
    assert not (tmp_path / "cards" / "upload-demo.py").exists()


def test_fresh_install_does_not_seed_cards_by_default(tmp_path, monkeypatch):
    """A fresh installation stays empty unless bundled cards are explicitly enabled."""
    from moviesync.app import create_app

    monkeypatch.setenv("MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS", "0")
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    assert services["file_card_plugins"].list_plugins() == []
    assert all(
        services["card_registry"].get(card_id) is None
        for card_id in (
            "douban",
            "tmdb",
            "pansou",
            "telegram",
            "quark",
            "filename_recognition",
        )
    )


def test_pansou_is_seeded_as_a_removable_file_card(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    plugin_path = tmp_path / "cards" / "pansou.py"

    assert plugin_path.is_file()
    assert services["card_registry"].get("pansou") is not None

    services["file_card_plugins"].uninstall("pansou.py")
    assert services["card_registry"].get("pansou") is None

    restarted = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    assert restarted.extensions["moviesync"]["card_registry"].get("pansou") is None


def test_builtin_cards_are_independent_single_file_plugins(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    registry = services["card_registry"]
    plugin_manager = services["file_card_plugins"]

    assert registry.get("telegram") is not None
    assert registry.get("pansou") is not None
    assert registry.get("quark") is not None
    assert registry.get("douban") is not None
    assert all((tmp_path / "cards" / name).is_file() for name in (
        "telegram.py", "pansou.py", "quark.py", "douban.py"
    ))

    removed = plugin_manager.uninstall("telegram.py")

    assert removed["card_id"] == "telegram"
    assert registry.get("telegram") is None
    assert registry.get("pansou") is not None
    assert registry.get("quark") is not None
    assert registry.get("douban") is not None


def test_uninstall_keeps_card_registered_by_another_owner(tmp_path):
    from moviesync.cards import Card, CardManifest

    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / "sample-plugin.py").write_bytes(_plugin_source("sample-plugin"))
    registry = CardRegistry()
    manager = CardFilePluginManager(plugin_dir, registry, logging.getLogger("test"))
    manager.load_all()
    registry.unregister("sample-plugin")

    class OtherCard(Card):
        manifest = CardManifest(id="sample-plugin", name="Other", type="custom")

    other = OtherCard()
    registry.register(other)
    assert manager.list_plugins()[0]["loaded"] is False
    manager.uninstall("sample-plugin.py")
    assert registry.get("sample-plugin") is other


@pytest.mark.parametrize(
    ("reference_field", "card_id"),
    [("source_id", "telegram"), ("storage_target_id", "quark")],
)
def test_uninstall_rejects_cards_referenced_by_subscriptions(
    tmp_path, reference_field, card_id
):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    if reference_field == "storage_target_id":
        # Exercise the subscription-reference guard rather than the separate
        # safeguard for the currently selected default storage target.
        services["config"].set_default_storage_target_id("")
    services["subscriptions"].add_subscription(
        title="Referenced show",
        pwd_id="share123",
        source_id=card_id if reference_field == "source_id" else "",
        channel="movies" if reference_field == "source_id" else "",
        storage_target_id=card_id if reference_field == "storage_target_id" else "",
    )

    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.delete(
        f"/api/cards/plugins/{card_id}.py",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert "自动追剧订阅" in response.get_json()["message"]
    assert (tmp_path / "cards" / f"{card_id}.py").is_file()
    assert services["card_registry"].get(card_id) is not None

def test_cards_api_uses_card_interface_for_configuration_status(tmp_path):
    """The platform should not special-case card IDs to determine configured state."""
    from moviesync.app import create_app
    from moviesync.cards import Card, CardManifest

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class CustomConfiguredCard(Card):
        manifest = CardManifest(
            id="custom-configured",
            name="Custom Configured",
            type="custom",
            config_fields=({"key": "endpoint", "label": "Endpoint", "required": True},),
        )

    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    services["card_registry"].register(CustomConfiguredCard())
    services["config"].save_card_config(
        "custom-configured", {"endpoint": "https://example.invalid"}
    )

    response = client.get("/api/cards")
    assert response.status_code == 200
    cards = response.get_json()["cards"]
    custom = next(item for item in cards if item["id"] == "custom-configured")
    assert custom["configured"] is True
    assert custom["health"]["status"] == "idle"


def test_cards_api_does_not_treat_quark_default_fid_as_configuration(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    cards = client.get("/api/cards").get_json()["cards"]
    quark = next(item for item in cards if item["id"] == "quark")
    assert quark["configured"] is False
    assert quark["health"]["status"] == "idle"


def test_cards_api_does_not_treat_telegram_regex_defaults_as_configuration(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    cards = client.get("/api/cards").get_json()["cards"]
    telegram = next(item for item in cards if item["id"] == "telegram")
    assert telegram["configured"] is False
    assert telegram["health"]["status"] == "unknown"



def test_legacy_persistent_douban_card_factory_is_migrated_safely(tmp_path):
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    marker_dir = plugin_dir / ".seeded"
    marker_dir.mkdir()
    (marker_dir / "douban.py.seeded").touch()
    plugin_path = plugin_dir / "douban.py"
    plugin_path.write_text(
        'from moviesync.clients.douban import DoubanClient\n'
        'from card_templates.douban import DoubanMetadataCard\n'
        '\n'
        '# Keep this local note when migrating the built-in factory.\n'
        'def create_card(context):\n'
        '    return DoubanMetadataCard(context["douban"])\n',
        encoding="utf-8",
    )

    manager = CardFilePluginManager(
        plugin_dir,
        CardRegistry(),
        logging.getLogger("test-card-plugins"),
        Path(__file__).resolve().parents[1] / "card_templates",
    )

    loaded = manager.load_all()

    assert "douban" in loaded
    assert manager.registry.get("douban") is not None
    migrated = plugin_path.read_text(encoding="utf-8")
    assert "return DoubanMetadataCard(DoubanClient())" in migrated
    assert "context[\"douban\"]" not in migrated
    assert "# Keep this local note" in migrated



def test_legacy_quark_card_template_is_registered_for_safe_upgrade():
    # This exact pre-feature template was bundled in the prior release. Registering
    # its blob hash lets the seed migration upgrade it without touching custom files.
    assert "93f21c01135b97dbf772d95cdba4e7d785da96da" in (
        card_plugins._LEGACY_BUNDLED_CARD_BLOBS["quark.py"]
    )


def test_seeded_card_template_updates_when_persisted_copy_is_unchanged(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    source = bundled_dir / "demo.py"
    source.write_text("# version 1\n", encoding="utf-8")
    plugin_dir = tmp_path / "cards"
    logger = logging.getLogger("test-card-seed-upgrade")

    CardFilePluginManager(plugin_dir, CardRegistry(), logger, bundled_dir)
    persisted = plugin_dir / "demo.py"
    marker = plugin_dir / ".seeded" / "demo.py.seeded"
    assert persisted.read_text(encoding="utf-8") == "# version 1\n"
    assert marker.read_text(encoding="ascii") == card_plugins._git_blob_sha(persisted.read_bytes())

    source.write_text("# version 2\n", encoding="utf-8")
    CardFilePluginManager(plugin_dir, CardRegistry(), logger, bundled_dir)

    assert persisted.read_text(encoding="utf-8") == "# version 2\n"
    assert marker.read_text(encoding="ascii") == card_plugins._git_blob_sha(source.read_bytes())


def test_seeded_card_template_preserves_administrator_edits(tmp_path):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    source = bundled_dir / "demo.py"
    source.write_text("# bundled version 1\n", encoding="utf-8")
    plugin_dir = tmp_path / "cards"
    logger = logging.getLogger("test-card-seed-custom")

    CardFilePluginManager(plugin_dir, CardRegistry(), logger, bundled_dir)
    persisted = plugin_dir / "demo.py"
    persisted.write_text("# administrator custom version\n", encoding="utf-8")
    source.write_text("# bundled version 2\n", encoding="utf-8")

    CardFilePluginManager(plugin_dir, CardRegistry(), logger, bundled_dir)

    assert persisted.read_text(encoding="utf-8") == "# administrator custom version\n"


@pytest.mark.parametrize(
    "marker_value",
    ["", "stale-bundled-hash", "customized:stale-bundled-hash"],
)
def test_seeded_card_template_migrates_known_legacy_copy_with_old_marker(
    tmp_path, monkeypatch, marker_value
):
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    source = bundled_dir / "demo.py"
    source.write_text("# new bundled version\n", encoding="utf-8")
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / ".seeded").mkdir()
    persisted = plugin_dir / "demo.py"
    old_content = b"# known legacy bundled version\n"
    persisted.write_bytes(old_content)
    (plugin_dir / ".seeded" / "demo.py.seeded").write_text(marker_value, encoding="ascii")
    monkeypatch.setitem(
        card_plugins._LEGACY_BUNDLED_CARD_BLOBS,
        "demo.py",
        {card_plugins._git_blob_sha(old_content)},
    )

    CardFilePluginManager(
        plugin_dir, CardRegistry(), logging.getLogger("test-card-seed-legacy"), bundled_dir
    )

    assert persisted.read_text(encoding="utf-8") == "# new bundled version\n"
    assert (plugin_dir / ".seeded" / "demo.py.seeded").read_text(encoding="ascii") == (
        card_plugins._git_blob_sha(source.read_bytes())
    )



def test_legacy_customized_builtin_card_gets_bundled_form_schema_without_replacing_code(tmp_path):
    from moviesync.card_plugins import CardFilePluginManager
    from moviesync.cards import CardRegistry

    plugin_dir = tmp_path / "data-cards"
    bundled_dir = tmp_path / "bundled-cards"
    plugin_dir.mkdir()
    bundled_dir.mkdir()

    legacy_code = '''from moviesync.cards import CardManifest, ResourceSourceCard

class TelegramResourceSource(ResourceSourceCard):
    manifest = CardManifest(
        id="telegram",
        name="Telegram",
        type="resource_source",
        capabilities=("resource.search",),
        config_fields=(
            {"key": "channels", "label": "资源频道", "type": "json",
             "default": [], "description": "JSON 数组"},
        ),
    )

    def search(self, movie, config):
        return []

def create_card(context):
    return TelegramResourceSource()
'''
    canonical_code = '''from moviesync.cards import CardManifest, ResourceSourceCard

class TelegramResourceSource(ResourceSourceCard):
    manifest = CardManifest(
        id="telegram",
        name="Telegram",
        type="resource_source",
        capabilities=("resource.search",),
        config_fields=(
            {"key": "channels", "label": "资源频道", "type": "json",
             "editor": "object_list",
             "item_fields": ({"key": "id", "label": "频道用户名"},),
             "default": [], "description": "点击添加频道"},
        ),
    )

    def search(self, movie, config):
        return []

def create_card(context):
    return TelegramResourceSource()
'''
    (plugin_dir / "telegram.py").write_text(legacy_code, encoding="utf-8")
    (bundled_dir / "telegram.py").write_text(canonical_code, encoding="utf-8")

    class Logger:
        def exception(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

    manager = CardFilePluginManager(plugin_dir, CardRegistry(), Logger(), bundled_dir)
    loaded = manager.load_file("telegram.py")
    fields = {field["key"]: field for field in loaded.manifest.config_fields}

    assert fields["channels"]["editor"] == "object_list"
    assert fields["channels"]["item_fields"][0]["key"] == "id"
    assert fields["channels"]["description"] == "点击添加频道"
    assert "JSON 数组" in (plugin_dir / "telegram.py").read_text(encoding="utf-8")



def test_custom_legacy_card_keeps_shared_telegram_context(tmp_path):
    plugin_dir = tmp_path / "cards"
    bundled_dir = tmp_path / "bundled"
    plugin_dir.mkdir()
    bundled_dir.mkdir()
    (bundled_dir / "telegram.py").write_text(
        "from moviesync.cards import Card, CardManifest\n"
        "class TelegramCard(Card):\n"
        "    manifest = CardManifest(id='telegram', name='Telegram', type='custom')\n"
        "    def __init__(self, telegram):\n"
        "        self.telegram = telegram\n"
        "def create_card(context):\n"
        "    return TelegramCard(context.get('telegram'))\n",
        encoding="utf-8",
    )
    manager = CardFilePluginManager(
        plugin_dir,
        CardRegistry(),
        logging.getLogger("test-card-plugins"),
        bundled_dir,
    )
    (plugin_dir / "legacy_custom.py").write_text(
        "from moviesync.cards import Card, CardManifest\n"
        "class LegacyCard(Card):\n"
        "    manifest = CardManifest(id='legacy_custom', name='Legacy', type='custom')\n"
        "    def __init__(self, telegram):\n"
        "        self.telegram = telegram\n"
        "def create_card(context):\n"
        "    return LegacyCard(context['telegram'])\n",
        encoding="utf-8",
    )
    legacy_client = object()

    bundled_card = manager.load_file("telegram.py", {"telegram": legacy_client})
    legacy_card = manager.load_file("legacy_custom.py", {"telegram": legacy_client})

    assert bundled_card.telegram is None
    assert legacy_card.telegram is legacy_client


def test_legacy_quark_manifest_upgrades_category_fids_schema_without_replacing_code(tmp_path):
    plugin_dir = tmp_path / "data-cards"
    bundled_dir = tmp_path / "bundled-cards"
    plugin_dir.mkdir()
    bundled_dir.mkdir()

    (plugin_dir / "quark.py").write_text(
        '''from moviesync.cards import CardManifest, StorageTargetCard

class QuarkCard(StorageTargetCard):
    manifest = CardManifest(id="quark", name="Quark", type="storage_target",
        config_fields=({"key": "category_fids", "label": "分类目录 FID", "type": "json"},))

def create_card(context):
    return QuarkCard()
''',
        encoding="utf-8",
    )
    (bundled_dir / "quark.py").write_text(
        '''from moviesync.cards import CardManifest, StorageTargetCard

class QuarkCard(StorageTargetCard):
    manifest = CardManifest(id="quark", name="Quark", type="storage_target",
        config_fields=({"key": "category_fids", "label": "分类目录 FID",
            "type": "textarea", "editor": "fid_lines", "default": ""},))

def create_card(context):
    return QuarkCard()
''',
        encoding="utf-8",
    )

    manager = CardFilePluginManager(
        plugin_dir, CardRegistry(), logging.getLogger("test-quark-schema"), bundled_dir
    )
    loaded = manager.load_file("quark.py")
    field = next(item for item in loaded.manifest.config_fields if item["key"] == "category_fids")

    assert field["type"] == "textarea"
    assert field["editor"] == "fid_lines"
    # The persistent Python card is intentionally left untouched.
    assert '"type": "json"' in (plugin_dir / "quark.py").read_text(encoding="utf-8")


def test_legacy_telegram_filename_rules_are_removed_from_form_manifest(tmp_path):
    plugin_dir = tmp_path / "data-cards"
    bundled_dir = tmp_path / "bundled-cards"
    plugin_dir.mkdir()
    bundled_dir.mkdir()

    legacy_source = '''from moviesync.cards import CardManifest, ResourceSourceCard

class TelegramCard(ResourceSourceCard):
    manifest = CardManifest(id="telegram", name="Telegram", type="resource_source",
        config_fields=(
            {"key": "channels", "label": "资源频道", "type": "json"},
            {"key": "magic_regex", "label": "文件名识别增强", "type": "json"},
        ))
    def search(self, movie, config):
        return []

def create_card(context):
    return TelegramCard()
'''
    bundled_source = '''from moviesync.cards import CardManifest, ResourceSourceCard

class TelegramCard(ResourceSourceCard):
    manifest = CardManifest(id="telegram", name="Telegram", type="resource_source",
        config_fields=(
            {"key": "channels", "label": "资源频道", "type": "json",
             "editor": "channel_chips", "default": []},
        ))
    def search(self, movie, config):
        return []

def create_card(context):
    return TelegramCard()
'''
    legacy_path = plugin_dir / "telegram.py"
    legacy_path.write_text(legacy_source, encoding="utf-8")
    (bundled_dir / "telegram.py").write_text(bundled_source, encoding="utf-8")

    manager = CardFilePluginManager(
        plugin_dir, CardRegistry(), logging.getLogger("test-telegram-legacy-ui"), bundled_dir
    )
    card = manager.load_file("telegram.py")
    field_keys = [field["key"] for field in card.manifest.config_fields]

    assert field_keys == ["channels"]
    assert card.manifest.config_fields[0]["editor"] == "channel_chips"
    # The legacy card source remains untouched; only its exposed form is migrated.
    assert '"key": "magic_regex"' in legacy_path.read_text(encoding="utf-8")




def test_uninstall_closes_the_loaded_card(tmp_path):
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / "closeable.py").write_text(
        "from moviesync.cards import Card, CardManifest\n"
        "class CloseableCard(Card):\n"
        "    manifest = CardManifest(id='closeable', name='Closeable', type='custom')\n"
        "    def __init__(self): self.close_calls = 0\n"
        "    def close(self): self.close_calls += 1\n"
        "def create_card(context): return CloseableCard()\n",
        encoding="utf-8",
    )
    registry = CardRegistry()
    manager = CardFilePluginManager(plugin_dir, registry, logging.getLogger("test-card-close"))
    card = manager.load_file("closeable.py")

    manager.uninstall("closeable.py")

    assert card.close_calls == 1
    assert registry.get("closeable") is None


def test_failed_registration_closes_factory_created_card(tmp_path):
    from moviesync.cards import Card, CardManifest

    marker = tmp_path / "closed.txt"
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / "duplicate.py").write_text(
        "from pathlib import Path\n"
        "from moviesync.cards import Card, CardManifest\n"
        "class DuplicateCard(Card):\n"
        "    manifest = CardManifest(id='duplicate', name='Duplicate', type='custom')\n"
        "    def close(self): Path(" + repr(str(marker)) + ").write_text('closed')\n"
        "def create_card(context): return DuplicateCard()\n",
        encoding="utf-8",
    )
    registry = CardRegistry()

    class ExistingCard(Card):
        manifest = CardManifest(id="duplicate", name="Existing", type="custom")

    existing = ExistingCard()
    registry.register(existing)
    manager = CardFilePluginManager(plugin_dir, registry, logging.getLogger("test-card-failed-close"))

    with pytest.raises(ValueError):
        manager.load_file("duplicate.py")

    assert marker.read_text(encoding="utf-8") == "closed"
    assert registry.get("duplicate") is existing
    assert manager.list_plugins()[0]["loaded"] is False
