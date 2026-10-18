"""统一存储目标卡片管理。

核心业务只通过 StorageTargetManager 使用存储能力，不直接依赖 Quark、115、
百度网盘等具体平台。具体平台差异由 StorageTargetCard 自己处理。
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any
from urllib.parse import urlsplit

from ..cards import Card, CardRegistry, StorageTargetCard


class StorageTargetManager:
    """管理已加载的存储目标卡片。"""

    def __init__(self, registry: CardRegistry, config_store, logger):
        self.registry = registry
        self.config_store = config_store
        self.logger = logger

    def _close_unregistered_plugin_card(self, card: object, plugin_name: str) -> None:
        """工厂已创建但未成功注册的卡片也必须释放资源。"""
        if not isinstance(card, Card):
            return
        try:
            if self.registry.get(card.card_id) is card:
                return
            card.close()
        except Exception:
            self.logger.exception("清理未注册存储卡片插件 %s 失败", plugin_name)

    def load_plugins(self, context: dict[str, Any] | None = None) -> list[str]:
        """加载已安装可信 Python 包声明的存储卡片插件。

        插件通过 moviesync.storage_targets entry point 暴露工厂函数。
        工厂接收 context 字典并返回 StorageTargetCard 实例。这里仅发现
        当前 Python 环境中已安装的包；不接受后台上传或执行任意代码。
        """
        loaded: list[str] = []
        plugin_context = dict(context or {})
        try:
            candidates = entry_points(group="moviesync.storage_targets")
        except Exception:
            self.logger.exception("读取存储卡片插件入口失败")
            return loaded

        for entry_point in candidates:
            card = None
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise TypeError("插件入口必须指向可调用的工厂函数")
                card = factory(plugin_context)
                if not isinstance(card, StorageTargetCard):
                    raise TypeError("存储卡片插件工厂必须返回 StorageTargetCard 实例")
                if card.card_type != "storage_target":
                    raise ValueError("卡片类型必须是 storage_target")
                if card.card_id != str(entry_point.name or "").strip():
                    raise ValueError(
                        f"存储卡片入口名称「{entry_point.name}」必须与 manifest ID「{card.card_id}」一致"
                    )
                registered = self.registry.register(card)
                loaded.append(registered.card_id)
                self.logger.info("已加载存储卡片插件: %s", registered.card_id)
            except Exception:
                self._close_unregistered_plugin_card(card, entry_point.name)
                self.logger.exception("存储卡片插件 %s 加载失败", entry_point.name)
        return loaded

    def _load_card_config(self) -> tuple[dict[str, Any], bool]:
        """读取卡片配置；返回值同时标记配置是否可信可用。"""
        try:
            config = self.config_store.load()
        except Exception:
            self.logger.exception("读取存储目标卡片配置失败")
            return {}, False
        return (config if isinstance(config, dict) else {}), True

    def _enabled_cards(self) -> list[StorageTargetCard]:
        config, available = self._load_card_config()
        if not available:
            # Never guess a destination when we cannot verify its enabled state.
            return []
        cards = []
        card_configs = config.get("cards") if isinstance(config, dict) else {}
        for card in self.registry.find_by_type("storage_target"):
            if not isinstance(card, StorageTargetCard):
                continue
            saved = card_configs.get(card.card_id) if isinstance(card_configs, dict) else {}
            if not isinstance(saved, dict) or saved.get("enabled", True):
                cards.append(card)
        return cards

    def _default_target_id(self) -> tuple[str, bool]:
        """读取默认目标；配置读取失败时明确返回不可用，不尝试其他目标。"""
        getter = getattr(self.config_store, "get_default_storage_target_id", None)
        if not callable(getter):
            return "", True
        try:
            return str(getter() or "").strip(), True
        except Exception:
            if self.logger is not None:
                self.logger.exception("读取默认存储目标失败")
            return "", False

    def _unavailable_message(self, target_id: str | None = None) -> str:
        is_default_target = not target_id
        if is_default_target:
            target_id, default_available = self._default_target_id()
            if not default_available:
                return "无法读取默认存储目标配置，请检查配置文件和服务日志后重试"

        if not target_id:
            _, available = self._load_card_config()
            if not available:
                return "无法读取存储目标配置，请检查配置文件和服务日志后重试"

        if target_id:
            card = self.registry.get(str(target_id))
            if isinstance(card, StorageTargetCard):
                config, available = self._load_card_config()
                if not available:
                    return "无法读取存储目标配置，请检查配置文件和服务日志后重试"
                card_configs = config.get("cards") if isinstance(config, dict) else {}
                saved = card_configs.get(card.card_id) if isinstance(card_configs, dict) else {}
                if isinstance(saved, dict) and not saved.get("enabled", True):
                    return f"存储目标卡片「{card.manifest.name}」已停用，请重新启用后重试"
            else:
                if is_default_target:
                    return (
                        f"默认存储目标卡片「{target_id}」不存在或未加载，"
                        "请检查插件安装状态或重新选择默认存储目标"
                    )
                return (
                    f"存储目标卡片「{target_id}」不存在或未加载，"
                    "请检查插件安装状态或重新选择存储目标"
                )
        return "未找到可用的存储目标卡片"

    def get(self, target_id: str | None = None) -> StorageTargetCard | None:
        if target_id:
            card = self.registry.get(str(target_id))
            if not isinstance(card, StorageTargetCard):
                return None
            config, available = self._load_card_config()
            if not available:
                return None
            card_configs = config.get("cards") if isinstance(config, dict) else {}
            saved = card_configs.get(card.card_id) if isinstance(card_configs, dict) else {}
            if isinstance(saved, dict) and not saved.get("enabled", True):
                return None
            return card
        default_id, default_available = self._default_target_id()
        if not default_available:
            return None
        if default_id:
            card = self.registry.get(default_id)
            if not isinstance(card, StorageTargetCard):
                # Do not silently send files to another destination when the
                # configured default plugin is missing or has been removed.
                return None
            config, available = self._load_card_config()
            if not available:
                return None
            card_configs = config.get("cards") if isinstance(config, dict) else {}
            saved = card_configs.get(card.card_id) if isinstance(card_configs, dict) else {}
            if isinstance(saved, dict) and not saved.get("enabled", True):
                # An explicitly selected default that was disabled should fail
                # safely rather than silently falling back to another cloud.
                return None
            return card
        cards = self._enabled_cards()
        return cards[0] if cards else None

    def select_target_id(self, resource: object) -> str:
        """根据资源类型与存储卡能力选择目标；显式目标永不被替换。"""
        resource_dict = resource if isinstance(resource, dict) else {}
        explicit_id = str(
            resource_dict.get("storage_target_id") or resource_dict.get("target_id") or ""
        ).strip()
        if explicit_id:
            return explicit_id

        resource_type = str(resource_dict.get("resource_type") or "").strip()
        if resource_type:
            required_capability = f"storage.accepts.{resource_type}"
            compatible = [
                card
                for card in self._enabled_cards()
                if required_capability in card.capabilities
                or "storage.accepts.*" in card.capabilities
            ]
            if not compatible:
                return ""
            default_id, available = self._default_target_id()
            if not available:
                # Do not guess a destination when the configured default cannot be read.
                return ""
            if default_id:
                if any(card.card_id == default_id for card in compatible):
                    return default_id
            # A single compatible target is unambiguous. If there are multiple,
            # require an explicit/default compatible choice rather than guessing.
            return compatible[0].card_id if len(compatible) == 1 else ""

        default = self.get()
        return default.card_id if default else ""

    def list_targets(self) -> list[dict[str, Any]]:
        config, available = self._load_card_config()
        card_configs = config.get("cards") if isinstance(config, dict) else {}
        results = []
        for card in self.registry.find_by_type("storage_target"):
            saved = card_configs.get(card.card_id) if isinstance(card_configs, dict) else {}
            enabled = available and (
                not isinstance(saved, dict) or bool(saved.get("enabled", True))
            )
            web_url = str(getattr(card, "web_url", "") or "").strip()
            try:
                parsed_web_url = urlsplit(web_url)
                if (
                    parsed_web_url.scheme not in {"http", "https"}
                    or not parsed_web_url.hostname
                    or parsed_web_url.username
                    or parsed_web_url.password
                ):
                    web_url = ""
            except ValueError:
                web_url = ""
            results.append({
                **card.manifest.to_dict(),
                "enabled": enabled,
                **({"web_url": web_url} if web_url else {}),
                **({"config_error": "无法读取存储目标配置"} if not available else {}),
            })
        return results

    def _log_plugin_failure(self, card: StorageTargetCard, operation: str) -> None:
        if self.logger is not None:
            self.logger.exception(
                "存储目标卡片 %s 的 %s 接口返回异常或执行失败",
                card.card_id,
                operation,
            )

    def destination_options(self, target_id: str | None = None) -> list[dict[str, Any]]:
        card = self.get(target_id)
        if not card:
            return []
        try:
            result = card.destination_options()
            if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise TypeError("destination_options 必须返回字典列表")
            return result
        except Exception:
            self._log_plugin_failure(card, "destination_options")
            return []

    def resolve_resource(
        self,
        resource: object,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        resource_dict = dict(resource) if isinstance(resource, dict) else {}
        selected_id = (
            str(target_id).strip()
            if target_id
            else self.select_target_id(resource_dict)
        )
        if not selected_id and resource_dict.get("resource_type"):
            return {
                "target_id": "",
                "files": [],
                "token": None,
                "error": (
                    "没有已启用的存储卡片支持此资源类型，"
                    "请安装或启用兼容的存储卡片后重试"
                ),
            }
        card = self.get(str(selected_id) if selected_id else None)
        if not card:
            return {
                "target_id": str(selected_id or ""),
                "files": [],
                "token": None,
                "error": self._unavailable_message(str(selected_id) if selected_id else None),
            }
        resource_type = str(resource_dict.get("resource_type") or "").strip()
        if resource_type:
            required_capability = f"storage.accepts.{resource_type}"
            if (
                required_capability not in card.capabilities
                and "storage.accepts.*" not in card.capabilities
            ):
                return {
                    "target_id": card.card_id,
                    "files": [],
                    "token": None,
                    "error": (
                        f"存储目标卡片「{card.manifest.name}」不支持此资源类型，"
                        "请重新选择兼容的存储目标"
                    ),
                }
        try:
            result = card.resolve_resource(resource_dict)
            if not isinstance(result, dict):
                raise TypeError("resolve_resource 必须返回字典")
            files = result.get("files", [])
            if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
                raise TypeError("resolve_resource 的 files 必须是字典列表")
            normalized = dict(result)
            normalized["files"] = files
            normalized["target_id"] = card.card_id
            if normalized.get("token") is not None and not isinstance(normalized["token"], str):
                raise TypeError("resolve_resource 的 token 必须是文本或 null")
            if normalized.get("error") is not None and not isinstance(normalized["error"], str):
                raise TypeError("resolve_resource 的 error 必须是文本或 null")
            return normalized
        except Exception:
            self._log_plugin_failure(card, "resolve_resource")
            return {
                "target_id": card.card_id,
                "files": [],
                "token": None,
                "error": "存储目标资源解析失败，请检查插件实现或查看服务日志",
            }

    def list_files(
        self,
        resource: object,
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        card = self.get(target_id)
        if not card:
            return []
        try:
            result = card.list_files(resource)
            if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise TypeError("list_files 必须返回字典列表")
            return result
        except Exception:
            self._log_plugin_failure(card, "list_files")
            return []

    def create_folder(
        self,
        name: str,
        parent_id: str = "0",
        target_id: str | None = None,
    ) -> str:
        card = self.get(target_id)
        if not card:
            raise RuntimeError(self._unavailable_message(target_id))
        try:
            result = card.create_folder(name, parent_id)
            if not isinstance(result, str) or not result.strip():
                raise TypeError("create_folder 必须返回非空目录 ID")
            return result
        except Exception as exc:
            self._log_plugin_failure(card, "create_folder")
            raise RuntimeError("存储目标创建目录失败，请检查插件实现或查看服务日志") from exc

    def transfer(
        self,
        resource: object,
        files: list[dict[str, Any]],
        target_id: str = "0",
        storage_target_id: str | None = None,
        token: str | None = None,
    ) -> tuple[bool, str]:
        card = self.get(storage_target_id)
        if not card:
            return False, self._unavailable_message(storage_target_id)
        payload = dict(resource) if isinstance(resource, dict) else {}
        resource_type = str(payload.get("resource_type") or "").strip().lower()
        if resource_type:
            required_capability = f"storage.accepts.{resource_type}"
            if (
                required_capability not in card.capabilities
                and "storage.accepts.*" not in card.capabilities
            ):
                return False, (
                    f"存储目标卡片「{card.manifest.name}」不支持此资源类型，"
                    "请重新选择兼容的存储目标"
                )
        if token:
            payload["stoken"] = token
        try:
            result = card.transfer(payload, files, target_id)
            if (
                not isinstance(result, tuple)
                or len(result) != 2
                or not isinstance(result[0], bool)
                or not isinstance(result[1], str)
            ):
                raise TypeError("transfer 必须返回 (bool, str)")
            return result
        except Exception:
            self._log_plugin_failure(card, "transfer")
            return False, "存储目标转存失败，转存结果不确定，请检查目标网盘后再决定是否重试"
