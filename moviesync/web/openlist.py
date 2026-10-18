"""MovieSync endpoints for the OpenList mount manager.

Every mutation is protected by the same login and CSRF gates as other MovieSync APIs.
The OpenList administrator token is read server-side from the OpenList card config and
is never returned to the browser.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ..services.openlist_manager import OpenListManager
from .routes import require_csrf

openlist_api = Blueprint("openlist_api", __name__, url_prefix="/api/openlist")


def _manager():
    services = current_app.extensions["moviesync"]
    return OpenListManager(services["config"])


def _error(exc):
    if isinstance(exc, PermissionError):
        status = 403
    elif isinstance(exc, ValueError):
        status = 400
    else:
        status = 502
    return jsonify({"success": False, "message": str(exc)[:220]}), status


@openlist_api.get("/drivers")
def list_drivers():
    manager = _manager()
    try:
        return jsonify({"success": True, "drivers": manager.drivers(), "names": manager.driver_names()})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.get("/drivers/<path:driver_name>")
def driver_info(driver_name):
    try:
        return jsonify({"success": True, "driver": driver_name, "fields": _manager().driver_info(driver_name)})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.get("/storages")
def list_storages():
    try:
        page = request.args.get("page", "1")
        per_page = request.args.get("per_page", "0")
        return jsonify({"success": True, "storages": _manager().storages(page=page, per_page=per_page)})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.post("/storages")
@require_csrf
def create_storage():
    try:
        return jsonify({"success": True, "result": _manager().create_storage(request.get_json(silent=True) or {})})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.put("/storages")
@require_csrf
def update_storage():
    try:
        return jsonify({"success": True, "result": _manager().update_storage(request.get_json(silent=True) or {})})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.delete("/storages/<storage_id>")
@require_csrf
def delete_storage(storage_id):
    try:
        return jsonify({"success": True, "result": _manager().delete_storage(storage_id)})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)


@openlist_api.post("/storages/<storage_id>/enabled")
@require_csrf
def set_storage_enabled(storage_id):
    payload = request.get_json(silent=True) or {}
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"success": False, "message": "enabled 必须是布尔值"}), 400
    try:
        return jsonify({"success": True, "result": _manager().set_storage_enabled(storage_id, enabled)})
    except (ValueError, PermissionError, RuntimeError) as exc:
        return _error(exc)
