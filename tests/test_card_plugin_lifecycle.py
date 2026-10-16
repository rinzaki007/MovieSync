"""单文件卡片安装、加载与卸载生命周期回归测试。"""

import importlib
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

CARD_SOURCE = b'''from moviesync.cards import Card, CardManifest


class DemoCard(Card):
    manifest = CardManifest(id="demo", name="Demo card")


def create_card(context):
    return DemoCard()
'''


def _manager(tmp_path):
    registry = importlib.import_module("moviesync.cards").CardRegistry()
    manager = importlib.import_module("moviesync.card_plugins").CardFilePluginManager(
        plugin_dir=tmp_path / "cards",
        registry=registry,
        logger=logging.getLogger("test.card_plugins"),
        seed_missing=False,
    )
    return manager, registry


def test_install_load_and_uninstall_card_lifecycle(tmp_path):
    manager, registry = _manager(tmp_path)

    card = manager.install("demo.py", CARD_SOURCE)

    assert registry.get("demo") is card
    assert manager.list_plugins() == [
        {
            "filename": "demo.py",
            "loaded": True,
            "card_id": "demo",
            "error": "",
            "size": len(CARD_SOURCE),
        }
    ]

    result = manager.uninstall("demo.py")

    assert result == {"filename": "demo.py", "card_id": "demo"}
    assert registry.get("demo") is None
    assert manager.list_plugins() == []


def test_failed_install_rolls_back_file_and_registry(tmp_path):
    manager, registry = _manager(tmp_path)
    broken_source = b"def create_card(context):\n    raise RuntimeError('broken factory')\n"

    try:
        manager.install("broken.py", broken_source)
    except RuntimeError as exc:
        assert "broken factory" in str(exc)
    else:
        raise AssertionError("broken card factory should fail installation")

    assert not (tmp_path / "cards" / "broken.py").exists()
    assert registry.list() == []
    assert manager.list_plugins() == []


def test_load_all_isolates_broken_card_from_valid_card(tmp_path):
    manager, registry = _manager(tmp_path)
    plugin_dir = tmp_path / "cards"
    (plugin_dir / "broken.py").write_text(
        "def create_card(context):\n    raise RuntimeError('broken card')\n",
        encoding="utf-8",
    )
    (plugin_dir / "demo.py").write_bytes(CARD_SOURCE)

    loaded = manager.load_all()

    assert loaded == ["demo"]
    assert registry.get("demo") is not None
    assert registry.get("broken") is None
    status = {item["filename"]: item for item in manager.list_plugins()}
    assert status["demo.py"]["loaded"] is True
    assert status["broken.py"]["loaded"] is False
    assert "broken card" in status["broken.py"]["error"]



def test_concurrent_load_all_keeps_one_consistent_context(tmp_path, monkeypatch):
    manager, registry = _manager(tmp_path)
    plugin_dir = tmp_path / "cards"
    context_card_source = b'''from moviesync.cards import Card, CardManifest


class ContextCard(Card):
    manifest = CardManifest(id="alpha", name="Alpha card")

    def __init__(self, context):
        self.marker = context.get("marker")


def create_card(context):
    return ContextCard(context)
'''
    (plugin_dir / "alpha.py").write_bytes(context_card_source)
    beta_source = context_card_source.replace(b'id="alpha"', b'id="beta"').replace(
        b'name="Alpha card"', b'name="Beta card"'
    )
    (plugin_dir / "beta.py").write_bytes(beta_source)

    first_card_loaded = Event()
    release_first_load = Event()
    second_call_started = Event()
    original_load_file = manager.load_file

    def pause_after_first_card(filename, context=None):
        card = original_load_file(filename, context)
        if filename == "alpha.py" and context.get("marker") == "first":
            first_card_loaded.set()
            assert release_first_load.wait(timeout=5)
        return card

    monkeypatch.setattr(manager, "load_file", pause_after_first_card)

    def load_second_context():
        second_call_started.set()
        return manager.load_all({"marker": "second"})

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(manager.load_all, {"marker": "first"})
        assert first_card_loaded.wait(timeout=5)
        second = executor.submit(load_second_context)
        assert second_call_started.wait(timeout=5)
        # The second load pass must wait for the first pass to finish.
        assert not second.done()
        release_first_load.set()
        assert first.result(timeout=5) == ["alpha", "beta"]
        assert second.result(timeout=5) == []

    assert registry.get("alpha").marker == "first"
    assert registry.get("beta").marker == "first"
    assert all(not item["error"] for item in manager.list_plugins())
