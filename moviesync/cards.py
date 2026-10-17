"""MovieSync 通用卡片接口与注册表。

卡片是 MovieSync 的可插拔能力单元。Core 只认识卡片的身份、类型和能力，
不要求卡片必须是 Telegram、夸克或某一种具体实现。

当前版本只建立稳定的接口层，不负责动态安装第三方代码。
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlparse

from .errors import ConfigValidationError
from .regex_safety import has_nested_unbounded_quantifier

CARD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CARD_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CARD_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9*]+)*$")
CARD_CONFIG_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")



def _freeze_manifest_value(value: Any) -> Any:
    """Recursively freeze manifest metadata after validation."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_manifest_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_manifest_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_manifest_value(item) for item in value)
    return value


def _thaw_manifest_value(value: Any) -> Any:
    """Return JSON-friendly copies of recursively frozen manifest metadata."""
    if isinstance(value, Mapping):
        return {key: _thaw_manifest_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_manifest_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw_manifest_value(item) for item in value]
    return deepcopy(value)


CARD_TYPES = {
    "resource_source",
    "storage_target",
    "playback_provider",
    "metadata_provider",
    "notification",
    "automation",
    "filename_processor",
    "custom",
}


@dataclass(frozen=True)
class CardManifest:
    """描述一张卡片的公开身份与能力。"""

    id: str
    name: str
    version: str = "1.0.0"
    type: str = "custom"
    description: str = ""
    capabilities: tuple[str, ...] = ()
    config_version: int = 1
    config_fields: tuple[dict[str, Any], ...] = ()
    image_hosts: tuple[str, ...] = ()
    image_referer: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not CARD_ID_RE.fullmatch(self.id):
            raise ValueError(f"卡片 ID 无效: {self.id}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("卡片名称不能为空")
        if not isinstance(self.version, str) or not CARD_VERSION_RE.fullmatch(self.version):
            raise ValueError("卡片版本必须使用语义化版本号，例如 1.0.0")
        if not isinstance(self.description, str):
            raise ValueError("卡片描述必须是文本")
        if not isinstance(self.capabilities, (tuple, list)):
            raise ValueError("卡片 capabilities 必须是字符串列表")
        if any(
            not isinstance(capability, str)
            or not CARD_CAPABILITY_RE.fullmatch(capability)
            for capability in self.capabilities
        ):
            raise ValueError("卡片 capabilities 包含无效能力标识")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("卡片 capabilities 不能重复")
        if not isinstance(self.config_fields, (tuple, list)):
            raise ValueError("卡片 config_fields 必须是对象列表")
        if any(not isinstance(field, dict) for field in self.config_fields):
            raise ValueError("卡片配置字段必须是对象")
        if not isinstance(self.image_hosts, (tuple, list)):
            raise ValueError("卡片 image_hosts 必须是域名列表")
        if not isinstance(self.image_referer, str):
            raise ValueError("卡片 image_referer 必须是文本")
        if isinstance(self.config_version, bool) or not isinstance(self.config_version, int) or self.config_version < 1:
            raise ValueError("卡片 config_version 必须是正整数")
        if self.type not in CARD_TYPES:
            raise ValueError(f"卡片类型无效: {self.type}")
        for host in self.image_hosts:
            if (
                not isinstance(host, str)
                or not host
                or host != host.lower()
                or any(char in host for char in "/:@?#")
                or "." not in host
            ):
                raise ValueError("卡片 image_hosts 必须是小写域名列表")
        if self.image_referer and not self.image_referer.startswith("https://"):
            raise ValueError("卡片 image_referer 必须使用 HTTPS")
        keys = [str(field.get("key") or "") for field in self.config_fields]
        if any(
            not CARD_CONFIG_KEY_RE.fullmatch(key)
            for key in keys
        ):
            raise ValueError("卡片配置字段 key 必须以小写字母开头，且只包含小写字母、数字、下划线或连字符")
        if len(keys) != len(set(keys)):
            raise ValueError("卡片配置字段必须具有唯一且非空的 key")
        for field in self.config_fields:
            for flag in ("required", "secret"):
                if flag in field and not isinstance(field[flag], bool):
                    raise ValueError(f"配置字段「{field.get('label') or field.get('key')}」的 {flag} 必须是布尔值")
        allowed_field_types = {"string", "password", "textarea", "boolean", "number", "json"}
        text_field_types = {"string", "password", "textarea"}
        for field in self.config_fields:
            field_type = field.get("type", "string")
            if field_type not in allowed_field_types:
                raise ValueError(f"不支持的配置字段类型: {field_type}")

            for bound in ("min_length", "max_length"):
                value = field.get(bound)
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                ):
                    raise ValueError(f"配置字段「{field.get('label') or field.get('key')}」的 {bound} 必须是非负整数")

            min_length = field.get("min_length")
            max_length = field.get("max_length")
            if min_length is not None and max_length is not None and min_length > max_length:
                raise ValueError(f"配置字段「{field.get('label') or field.get('key')}」的最小长度不能大于最大长度")

            pattern = field.get("pattern")
            if pattern is not None:
                if field_type not in text_field_types or not isinstance(pattern, str):
                    raise ValueError("pattern 只能用于文本字段，且必须是字符串")
                if len(pattern) > 500:
                    label = field.get("label") or field.get("key")
                    raise ValueError(
                        f"配置字段「{label}」的正则规则不能超过 500 个字符"
                    )
                if has_nested_unbounded_quantifier(pattern):
                    raise ValueError(
                        f"配置字段「{field.get('label') or field.get('key')}」的正则规则包含不安全的嵌套重复"
                    )
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"配置字段「{field.get('label') or field.get('key')}」的正则规则无效") from exc

            value_format = field.get("format")
            if value_format is not None:
                if field_type not in text_field_types or value_format not in {"url", "email"}:
                    raise ValueError(f"配置字段「{field.get('label') or field.get('key')}」的格式规则无效")

        # Detach the validated manifest from caller-owned mutable containers.
        # A frozen dataclass does not freeze lists or dictionaries nested inside it.
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "config_fields", tuple(_freeze_manifest_value(field) for field in self.config_fields))
        object.__setattr__(self, "image_hosts", tuple(self.image_hosts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "type": self.type,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "config_version": self.config_version,
            "config_fields": [_thaw_manifest_value(field) for field in self.config_fields],
            "image_hosts": list(self.image_hosts),
            "image_referer": self.image_referer,
        }


class Card:
    """所有 MovieSync 卡片的最小统一接口。

    卡片可以提供任意能力，但必须明确自己的身份、类型和 capabilities。
    具体业务接口由卡片类型继续扩展，例如 ResourceSource。
    """

    manifest = CardManifest(
        id="unknown",
        name="未命名卡片",
    )

    def check(self, config: dict) -> dict[str, Any]:
        """检查卡片当前是否可用。默认实现表示无需检查。"""
        return {
            "status": "healthy",
            "message": "卡片可用",
        }

    def is_configured(self, config: dict[str, Any]) -> bool:
        """根据 Manifest 判断是否完成配置；具体卡片可覆盖，无需核心识别卡片 ID。"""
        config = config if isinstance(config, dict) else {}
        fields = self.manifest.config_fields
        required = [field for field in fields if field.get("required")]
        if required:
            return all(
                _has_config_value(config.get(str(field.get("key") or "")))
                for field in required
            )
        return any(
            _has_config_value(config.get(str(field.get("key") or "")))
            for field in fields
            if str(field.get("key") or "") in config
        )

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """校验并返回规范化后的完整配置；具体卡片可覆盖业务规则。

        Manifest 可为文本字段声明 min_length、max_length、pattern 或 format
        （目前支持 url、email），供通用卡片配置接口执行一致的基础校验。
        """
        if not isinstance(config, dict):
            raise ConfigValidationError("卡片配置必须是 JSON 对象")

        normalized = deepcopy(config)
        for field in self.manifest.config_fields:
            key = str(field.get("key") or "")
            if key not in normalized or normalized[key] is None:
                continue

            field_type = field.get("type", "string")
            value = normalized[key]
            label = str(field.get("label") or key)

            if field_type in {"string", "password", "textarea"}:
                if not isinstance(value, str):
                    raise ConfigValidationError(f"配置字段「{label}」必须是文本")
                min_length = field.get("min_length")
                max_length = field.get("max_length")
                if min_length is not None and len(value) < min_length:
                    raise ConfigValidationError(
                        f"配置字段「{label}」长度不能少于 {min_length} 个字符"
                    )
                if max_length is not None and len(value) > max_length:
                    raise ConfigValidationError(
                        f"配置字段「{label}」长度不能超过 {max_length} 个字符"
                    )

                pattern = field.get("pattern")
                if pattern:
                    # Python's re engine has no match timeout. Keep generic card
                    # fields bounded even when a plugin omitted max_length.
                    if len(value) > 4096:
                        raise ConfigValidationError(
                            f"配置字段「{label}」用于正则校验的内容不能超过 4096 个字符"
                        )
                    if has_nested_unbounded_quantifier(str(pattern)):
                        raise ConfigValidationError(
                            f"配置字段「{label}」的正则规则包含不安全的嵌套重复"
                        )
                    try:
                        matches = re.fullmatch(str(pattern), value) is not None
                    except re.error as exc:
                        raise ConfigValidationError(
                            f"配置字段「{label}」的格式规则无效"
                        ) from exc
                    if not matches:
                        raise ConfigValidationError(
                            f"配置字段「{label}」格式不正确"
                        )

                value_format = field.get("format")
                if value_format == "url":
                    try:
                        parsed = urlparse(value)
                        hostname = parsed.hostname
                        # Accessing .port validates malformed and out-of-range ports.
                        port = parsed.port
                        valid_url = (
                            parsed.scheme in {"http", "https"}
                            and bool(hostname)
                            and not any(char.isspace() for char in hostname or "")
                            and (port is None or 1 <= port <= 65535)
                        )
                    except ValueError:
                        valid_url = False
                    if not valid_url:
                        raise ConfigValidationError(
                            f"配置字段「{label}」必须是有效的 HTTP 或 HTTPS 地址"
                        )
                elif value_format == "email":
                    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                        raise ConfigValidationError(
                            f"配置字段「{label}」必须是有效的邮箱地址"
                        )
                elif value_format:
                    raise ConfigValidationError(
                        f"配置字段「{label}」使用了不支持的格式规则: {value_format}"
                    )

        return normalized

    def validate_enabled_config(self, config: dict[str, Any]) -> None:
        """Validate cross-field requirements before a card is enabled.

        Cards with alternative credentials or other conditional requirements can
        override this hook. The default keeps existing cards' behavior unchanged.
        """
        return None

    def close(self) -> None:
        """应用退出时释放卡片资源。默认无需处理。"""
        return None

    def migrate_config(self, config: dict[str, Any], from_version: int) -> dict[str, Any]:
        """将旧版卡片配置迁移到当前 manifest.config_version。

        需要变更配置结构的卡片应覆盖此方法。默认拒绝隐式升级，避免在
        没有迁移规则时错误地标记旧配置为新版本。
        """
        if from_version != self.manifest.config_version:
            raise NotImplementedError(
                f"卡片 {self.card_id} 未实现从配置版本 {from_version} 的迁移"
            )
        return deepcopy(config)

    @property
    def card_id(self) -> str:
        return self.manifest.id

    @property
    def card_type(self) -> str:
        return self.manifest.type

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.manifest.capabilities


def _has_config_value(value: Any) -> bool:
    """配置状态判断的共享规则；空白文本、空对象和空数组视为未配置。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


class ResourceSourceCard(Card):
    """资源发现卡片接口。"""

    manifest = CardManifest(
        id="unknown.resource",
        name="未命名资源源",
        type="resource_source",
        capabilities=("resource.search", "resource.health_check"),
    )

    def search(self, movie: object, config: dict) -> list[dict[str, Any]]:
        raise NotImplementedError

    def check(self, config: dict) -> dict[str, Any]:
        raise NotImplementedError

    def public_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """返回可给管理界面展示的配置；默认递归隐藏疑似凭据字段。"""
        sensitive = ("cookie", "token", "secret", "password", "api_key", "authorization", "credential", "private_key")

        def sanitize(value):
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if any(marker in str(key).lower() for marker in sensitive):
                        result[f"has_{key}"] = bool(item)
                    else:
                        result[key] = sanitize(item)
                return result
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value

        return sanitize(config if isinstance(config, dict) else {})

    def search_channel(self, channel: object, title: str, config: dict) -> list[dict[str, Any]]:
        """在指定资源源频道中搜索资源；没有频道级能力的卡片返回空列表。"""
        return []


class PlaybackProviderCard(Card):
    """兼容旧版/第三方插件的播放能力接口；MovieSync 当前不注册在线播放页面或 API。"""

    manifest = CardManifest(
        id="unknown.playback",
        name="未命名播放提供方",
        type="playback_provider",
        capabilities=("playback.list_files", "playback.resolve"),
    )

    def list_files(self, parent_fid: str = "0") -> list[dict[str, Any]]:
        raise NotImplementedError

    def resolve_playback(self, fid: str) -> dict[str, Any]:
        raise NotImplementedError


class StorageTargetCard(Card):
    """存储/转存目标卡片接口。

    资源来源应优先输出通用 resource_id 和 resource_type；pwd_id 只作为旧数据
    兼容字段。目标卡片通过 storage.accepts.<resource_type> 声明可解析并原生
    接收的分享类型；storage.accepts.* 表示该卡片明确支持多种类型。
    resource_id 在不同 resource_type 之间不保证唯一，核心会按类型隔离缓存和任务指纹。
    """

    manifest = CardManifest(
        id="unknown.storage",
        name="未命名存储目标",
        type="storage_target",
        capabilities=(
            "storage.check",
            "storage.resolve_resource",
            "storage.list_files",
            "storage.create_folder",
            "storage.transfer",
        ),
    )

    def resolve_resource(self, resource: object) -> dict[str, Any]:
        """解析一个资源并返回可供核心业务使用的标准结果。"""
        raise NotImplementedError

    def list_files(self, resource: object) -> list[dict[str, Any]]:
        raise NotImplementedError

    def destination_options(self) -> list[dict[str, Any]]:
        return []

    def create_folder(self, name: str, parent_id: str = "0") -> str:
        raise NotImplementedError

    def transfer(
        self,
        resource: object,
        files: list[dict[str, Any]],
        target_id: str = "0",
    ) -> tuple[bool, str]:
        raise NotImplementedError


class FilenameProcessorCard(Card):
    """可扩展的文件名识别接口。

    识别卡片只负责把文件名转换为 (season, episode)，不负责决定是否转存。
    rule_priority 越大越先尝试；无法识别时返回 (None, None)，让后续规则继续。
    """

    manifest = CardManifest(
        id="unknown.filename-processor",
        name="未命名文件名识别器",
        type="filename_processor",
        capabilities=("filename.parse",),
    )
    rule_priority = 100

    def parse_episode(
        self, file_name: str, config: dict[str, Any]
    ) -> tuple[int | None, int | None]:
        """兼容接口：识别文件名中的季集信息；无法识别时返回 (None, None)。"""
        raise NotImplementedError

    def parse_episode_for_source(
        self,
        file_name: str,
        source_id: str,
        config: dict[str, Any],
    ) -> tuple[int | None, int | None]:
        """可选的来源感知接口；旧卡片无需改造即可继续工作。

        来源专属识别卡片可覆盖此方法，根据 source_id 选择规则；
        默认委托给原有 parse_episode()，保持现有插件兼容。
        """
        return self.parse_episode(file_name, config)


class MetadataProviderCard(Card):
    """影视/媒体元数据卡片接口。"""

    manifest = CardManifest(
        id="unknown.metadata",
        name="未命名元数据源",
        type="metadata_provider",
        capabilities=("metadata.list", "metadata.search", "metadata.detail"),
    )

    def list_movies(self, tag: str, sort_type: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def search(self, query: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_detail(self, item_id: str) -> dict[str, Any] | None:
        raise NotImplementedError


class NotificationCard(Card):
    """通知卡片接口。"""

    manifest = CardManifest(
        id="unknown.notification",
        name="未命名通知服务",
        type="notification",
        capabilities=("notification.send",),
    )

    def send(self, message: str, **kwargs: Any) -> bool:
        raise NotImplementedError


class CardRegistry:
    """进程内卡片注册表。

    Registry 只负责发现和管理已经加载的卡片实例，不负责持久化配置，
    也不负责下载/执行第三方代码。这样即使未来某张卡片不可用，
    Core 仍然可以继续运行其它卡片。
    """

    def __init__(self):
        self._cards: dict[str, Card] = {}
        self.lock = RLock()

    def _close_card(self, card: Card) -> None:
        """释放卡片资源；单张卡片的清理失败不应影响注册表操作。"""
        try:
            card.close()
        except Exception:
            logging.getLogger(__name__).exception(
                "关闭卡片 %s 失败",
                card.card_id,
            )

    def register(self, card: Card, *, replace: bool = False) -> Card:
        if not isinstance(card, Card):
            raise TypeError("只能注册 Card 实例")

        card_id = card.card_id
        with self.lock:
            previous = self._cards.get(card_id)
            if previous is not None and not replace:
                raise ValueError(f"卡片已注册: {card_id}")
            self._cards[card_id] = card

        # Do not call third-party cleanup code while holding the registry lock.
        if previous is not None and previous is not card:
            self._close_card(previous)
        return card

    def unregister(self, card_id: str) -> Card | None:
        with self.lock:
            card = self._cards.pop(str(card_id), None)
        if card is not None:
            self._close_card(card)
        return card

    def close_all(self) -> None:
        """移除并关闭全部已注册卡片，逐张隔离清理异常。"""
        with self.lock:
            cards = list(self._cards.values())
            self._cards.clear()

        for card in cards:
            self._close_card(card)

    def migrate_configs(self, config_store, logger=None) -> list[str]:
        """将已保存配置迁移到各卡片声明的版本；逐张隔离迁移失败。"""
        try:
            config = config_store.load()
        except Exception:
            if logger:
                logger.exception("读取卡片配置以执行版本迁移失败")
            return []

        saved_cards = config.get("cards") if isinstance(config, dict) else {}
        migrated: list[str] = []
        for card in self.list():
            saved = saved_cards.get(card.card_id) if isinstance(saved_cards, dict) else None
            if not isinstance(saved, dict):
                continue

            raw_version = saved.get("config_version", 1)
            if isinstance(raw_version, bool) or not isinstance(raw_version, int) or raw_version < 1:
                if logger:
                    logger.warning("卡片 %s 的配置版本无效，跳过迁移", card.card_id)
                continue

            target_version = card.manifest.config_version
            if raw_version >= target_version:
                if raw_version > target_version and logger:
                    logger.warning(
                        "卡片 %s 的已保存配置版本 %s 高于当前支持版本 %s，跳过迁移",
                        card.card_id,
                        raw_version,
                        target_version,
                    )
                continue

            old_config = saved.get("config")
            old_config = deepcopy(old_config) if isinstance(old_config, dict) else {}
            try:
                new_config = card.migrate_config(old_config, raw_version)
                if not isinstance(new_config, dict):
                    raise TypeError("migrate_config 必须返回字典")
                config_store.save_card_config(
                    card.card_id,
                    new_config,
                    enabled=bool(saved.get("enabled", True)),
                    config_version=target_version,
                    replace_config=True,
                )
                migrated.append(card.card_id)
                if logger:
                    logger.info(
                        "已迁移卡片 %s 配置版本 %s -> %s",
                        card.card_id,
                        raw_version,
                        target_version,
                    )
            except Exception:
                if logger:
                    logger.exception("迁移卡片 %s 配置失败，保留旧配置", card.card_id)
        return migrated

    def get(self, card_id: str) -> Card | None:
        with self.lock:
            return self._cards.get(str(card_id))

    def list(self) -> list[Card]:
        with self.lock:
            return list(self._cards.values())

    def manifests(self) -> list[dict[str, Any]]:
        with self.lock:
            return [card.manifest.to_dict() for card in self._cards.values()]

    def find_by_type(self, card_type: str) -> list[Card]:
        with self.lock:
            return [
                card
                for card in self._cards.values()
                if card.card_type == card_type
            ]

    def find_by_capability(self, capability: str) -> list[Card]:
        with self.lock:
            return [
                card
                for card in self._cards.values()
                if capability in card.capabilities
            ]


