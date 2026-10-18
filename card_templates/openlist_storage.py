"""Native MovieSync card for managing OpenList storage-driver mounts."""
from __future__ import annotations

from typing import Any

from moviesync.cards import Card, CardManifest


class OpenListStorageManagerCard(Card):
    """Expose OpenList's native storage drivers through MovieSync's card manager.

    Mounts and their provider credentials remain in OpenList's persistent database.
    MovieSync only provides the UI and a server-side adapter to the official API.
    """

    manifest = CardManifest(
        id="openlist_storage",
        name="OpenList 存储卡片",
        version="1.0.0",
        type="storage_manager",
        description=(
            "在 MovieSync 内添加、编辑、启停和删除 OpenList 网盘挂载。"
            "使用 OpenList 官方驱动参数，不需要进入 OpenList 管理后台。"
        ),
        capabilities=("storage.manage", "storage.manage.check"),
        config_fields=(
            {
                "key": "manager_ui",
                "label": "网盘挂载管理",
                "type": "json",
                "editor": "openlist_mount_manager",
                "default": {},
                "description": (
                    "从这里配置夸克、115、123 网盘等 OpenList 官方驱动。"
                    "新建挂载前必须填写该驱动要求的授权参数。"
                ),
            },
        ),
    )

    def __init__(self, context: dict[str, Any] | None = None):
        self.context = dict(context or {})
        self.openlist_admin = self.context.get("openlist_admin")

    def is_configured(self, config: dict[str, Any] | None = None) -> bool:
        # The card is a management surface, not a saved-credentials form. Its
        # state is discovered from the internal OpenList service on demand.
        return True

    def check(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Report engine availability without changing any mount or credential."""
        client = self.openlist_admin
        if client is None:
            return {
                "status": "unavailable",
                "message": "MovieSync 内置 OpenList 管理服务尚未初始化。",
            }
        try:
            drivers = client.driver_map()
        except Exception:
            return {
                "status": "unavailable",
                "message": "无法连接内置 OpenList；请确认服务已启动且未被 MOVIESYNC_BUNDLED_OPENLIST_ENABLED 关闭。",
            }
        return {
            "status": "healthy",
            "message": f"内置 OpenList 已连接，可使用 {len(drivers)} 个官方存储驱动。",
            "details": {"driver_count": len(drivers)},
        }

    def close(self) -> None:
        # The shared OpenList client is owned and closed by the MovieSync app,
        # because other card-management API routes use the same instance.
        return None


def create_card(context):
    """Factory used by MovieSync's trusted single-file card loader."""
    return OpenListStorageManagerCard(context)
