"""资源来源抽象层。

用途：将资源发现入口与 MovieSync 核心业务解耦。具体资源源由独立卡片文件提供，
核心只负责注册、搜索调度、结果规范化与健康检查。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import entry_points
from typing import Any

from ..cards import Card, CardManifest, CardRegistry, FilenameProcessorCard, ResourceSourceCard
from .filename_rules import parse_tv_episode


class ResourceSource(ResourceSourceCard):
    """资源发现卡片的兼容基类。

    旧代码仍可继续通过 ResourceSource 使用，新的实现统一遵循 Card 接口。
    """

    source_id = "unknown"
    name = "未命名资源源"
    source_type = "custom"
    manifest = CardManifest(
        id="unknown.resource",
        name="未命名资源源",
        type="resource_source",
        capabilities=("resource.search", "resource.health_check"),
    )

    def search(
        self,
        movie: object,
        config: dict,
        *,
        source_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def check(self, config: dict) -> dict[str, Any]:
        raise NotImplementedError


class ResourceSourceManager:
    """管理已注册资源源，并提供统一搜索/健康检查入口。"""

    def __init__(
        self,
        config_store,
        logger,
        registry: CardRegistry | None = None,
        resource_cards: Iterable[ResourceSourceCard] | None = None,
    ):
        self.logger = logger
        self.config_store = config_store
        self.registry = registry or CardRegistry()

        # Core never creates a platform-specific source. All built-in and optional
        # sources must be registered by the card loader or explicit injection.
        for card in resource_cards or ():
            self.register(card)

    @property
    def sources(self) -> dict[str, ResourceSourceCard]:
        """返回注册表中的当前资源源快照，避免保留已注销卡片的旧引用。"""
        return {
            card.card_id: card
            for card in self.registry.find_by_type("resource_source")
            if isinstance(card, ResourceSourceCard)
        }

    def register(self, card: ResourceSourceCard, *, replace: bool = False) -> ResourceSourceCard:
        """注册资源来源卡片。第三方实现应由应用启动时的可信代码显式装配。"""
        if not isinstance(card, ResourceSourceCard):
            raise TypeError("资源来源卡片必须继承 ResourceSourceCard")
        if card.card_type != "resource_source":
            raise ValueError("卡片类型必须是 resource_source")
        return self.registry.register(card, replace=replace)

    @staticmethod
    def _source_name(source: ResourceSourceCard) -> str:
        return str(getattr(source, "name", "") or source.manifest.name or source.card_id)

    @staticmethod
    def _source_type(source: ResourceSourceCard) -> str:
        return str(getattr(source, "source_type", "") or source.card_type)


    def load_plugins(self, context: dict[str, Any] | None = None) -> list[str]:
        """加载已安装 Python 包声明的资源卡片插件。

        插件通过 moviesync.resource_sources entry point 暴露一个工厂函数，
        工厂接收 context 字典并返回 ResourceSourceCard。仅加载环境中已安装
        的可信 Python 包；不接受从 Web 上传或执行任意代码。
        """
        loaded: list[str] = []
        plugin_context = dict(context or {})
        try:
            candidates = entry_points(group="moviesync.resource_sources")
        except Exception as exc:
            self.logger.exception("读取资源卡片插件入口失败: %s", exc)
            return loaded

        for entry_point in candidates:
            card = None
            try:
                factory = entry_point.load()
                if not callable(factory):
                    raise TypeError("插件入口必须指向可调用的工厂函数")
                card = factory(plugin_context)
                self.register(card)
                loaded.append(card.card_id)
                self.logger.info("已加载资源卡片插件: %s", card.card_id)
            except Exception as exc:
                self._close_unregistered_plugin_card(card, entry_point.name)
                self.logger.exception("资源卡片插件 %s 加载失败: %s", entry_point.name, exc)
        return loaded


    def _close_unregistered_plugin_card(self, card: object, plugin_name: str) -> None:
        """工厂已创建但未成功注册的卡片也必须释放资源。"""
        if not isinstance(card, Card):
            return
        try:
            if self.registry.get(card.card_id) is card:
                return
            card.close()
        except Exception:
            self.logger.exception("清理未注册资源卡片插件 %s 失败", plugin_name)

    def get_cards(self) -> list[dict[str, Any]]:
        """仅返回已加载资源来源卡片的 Manifest。"""
        return [
            card.manifest.to_dict()
            for card in self.registry.find_by_type("resource_source")
            if isinstance(card, ResourceSourceCard)
        ]
    def _load_config(self) -> tuple[dict[str, Any], bool]:
        """读取资源卡片配置；失败时禁止按默认启用状态执行外部调用。"""
        try:
            config = self.config_store.load()
        except Exception:
            self.logger.exception("读取资源卡片配置失败")
            return {}, False
        if not isinstance(config, dict):
            self.logger.warning("资源卡片配置根结构无效")
            return {}, False
        return config, True

    def _card_config(self, config: dict, source_id: str) -> dict[str, Any]:
        cards = config.get("cards") if isinstance(config, dict) else {}
        card = cards.get(source_id) if isinstance(cards, dict) else {}
        if not isinstance(card, dict):
            return {}
        card_config = card.get("config")
        return dict(card_config) if isinstance(card_config, dict) else {}

    def _enabled_sources(self, config: dict) -> list[ResourceSourceCard]:
        enabled = []
        cards = config.get("cards") if isinstance(config, dict) else {}
        for source_id, source in self.sources.items():
            card = cards.get(source_id) if isinstance(cards, dict) else {}
            if not isinstance(card, dict) or card.get("enabled", True):
                enabled.append(source)
        return enabled

    def search(
        self,
        movie: object,
        config: dict,
        *,
        source_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search enabled sources, preserving the legacy list-only return shape."""
        items, _diagnostics = self.search_with_diagnostics(
            movie,
            config,
            source_ids=source_ids,
        )
        return items

    def search_with_diagnostics(
        self,
        movie: object,
        config: dict,
        *,
        source_ids: Iterable[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Search sources concurrently and return safe, per-source execution diagnostics.

        Exception details are kept in server logs only. The returned diagnostics are
        intended for UI display and must never contain provider tokens or raw errors.
        """
        enabled_sources = self._enabled_sources(config)
        if source_ids is not None:
            selected_ids = {
                str(source_id).strip()
                for source_id in source_ids
                if str(source_id).strip()
            }
            enabled_sources = [
                source for source in enabled_sources
                if source.card_id in selected_ids
            ]
        if not enabled_sources:
            return [], []

        def search_one(source: ResourceSourceCard) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            started_at = time.perf_counter()
            source_name = self._source_name(source)
            base = {"id": source.card_id, "name": source_name}
            try:
                raw_items = source.search(movie, self._card_config(config, source.card_id))
                if not isinstance(raw_items, list):
                    raise TypeError("资源卡片 search 必须返回列表")
                invalid_items = sum(not isinstance(item, dict) for item in raw_items)
                items = [
                    {
                        **item,
                        # Resource identity is owned by the manager, not plugin output.
                        "source_id": source.card_id,
                        "source_name": source_name,
                    }
                    for item in raw_items
                    if isinstance(item, dict)
                ]
                status = "empty" if not raw_items else ("partial" if invalid_items else "success")
                if invalid_items:
                    message = f"已忽略 {invalid_items} 条格式无效的资源记录"
                elif items:
                    message = f"返回 {len(items)} 条资源记录"
                else:
                    message = "本次没有发现资源"
                diagnostic = {
                    **base,
                    "status": status,
                    "result_count": len(items),
                    "invalid_items": invalid_items,
                    "duration_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
                    "message": message,
                }
                return items, diagnostic
            except Exception as exc:
                self.logger.exception(
                    "资源源 %s 搜索异常: %s",
                    source.card_id,
                    exc,
                )
                diagnostic = {
                    **base,
                    "status": "error",
                    "result_count": 0,
                    "invalid_items": 0,
                    "duration_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
                    "message": "资源来源请求失败，请检查卡片配置或查看服务日志",
                }
                return [], diagnostic

        # Resource cards are independent network sources. executor.map preserves
        # registration order so results and diagnostics remain deterministic.
        with ThreadPoolExecutor(
            max_workers=min(8, len(enabled_sources)),
            thread_name_prefix="moviesync-resource-search",
        ) as executor:
            source_results = list(executor.map(search_one, enabled_sources))

        items = [
            item
            for source_items, _diagnostic in source_results
            for item in source_items
        ]
        diagnostics = [diagnostic for _items, diagnostic in source_results]
        return items, diagnostics

    def parse_tv_episode(self, source_id: str, file_name: str) -> tuple[int | None, int | None]:
        """优先使用全局文件名识别规则，兼容旧资源卡片的专属规则。"""
        config, available = self._load_config()
        if not available:
            return parse_tv_episode(file_name)
        cards = config.get("cards", {})
        # 通过卡片类型发现识别能力，不绑定固定卡片 ID。高优先级规则先尝试；
        # 同优先级按卡片 ID 排序，避免依赖注册顺序造成结果不稳定。
        def processor_sort_key(item: object) -> tuple[int, str]:
            try:
                raw_priority = getattr(item, "rule_priority", 100)
                priority = 100 if isinstance(raw_priority, bool) else int(raw_priority)
            except Exception:
                # A malformed optional card must not disable episode recognition.
                priority = 100
            try:
                card_id = str(getattr(item, "card_id", ""))
            except Exception:
                card_id = ""
            return -priority, card_id

        processors = sorted(
            self.registry.find_by_type("filename_processor"),
            key=processor_sort_key,
        )
        for processor in processors:
            if not isinstance(processor, FilenameProcessorCard):
                continue
            saved = cards.get(processor.card_id) if isinstance(cards, dict) else {}
            if isinstance(saved, dict) and not saved.get("enabled", True):
                continue
            processor_config = self._card_config(config, processor.card_id)
            try:
                parsed = processor.parse_episode_for_source(
                    file_name, str(source_id or ""), processor_config
                )
            except Exception:
                self.logger.exception("文件名识别卡片 %s 执行失败", processor.card_id)
                continue
            if (
                not isinstance(parsed, (tuple, list))
                or len(parsed) != 2
                or any(
                    value is not None
                    and (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 1
                    )
                    for value in parsed
                )
            ):
                self.logger.warning(
                    "文件名识别卡片 %s 返回了无效的季集结果，已跳过",
                    processor.card_id,
                )
                continue
            if parsed[1] is not None:
                return parsed[0], parsed[1]

        # 旧版本将增强规则存放在资源来源卡片内。全局规则没有识别出集数时，
        # 继续尝试旧规则，避免升级后已有订阅的特殊命名突然失效。
        source = self.sources.get(str(source_id))
        card_config = self._card_config(config, source.card_id) if source else {}
        legacy_magic = card_config.get("magic_regex")
        if isinstance(legacy_magic, dict):
            parsed = parse_tv_episode(file_name, legacy_magic)
            if parsed[1] is not None:
                return parsed
        return parse_tv_episode(file_name)

    def search_channel(self, source_id: str, channel: object, title: str) -> list[dict[str, Any]]:
        source = self.sources.get(str(source_id))
        if not source:
            return []
        config, available = self._load_config()
        if not available:
            return []
        cards = config.get("cards", {})
        card = cards.get(source.card_id) if isinstance(cards, dict) else {}
        if isinstance(card, dict) and not card.get("enabled", True):
            return []
        try:
            items = source.search_channel(
                channel,
                str(title or "").strip(),
                self._card_config(config, source.card_id),
            )
            if not isinstance(items, list):
                raise TypeError("资源卡片 search_channel 必须返回列表")
            return [
                {
                    **item,
                    # 资源身份由管理器确定，避免插件结果伪造其他卡片的来源。
                    "source_id": source.card_id,
                    "source_name": self._source_name(source),
                }
                for item in items
                if isinstance(item, dict)
            ]
        except Exception as exc:
            self.logger.exception("资源源 %s 频道检索异常: %s", source.card_id, exc)
            return []


    def search_channel_updates(
        self,
        source_id: str,
        channel: object,
        title: str,
        after_post_id: object = None,
    ) -> dict[str, Any]:
        """Discover recent channel shares and return a safe persistent scan cursor."""
        source = self.sources.get(str(source_id))
        if not source:
            return {"items": [], "cursor": None, "complete": False}
        config, available = self._load_config()
        if not available:
            return {"items": [], "cursor": None, "complete": False}
        cards = config.get("cards", {})
        card = cards.get(source.card_id) if isinstance(cards, dict) else {}
        if isinstance(card, dict) and not card.get("enabled", True):
            return {"items": [], "cursor": None, "complete": False}
        search_updates = getattr(source, "search_channel_updates", None)
        if not callable(search_updates):
            return {
                "items": self.search_channel(source_id, channel, title),
                "cursor": None,
                "complete": False,
            }
        try:
            state = search_updates(
                channel,
                str(title or "").strip(),
                self._card_config(config, source.card_id),
                after_post_id=after_post_id,
            )
            if not isinstance(state, dict) or not isinstance(state.get("items"), list):
                raise TypeError("资源卡片 search_channel_updates 必须返回包含 items 的对象")
            return {
                "items": [
                    {
                        **item,
                        "source_id": source.card_id,
                        "source_name": self._source_name(source),
                    }
                    for item in state["items"]
                    if isinstance(item, dict)
                ],
                "cursor": state.get("cursor"),
                "complete": bool(state.get("complete")),
            }
        except Exception as exc:
            self.logger.exception("资源源 %s 增量频道检索异常: %s", source.card_id, exc)
            return {"items": [], "cursor": None, "complete": False}

    def search_channel_history(
        self,
        source_id: str,
        channel: object,
        title: str,
        before_post_id: object = None,
    ) -> dict[str, Any]:
        """Scan one bounded older-history batch through the configured source card."""
        source = self.sources.get(str(source_id))
        if not source:
            return {"items": [], "cursor": None, "complete": False}
        config, available = self._load_config()
        if not available:
            return {"items": [], "cursor": None, "complete": False}
        cards = config.get("cards", {})
        card = cards.get(source.card_id) if isinstance(cards, dict) else {}
        if isinstance(card, dict) and not card.get("enabled", True):
            return {"items": [], "cursor": None, "complete": False}
        search_history = getattr(source, "search_channel_history", None)
        if not callable(search_history):
            return {"items": [], "cursor": None, "complete": False}
        try:
            state = search_history(
                channel,
                str(title or "").strip(),
                self._card_config(config, source.card_id),
                before_post_id=before_post_id,
            )
            if not isinstance(state, dict) or not isinstance(state.get("items"), list):
                raise TypeError("资源卡片 search_channel_history 必须返回包含 items 的对象")
            return {
                "items": [
                    {
                        **item,
                        "source_id": source.card_id,
                        "source_name": self._source_name(source),
                    }
                    for item in state["items"]
                    if isinstance(item, dict)
                ],
                "cursor": state.get("cursor"),
                "complete": bool(state.get("complete")),
            }
        except Exception as exc:
            self.logger.exception("资源源 %s 历史回填异常: %s", source.card_id, exc)
            return {"items": [], "cursor": None, "complete": False}

    def check_all(self) -> list[dict[str, Any]]:
        config, available = self._load_config()
        if not available:
            # Do not call upstream providers when their enabled state cannot be
            # verified. Return a per-card health result instead of failing the
            # whole endpoint with HTTP 500.
            return [
                {
                    "id": source_id,
                    "name": self._source_name(source),
                    "type": self._source_type(source),
                    "status": "unavailable",
                    "message": "无法读取资源卡片配置，未执行连接检查，请检查配置文件后重试",
                    "total": 0,
                    "valid_count": 0,
                }
                for source_id, source in self.sources.items()
            ]
        cards = config.get("cards", {})
        results = []

        for source_id, source in self.sources.items():
            card = cards.get(source_id) if isinstance(cards, dict) else {}
            enabled = not isinstance(card, dict) or bool(card.get("enabled", True))
            if not enabled:
                result = {
                    "id": source_id,
                    "name": self._source_name(source),
                    "type": self._source_type(source),
                    "enabled": False,
                    "status": "disabled",
                    "message": "资源源已停用",
                    "total": 0,
                    "valid_count": 0,
                }
            else:
                try:
                    check = source.check(self._card_config(config, source_id))
                    result = {
                        **check,
                        "id": source_id,
                        "name": self._source_name(source),
                        "type": self._source_type(source),
                        "enabled": True,
                    }
                except Exception:
                    self.logger.exception(
                        "资源源 %s 健康检查异常",
                        source_id,
                    )
                    result = {
                        "id": source_id,
                        "name": self._source_name(source),
                        "type": self._source_type(source),
                        "enabled": True,
                        "status": "unavailable",
                        "message": "资源来源连接检查失败，请查看服务日志",
                        "total": 0,
                        "valid_count": 0,
                    }

            results.append(result)
            try:
                self._save_health(source_id, result)
            except Exception:
                # Persisting one card's health must not prevent checking the
                # remaining sources or returning the already-computed results.
                self.logger.exception("保存资源源 %s 健康状态失败", source_id)

        return results

    def _save_health(self, source_id: str, result: dict[str, Any]) -> None:
        """Persist health on the matching card, without assuming a built-in source ID."""
        saver = getattr(self.config_store, "save_card_config", None)
        if callable(saver):
            config = self.config_store.load()
            saved = self._card_config(config, source_id)
            health = dict(saved.get("health") or {})
            import time

            now = time.time()
            health["status"] = str(result.get("status") or "unknown")
            health["message"] = str(result.get("message") or "")[:200]
            health["last_checked_at"] = now
            if isinstance(result.get("channels"), list):
                health["channels"] = [
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "status": str(item.get("status") or "unknown"),
                        "message": str(item.get("message") or "")[:200],
                    }
                    for item in result["channels"]
                    if isinstance(item, dict)
                ]
            if result.get("status") == "healthy":
                health["last_success_at"] = now
            elif result.get("status") == "unavailable":
                try:
                    failures = int(health.get("failure_count", 0) or 0)
                except (TypeError, ValueError, OverflowError):
                    failures = 0
                health["failure_count"] = max(0, failures) + 1
            saver(source_id, {"health": health})
            return

        # Compatibility for test doubles and older ConfigStore-like adapters.
        updater = getattr(self.config_store, "update_resource_source_health", None)
        if callable(updater):
            updater(
                source_id,
                result.get("status", "unknown"),
                result.get("message", ""),
                result.get("channels"),
            )

    def get_status(self) -> list[dict[str, Any]]:
        """Return health status for every loaded resource card."""
        config = self.config_store.load()
        cards = config.get("cards") if isinstance(config, dict) else {}
        statuses = []
        for source_id, source in self.sources.items():
            saved = cards.get(source_id, {}) if isinstance(cards, dict) else {}
            saved = saved if isinstance(saved, dict) else {}
            card_config = saved.get("config", {})
            card_config = card_config if isinstance(card_config, dict) else {}
            health = card_config.get("health", {})
            health = dict(health) if isinstance(health, dict) else {}
            statuses.append({
                "id": source_id,
                "name": self._source_name(source),
                "type": self._source_type(source),
                "enabled": bool(saved.get("enabled", True)),
                "health": health,
            })
        return statuses
