import pytest
import requests

from moviesync.services.openlist_manager import OpenListManager


class ConfigStore:
    def __init__(self, config):
        self.config = config

    def get_card_config(self, card_id):
        assert card_id == "openlist"
        return dict(self.config)


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.body


class Session:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.body, self.status)


def test_driver_info_uses_openlist_admin_api_and_server_side_token():
    session = Session({"code": 200, "data": [{"name": "refresh_token"}]})
    manager = OpenListManager(
        ConfigStore({"base_url": "http://127.0.0.1:5244", "admin_token": "private-token"}),
        session=session,
    )

    assert manager.driver_info("Quark") == [{"name": "refresh_token"}]
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "http://127.0.0.1:5244/api/admin/driver/info"
    assert kwargs["params"] == {"driver": "Quark"}
    assert kwargs["headers"]["Authorization"] == "private-token"


def test_storage_create_and_update_validate_payloads():
    session = Session({"code": 200, "data": {"id": 12}})
    manager = OpenListManager(
        ConfigStore({"admin_token": "private-token"}),
        session=session,
    )

    assert manager.create_storage({"mount_path": "/quark", "driver": "Quark"}) == {"id": 12}
    with pytest.raises(ValueError, match="ID"):
        manager.update_storage({"mount_path": "/quark"})
    assert len(session.calls) == 1
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/api/admin/storage/create")


def test_storage_id_validation_blocks_bad_ids():
    manager = OpenListManager(ConfigStore({"admin_token": "private-token"}), session=Session({"code": 200}))
    with pytest.raises(ValueError, match="ID"):
        manager.delete_storage("../1")
    with pytest.raises(ValueError, match="ID"):
        manager.set_storage_enabled("0", True)


def test_management_requires_token():
    manager = OpenListManager(ConfigStore({"base_url": "http://127.0.0.1:5244"}), session=Session({}))
    with pytest.raises(PermissionError, match="Token"):
        manager.storages()
