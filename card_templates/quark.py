"""Standalone Quark storage-target card shipped as a single Python file."""
from __future__ import annotations

import re
from typing import Any

from moviesync.cards import CardManifest, StorageTargetCard
from moviesync.clients.quark import QuarkClient, sanitize_pwd_id
from moviesync.errors import ConfigValidationError

_FID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CATEGORY_FID_KEYS = ("电影", "电视剧", "综艺", "动漫")


def _normalize_fid(value: object, field: str, *, allow_empty: bool = False) -> str:
    """Validate storage folder IDs inside the storage card, not ConfigStore."""
    fid = str(value or "").strip()
    if not fid:
        return "" if allow_empty else "0"
    if not _FID_RE.fullmatch(fid):
        raise ConfigValidationError(f"{field} 格式无效")
    return fid


class QuarkStorageCard(StorageTargetCard):
    """内置夸克存储卡。

    这里只负责把现有 QuarkClient 包装成标准 StorageTargetCard；
    原有 QuarkClient 业务保持不变，降低迁移风险。
    """

    web_url = "https://pan.quark.cn/"

    manifest = CardManifest(
        id="quark",
        name="Quark",
        version="1.0.0",
        type="storage_target",
        description="夸克网盘存储与转存卡片",
        capabilities=(
            "storage.check",
            "storage.accepts.quark_share",
            "storage.resolve_resource",
            "storage.list_files",
            "storage.list_destination_files",
            "storage.create_folder",
            "storage.transfer",
        ),
        config_fields=(
            {
                "key": "cookie",
                "label": "夸克 Cookie",
                "type": "password",
                "secret": True,
                "required": False,
                "placeholder": "粘贴 Cookie；留空则保留已保存的值",
                "description": "敏感凭据不会回显到页面。",
            },
            {
                "key": "default_fid",
                "label": "默认目录 FID",
                "type": "string",
                "required": True,
                "default": "0",
                "placeholder": "0",
            },
            {
                "key": "category_fids",
                "label": "分类目录 FID",
                "type": "json",
                "editor": "object_fields",
                "default": {},
                "item_fields": (
                    {
                        "key": "动漫",
                        "label": "动漫目录 FID",
                        "placeholder": "请输入动漫目录 FID",
                    },
                    {
                        "key": "电影",
                        "label": "电影目录 FID",
                        "placeholder": "请输入电影目录 FID",
                    },
                    {
                        "key": "电视剧",
                        "label": "电视剧目录 FID",
                        "placeholder": "请输入电视剧目录 FID",
                    },
                    {
                        "key": "综艺",
                        "label": "综艺目录 FID",
                        "placeholder": "请输入综艺目录 FID",
                    },
                ),
                "description": "四个分类分别填写目录 FID；留空的分类会使用默认目录。",
            },
        ),
    )

    def __init__(self, config_store):
        self.config_store = config_store

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """验证夸克目录配置，并兼容旧版“分类=FID”文本。"""
        incoming = dict(config) if isinstance(config, dict) else config
        if isinstance(incoming, dict):
            incoming = dict(incoming)
            category_fids = incoming.get("category_fids", {})
            if category_fids is None or category_fids == "":
                category_fids = {}
            # 兼容旧版前端提交的多行文本，统一转换为对象后再做字段校验。
            if isinstance(category_fids, str):
                parsed: dict[str, str] = {}
                for line_number, line in enumerate(category_fids.splitlines(), start=1):
                    line = line.strip()
                    if not line:
                        continue
                    if "=" not in line:
                        raise ConfigValidationError(
                            f"分类目录 FID 第 {line_number} 行格式无效，请使用“分类=FID”"
                        )
                    category, fid = (part.strip() for part in line.split("=", 1))
                    if not category or not fid:
                        raise ConfigValidationError(
                            f"分类目录 FID 第 {line_number} 行格式无效，请使用“分类=FID”"
                        )
                    if category in parsed:
                        raise ConfigValidationError(f"分类目录重复：{category}")
                    parsed[category] = fid
                category_fids = parsed
            incoming["category_fids"] = category_fids

        normalized = super().validate_config(incoming)
        normalized["default_fid"] = _normalize_fid(
            normalized.get("default_fid", "0"), "cards.quark.config.default_fid"
        )
        category_fids = normalized.get("category_fids", {})
        if category_fids is None or category_fids == "":
            category_fids = {}
        if not isinstance(category_fids, dict):
            raise ConfigValidationError("分类目录 FID 必须是对象")
        unknown = set(category_fids) - set(_CATEGORY_FID_KEYS)
        if unknown:
            raise ConfigValidationError("未知分类目录: " + ", ".join(sorted(map(str, unknown))))
        normalized["category_fids"] = {
            key: _normalize_fid(
                category_fids.get(key, ""),
                f"cards.quark.config.category_fids.{key}",
                allow_empty=True,
            )
            for key in _CATEGORY_FID_KEYS
        }
        return normalized

    def _config(self) -> dict[str, Any]:
        getter = getattr(self.config_store, "get_card_config", None)
        if not callable(getter):
            return {}
        config = getter(self.card_id)
        return config if isinstance(config, dict) else {}

    def destination_options(self) -> list[dict[str, Any]]:
        config = self._config()
        options = []
        default_fid = str(config.get("default_fid") or "0")
        options.append({"id": default_fid, "name": "默认目录", "is_default": True})
        for name, fid in (config.get("category_fids") or {}).items():
            fid = str(fid or "").strip()
            if fid:
                options.append({
                    "id": fid,
                    "name": f"{name}目录",
                    "category": name,
                    "is_default": False,
                })
        seen = set()
        return [item for item in options if not (item["id"] in seen or seen.add(item["id"]))]


    def _client(self) -> QuarkClient:
        return QuarkClient(str(self._config().get("cookie") or "").strip())

    def is_configured(self, config: dict[str, Any]) -> bool:
        # Cookie is the credential that determines whether this target can be used;
        # default FIDs alone must not make an unconfigured target appear ready.
        cookie = config.get("cookie") if isinstance(config, dict) else ""
        return bool(str(cookie or "").strip())

    def check(self, config: dict | None = None) -> dict[str, Any]:
        saved_config = config if isinstance(config, dict) else self._config()
        cookie = str(saved_config.get("cookie") or "").strip()
        if not cookie:
            return {
                "status": "unconfigured",
                "message": "尚未配置夸克 Cookie",
            }
        valid = self._client().check_cookie_valid()
        return {
            "status": "healthy" if valid else "unavailable",
            "message": "夸克 Cookie 有效" if valid else "夸克 Cookie 无效或已过期",
        }

    def resolve_resource(self, resource: object) -> dict[str, Any]:
        if not isinstance(resource, dict):
            return {"files": [], "token": None, "error": "资源参数无效"}
        pwd_id = sanitize_pwd_id(resource.get("resource_id") or resource.get("pwd_id"))
        if not pwd_id:
            return {"files": [], "token": None, "error": "分享资源 ID 无效"}
        files, stoken, error = self._client().get_share_files(
            pwd_id,
            passcode=str(resource.get("password") or resource.get("passcode") or ""),
        )
        return {
            "files": files or [],
            "token": stoken,
            "error": error,
        }

    def list_files(self, resource: object) -> list[dict[str, Any]]:
        return self.resolve_resource(resource).get("files") or []

    def list_destination_files(self, parent_id: str = "0") -> list[dict[str, Any]]:
        """列出本人夸克网盘目标目录，供转存结果核对使用。"""
        return self._client().list_drive_files(parent_id)

    def create_folder(self, name: str, parent_id: str = "0") -> str:
        fid, error = self._client().get_or_create_subfolder(name, parent_id)
        if not fid:
            raise RuntimeError(error or "创建目录失败")
        return fid

    def transfer(
        self,
        resource: object,
        files: list[dict[str, Any]],
        target_id: str = "0",
    ) -> tuple[bool, str]:
        if not isinstance(resource, dict):
            return False, "资源参数无效"
        pwd_id = sanitize_pwd_id(resource.get("pwd_id"))
        stoken = resource.get("stoken")
        if not pwd_id or not stoken:
            return False, "分享资源参数无效"
        return self._client().save_files(
            pwd_id,
            files,
            stoken,
            target_id,
        )


def create_card(context):
    """Create this card using the platform's shared configuration store."""
    return QuarkStorageCard(context["config_store"])
