import pytest

from moviesync.services.openlist_manager import OpenListManager


class BundledAdminClient:
    base_url = "http://127.0.0.1:5244"

    def __init__(self):
        self.calls = []

    def _admin_token(self):
        return "generated-by-openlist-cli"

    def driver_map(self):
        return {"Quark": {"additional": []}, "115": {"additional": []}}

    def _request(self, method, endpoint, *, payload=None, params=None):
        self.calls.append((method, endpoint, payload, params, self._admin_token()))
        if endpoint == "/api/admin/driver/info":
            return {"code": 200, "data": {"common": [], "additional": [{"name": "refresh_token"}]}}
        if endpoint == "/api/admin/storage/list":
            return {"code": 200, "data": {"content": [], "total": 0}}
        return {"code": 200, "data": {"id": 12}}


def test_driver_info_uses_shared_bundled_admin_client():
    client = BundledAdminClient()
    manager = OpenListManager(client)

    assert manager.drivers() == client.driver_map()
    assert manager.driver_names() == ["Quark", "115"]
    assert manager.driver_info("Quark") == {
        "common": [],
        "additional": [{"name": "refresh_token"}],
    }
    method, endpoint, payload, params, token = client.calls[0]
    assert method == "GET"
    assert endpoint == "/api/admin/driver/info"
    assert params == {"driver": "Quark"}
    assert token == "generated-by-openlist-cli"


def test_storage_create_and_update_validate_payloads():
    client = BundledAdminClient()
    manager = OpenListManager(client)

    assert manager.create_storage({"mount_path": "/quark", "driver": "Quark"}) == {"id": 12}
    with pytest.raises(ValueError, match="ID"):
        manager.update_storage({"mount_path": "/quark"})
    assert len(client.calls) == 1
    assert client.calls[0][0:3] == (
        "POST",
        "/api/admin/storage/create",
        {"mount_path": "/quark", "driver": "Quark"},
    )


def test_storage_id_validation_blocks_bad_ids():
    manager = OpenListManager(BundledAdminClient())
    with pytest.raises(ValueError, match="ID"):
        manager.delete_storage("../1")
    with pytest.raises(ValueError, match="ID"):
        manager.set_storage_enabled("0", True)


def test_storage_list_uses_bundled_client_without_user_supplied_token():
    client = BundledAdminClient()
    manager = OpenListManager(client)

    assert manager.storages() == {"content": [], "total": 0}
    assert client.calls[0][1] == "/api/admin/storage/list"
    assert client.calls[0][4] == "generated-by-openlist-cli"


def test_manager_requires_initialized_bundled_client():
    with pytest.raises(RuntimeError, match="尚未初始化"):
        OpenListManager(None)
