"""统一影视元数据提供方管理。

元数据提供方是独立卡片。单张卡片异常或返回值不符合接口约定时，
不应让首页、搜索或其它提供方一起失败。
"""
from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any, TypeVar

from ..cards import Card, CardRegistry, MetadataProviderCard

T = TypeVar("T")


class MetadataProviderManager:
    def __init__(self, registry: CardRegistry, logger, config_store=None):
        self.registry = registry
        self.logger = logger
        self.config_store = config_store

    def _close_unregistered_plugin_card(self, card: object, plugin_name: str) -> None:
        """工厂已创建但未成功注册的卡片也必须释放资源。"""
        if not isinstance(card, Card):
            return
        try:
            if self.registry.get(card.card_id) is card:
                return
            card.close()
        except Exception:
            self.logger.exception("清理未注册元数据卡片插件 %s 失败", plugin_name)

    def load_plugins(self, context: dict[str, Any] | None = None) -> list[str]:
        """Load installed metadata provider cards from trusted Python packages.

        Plugins expose a factory through the moviesync.metadata_providers entry
        point group. A broken plugin is logged and skipped without blocking the
        built-in Douban provider or other installed providers.
        """
        loaded: list[str] = []
        plugin_context = dict(context or {})
        try:
            candidates = entry_points(group="moviesync.metadata_providers")
        except Exception:
            self.logger.exception("读取元数据卡片插件入口失败")
            return loaded

        for entry_point in candidates:
            card = None
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise TypeError("插件入口必须指向可调用的工厂函数")
                card = factory(plugin_context)
                if not isinstance(card, MetadataProviderCard):
                    raise TypeError("元数据卡片插件工厂必须返回 MetadataProviderCard 实例")
                if card.card_type != "metadata_provider":
                    raise ValueError("卡片类型必须是 metadata_provider")
                registered = self.registry.register(card)
                loaded.append(registered.card_id)
                self.logger.info("已加载元数据卡片插件: %s", registered.card_id)
            except Exception:
                self._close_unregistered_plugin_card(card, entry_point.name)
                self.logger.exception("元数据卡片插件 %s 加载失败", entry_point.name)
        return loaded

    def _card_config(self) -> dict[str, Any]:
        """读取卡片配置；配置文件暂时不可读时，不让元数据功能拖垮页面。"""
        if self.config_store is None:
            return {}
        try:
            config = self.config_store.load()
        except Exception:
            self.logger.exception("读取元数据卡片配置失败，将暂按卡片默认启用状态处理")
            return {}
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _is_enabled(card: MetadataProviderCard, config: dict[str, Any]) -> bool:
        cards = config.get("cards", {})
        saved = cards.get(card.card_id, {}) if isinstance(cards, dict) else {}
        return not isinstance(saved, dict) or bool(saved.get("enabled", True))

    def _providers(
        self,
        provider_id: str | None = None,
        capability: str | None = None,
    ) -> list[MetadataProviderCard]:
        if provider_id:
            card = self.registry.get(provider_id)
            candidates = [card] if isinstance(card, MetadataProviderCard) else []
        else:
            candidates = [
                card
                for card in self.registry.find_by_type("metadata_provider")
                if isinstance(card, MetadataProviderCard)
            ]

        config = self._card_config()
        result = []
        for card in candidates:
            if not self._is_enabled(card, config):
                continue
            if capability and capability not in card.capabilities:
                continue
            result.append(card)
        return result

    def _call(
        self,
        operation: str,
        capability: str,
        callback: Callable[[MetadataProviderCard], T],
        *,
        provider_id: str | None = None,
        empty: T,
        valid: Callable[[object], bool],
    ) -> T:
        """Call providers independently, falling through on failures/empty results.

        An explicitly selected provider is never silently replaced by another
        provider, so the caller's choice remains authoritative.
        """
        providers = self._providers(provider_id, capability)
        if not providers:
            return empty

        for card in providers:
            try:
                result = callback(card)
                if not valid(result):
                    raise TypeError(f"{operation} 返回值不符合卡片接口约定")
                if result:
                    return result
            except Exception:
                self.logger.exception(
                    "元数据卡片 %s 的 %s 调用失败",
                    card.card_id,
                    operation,
                )
                if provider_id:
                    return empty

        return empty

    @staticmethod
    def _valid_items(value: object) -> bool:
        return isinstance(value, list) and all(isinstance(item, dict) for item in value)

    def list_movies(
        self,
        tag: str = "电影",
        sort_type: str = "U",
        provider_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._call(
            "list_movies",
            "metadata.list",
            lambda card: card.list_movies(tag, sort_type),
            provider_id=provider_id,
            empty=[],
            valid=self._valid_items,
        )

    def search(
        self,
        query: str,
        provider_id: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        def search_card(card: MetadataProviderCard):
            category_search = getattr(card, "search_by_category", None)
            if category and callable(category_search):
                return category_search(query, category)
            return card.search(query)

        return self._call(
            "search",
            "metadata.search",
            search_card,
            provider_id=provider_id,
            empty=[],
            valid=self._valid_items,
        )

    def get_detail(self, item_id: str, provider_id: str | None = None) -> dict[str, Any] | None:
        return self._call(
            "get_detail",
            "metadata.detail",
            lambda card: card.get_detail(item_id),
            provider_id=provider_id,
            empty=None,
            valid=lambda value: value is None or isinstance(value, dict),
        )
