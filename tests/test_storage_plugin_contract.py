"""Regression tests for the Stage C installed storage-card contract."""
from __future__ import annotations

import importlib.util
import logging
import tomllib
from pathlib import Path

from moviesync.cards import CardManifest, CardRegistry, StorageTargetCard
from moviesync.services import storage_targets


class FakeStore:
    def load(self):
        return {"cards": {}}

    def get_default_storage_target_id(self):
        return ""


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
        capabilities=("storage.check", "storage.resolve_resource", "storage.transfer"),
    )

    def __init__(self, closed=None):
        self.closed = closed if closed is not None else []

    def resolve_resource(self, resource):
        return {"files": [], "token": None, "error": None}

    def list_files(self, resource):
        return []

    def create_folder(self, name, parent_id="0"):
        return "folder"

    def transfer(self, resource, files, target_id="0"):
        return False, "demo only"

    def close(self):
        self.closed.append(self.card_id)


class EntryPoint:
    def __init__(self, name, factory):
        self.name = name
        self.factory = factory

    def load(self):
        return self.factory


def _manager(monkeypatch, entry_points):
    monkeypatch.setattr(storage_targets, "entry_points", entry_points)
    registry = CardRegistry()
    manager = storage_targets.StorageTargetManager(registry, FakeStore(), FakeLogger())
    return manager, registry


def test_storage_plugin_rejects_entry_point_name_that_disagrees_with_manifest(monkeypatch):
    closed = []
    card = DemoStorage(closed)

    manager, registry = _manager(
        monkeypatch,
        lambda group: [EntryPoint("different-name", lambda context: card)],
    )

    assert manager.load_plugins() == []
    assert registry.list() == []
    assert closed == ["demo-storage"]


def test_storage_plugin_duplicate_registration_closes_only_rejected_instance(monkeypatch):
    closed = []
    first = DemoStorage(closed)
    duplicate = DemoStorage(closed)

    manager, registry = _manager(
        monkeypatch,
        lambda group: [
            EntryPoint("demo-storage", lambda context: first),
            EntryPoint("demo-storage", lambda context: duplicate),
        ],
    )

    assert manager.load_plugins() == ["demo-storage"]
    assert registry.get("demo-storage") is first
    assert closed == ["demo-storage"]


def test_storage_plugin_entry_point_discovery_failure_is_isolated(monkeypatch):
    def broken_entry_points(group):
        raise RuntimeError("metadata unavailable")

    manager, registry = _manager(monkeypatch, broken_entry_points)

    assert manager.load_plugins() == []
    assert registry.list() == []


def test_bundled_example_storage_package_declares_matching_entry_point():
    root = Path(__file__).resolve().parents[1]
    package_root = root / "examples" / "storage-card-plugin"
    pyproject = tomllib.loads((package_root / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["moviesync.storage_targets"]
    assert entry_points == {
        "example-storage": "moviesync_example_storage:create_card"
    }

    module_path = package_root / "src" / "moviesync_example_storage" / "__init__.py"
    spec = importlib.util.spec_from_file_location("moviesync_example_storage_contract_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    card = module.create_card({"logger": logging.getLogger("test-example-storage")})
    assert isinstance(card, StorageTargetCard)
    assert card.card_id == "example-storage"
    assert card.card_type == "storage_target"
    assert "storage.transfer" in card.capabilities
