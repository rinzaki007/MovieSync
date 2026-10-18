"""MovieSync 应用组装层。

用途：加载设置、初始化认证/配置/豆瓣/Telegram/夸克/订阅等组件，组装 Flask 应用并注册 Web 路由。
维护说明：这里主要负责依赖注入和应用生命周期，不建议在此直接堆业务逻辑。
"""
from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, session

from .auth import AuthStore
from .card_plugins import CardFilePluginManager
from .cards import CardRegistry
from .clients.compat import LazyTelegramClient
from .clients.http import HttpClient
from .config_store import ConfigStore
from .logging_setup import configure_logging, recent_logs
from .openlist_control import OpenListAdminClient
from .services.metadata import MetadataProviderManager
from .services.notifications import NotificationManager
from .services.resource_sources import ResourceSourceManager
from .services.search import SearchService
from .services.storage_targets import StorageTargetManager
from .services.subscriptions import SubscriptionManager
from .services.tasks import TaskManager
from .settings import PROJECT_ROOT, load_settings
from .web import LoginRateLimiter, register_web


def _load_optional_cards(logger, label: str, loader, context: dict) -> list[str]:
    """Load one card group without letting an extension failure stop the app."""
    try:
        loaded = loader(context)
        return loaded if isinstance(loaded, list) else []
    except Exception:
        logger.exception(
            "加载%s失败，MovieSync 会继续启动；相关卡片功能可能暂时不可用",
            label,
        )
        return []


def create_app(
    test_config: dict | None = None,
    *,
    start_scheduler: bool = True,
) -> Flask:
    settings = load_settings()

    if test_config and "MOVIESYNC_DATA_DIR" in test_config:
        settings = settings.__class__(
            data_dir=Path(
                test_config["MOVIESYNC_DATA_DIR"]
            ).resolve(),
            host=settings.host,
            port=settings.port,
            secret_key=test_config.get(
                "SECRET_KEY",
                settings.secret_key,
            ),
            cookie_secure=False,
            debug=True,
        )

        settings.data_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    logger = configure_logging(settings.log_dir)

    auth = AuthStore(
        settings.auth_file,
        PROJECT_ROOT / "auth.json",
    )

    config_store = ConfigStore(
        settings.config_file,
        PROJECT_ROOT,
    )
    openlist_admin = OpenListAdminClient(data_dir=settings.data_dir)

    # Compatibility shim for older third-party cards; creates the legacy client only on first use.
    telegram = LazyTelegramClient()
    card_registry = CardRegistry()
    file_card_plugins = CardFilePluginManager(
        settings.data_dir / "cards",
        card_registry,
        logger,
        PROJECT_ROOT / "card_templates",
        seed_missing=(
            os.getenv("MOVIESYNC_AUTO_INSTALL_BUNDLED_CARDS", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ),
    )
    card_context = {
        "telegram": telegram,
        "config_store": config_store,
        "logger": logger,
        "data_dir": settings.data_dir,
        "openlist_admin": openlist_admin,
    }
    _load_optional_cards(
        logger,
        "卡片文件",
        file_card_plugins.load_all,
        card_context,
    )
    resource_sources = ResourceSourceManager(
        config_store,
        logger,
        registry=card_registry,
    )
    _load_optional_cards(
        logger,
        "资源搜索",
        resource_sources.load_plugins,
        card_context,
    )
    metadata = MetadataProviderManager(card_registry, logger, config_store=config_store)
    _load_optional_cards(
        logger,
        "影视资料",
        metadata.load_plugins,
        {"config_store": config_store, "logger": logger},
    )
    storage_targets = StorageTargetManager(
        card_registry,
        config_store,
        logger,
    )
    _load_optional_cards(
        logger,
        "转存位置",
        storage_targets.load_plugins,
        {"config_store": config_store, "logger": logger},
    )
    notifications = NotificationManager(card_registry, logger, config_store=config_store)
    _load_optional_cards(
        logger,
        "消息通知",
        notifications.load_plugins,
        {"config_store": config_store, "logger": logger},
    )
    card_registry.migrate_configs(config_store, logger)

    http = HttpClient()

    tasks = TaskManager(
        settings.data_dir / "tasks.json",
        logger,
    )

    subscriptions = SubscriptionManager(
        settings.subscriptions_file,
        storage_targets,
        resource_sources,
        logger,
    )

    search_service = SearchService(
        resource_sources,
        storage_targets,
        logger,
    )

    shutdown_state = {"closed": False}

    def shutdown() -> None:
        """停止后台调度器并释放卡片资源；重复调用是安全的。"""
        if shutdown_state["closed"]:
            return
        shutdown_state["closed"] = True

        try:
            stop_subscriptions = getattr(subscriptions, "shutdown", None)
            if callable(stop_subscriptions):
                stop_subscriptions()
            else:
                subscriptions.stop_scheduler()
        except Exception:
            logger.exception("停止自动追剧调度器失败")

        try:
            tasks.shutdown()
        except Exception:
            logger.exception("关闭任务执行器失败")

        try:
            card_registry.close_all()
        except Exception:
            logger.exception("关闭卡片注册表失败")

        try:
            http.close()
        except Exception:
            logger.exception("关闭应用 HTTP 客户端失败")

        try:
            telegram.close()
        except Exception:
            logger.exception("关闭旧版 Telegram 兼容客户端失败")

        try:
            openlist_admin.close()
        except Exception:
            logger.exception("关闭 OpenList 管理 API 客户端失败")

    services = {
        "logger": logger,
        "logs": recent_logs,
        "auth": auth,
        "config": config_store,
        "metadata": metadata,
        "notifications": notifications,
        "telegram": telegram,
        "resource_sources": resource_sources,
        "card_registry": card_registry,
        "file_card_plugins": file_card_plugins,
        "openlist_admin": openlist_admin,
        "storage_targets": storage_targets,
        "http": http,
        "subscriptions": subscriptions,
        "tasks": tasks,
        "search": search_service,
        "csrf": lambda: secrets.token_urlsafe(32),
        "login_limiter": LoginRateLimiter(),
        "scheduler_enabled": bool(start_scheduler),
        "shutdown": shutdown,
    }

    app = Flask(
        __name__,
        template_folder=str(
            PROJECT_ROOT / "templates"
        ),
        static_folder=str(
            PROJECT_ROOT / "static"
        ),
    )

    app.secret_key = settings.secret_key

    app.config.update(
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,

        SESSION_COOKIE_NAME="moviesync_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.cookie_secure,

        # 登录有效期 24 小时。
        # 有请求时自动续期。
        PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
        SESSION_REFRESH_EACH_REQUEST=True,

        JSON_SORT_KEYS=False,
    )

    app.extensions["moviesync"] = services
    app.extensions["moviesync_settings"] = settings

    @app.context_processor
    def inject_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

        return {
            "csrf_token": session["csrf_token"]
        }

    register_web(app, services)

    if start_scheduler and not app.testing:
        subscriptions.start_scheduler()

    return app
