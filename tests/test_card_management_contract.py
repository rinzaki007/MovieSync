from card_templates.douban import DoubanMetadataCard
from moviesync.cards import CardRegistry


def test_filename_recognition_admin_shows_builtin_catalog_and_preview():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "templates" / "admin.html").read_text(
        encoding="utf-8"
    )
    assert "内置识别规则（自动生效）" in html
    assert "试一试：这个文件名会被识别成什么？" in html
    assert "data-filename-preview-run" in html
    assert "高级设置：自定义识别规则（大多数人不需要修改）" in html
    assert "肖申克的救赎 (1994) 1080p.mkv" in html
    assert "庆余年.S02E07.1080p.mkv" in html


def test_card_management_uses_registry_manifests():
    from moviesync.cards import CardManifest, ResourceSourceCard

    class CustomSource(ResourceSourceCard):
        manifest = CardManifest(
            id="custom-source",
            name="Custom Source",
            type="resource_source",
            capabilities=("resource.search",),
        )

        def search(self, movie, config):
            return []

    registry = CardRegistry()
    registry.register(DoubanMetadataCard(object()))
    registry.register(CustomSource())

    cards = registry.manifests()

    assert {item["id"] for item in cards} == {"douban", "custom-source"}
    assert {item["type"] for item in cards} == {"metadata_provider", "resource_source"}


