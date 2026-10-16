"""统一通知通道管理。

核心仅向通知卡片发送标准事件，不依赖 Telegram、邮件或其他具体渠道。
每张卡片的失败都独立隔离，且配置与启用状态由通用卡片配置管理。
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

from ..cards import Card, CardRegistry, NotificationCard


class NotificationManager:
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
            self.logger.exception("清理未注册通知卡片插件 %s 失败", plugin_name)

    def load_plugins(self, context: dict[str, Any] | None = None) -> list[str]:
        """加载已安装可信 Python 包中的通知卡片工厂。"""
        loaded: list[str] = []
        plugin_context = dict(context or {})
        try:
            candidates = entry_points(group="moviesync.notification_channels")
        except Exception:
            self.logger.exception("读取通知卡片插件入口失败")
            return loaded

        for entry_point in candidates:
            card = None
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise TypeError("插件入口必须指向可调用的工厂函数")
                card = factory(plugin_context)
                if not isinstance(card, NotificationCard):
                    raise TypeError("通知插件工厂必须返回 NotificationCard 实例")
                if card.card_type != "notification":
                    raise ValueError("卡片类型必须是 notification")
                registered = self.registry.register(card)
                loaded.append(registered.card_id)
                self.logger.info("已加载通知卡片插件: %s", registered.card_id)
            except Exception:
                self._close_unregistered_plugin_card(card, entry_point.name)
                self.logger.exception("通知卡片插件 %s 加载失败", entry_point.name)
        return loaded

    def _config(self) -> tuple[dict[str, Any], bool]:
        # No config store is supported for legacy callers and keeps the historical
        # default-enabled behavior. Once a store exists, read/shape errors must
        # fail closed so disabled channels are never contacted on a broken config.
        if self.config_store is None:
            return {}, True
        try:
            config = self.config_store.load()
        except Exception:
            self.logger.exception("读取通知卡片配置失败，已跳过本次通知投递")
            return {}, False
        if not isinstance(config, dict):
            self.logger.error("通知卡片配置格式无效，已跳过本次通知投递")
            return {}, False
        return config, True

    @staticmethod
    def _card_config(config: dict[str, Any], card_id: str) -> dict[str, Any]:
        cards = config.get("cards")
        saved = cards.get(card_id) if isinstance(cards, dict) else None
        if not isinstance(saved, dict):
            return {}
        card_config = saved.get("config")
        return dict(card_config) if isinstance(card_config, dict) else {}

    def send(self, event: str, payload: dict[str, Any]) -> dict[str, bool]:
        """向所有已启用的通知卡片发送事件，返回各卡片的投递结果。"""
        if not isinstance(event, str) or not event.strip():
            raise ValueError("通知事件名称不能为空")
        if not isinstance(payload, dict):
            raise TypeError("通知负载必须是字典")

        config, available = self._config()
        cards = [
            card
            for card in self.registry.find_by_type("notification")
            if isinstance(card, NotificationCard)
        ]
        if not available:
            return {card.card_id: False for card in cards}

        saved_cards = config.get("cards")
        results: dict[str, bool] = {}
        for card in cards:
            saved = saved_cards.get(card.card_id) if isinstance(saved_cards, dict) else None
            if isinstance(saved, dict) and not saved.get("enabled", True):
                continue
            try:
                result = card.send(
                    event.strip(),
                    payload=dict(payload),
                    config=self._card_config(config, card.card_id),
                )
                if not isinstance(result, bool):
                    raise TypeError("通知卡片 send 必须返回 bool")
                results[card.card_id] = result
            except Exception:
                self.logger.exception("通知卡片 %s 投递事件 %s 失败", card.card_id, event)
                results[card.card_id] = False
        return results
