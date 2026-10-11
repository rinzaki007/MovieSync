"""核心回归测试：空卡片启动、可选卡片故障隔离与配置迁移隔离。"""

from copy import deepcopy

from moviesync.app import create_app
from moviesync.cards import Card, CardManifest, CardRegistry


def _disable_entry_point_cards(monkeypatch):
    """让应用启动测试不依赖当前 Python 环境安装了哪些第三方插件。"""
    import moviesync.app as app_module

    monkeypatch.setattr(
        app_module.ResourceSourceManager,
        "load_plugins",
        lambda self, context=None: [],
    )
    monkeypatch.setattr(
        app_module.MetadataProviderManager,
        "load_plugins",
        lambda self, context=None: [],
    )
    monkeypatch.setattr(
        app_module.StorageTargetManager,
        "load_plugins",
        lambda self, context=None: [],
    )
    monkeypatch.setattr(
        app_module.NotificationManager,
        "load_plugins",
        lambda self, context=None: [],
    )


def _close_app(app):
    services = app.extensions["moviesync"]
    services["shutdown"]()
    services["tasks"].shutdown()


def test_app_starts_with_no_installed_cards(tmp_path, monkeypatch):
    """全新安装没有任何卡片时，核心 Web 服务仍应正常启动。"""
    import moviesync.app as app_module

    monkeypatch.delenv("MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS", raising=False)
    _disable_entry_point_cards(monkeypatch)
    monkeypatch.setattr(
        app_module.CardFilePluginManager,
        "load_all",
        lambda self, context=None: [],
    )

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)

    try:
        assert app.test_client().get("/healthz").status_code == 200
        assert app.extensions["moviesync"]["card_registry"].list() == []
    finally:
        _close_app(app)


def test_broken_optional_card_loader_does_not_block_app_startup(tmp_path, monkeypatch):
    """某一组可选卡片加载失败时，应用仍应可启动并响应健康检查。"""
    import moviesync.app as app_module

    _disable_entry_point_cards(monkeypatch)

    def fail_card_loading(self, context=None):
        raise RuntimeError("simulated optional card load failure")

    monkeypatch.setattr(
        app_module.CardFilePluginManager,
        "load_all",
        fail_card_loading,
    )

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)

    try:
        response = app.test_client().get("/healthz")
        assert response.status_code == 200
        assert "card_registry" in app.extensions["moviesync"]
    finally:
        _close_app(app)


def test_one_card_migration_failure_does_not_block_other_cards():
    """单张卡片配置迁移失败不能阻断其他卡片，也不能覆盖失败卡片的旧配置。"""

    class FakeConfigStore:
        def __init__(self):
            self.data = {
                "cards": {
                    "broken.card": {
                        "enabled": True,
                        "config_version": 1,
                        "config": {"keep": "original"},
                    },
                    "working.card": {
                        "enabled": False,
                        "config_version": 1,
                        "config": {"old_name": "value"},
                    },
                }
            }

        def load(self):
            return deepcopy(self.data)

        def save_card_config(
            self,
            card_id,
            incoming,
            *,
            enabled=None,
            config_version=None,
            replace_config=False,
        ):
            saved = self.data["cards"][card_id]
            saved["config"] = (
                deepcopy(incoming)
                if replace_config
                else {**saved.get("config", {}), **deepcopy(incoming)}
            )
            if enabled is not None:
                saved["enabled"] = enabled
            if config_version is not None:
                saved["config_version"] = config_version
            return deepcopy(saved)

    class BrokenMigrationCard(Card):
        manifest = CardManifest(
            id="broken.card",
            name="Broken migration",
            config_version=2,
        )

        def migrate_config(self, config, from_version):
            raise RuntimeError("simulated migration failure")

    class WorkingMigrationCard(Card):
        manifest = CardManifest(
            id="working.card",
            name="Working migration",
            config_version=2,
        )

        def migrate_config(self, config, from_version):
            assert from_version == 1
            return {"new_name": config["old_name"]}

    registry = CardRegistry()
    registry.register(BrokenMigrationCard())
    registry.register(WorkingMigrationCard())
    store = FakeConfigStore()

    assert registry.migrate_configs(store) == ["working.card"]

    broken = store.data["cards"]["broken.card"]
    assert broken["config"] == {"keep": "original"}
    assert broken["config_version"] == 1
    assert broken["enabled"] is True

    working = store.data["cards"]["working.card"]
    assert working["config"] == {"new_name": "value"}
    assert working["config_version"] == 2
    assert working["enabled"] is False