def test_generic_resource_card_config_api_sanitizes_secrets_and_saves(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class ConfigurableCard(ResourceSourceCard):
        manifest = CardManifest(
            id="configurable-source",
            name="Configurable Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
            config_fields=(
                {"key": "endpoint", "label": "Endpoint", "type": "string"},
                {"key": "api_token", "label": "API Token", "type": "password", "secret": True},
            ),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy", "message": "ok"}

    services["resource_sources"].register(ConfigurableCard())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/configurable-source/config",
        json={"enabled": True, "config": {"endpoint": "https://example.test", "api_token": "do-not-leak"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["config"]["endpoint"] == "https://example.test"
    assert body["config"]["api_token"] == ""
    assert body["has_value"]["api_token"] is True
    assert "do-not-leak" not in str(body)

    response = client.get("/api/cards/configurable-source/config")
    assert response.status_code == 200
    assert response.get_json()["has_value"]["api_token"] is True
    assert response.get_json()["config"]["api_token"] == ""

    response = client.post(
        "/api/cards/configurable-source/config",
        json={"config": {"api_token": ""}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.get_json()["has_value"]["api_token"] is True

    response = client.post(
        "/api/cards/configurable-source/config",
        json={"config": {"unexpected": "value"}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 400

    response = client.post(
        "/api/cards/configurable-source/check",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"

    response = client.get("/api/cards/not-loaded/config")
    assert response.status_code == 404



def test_generic_builtin_card_config_rejects_invalid_values_without_writing(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    config_store = services["config"]
    original_quark = config_store.get_quark_config()
    response = client.post(
        "/api/cards/quark/config",
        json={"config": {"default_fid": "../invalid"}},
        headers=headers,
    )
    assert response.status_code == 400
    assert config_store.get_quark_config() == original_quark

    response = client.post(
        "/api/cards/quark/config",
        json={"config": {"category_fids": []}},
        headers=headers,
    )
    assert response.status_code == 400
    assert config_store.get_quark_config() == original_quark


def test_generic_card_config_runs_card_validator_and_persists_normalized_values(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class ValidatingCard(ResourceSourceCard):
        manifest = CardManifest(
            id="validating-source",
            name="Validating Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
            config_fields=({"key": "value", "type": "string"},),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

        def validate_config(self, config):
            normalized = super().validate_config(config)
            normalized["value"] = normalized["value"].strip()
            if not normalized["value"]:
                raise ValueError("value 不能为空")
            return normalized

    services["resource_sources"].register(ValidatingCard())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    response = client.post(
        "/api/cards/validating-source/config",
        json={"config": {"value": "  accepted  "}},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()["config"]["value"] == "accepted"

    response = client.post(
        "/api/cards/validating-source/config",
        json={"config": {"value": "   "}},
        headers=headers,
    )
    assert response.status_code == 400
    assert services["config"].load()["cards"]["validating-source"]["config"]["value"] == "accepted"


def test_disabled_storage_target_returns_clear_error_and_blocks_new_calls(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/quark/config",
        json={"enabled": False, "config": {}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200

    resolved = services["storage_targets"].resolve_resource(
        {"pwd_id": "share-1", "storage_target_id": "quark"}
    )
    assert "已停用" in resolved["error"]

    ok, message = services["storage_targets"].transfer(
        {"pwd_id": "share-1"}, [{"fid": "file-1"}], storage_target_id="quark"
    )
    assert ok is False
    assert "已停用" in message

    response = client.post(
        "/api/cards/quark/config",
        json={"enabled": True, "config": {}},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    resolved = services["storage_targets"].resolve_resource(
        {"pwd_id": "share-1", "storage_target_id": "quark"}
    )
    assert "已停用" not in (resolved.get("error") or "")



def test_installed_storage_card_config_and_enable_lifecycle(tmp_path):
    """Installed storage cards use the shared config API and obey enabled state."""
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, StorageTargetCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class PluginStorageCard(StorageTargetCard):
        manifest = CardManifest(
            id="demo-storage-plugin",
            name="Demo Storage Plugin",
            type="storage_target",
            capabilities=("storage.check", "storage.resolve_resource", "storage.list_files",
                          "storage.create_folder", "storage.transfer"),
            config_fields=(
                {"key": "api_token", "label": "API Token", "type": "password",
                 "secret": True, "required": True},
                {"key": "root_id", "label": "Root ID", "type": "string",
                 "default": "root"},
            ),
        )

        def check(self, config):
            return {"status": "healthy" if config.get("api_token") else "unconfigured"}

        def resolve_resource(self, resource):
            return {"files": [], "token": None, "error": None}

        def list_files(self, resource):
            return []

        def create_folder(self, name, parent_id="0"):
            return "folder-id"

        def transfer(self, resource, files, target_id="0"):
            return True, "ok"

    card = PluginStorageCard()
    services["card_registry"].register(card)
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    endpoint = "/api/cards/demo-storage-plugin/config"

    response = client.post(
        endpoint,
        json={"enabled": True, "config": {"api_token": "secret-token", "root_id": "drive-root"}},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["config"]["api_token"] == ""
    assert body["has_value"]["api_token"] is True
    assert "secret-token" not in response.get_data(as_text=True)
    assert services["config"].load()["cards"]["demo-storage-plugin"]["config"] == {
        "api_token": "secret-token", "root_id": "drive-root"
    }
    assert services["storage_targets"].get("demo-storage-plugin") is card

    response = client.post(
        endpoint,
        json={"enabled": False, "config": {"api_token": "", "root_id": "drive-root"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert services["storage_targets"].get("demo-storage-plugin") is None
    target = next(item for item in services["storage_targets"].list_targets()
                  if item["id"] == "demo-storage-plugin")
    assert target["enabled"] is False
    assert services["config"].load()["cards"]["demo-storage-plugin"]["config"]["api_token"] == "secret-token"

    response = client.post(
        endpoint,
        json={"enabled": True, "config": {"api_token": "", "root_id": "drive-root"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert services["storage_targets"].get("demo-storage-plugin") is card



def test_card_listing_does_not_call_plugin_health_check_and_hides_exception_details(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    calls = []

    class UnstablePlugin(ResourceSourceCard):
        manifest = CardManifest(
            id="unstable-plugin",
            name="Unstable Plugin",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
            config_fields=({"key": "api_token", "type": "password", "secret": True},),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            calls.append(config)
            raise RuntimeError("upstream rejected token secret-token-value")

    services["card_registry"].register(UnstablePlugin())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.get("/api/cards")
    assert response.status_code == 200
    item = next(card for card in response.get_json()["cards"] if card["id"] == "unstable-plugin")
    assert item["health"]["status"] == "idle"
    assert item["configured"] is False
    assert calls == []

    response = client.post(
        "/api/cards/unstable-plugin/check",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 502
    assert "secret-token-value" not in response.get_data(as_text=True)
    assert "请查看服务日志" in response.get_json()["message"]
    assert len(calls) == 1



def test_generic_card_config_preserves_secrets_on_whitespace_and_rejects_blank_required_fields(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class CredentialCard(ResourceSourceCard):
        manifest = CardManifest(
            id="credential-source",
            name="Credential Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
            config_fields=(
                {"key": "api_token", "label": "API Token", "type": "password",
                 "secret": True, "required": True},
                {"key": "endpoint", "label": "Endpoint", "type": "string",
                 "required": True},
            ),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

    services["resource_sources"].register(CredentialCard())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    endpoint = "/api/cards/credential-source/config"

    response = client.post(
        endpoint,
        json={"config": {"api_token": "keep-this-secret", "endpoint": "https://example.com"}},
        headers=headers,
    )
    assert response.status_code == 200

    # Blank/whitespace secret input means unchanged, not erase or overwrite.
    response = client.post(
        endpoint,
        json={"config": {"api_token": "  \t  ", "endpoint": "https://example.org"}},
        headers=headers,
    )
    assert response.status_code == 200
    saved = services["config"].load()["cards"]["credential-source"]["config"]
    assert saved == {
        "api_token": "keep-this-secret",
        "endpoint": "https://example.org",
    }
    assert "keep-this-secret" not in response.get_data(as_text=True)

    # A required non-secret field containing only whitespace must be rejected.
    response = client.post(
        endpoint,
        json={"config": {"endpoint": "   "}},
        headers=headers,
    )
    assert response.status_code == 400
    assert "必填配置" in response.get_json()["message"]
    assert services["config"].load()["cards"]["credential-source"]["config"] == saved



def test_generic_card_health_check_cannot_override_api_success(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class MisleadingHealthCard(ResourceSourceCard):
        manifest = CardManifest(
            id="misleading-health",
            name="Misleading Health",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {
                "success": False,
                "status": "healthy",
                "message": "plugin result must not override API success",
            }

    services["resource_sources"].register(MisleadingHealthCard())
    client = app.test_client()
    client.post(
        "/api/setup",
        json={"username": "admin", "password": "password123"},
    )
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/misleading-health/check",
        json={},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["status"] == "healthy"


def test_private_key_fields_are_redacted_from_card_config_api(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class PrivateKeyCard(ResourceSourceCard):
        manifest = CardManifest(
            id="private-key-source",
            name="Private Key Source",
            type="resource_source",
            capabilities=("resource.search", "resource.health_check"),
            config_fields=(
                {"key": "private_key", "label": "Private Key", "type": "textarea"},
            ),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

    services["resource_sources"].register(PrivateKeyCard())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/private-key-source/config",
        json={"config": {"private_key": "test-value"}},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["config"]["private_key"] == ""
    assert body["has_value"]["private_key"] is True
    assert "test-value" not in str(body)

    card = services["card_registry"].get("private-key-source")
    assert card.public_config({"nested": {"private_key": "test-value"}}) == {
        "nested": {"has_private_key": True}
    }



def test_card_list_handles_malformed_persisted_card_config(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    config = services["config"].load()
    config["cards"]["douban"]["config"] = []
    services["config"].store.write(config)

    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    response = client.get("/api/cards")

    assert response.status_code == 200
    body = response.get_json()
    douban = next(card for card in body["cards"] if card["id"] == "douban")
    assert douban["configured"] is False
    assert isinstance(douban["health"], dict)


def test_private_key_config_empty_submission_preserves_saved_secret(tmp_path):
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, ResourceSourceCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class PrivateKeyCard(ResourceSourceCard):
        manifest = CardManifest(
            id="private-key-source",
            name="Private Key Source",
            type="resource_source",
            config_fields=({"key": "private_key", "type": "string"},),
        )

        def search(self, movie, config):
            return []

        def check(self, config):
            return {"status": "healthy"}

    services["resource_sources"].register(PrivateKeyCard())
    services["config"].save_card_config("private-key-source", {"private_key": "KEEP_ME"})
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/private-key-source/config",
        json={"config": {"private_key": ""}},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert services["config"].load()["cards"]["private-key-source"]["config"]["private_key"] == "KEEP_ME"
    assert "KEEP_ME" not in response.get_data(as_text=True)



def test_card_config_routes_are_generic_and_do_not_register_builtin_card_endpoints(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/api/cards/<card_id>/config" in rules
    assert "/api/cards/<card_id>/check" in rules
    assert "/api/cards/quark/config" not in rules
    assert "/api/cards/telegram/config" not in rules



def test_get_card_config_reads_arbitrary_card_without_card_specific_accessors(tmp_path):
    from moviesync.config_store import ConfigStore

    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy")
    store.save_card_config(
        "example-provider",
        {"endpoint": "https://example.test", "language": "zh-CN"},
        enabled=True,
    )

    assert store.get_card_config("example-provider") == {
        "endpoint": "https://example.test",
        "language": "zh-CN",
    }
    assert store.get_card_config("not-installed") == {}def test_quark_category_fids_use_four_structured_inputs_and_keep_legacy_support():
    from card_templates.quark import QuarkStorageCard

    fields = {field["key"]: field for field in QuarkStorageCard.manifest.config_fields}
    category_fids = fields["category_fids"]
    assert category_fids["type"] == "json"
    assert category_fids["editor"] == "object_fields"
    assert [field["key"] for field in category_fids["item_fields"]] == ["动漫", "电影", "电视剧", "综艺"]

    card = object.__new__(QuarkStorageCard)
    legacy = card.validate_config({"category_fids": {"电影": "123"}})
    assert legacy["category_fids"]["电影"] == "123"
    assert legacy["category_fids"]["电视剧"] == ""

    parsed = card.validate_config({"category_fids": "电影=123\n电视剧=456"})
    assert parsed["category_fids"]["电影"] == "123"
    assert parsed["category_fids"]["电视剧"] == "456"


def test_generic_card_form_has_channel_chips_and_quark_textarea():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    admin = (root / "templates" / "admin.html").read_text(encoding="utf-8")
    plugin_manager = (root / "moviesync" / "card_plugins.py").read_text(encoding="utf-8")

    assert 'field.editor === "channel_chips"' in admin
    assert "data-card-add-channel" in admin
    assert "data-card-remove-channel" in admin
    assert 'type === "textarea"' in admin
    assert 'for key in ("type", "editor", "item_fields", "placeholder", "description", "label")' in plugin_managerdef test_filename_recognition_rules_are_global_and_share_the_generic_form_contract():
    from card_templates.filename_recognition import FilenameRecognitionCard
    from pathlib import Path

    fields = {
        field["key"]: field
        for field in FilenameRecognitionCard.manifest.config_fields
    }
    template = (Path(__file__).resolve().parents[1] / "templates" / "admin.html").read_text(
        encoding="utf-8"
    )

    assert FilenameRecognitionCard.manifest.type == "filename_processor"
    assert list(fields) == ["rules"]
    assert fields["rules"]["editor"] == "rule_list"
    assert [field["key"] for field in fields["rules"]["item_fields"]] == [
        "pattern", "replace", "source_ids"
    ]
    assert 'field.editor === "channel_chips"' in template
    assert "data-card-add-channel" in template
    assert "data-card-remove-channel" in template


def test_fid_lines_textarea_saves_through_legacy_object_validator(tmp_path):
    """The generic API converts textarea lines before calling older card validators."""
    from moviesync.app import create_app
    from moviesync.cards import CardManifest, StorageTargetCard

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]

    class LegacyQuarkCard(StorageTargetCard):
        manifest = CardManifest(
            id="legacy-quark",
            name="Legacy Quark",
            type="storage_target",
            config_fields=(
                {"key": "category_fids", "label": "分类目录 FID",
                 "type": "json", "editor": "fid_lines"},
            ),
        )

        def validate_config(self, config):
            value = config.get("category_fids")
            if not isinstance(value, dict):
                raise ValueError("分类目录 FID 必须是对象")
            return {"category_fids": value}

    services["card_registry"].register(LegacyQuarkCard())
    client = app.test_client()
    client.post("/api/setup", json={"username": "admin", "password": "password123"})
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    response = client.post(
        "/api/cards/legacy-quark/config",
        json={
            "enabled": True,
            "config": {"category_fids": "动漫=55a44b99fac641679e1ebf55dcb38be9"},
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert services["config"].get_card_config("legacy-quark")["category_fids"] == {
        "动漫": "55a44b99fac641679e1ebf55dcb38be9"
    }
    # The saved mapping is rendered back to the textarea representation on GET.
    response = client.get("/api/cards/legacy-quark/config")
    assert response.status_code == 200
    assert response.get_json()["config"]["category_fids"] == {
        "动漫": "55a44b99fac641679e1ebf55dcb38be9"
    }


def test_quark_category_fids_render_as_structured_object_inputs():
    from pathlib import Path

    admin_template = Path(__file__).resolve().parents[1] / "templates" / "admin.html"
    source = admin_template.read_text(encoding="utf-8")

    assert 'else if (controlType === "json" && field.editor === "object_fields")' in source
    assert 'data-card-structured-object="' in source
    assert 'field.editor === "fid_lines" && rawValue' not in source



def test_legacy_fid_lines_field_is_exposed_as_textarea_by_config_api():
    from moviesync.cards import CardManifest, StorageTargetCard
    from moviesync.web.routes import _card_config_view

    class LegacyQuarkCard(StorageTargetCard):
        manifest = CardManifest(
            id="quark",
            name="Quark",
            type="storage_target",
            config_fields=(
                {"key": "category_fids", "label": "分类目录 FID",
                 "type": "json", "editor": "fid_lines"},
            ),
        )

    fields, config, _ = _card_config_view(
        LegacyQuarkCard(),
        {"category_fids": {"电影": "123", "电视剧": "456"}},
    )

    assert fields[0]["type"] == "textarea"
    assert fields[0]["editor"] == "fid_lines"
    assert config["category_fids"] == {"电影": "123", "电视剧": "456"}def test_card_management_surfaces_unhealthy_connection_state():
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / "templates" / "admin.html"
    source = template.read_text(encoding="utf-8")
    assert 'healthStatus === "unhealthy"' in source
    assert '"连接异常"' in source
    assert "border-rose-400/20 bg-rose-400/5 text-rose-300" in source
