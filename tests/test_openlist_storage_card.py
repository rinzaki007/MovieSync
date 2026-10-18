import json
import logging
from pathlib import Path

import pytest

from card_templates.openlist_storage import OpenListStorageManagerCard
from moviesync.card_plugins import CardFilePluginManager
from moviesync.cards import CardRegistry
from moviesync.openlist_control import (
    OpenListAdminClient,
    is_sensitive_parameter,
    normalize_mount_path,
    normalize_root_path,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        response = self.responses.pop(0)
        return response

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        response = self.responses.pop(0)
        return response

    def close(self):
        return None



def test_openlist_storage_card_loads_through_standard_card_plugin_manager(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    template = repo_root / "card_templates" / "openlist_storage.py"
    plugin_dir = tmp_path / "cards"
    plugin_dir.mkdir()
    (plugin_dir / "openlist_storage.py").write_bytes(template.read_bytes())

    registry = CardRegistry()
    manager = CardFilePluginManager(plugin_dir, registry, logging.getLogger("test-openlist-storage"))
    loaded = manager.load_all({"openlist_admin": None})

    assert loaded == ["openlist_storage"]
    card = registry.get("openlist_storage")
    assert card is not None
    assert card.card_type == "storage_manager"
    registry.close_all()

def test_openlist_storage_manager_card_uses_native_card_type():
    card = OpenListStorageManagerCard({"openlist_admin": None})

    assert card.card_id == "openlist_storage"
    assert card.card_type == "storage_manager"
    assert card.is_configured({})
    assert card.manifest.config_fields[0]["editor"] == "openlist_mount_manager"
    assert card.check({})["status"] == "unavailable"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("夸克网盘", "/夸克网盘"),
        ("/夸克网盘/", "/夸克网盘"),
        ("//影视//夸克", "/影视/夸克"),
    ],
)
def test_normalize_openlist_mount_path(raw, expected):
    assert normalize_mount_path(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "/../private", "/a/../../private"])
def test_normalize_openlist_mount_path_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        normalize_mount_path(raw)


def test_normalize_openlist_root_path():
    assert normalize_root_path("/") == "/"
    assert normalize_root_path("Movies/2026") == "/Movies/2026"
    with pytest.raises(ValueError):
        normalize_root_path("/../../private")


def test_sensitive_driver_parameters_are_detected():
    assert is_sensitive_parameter("cookie")
    assert is_sensitive_parameter("refresh_token")
    assert is_sensitive_parameter("client_secret")
    assert not is_sensitive_parameter("root_folder_id")


def test_driver_schema_comes_from_official_openlist_driver_registry():
    session = FakeSession([
        FakeResponse({
            "code": 200,
            "data": {
                "Quark": {
                    "config": {"name": "夸克网盘"},
                    "additional": [
                        {"name": "cookie", "type": "string", "required": True},
                        {"name": "root_folder_id", "type": "string", "default": "0"},
                    ],
                }
            },
        })
    ])
    client = OpenListAdminClient(base_url="http://127.0.0.1:5244", token="test-token", session=session)

    schema = client.driver_schema("Quark")

    assert schema["label"] == "夸克网盘"
    assert schema["root_mode"] == "id"
    assert [field["name"] for field in schema["fields"]] == ["cookie"]
    assert schema["fields"][0]["secret"] is True
    assert session.calls[0][1] == "http://127.0.0.1:5244/api/admin/driver/list"
    assert session.calls[0][2]["headers"]["Authorization"] == "test-token"


def test_create_storage_uses_official_driver_schema_and_root_mapping():
    session = FakeSession([
        FakeResponse({
            "code": 200,
            "data": {
                "Quark": {
                    "config": {"name": "夸克网盘"},
                    "additional": [
                        {"name": "cookie", "type": "string", "required": True},
                        {"name": "root_folder_id", "type": "string", "default": "0"},
                    ],
                }
            },
        }),
        FakeResponse({
            "code": 200,
            "data": {
                "Quark": {
                    "config": {"name": "夸克网盘"},
                    "additional": [
                        {"name": "cookie", "type": "string", "required": True},
                        {"name": "root_folder_id", "type": "string", "default": "0"},
                    ],
                }
            },
        }),
        FakeResponse({"code": 200, "data": {"content": [], "total": 0}}),
        FakeResponse({"code": 200, "data": {"id": 7}}),
    ])
    client = OpenListAdminClient(base_url="http://127.0.0.1:5244", token="test-token", session=session)

    result = client.save_storage(
        driver="Quark",
        display_name="夸克网盘",
        mount_path="/夸克网盘",
        root_path="/",
        authorization_parameters={"cookie": "real-cookie-value"},
    )

    assert result == {
        "id": 7,
        "created": True,
        "driver": "Quark",
        "mount_path": "/夸克网盘",
        "display_name": "夸克网盘",
    }
    method, url, kwargs = session.calls[-1]
    assert method == "POST"
    assert url.endswith("/api/admin/storage/create")
    payload = kwargs["json"]
    assert payload["mount_path"] == "/夸克网盘"
    assert payload["remark"] == "夸克网盘"
    assert json.loads(payload["addition"]) == {
        "cookie": "real-cookie-value",
        "root_folder_id": "0",
    }
    assert "real-cookie-value" not in json.dumps(result, ensure_ascii=False)


def test_storage_list_masks_saved_credentials_before_returning_to_browser():
    session = FakeSession([
        FakeResponse({
            "code": 200,
            "data": {
                "Quark": {
                    "config": {"name": "夸克网盘"},
                    "additional": [{"name": "cookie", "type": "string"}],
                }
            },
        }),
        FakeResponse({
            "code": 200,
            "data": {
                "content": [{
                    "id": 7,
                    "driver": "Quark",
                    "mount_path": "/夸克网盘",
                    "remark": "夸克网盘",
                    "addition": json.dumps({"cookie": "saved-secret", "root_folder_id": "0"}),
                    "status": "work",
                    "disabled": False,
                }],
                "total": 1,
            },
        }),
    ])
    client = OpenListAdminClient(base_url="http://127.0.0.1:5244", token="test-token", session=session)

    mounts = client.list_public_storages()

    assert mounts[0]["display_name"] == "夸克网盘"
    assert mounts[0]["mount_path"] == "/夸克网盘"
    assert mounts[0]["root_path"] == "/"
    assert mounts[0]["authorization_parameters"]["cookie"] == ""
    assert mounts[0]["has_value"]["cookie"] is True
    assert "saved-secret" not in json.dumps(mounts, ensure_ascii=False)
