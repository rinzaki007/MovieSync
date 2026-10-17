# ruff: noqa: I001
"""Architecture regression tests for built-in single-file card independence."""

import pathlib

import pytest

from moviesync.app import create_app


BUILTIN_CARD_IDS = (
    "douban",
    "filename_recognition",
    "pansou",
    "quark",
    "telegram",
    "tmdb",
)


@pytest.mark.parametrize("card_id", BUILTIN_CARD_IDS)
def test_uninstalling_one_builtin_card_does_not_break_other_cards_or_platform(
    tmp_path, card_id
):
    """Every bundled card can be removed without taking the host or peers down."""
    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    registry = services["card_registry"]
    manager = services["file_card_plugins"]

    # Quark cannot be removed while it is the selected default target. Clear the
    # selection first so this test measures card isolation, not the safety guard.
    if card_id == "quark":
        services["config"].set_default_storage_target_id("")

    for other_id in BUILTIN_CARD_IDS:
        assert registry.get(other_id) is not None, f"{other_id} should load independently"
        assert (tmp_path / "cards" / f"{other_id}.py").is_file()

    removed = manager.uninstall(f"{card_id}.py")

    assert removed["card_id"] == card_id
    assert registry.get(card_id) is None
    assert not (tmp_path / "cards" / f"{card_id}.py").exists()

    for other_id in BUILTIN_CARD_IDS:
        if other_id != card_id:
            assert registry.get(other_id) is not None, (
                f"removing {card_id} must not unregister {other_id}"
            )

    # The admin card API must continue to work when any optional card is missing.
    client = app.test_client()
    setup = client.post(
        "/api/setup", json={"username": "admin", "password": "password123"}
    )
    assert setup.status_code in {200, 201}
    response = client.get("/api/cards")
    assert response.status_code == 200
    listed_ids = {item["id"] for item in response.get_json()["cards"]}
    assert card_id not in listed_ids
    assert set(BUILTIN_CARD_IDS) - {card_id} <= listed_ids


def test_all_bundled_card_implementations_are_single_file_factories():
    """Bundled implementations must expose the loader contract in their own file."""
    template_dir = pathlib.Path(__file__).resolve().parents[1] / "card_templates"
    files = {
        path.stem: path
        for path in template_dir.glob("*.py")
        if path.name != "__init__.py"
    }

    assert set(BUILTIN_CARD_IDS) <= set(files)
    for card_id in BUILTIN_CARD_IDS:
        source = files[card_id].read_text(encoding="utf-8")
        assert "def create_card(context)" in source, (
            f"{card_id}.py must provide its own create_card(context) factory"
        )
        assert "from card_templates." not in source, (
            f"{card_id}.py must not import another bundled card implementation"
        )


def test_core_services_do_not_import_concrete_platform_clients_or_card_templates():
    """New cards must plug into the contracts, not concrete built-in implementations."""
    import ast

    services_dir = pathlib.Path(__file__).resolve().parents[1] / "moviesync" / "services"
    forbidden_import_prefixes = (
        "card_templates.",
        "moviesync.clients.quark",
        "moviesync.clients.telegram",
    )
    forbidden_names = {"QuarkStorageCard", "TelegramResourceSource"}

    for path in sorted(services_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                imported = []
            for module in imported:
                assert not module.startswith(forbidden_import_prefixes), (
                    f"{path.relative_to(services_dir.parent.parent)} must use card "
                    f"contracts instead of importing concrete platform module {module!r}"
                )
            if isinstance(node, ast.Name):
                assert node.id not in forbidden_names, (
                    f"{path.relative_to(services_dir.parent.parent)} must not reference "
                    f"concrete built-in card class {node.id!r}"
                )
