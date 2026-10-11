"""单文件卡片安装、加载与卸载生命周期回归测试。"""

import importlib
import logging

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
