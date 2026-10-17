"""Verify that the documented third-party resource plugin can be installed and loaded."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path

from moviesync.cards import CardRegistry
from moviesync.services.resource_sources import ResourceSourceManager


class PluginConfig:
    def load(self):
        return {
            "cards": {
                "example-resource": {
                    "enabled": True,
                    "config": {},
                }
            }
        }


class PluginLogger:
    def exception(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def test_example_resource_package_installs_and_loads_through_entry_point(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    package_dir = repo_root / "examples" / "resource-card-plugin"
    install_dir = tmp_path / "installed-plugin"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "--target",
            str(install_dir),
            str(package_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.syspath_prepend(str(install_dir))

    entry_points = importlib.metadata.entry_points(group="moviesync.resource_sources")
    assert any(
        entry_point.name == "example-resource"
        and entry_point.value == "moviesync_example_resource:create_card"
        for entry_point in entry_points
    )

    registry = CardRegistry()
    config = PluginConfig()
    manager = ResourceSourceManager(config, PluginLogger(), registry=registry)

    loaded = manager.load_plugins({"config_store": config, "logger": PluginLogger()})

    assert "example-resource" in loaded
    card = registry.get("example-resource")
    assert card is not None
    assert card.manifest.name == "示例资源来源（仅演示）"
    assert card.check({})["status"] == "healthy"
    assert card.search({"title": "示例作品"}, {}) == []
    assert manager.search({"title": "示例作品"}, config.load()) == []
