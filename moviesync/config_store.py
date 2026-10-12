"""MovieSync 持久化配置管理。

通用卡片配置和平台级设置统一保存在 data/config.json。历史 Quark/Telegram
字段的默认值与迁移逻辑位于 legacy_card_config 兼容模块；public() 会脱敏敏感值。
"""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from .errors import ConfigValidationError
from .legacy_card_config import (
    LegacyCardConfigAdapter,
    legacy_card_defaults,
    migrate_legacy_files,
    normalize_legacy_card_config,
)
from .settings import DEFAULT_CATEGORY_FIDS, DEFAULT_OPENLIST_URL
from .storage import JsonStore

CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_]{2,64}$")
FID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

MAX_CHANNELS = 100
CONFIG_SCHEMA_VERSION = 5


class ConfigStore:
    def __init__(self, config_file: Path, legacy_root: Path):
        self.store = JsonStore(config_file, self._defaults)
        self.legacy_root = legacy_root
        self.legacy_cards = LegacyCardConfigAdapter(self, CONFIG_SCHEMA_VERSION)
        self._migrate_legacy()

    @staticmethod
    def _defaults() -> dict:
        return {
            **legacy_card_defaults(),
            "openlist_url": DEFAULT_OPENLIST_URL,
            "default_storage_target_id": "",
            "schema_version": CONFIG_SCHEMA_VERSION,
        }

    def _migrate_legacy(self) -> None:
        migrate_legacy_files(self.store, self.legacy_root)

    @staticmethod
    def _normalize_fid(
        value: object,
        field: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        fid = str(value or "").strip()

        if not fid:
            if allow_empty:
                return ""
            return "0"

        if not FID_RE.fullmatch(fid):
            raise ConfigValidationError(f"{field} 格式无效")

        return fid

    @staticmethod
    def _normalize_channels(
        channels: object,
    ) -> list[dict[str, str]]:
        if channels is None:
            return []

        if not isinstance(channels, list):
            raise ConfigValidationError("channels 必须是数组")

        result: list[dict[str, str]] = []
        seen: set[str] = set()

        for index, item in enumerate(channels):
            if isinstance(item, str):
                channel_id = item.strip().lstrip("@")
                name = channel_id

            elif isinstance(item, dict):
                channel_id = str(
                    item.get("id", "")
                ).strip().lstrip("@")

                name = str(
                    item.get("name", channel_id)
                ).strip()

            else:
                raise ConfigValidationError(
                    f"第 {index + 1} 个频道配置无效"
                )

            if not CHANNEL_ID_RE.fullmatch(channel_id):
                raise ConfigValidationError(
                    f"第 {index + 1} 个频道 ID 无效"
                )

            key = channel_id.lower()

            if key in seen:
                continue

            if len(name) > 100:
                name = name[:100]

            seen.add(key)

            result.append(
                {
                    "id": channel_id,
                    "name": name or channel_id,
                }
            )

        if len(result) > MAX_CHANNELS:
            raise ConfigValidationError(
                f"频道数量不能超过 {MAX_CHANNELS} 个"
            )

        return result

    @staticmethod
    def _normalize_openlist(value: object) -> str:
        url = str(
            value or DEFAULT_OPENLIST_URL
        ).strip()

        if not url:
            return ""

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigValidationError("OpenList 地址无效")

        return url

    def load(self) -> dict:
        data = self.store.read()
        original_data = deepcopy(data)
        defaults = self._defaults()
        if isinstance(data, dict):
            defaults.update(data)

        normalized = normalize_legacy_card_config(
            defaults,
            normalize_fid=self._normalize_fid,
            normalize_channels=self._normalize_channels,
            default_category_fids=DEFAULT_CATEGORY_FIDS,
            schema_version=CONFIG_SCHEMA_VERSION,
        )
        # Persist normalized legacy data and the current schema version once.
        if normalized != original_data:
            self.store.write(normalized)

        return normalized

    def save_card_config(
        self,
        card_id: str,
        incoming: dict,
        *,
        enabled: bool | None = None,
        config_version: int | None = None,
        replace_config: bool = False,
    ) -> dict:
        """保存已注册卡片的通用配置；调用方必须先确认卡片已加载。"""
        card_id = str(card_id or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", card_id):
            raise ConfigValidationError("卡片 ID 无效")
        if not isinstance(incoming, dict):
            raise ConfigValidationError("卡片配置必须是 JSON 对象")
        current = self.load()
        cards = current.setdefault("cards", {})
        saved = cards.get(card_id)
        saved = saved if isinstance(saved, dict) else {}
        config = saved.get("config")
        config = dict(config) if isinstance(config, dict) else {}
        if replace_config:
            config = deepcopy(incoming)
        else:
            config.update(deepcopy(incoming))
        saved["config"] = config
        if config_version is not None:
            if (
                isinstance(config_version, bool)
                or not isinstance(config_version, int)
                or config_version < 1
            ):
                raise ConfigValidationError("卡片配置版本必须是正整数")
            saved["config_version"] = config_version
        if enabled is not None:
            saved["enabled"] = bool(enabled)
        else:
            saved["enabled"] = bool(saved.get("enabled", True))
        cards[card_id] = saved
        current["cards"] = cards
        current["schema_version"] = CONFIG_SCHEMA_VERSION
        self.store.write(current)
        return deepcopy(saved)

    def get_card_config(self, card_id: str) -> dict:
        """Return a card's saved config by ID without knowing its implementation."""
        card_id = str(card_id or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", card_id):
            raise ConfigValidationError("卡片 ID 无效")
        config = self.load()
        cards = config.get("cards", {}) if isinstance(config, dict) else {}
        saved = cards.get(card_id, {}) if isinstance(cards, dict) else {}
        card_config = saved.get("config", {}) if isinstance(saved, dict) else {}
        return deepcopy(card_config) if isinstance(card_config, dict) else {}

    def get_quark_config(self) -> dict:
        """Legacy compatibility wrapper; new card code should use get_card_config()."""
        return self.legacy_cards.get_quark_config()

    def save_quark_config(self, incoming: dict) -> dict:
        return self.legacy_cards.save_quark_config(incoming)

    def get_cookie(self) -> str:
        return self.legacy_cards.get_cookie()

    def get_default_storage_target_id(self) -> str:
        return str(self.load().get("default_storage_target_id") or "").strip()

    def set_default_storage_target_id(self, target_id: object) -> str:
        target_id = str(target_id or "").strip()
        if len(target_id) > 64 or (target_id and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", target_id)):
            raise ConfigValidationError("默认存储目标 ID 无效")
        current = self.load()
        current["default_storage_target_id"] = target_id
        current["schema_version"] = CONFIG_SCHEMA_VERSION
        self.store.write(current)
        return target_id

    def get_channels(self) -> list[dict[str, str]]:
        return self.legacy_cards.get_channels()

    def get_telegram_config(self) -> dict:
        """Legacy compatibility wrapper for the Telegram card."""
        return self.legacy_cards.get_telegram_config()

    def save_telegram_config(self, incoming: dict) -> dict:
        return self.legacy_cards.save_telegram_config(incoming)

    @classmethod
    def _sanitize_public_value(cls, value):
        """递归隐藏卡片配置中的敏感值，同时保留是否已配置的信息。"""
        sensitive_markers = (
            "cookie",
            "token",
            "secret",
            "password",
            "api_key",
            "authorization",
            "credential",
            "private_key",
        )

        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                # has_* 是给管理界面使用的布尔状态，不是凭据本身。
                if lowered.startswith("has_") and isinstance(item, bool):
                    result[key] = item
                elif any(marker in lowered for marker in sensitive_markers):
                    result[key] = ""
                    result[f"has_{key_text}"] = bool(item)
                else:
                    result[key] = cls._sanitize_public_value(item)
            return result

        if isinstance(value, list):
            return [cls._sanitize_public_value(item) for item in value]

        return deepcopy(value)

    def public(self) -> dict:
        """返回脱敏后的配置，不要求任何特定卡片已安装。"""
        return self._sanitize_public_value(self.load())

    def _import_legacy_card_settings(
        self,
        current: dict,
        imported_cards: object,
    ) -> dict:
        """兼容旧版备份中的 Quark/Telegram 配置；新卡片走通用导入流程。"""
        if not isinstance(imported_cards, dict):
            return current
        if isinstance(imported_cards.get("quark"), dict):
            quark = imported_cards["quark"]
            quark_config = quark.get("config")
            if isinstance(quark_config, dict):
                imported_quark = dict(quark_config)
                if "cookie" in imported_quark:
                    # A public config export intentionally redacts secrets to
                    # an empty string. Treat that as "unchanged", not as an
                    # explicit request to erase the locally stored cookie.
                    cookie = str(imported_quark.pop("cookie") or "").strip()
                    if cookie:
                        imported_quark["cookie"] = cookie
                self.save_quark_config(imported_quark)
            current = self.load()
            if "enabled" in quark:
                current["cards"]["quark"]["enabled"] = bool(quark["enabled"])
                self.store.write(current)
        if isinstance(imported_cards.get("telegram"), dict):
            telegram = imported_cards["telegram"]
            telegram_config = telegram.get("config")
            if isinstance(telegram_config, dict):
                self.save_telegram_config({
                    key: value
                    for key, value in telegram_config.items()
                    if key in {"channels", "magic_regex"}
                })
            current = self.load()
            if "enabled" in telegram:
                current["cards"]["telegram"]["enabled"] = bool(telegram["enabled"])
                self.store.write(current)
        return current

    def save(self, incoming: dict) -> dict:
        if not isinstance(incoming, dict):
            raise ConfigValidationError(
                "配置必须是 JSON 对象"
            )

        current = self.load()

        legacy_quark = {}
        if incoming.get("clear_quark_cookie") is True:
            legacy_quark["clear_cookie"] = True
        elif "quark_cookie" in incoming:
            legacy_quark["cookie"] = incoming.get("quark_cookie")
        if "default_fid" in incoming:
            legacy_quark["default_fid"] = incoming["default_fid"]
        if "category_fids" in incoming:
            legacy_quark["category_fids"] = incoming["category_fids"]
        if legacy_quark:
            self.save_quark_config(legacy_quark)
            current = self.load()

        if "openlist_url" in incoming:
            current["openlist_url"] = self._normalize_openlist(
                incoming["openlist_url"]
            )
        if "default_storage_target_id" in incoming:
            target_id = str(incoming.get("default_storage_target_id") or "").strip()
            if len(target_id) > 64 or (target_id and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", target_id)):
                raise ConfigValidationError("默认存储目标 ID 无效")
            current["default_storage_target_id"] = target_id

        imported_cards = incoming.get("cards")
        current = self._import_legacy_card_settings(current, imported_cards)

        # Restore third-party card settings too. Keep entries for cards that
        # are not currently installed so their configuration survives backups.
        if isinstance(imported_cards, dict):
            current_cards = current.setdefault("cards", {})
            sensitive_markers = (
                "cookie",
                "token",
                "secret",
                "password",
                "api_key",
                "authorization",
                "credential",
                "private_key",
            )
            for card_id, imported_card in imported_cards.items():
                if card_id in {"quark", "telegram"} or not isinstance(imported_card, dict):
                    continue
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(card_id)):
                    continue
                imported_config = imported_card.get("config")
                if not isinstance(imported_config, dict):
                    continue
                saved_card = current_cards.get(card_id)
                saved_card = dict(saved_card) if isinstance(saved_card, dict) else {}
                saved_config = saved_card.get("config")
                saved_config = dict(saved_config) if isinstance(saved_config, dict) else {}
                def merge_imported_values(existing, imported):
                    merged = dict(existing) if isinstance(existing, dict) else {}
                    for key, value in imported.items():
                        lowered_key = str(key).lower()
                        is_secret = any(marker in lowered_key for marker in sensitive_markers)
                        # Public/redacted exports can contain empty secret fields;
                        # don't erase a credential that is already stored locally.
                        if is_secret and (
                            value is None or (isinstance(value, str) and not value.strip())
                        ):
                            continue
                        if isinstance(value, dict):
                            merged[key] = merge_imported_values(merged.get(key), value)
                        else:
                            merged[key] = deepcopy(value)
                    return merged

                saved_config = merge_imported_values(saved_config, imported_config)
                saved_card["config"] = saved_config
                if isinstance(imported_card.get("enabled"), bool):
                    saved_card["enabled"] = imported_card["enabled"]
                else:
                    saved_card["enabled"] = bool(saved_card.get("enabled", True))
                current_cards[card_id] = saved_card

        if "channels" in incoming:
            self.save_telegram_config({"channels": incoming["channels"]})
            current = self.load()

        current["schema_version"] = CONFIG_SCHEMA_VERSION
        self.store.write(current)

        return current

    def get_resource_sources(self) -> list[dict]:
        return self.legacy_cards.get_resource_sources()

    def update_resource_source_health(
        self,
        source_id: str,
        status: str,
        message: str,
        channels: list[dict] | None = None,
    ) -> None:
        self.legacy_cards.update_resource_source_health(
            source_id,
            status,
            message,
            channels,
        )

    def save_channels(
        self,
        channels: object,
    ) -> list[dict[str, str]]:
        return self.legacy_cards.save_channels(channels)
