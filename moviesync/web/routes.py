"""MovieSync Flask 页面与 API 路由。

用途：提供首页、登录/初始化、后台配置、豆瓣数据、资源检索/转存、自动追剧和图片代理等 HTTP 接口。
维护说明：修改数据的 API 使用 CSRF 校验；FID 会在路由层先校验，再交给业务服务继续处理。
"""
from __future__ import annotations

import math
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)

from ..services.episode_coverage import build_episode_coverage
from ..services.filename_rules import parse_media_filename, parse_tv_episode
from ..services.media_classification import classify_metadata_item, normalize_media_category

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__, url_prefix="/api")


def _services():
    return current_app.extensions["moviesync"]


def _json_error(message: str, status: int = 400):
    return jsonify(
        {
            "success": False,
            "message": message,
        }
    ), status

def _normalize_fid(
    value: object,
    default: str = "0",
) -> str:
    fid = str(
        value or default
    ).strip()

    if not fid:
        fid = default

    if len(fid) > 128:
        raise ValueError(
            "目标目录编号不能超过 128 个字符"
        )

    if not all(
        char.isalnum() or char in "_-"
        for char in fid
    ):
        raise ValueError(
            "目标目录编号格式不正确"
        )

    return fid


def _sanitize_persisted_numbers(value):
    """Keep non-finite JSON numbers from breaking task-center responses."""
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    if isinstance(value, dict):
        return {key: _sanitize_persisted_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_persisted_numbers(item) for item in value]
    return value


def _task_sort_timestamp(item: dict) -> float:
    """Sort task history safely when persisted timestamps are malformed."""
    value = item.get("updated_at") or item.get("created_at") or 0
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return timestamp if math.isfinite(timestamp) else 0.0


def _subscription_task_state(sub: dict) -> dict:
    status = str(sub.get("status") or "").strip()
    if not status:
        status = "error" if sub.get("last_error") else (
            "pending" if sub.get("pending_save_keys") else "waiting"
        )

    phase = str(sub.get("phase") or "").strip()
    if not phase:
        phase = "pending" if sub.get("pending_save_keys") else "waiting"

    phase_label = str(sub.get("phase_label") or "").strip()
    if not phase_label:
        phase_label = (
            "等待存储目标确认"
            if sub.get("pending_save_keys")
            else "等待下次检查"
        )

    return {
        "status": status,
        "phase": phase,
        "phase_label": phase_label,
        "progress": 100 if status == "success" else 0,
    }


def require_csrf(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            expected = session.get(
                "csrf_token"
            )

            supplied = (
                request.headers.get(
                    "X-CSRF-Token"
                )
                or request.form.get(
                    "csrf_token"
                )
            )

            if (
                not expected
                or not secrets.compare_digest(
                    str(expected),
                    str(supplied or ""),
                )
            ):
                return _json_error(
                    "CSRF 校验失败",
                    403,
                )

        return view(*args, **kwargs)

    return wrapped


@pages.get("/")
def index():
    return render_template(
        "index.html"
    )


@pages.get("/tasks")
def tasks():
    return render_template(
        "tasks.html"
    )

@pages.get("/resource-select")
def resource_select():
    return render_template(
        "resource_select.html"
    )


@pages.get("/favicon.ico")
def favicon():
    return Response(status=204)


@pages.get("/admin")
def admin():
    return render_template(
        "admin.html"
    )

@pages.get("/about")
def about():
    return render_template("about.html")


@pages.get("/setup")
def setup_page():
    return render_template(
        "login.html",
        is_setup=True,
    )


@pages.get("/login")
def login_page():
    return render_template(
        "login.html",
        is_setup=False,
    )


@pages.post("/logout")
@require_csrf
def logout():
    session.clear()
    return redirect("/login")


@pages.get("/healthz")
def healthz():
    services = _services()
    scheduler = services.get("subscriptions")

    scheduler_alive = bool(
        scheduler
        and scheduler.worker
        and scheduler.worker.is_alive()
    )

    scheduler_expected = bool(
        services.get("scheduler_enabled")
    )

    if scheduler_expected and not scheduler_alive:
        return jsonify(
            {
                "status": "degraded",
                "scheduler": "stopped",
            }
        ), 503

    return jsonify(
        {
            "status": "ok",
            "scheduler": (
                "running"
                if scheduler_alive
                else "disabled"
            ),
        }
    )


@pages.get("/openlist")
def openlist():
    url = str(_services()["config"].load().get("openlist_url") or "").strip()
    return redirect(url or "/")


@api.post("/setup")
def setup():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    username = str(
        data.get("username", "")
    )

    password = str(
        data.get("password", "")
    )

    try:
        _services()["auth"].setup(
            username,
            password,
        )

    except ValueError as exc:
        return _json_error(
            str(exc)
        )

    session.clear()
    session.permanent = True
    session["logged_in"] = True
    session["username"] = username.strip()
    session["csrf_token"] = (
        _services()["csrf"]()
    )

    _services()["logger"].info(
        "管理员完成系统初始化"
    )

    return jsonify(
        {
            "success": True,
            "message": "管理员账号创建成功！",
        }
    )


@api.post("/login")
def login():
    services = _services()

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    username = str(
        data.get("username", "")
    ).strip()

    password = str(
        data.get("password", "")
    )

    remote_addr = (
        request.remote_addr
        or "unknown"
    )

    allowed, retry_after = (
        services["login_limiter"].allow(
            remote_addr
        )
    )

    if not allowed:
        return jsonify(
            {
                "success": False,
                "message": (
                    f"尝试次数过多，请 "
                    f"{retry_after} 秒后再试"
                ),
            }
        ), 429

    if not services["auth"].verify(
        username,
        password,
    ):
        services[
            "login_limiter"
        ].record_failure(
            remote_addr
        )

        return _json_error(
            "用户名或密码错误",
            401,
        )

    services[
        "login_limiter"
    ].record_success(
        remote_addr
    )

    session.clear()
    session.permanent = True
    session["logged_in"] = True
    session["username"] = username
    session["csrf_token"] = (
        services["csrf"]()
    )

    services["logger"].info(
        "管理员登录成功: %s",
        username,
    )

    return jsonify(
        {
            "success": True,
            "message": "登录成功",
        }
    )


@api.get("/csrf")
def csrf():
    return jsonify(
        {
            "token": session.get(
                "csrf_token"
            )
        }
    )


@api.get("/config")
def get_config():
    return jsonify(
        {
            "success": True,
            "config": _services()[
                "config"
            ].public(),
        }
    )


@api.get("/config/export")
def export_config():
    return jsonify(
        {
            "success": True,
            "config": _services()["config"].load(),
        }
    )


@api.post("/config")
@require_csrf
def save_config():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        _services()["config"].save(
            data
        )

    except ValueError as exc:
        return _json_error(
            str(exc)
        )

    _services()["logger"].info(
        "系统配置已更新"
    )

    return jsonify(
        {
            "success": True,
            "message": "配置已保存",
        }
    )


@api.route(
    "/channels",
    methods=["GET", "POST"],
)
@require_csrf
def channels():
    config = _services()["config"]

    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "channels": (
                    config.get_channels()
                ),
            }
        )

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:
        saved = config.save_channels(
            data.get(
                "channels",
                [],
            )
        )

    except ValueError as exc:
        return _json_error(
            str(exc)
        )

    return jsonify(
        {
            "success": True,
            "channels": saved,
        }
    )


@api.get("/admin/logs")
def admin_logs():
    return jsonify(
        {
            "success": True,
            "logs": _services()[
                "logs"
            ](),
        }
    )


@api.get("/check-channels")
def check_channels_health():
    """兼容旧接口路径，但汇总所有资源来源卡片，不再绑定 Telegram。"""
    results = _services()["resource_sources"].check_all()
    total = sum(
        int(item.get("total") or 0)
        for item in results
        if isinstance(item, dict)
    )
    valid_count = sum(
        int(item.get("valid_count") or 0)
        for item in results
        if isinstance(item, dict)
    )
    return jsonify({
        "success": True,
        "total": total,
        "valid_count": valid_count,
        "resource_sources": results,
    })


@api.get("/resource-sources")
def resource_sources_health():
    return jsonify(
        {
            "success": True,
            "resource_sources": _services()["resource_sources"].get_status(),
        }
    )


@api.get("/storage-targets")
def storage_targets():
    manager = _services()["storage_targets"]
    return jsonify({
        "success": True,
        "targets": manager.list_targets(),
        "default_target_id": _services()["config"].get_default_storage_target_id(),
    })

@api.get("/storage-targets/<target_id>/destinations")
def storage_target_destinations(target_id):
    manager = _services()["storage_targets"]
    if not manager.get(target_id):
        return _json_error("这个转存位置尚未加载或已停用", 404)
    return jsonify({
        "success": True,
        "target_id": target_id,
        "destinations": manager.destination_options(target_id),
    })



@api.get("/storage-targets/<target_id>/files")
def storage_target_files(target_id):
    """列出存储目标指定目录中的文件；仅支持显式声明该能力的卡片。"""
    manager = _services()["storage_targets"]
    card = manager.get(str(target_id or "").strip())
    if card is None:
        return _json_error("这个转存位置尚未加载或已停用", 404)
    capabilities = set(getattr(card, "capabilities", ()) or ())
    list_destination_files = getattr(card, "list_destination_files", None)
    if "storage.list_destination_files" not in capabilities or not callable(list_destination_files):
        return _json_error("这个存储卡片暂不支持查看网盘目录", 501)
    try:
        parent_fid = _normalize_fid(request.args.get("parent_fid", "0"))
        files = list_destination_files(parent_fid)
        if not isinstance(files, list):
            raise TypeError("目录列表格式无效")
    except ValueError as exc:
        return _json_error(str(exc))
    except Exception:
        _services()["logger"].exception("读取存储目标目录失败: %s", target_id)
        return _json_error("读取网盘目录失败，请稍后刷新重试", 502)
    return jsonify({"success": True, "target_id": target_id, "parent_fid": parent_fid, "files": files})


@api.post("/storage-targets/default")
@require_csrf
def set_default_storage_target():
    data = request.get_json(silent=True) or {}
    target_id = str(data.get("target_id") or "").strip()
    if target_id and not _services()["storage_targets"].get(target_id):
        return _json_error("这个转存位置尚未加载或已停用")
    try:
        saved = _services()["config"].set_default_storage_target_id(target_id)
    except ValueError as exc:
        return _json_error(str(exc))
    return jsonify({"success": True, "default_target_id": saved})


@api.get("/cards")
def cards():
    """返回已加载卡片列表；单张卡片出错时只显示提示，不影响其他卡片。"""
    services = _services()
    registry = services["card_registry"]
    config_store = services["config"]
    logger = services["logger"]
    config = config_store.load()
    card_configs = config.get("cards") if isinstance(config, dict) else {}
    cards = []
    warnings = []

    for card in registry.list():
        card_id = "unknown"
        try:
            manifest = card.manifest.to_dict()
            card_id = str(manifest.get("id") or getattr(card, "card_id", "unknown"))
            saved = card_configs.get(card_id) if isinstance(card_configs, dict) else {}
            saved = saved if isinstance(saved, dict) else {}
            item = manifest
            item["enabled"] = bool(saved.get("enabled", True))
            card_config = saved.get("config", {})
            card_config = card_config if isinstance(card_config, dict) else {}

            try:
                item["configured"] = bool(card.is_configured(card_config))
            except Exception:
                logger.exception("读取卡片 %s 的配置状态失败", card_id)
                item["configured"] = False
                item["health"] = {
                    "status": "error",
                    "message": "卡片状态读取失败，请检查这张卡片的日志。",
                }
                warnings.append(f"「{item.get('name') or card_id}」状态读取失败，其他卡片仍可使用。")
            else:
                health = card_config.get("health")
                if isinstance(health, dict) and health:
                    item["health"] = health
                else:
                    item["health"] = {
                        "status": "idle",
                        "message": "还没有检查连接，可以点开卡片后手动检查。",
                    }
            cards.append(item)
        except Exception:
            logger.exception("读取卡片 %s 的信息失败", card_id)
            warnings.append("有一张卡片的信息读取失败，已跳过；其他卡片仍可使用。")

    plugin_manager = services.get("file_card_plugins")
    try:
        file_plugins = plugin_manager.list_plugins() if plugin_manager else []
    except Exception:
        logger.exception("读取卡片文件列表失败")
        file_plugins = []
        warnings.append("卡片文件列表暂时无法读取，已加载的功能不受影响。")

    return jsonify({
        "success": True,
        "cards": cards,
        "warnings": warnings,
        "dynamic_install_enabled": plugin_manager is not None,
        "file_plugins": file_plugins,
    })


@api.post("/cards/plugins")
@require_csrf
def install_card_plugin():
    """Install one trusted Python card file and load it immediately."""
    plugin_manager = _services().get("file_card_plugins")
    if plugin_manager is None:
        return _json_error("卡片文件管理功能暂不可用，请检查服务配置", 503)
    uploaded = request.files.get("file")
    if uploaded is None:
        return _json_error("请选择一个 .py 卡片文件")
    try:
        filename = plugin_manager.validate_filename(uploaded.filename or "")
        content = uploaded.read(256 * 1024 + 1)
        card = plugin_manager.install(filename, content)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    except Exception:
        _services()["logger"].exception("安装单文件卡片失败")
        return _json_error("卡片加载失败，请检查文件内容和服务日志", 400)
    return jsonify({
        "success": True,
        "card_id": card.card_id,
        "manifest": card.manifest.to_dict(),
        "message": "卡片已安装并启用",
    })


@api.post("/cards/plugins/<filename>/load")
@require_csrf
def load_card_plugin(filename):
    plugin_manager = _services().get("file_card_plugins")
    if plugin_manager is None:
        return _json_error("卡片文件管理功能暂不可用，请检查服务配置", 503)
    try:
        card = plugin_manager.load_file(filename)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    except Exception:
        _services()["logger"].exception("加载单文件卡片 %s 失败", filename)
        return _json_error("卡片加载失败，请检查文件内容和服务日志", 400)
    return jsonify({"success": True, "card_id": card.card_id, "message": "卡片已加载，可以使用了"})


@api.delete("/cards/plugins/<filename>")
@require_csrf
def uninstall_card_plugin(filename):
    plugin_manager = _services().get("file_card_plugins")
    if plugin_manager is None:
        return _json_error("卡片文件管理功能暂不可用，请检查服务配置", 503)
    try:
        safe_filename = plugin_manager.validate_filename(filename)
        services = _services()
        card_id = safe_filename[:-3]
        default_target = services["config"].get_default_storage_target_id()
        if default_target and str(default_target) == card_id:
            return _json_error("该卡片是当前默认存储目标，请先切换默认目标后再卸载", 409)

        # Subscriptions are durable references to both source and storage cards.
        # Hold the subscription lock through the check and unload so a scheduler
        # cannot start a referenced subscription between validation and removal.
        subscriptions = services.get("subscriptions")
        subscription_lock = getattr(subscriptions, "lock", None)
        if subscription_lock is not None:
            with subscription_lock:
                dependent = next(
                    (
                        item
                        for item in subscriptions.get_subscriptions()
                        if str(item.get("source_id") or "").strip() == card_id
                        or str(item.get("storage_target_id") or "").strip() == card_id
                    ),
                    None,
                )
                if dependent is not None:
                    role = (
                        "资源来源"
                        if str(dependent.get("source_id") or "").strip() == card_id
                        else "存储目标"
                    )
                    title = str(dependent.get("title") or dependent.get("id") or "未命名订阅")
                    return _json_error(
                        f"该卡片仍被自动追剧订阅「{title}」作为{role}引用，"
                        "请先删除或修改相关订阅后再卸载",
                        409,
                    )
                removed = plugin_manager.uninstall(safe_filename)
        else:
            removed = plugin_manager.uninstall(safe_filename)
    except FileNotFoundError as exc:
        return _json_error(str(exc), 404)
    except ValueError as exc:
        return _json_error(str(exc))
    except Exception:
        _services()["logger"].exception("卸载单文件卡片 %s 失败", filename)
        return _json_error("卸载卡片失败，请查看服务日志中的详细原因", 500)
    return jsonify({
        "success": True,
        **removed,
        "message": "卡片文件已删除，卡片已卸载；之前保存的设置会保留",
    })


def _has_config_value(value):
    """判断配置是否实际填写；字符串只含空白时视为未配置。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return True


def _card_config_view(card, raw_config):
    """仅向管理界面返回 Manifest 声明的字段，并对敏感值做脱敏。"""
    raw_config = raw_config if isinstance(raw_config, dict) else {}
    public_config, has_value, fields = {}, {}, []
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
    for declared in card.manifest.config_fields:
        field = dict(declared)
        key = str(field.get("key") or "")
        # Specialized editors define their input representation. In particular,
        # fid_lines must remain plain text even if an older manifest says "json".
        if field.get("editor") == "fid_lines":
            field["type"] = "textarea"
        secret = bool(field.get("secret")) or any(marker in key.lower() for marker in sensitive_markers)
        field["secret"] = secret
        value = raw_config.get(key, field.get("default", {} if field.get("type") == "json" else ""))
        if secret:
            public_config[key] = ""
            has_value[key] = bool(value)
        else:
            public_config[key] = value
        fields.append(field)
    return fields, public_config, has_value


@api.post("/filename-recognition/preview")
@require_csrf
def preview_filename_recognition():
    """用业务侧共享解析器预览文件名，避免后台前端重复实现识别规则。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_error("提交的数据格式不正确")

    file_name = str(data.get("file_name") or "").strip()
    if not file_name or len(file_name) > 1024:
        return _json_error("请输入文件名（最多 1024 个字符）")

    source_id = str(data.get("source_id") or "").strip()
    if len(source_id) > 64:
        return _json_error("资源来源 ID 不能超过 64 个字符")

    services = _services()
    card = services["card_registry"].get("filename_recognition")
    config = {}
    saved_cards = services["config"].load().get("cards", {})
    saved_card = saved_cards.get("filename_recognition", {}) if isinstance(saved_cards, dict) else {}
    if isinstance(saved_card, dict) and isinstance(saved_card.get("config"), dict):
        config = dict(saved_card["config"])

    # Use the unsaved rules currently shown in the editor, so users can test
    # edits before saving them. Server-side validation is the same as on save.
    if "rules" in data:
        if card is None:
            return _json_error("文件名识别卡片尚未加载，无法预览", 404)
        try:
            config = card.validate_config({**config, "rules": data.get("rules")})
        except Exception as exc:
            return _json_error(str(exc) or "识别规则无效")

    result = parse_media_filename(file_name)
    season, episode = result.get("season"), result.get("episode")
    rule_source = "内置规则" if episode is not None else "未识别"
    if card is not None and isinstance(config, dict):
        try:
            parsed_season, parsed_episode = card.parse_episode_for_source(
                file_name, source_id, config
            )
            if parsed_episode is not None:
                season, episode = parsed_season, parsed_episode
                builtin = parse_tv_episode(file_name, {"pattern": "", "replace": ""})
                # A custom rule may add a season or otherwise refine a partial
                # built-in match; compare the full result, not episode alone.
                rule_source = "内置规则" if builtin == (parsed_season, parsed_episode) else "自定义规则"
                if result.get("media_type") == "unknown":
                    result["media_type"] = "tv"
        except Exception:
            current_app.logger.exception("文件名识别预览失败")
            return _json_error("识别预览失败，请检查规则配置", 500)

    result["season"] = season
    result["episode"] = episode
    result["rule_source"] = rule_source
    result["recognized"] = bool(result.get("name")) and (
        result.get("media_type") != "unknown" or episode is not None
    )
    return jsonify({"success": True, "result": result})


@api.route("/cards/<card_id>/config", methods=["GET", "POST"])
@require_csrf
def resource_card_config(card_id):
    """所有已注册卡片共用的配置接口；字段由 CardManifest 声明。"""
    card = _services()["card_registry"].get(card_id)
    if card is None:
        return _json_error("这张卡片尚未加载，暂时无法配置", 404)

    config_store = _services()["config"]
    cards_config = config_store.load().get("cards", {})
    saved = cards_config.get(card_id, {}) if isinstance(cards_config, dict) else {}
    saved = saved if isinstance(saved, dict) else {}
    raw_config = saved.get("config", {})
    raw_config = raw_config if isinstance(saved.get("config", {}), dict) else {}

    if request.method == "GET":
        fields, public_config, has_value = _card_config_view(card, raw_config)
        return jsonify({"success": True, "card_id": card_id, "enabled": bool(saved.get("enabled", True)),
                        "fields": fields, "config": public_config, "has_value": has_value})

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_error("提交的数据格式不正确")
    incoming = data.get("config", {})
    if not isinstance(incoming, dict):
        return _json_error("卡片设置格式不正确")
    fields_by_key = {}
    for declared in card.manifest.config_fields:
        field = dict(declared)
        if field.get("editor") == "fid_lines":
            field["type"] = "textarea"
        fields_by_key[str(field.get("key"))] = field
    unknown = set(incoming) - set(fields_by_key)
    if unknown:
        return _json_error("包含这张卡片不支持的设置项：" + ", ".join(sorted(unknown)))

    merged, persisted_incoming = dict(raw_config), {}
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
    for key, value in incoming.items():
        field = fields_by_key[key]
        field_type = field.get("type", "string")
        fid_lines_editor = field.get("editor") == "fid_lines"
        secret = bool(field.get("secret")) or any(marker in key.lower() for marker in sensitive_markers)
        # 密钥字段提交空字符串、null 或纯空白时表示“未修改”，不能覆盖已保存凭据。
        if secret and (
            value is None or (isinstance(value, str) and not value.strip())
        ):
            continue

        # Specialized editors own their wire format. Convert fid_lines text to the
        # canonical mapping before card validation, so older card files whose
        # validator still expects a dict remain compatible with the newer textarea.
        if fid_lines_editor:
            if isinstance(value, str):
                parsed_fids = {}
                for line_number, line in enumerate(value.splitlines(), start=1):
                    line = line.strip()
                    if not line:
                        continue
                    if "=" not in line:
                        return _json_error(
                            f"配置字段「{field.get('label') or key}」第 {line_number} 行格式无效，请使用“分类=FID”"
                        )
                    category, fid = (part.strip() for part in line.split("=", 1))
                    if not category or not fid:
                        return _json_error(
                            f"配置字段「{field.get('label') or key}」第 {line_number} 行格式无效，请使用“分类=FID”"
                        )
                    if category in parsed_fids:
                        return _json_error(f"配置字段「{field.get('label') or key}」分类重复：{category}")
                    parsed_fids[category] = fid
                value = parsed_fids
            elif not isinstance(value, dict):
                return _json_error(f"配置字段「{field.get('label') or key}」必须是文本或对象")

        if field_type == "json" and not isinstance(value, (dict, list)):
            return _json_error(f"配置字段「{field.get('label') or key}」必须是 JSON 对象或数组")
        if field_type == "boolean" and not isinstance(value, bool):
            return _json_error(f"配置字段「{field.get('label') or key}」必须是布尔值")
        if field_type == "number":
            # HTML input.value is always a string. Accept numeric strings as well as
            # JSON numbers so card enable/disable saves are not blocked by the UI type.
            if isinstance(value, bool):
                return _json_error(f"配置字段「{field.get('label') or key}」必须是数字")
            if isinstance(value, str):
                raw_number = value.strip()
                if not raw_number:
                    return _json_error(f"配置字段「{field.get('label') or key}」必须是数字")
                try:
                    number_value = float(raw_number)
                except ValueError:
                    return _json_error(f"配置字段「{field.get('label') or key}」必须是数字")
                if not math.isfinite(number_value):
                    return _json_error(f"配置字段「{field.get('label') or key}」必须是有限数字")
                value = int(number_value) if number_value.is_integer() else number_value
            elif not isinstance(value, (int, float)) or not math.isfinite(value):
                return _json_error(f"配置字段「{field.get('label') or key}」必须是有限数字")
        if field_type in {"string", "password", "textarea"} and not fid_lines_editor and not isinstance(value, str):
            return _json_error(f"配置字段「{field.get('label') or key}」必须是文本")
        merged[key] = value
        persisted_incoming[key] = value

    enabled = data.get("enabled") if "enabled" in data else None
    if enabled is not None and not isinstance(enabled, bool):
        return _json_error("enabled 必须是布尔值")
    will_be_enabled = bool(saved.get("enabled", True)) if enabled is None else enabled
    for key, field in fields_by_key.items():
        secret = bool(field.get("secret")) or any(marker in key.lower() for marker in sensitive_markers)
        has_value = _has_config_value(merged.get(key))
        has_saved_secret = secret and _has_config_value(raw_config.get(key))
        # An unconfigured provider must still be disableable. Required fields
        # are enforced when enabling it, not when explicitly turning it off.
        if will_be_enabled and field.get("required") and not has_value and not has_saved_secret:
            return _json_error(f"请填写必填配置：{field.get('label') or key}")
    if will_be_enabled:
        try:
            card.validate_enabled_config(merged)
        except (ValueError, TypeError) as exc:
            return _json_error(str(exc))
    try:
        validated = card.validate_config(merged)
        if not isinstance(validated, dict):
            return _json_error("卡片配置校验必须返回 JSON 对象")
        # 只写入本次声明并提交的字段，使用卡片规范化后的值；其余内部配置保持不变。
        persisted_incoming = {
            key: validated.get(key, value)
            for key, value in persisted_incoming.items()
        }
        saved = config_store.save_card_config(card_id, persisted_incoming, enabled=enabled)
    except (ValueError, TypeError) as exc:
        return _json_error(str(exc))
    fields, public_config, has_value = _card_config_view(card, saved.get("config", {}))
    return jsonify({"success": True, "card_id": card_id, "enabled": bool(saved.get("enabled", True)),
                    "fields": fields, "config": public_config, "has_value": has_value,
                    "message": "卡片配置已保存"})


@api.post("/cards/<card_id>/check")
@require_csrf
def check_card_connection(card_id):
    """调用卡片自身的健康检查；卡片必须声明健康检查能力。"""
    card = _services()["card_registry"].get(card_id)
    if card is None:
        return _json_error("这张卡片尚未加载，暂时无法配置", 404)
    supports_check = any(
        capability.endswith(".health_check") or capability.endswith(".check")
        for capability in card.capabilities
    )
    if not supports_check:
        return _json_error("该卡片未声明连接检查能力", 400)
    cards_config = _services()["config"].load().get("cards", {})
    saved = cards_config.get(card_id, {}) if isinstance(cards_config, dict) else {}
    config = saved.get("config", {}) if isinstance(saved, dict) else {}
    try:
        result = card.check(config if isinstance(config, dict) else {})
    except Exception:
        _services()["logger"].exception("卡片 %s 连接检查失败", card_id)
        return _json_error("连接检查失败，请查看服务日志", 502)
    if not isinstance(result, dict):
        return _json_error("卡片检查结果格式无效", 502)
    # 插件结果不能覆盖 API 自身的 success 状态。
    return jsonify({**result, "success": True})


# Card configuration is served exclusively by /api/cards/<card_id>/config.


@api.get("/login-backdrop")
def login_backdrop():
    """登录页专用公开背景接口：只返回少量带海报的影视数据，不暴露任何登录后配置。"""
    try:
        movies = _services()["metadata"].list_movies("电影", "U")
        covers = [
            {
                "title": item.get("title", ""),
                "cover": item.get("cover", ""),
            }
            for item in movies
            if isinstance(item, dict) and item.get("cover")
        ][:12]
        return jsonify({"success": True, "movies": covers})
    except Exception:
        _services()["logger"].exception("获取登录页背景海报失败")
        return jsonify({"success": False, "movies": []})


@api.get("/get-movies")
def get_movies():
    tag = request.args.get(
        "tag",
        "电影",
    )

    sort_type = request.args.get(
        "sort",
        "U",
    )

    provider_id = str(request.args.get("provider_id") or "").strip() or None

    try:
        category = normalize_media_category(tag) or "电影"
        movies = _services()["metadata"].list_movies(
            category,
            sort_type,
            provider_id=provider_id,
        )
        movies = [
            classify_metadata_item(item, fallback_category=category)
            for item in movies
            if isinstance(item, dict)
        ]

        return jsonify(
            {
                "success": True,
                "provider_id": provider_id,
                "movies": movies,
            }
        )

    except Exception:
        _services()["logger"].exception("获取影视列表失败")
        return jsonify({
            "success": False,
            "movies": [],
            "message": "获取影视数据失败，请稍后重试",
        })


@api.get("/search-douban")
@api.get("/search-metadata")
def search_metadata():
    query = request.args.get(
        "q",
        "",
    ).strip()[:80]

    if not query:
        return jsonify(
            {
                "success": False,
                "movies": [],
            }
        )

    provider_id = str(request.args.get("provider_id") or "").strip() or None
    category = normalize_media_category(request.args.get("tag")) or "电影"

    try:
        movies = _services()["metadata"].search(
            query,
            provider_id=provider_id,
            category=category,
        )
        movies = [
            classify_metadata_item(item, fallback_category=category)
            for item in movies
            if isinstance(item, dict)
        ]
        return jsonify(
            {
                "success": True,
                "provider_id": provider_id,
                "category": category,
                "movies": movies,
            }
        )

    except Exception:
        _services()["logger"].warning("影视元数据搜索失败")

        return jsonify(
            {
                "success": False,
                "movies": [],
                "message": "影视搜索失败",
            }
        )


@api.post("/search-candidates")
@require_csrf
def search_candidates():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    movies = data.get(
        "movies",
        [],
    )

    config = _services()["config"]

    if not movies:
        return _json_error(
            "未选择影片"
        )

    if (
        not isinstance(
            movies,
            list,
        )
        or len(movies) > 10
    ):
        return _json_error(
            "一次最多检索 10 部影片"
        )

    services = _services()
    storage_manager = services["storage_targets"]
    storage_target_id = str(data.get("storage_target_id") or "").strip()
    if not storage_target_id:
        default_target = storage_manager.get()
        storage_target_id = str(getattr(default_target, "card_id", "") or "").strip()
    storage_target = storage_manager.get(storage_target_id or None)
    if not storage_target_id or storage_target is None:
        return _json_error("请先选择一个已启用的存储卡片，再搜索对应网盘的资源", 400)
    target_capabilities = set(getattr(storage_target, "capabilities", ()) or ())
    if not any(
        capability.startswith("storage.accepts.")
        and capability != "storage.accepts.*"
        for capability in target_capabilities
    ):
        return _json_error("所选存储卡片没有声明支持的分享类型，暂时无法按该网盘搜索", 400)

    service = services["search"]
    scan_limit_value = data.get("scan_limit")
    page_offset = 0
    scan_limit = None
    if scan_limit_value is not None:
        if len(movies) != 1:
            return _json_error("分批检索一次只能查询一部影片")
        try:
            page_offset = max(0, int(data.get("offset") or 0))
            scan_limit = max(1, min(20, int(scan_limit_value)))
        except (TypeError, ValueError):
            return _json_error("检索分页参数无效")
    candidates_map = {}
    candidate_pages = {}

    for movie in movies:
        title = (
            movie.get(
                "title",
                str(movie),
            )
            if isinstance(
                movie,
                dict,
            )
            else str(movie)
        )

        try:
            search_movie = dict(movie) if isinstance(movie, dict) else {"title": str(movie)}
            search_movie["storage_target_id"] = storage_target_id
            page_info = {} if scan_limit is not None else None
            if page_info is not None:
                candidates_map[title] = service.search_movie_candidates(
                    search_movie,
                    config.load(),
                    offset=page_offset,
                    scan_limit=scan_limit,
                    page_info=page_info,
                )
                candidate_pages[title] = page_info
            else:
                # 保持旧调用签名兼容，便于现有集成与第三方替代服务继续工作。
                candidates_map[title] = service.search_movie_candidates(
                    search_movie,
                    config.load(),
                )
        except Exception:
            _services()["logger"].exception("资源检索失败: %s", title)
            return _json_error("资源检索暂时失败，请稍后重试；若持续出现，请查看服务日志", 502)

    source_status = _services()["resource_sources"].get_status()
    unavailable_sources = [
        item.get("name", item.get("id", "未知来源"))
        for item in source_status
        if item.get("enabled", True)
        and (item.get("health") or {}).get("status") == "unavailable"
    ]

    message = ""
    if not any(candidates_map.values()):
        if unavailable_sources:
            message = (
                "当前没有找到可用资源；资源来源不可用："
                + "、".join(unavailable_sources)
                + "。请先检查资源来源状态。"
            )
        else:
            message = "当前已配置资源来源暂未找到可用候选，可稍后重试。"

    return jsonify(
        {
            "success": True,
            "candidates_map": candidates_map,
            "candidate_pages": candidate_pages,
            "storage_target_id": storage_target_id,
            "resource_sources": source_status,
            "message": message,
        }
    )


@api.post("/transfer-selected")
@require_csrf
def transfer_selected():
    data = request.get_json(silent=True) or {}
    movie = data.get("movie")
    candidate = data.get("candidate")
    if not isinstance(movie, dict) or not isinstance(candidate, dict):
        return _json_error("参数不完整")

    files = candidate.get("files")
    if not isinstance(files, list) or not files:
        return _json_error("请至少选择一个要转存的文件")
    if any(
        not isinstance(item, dict)
        or not str(item.get("fid") or "").strip()
        for item in files
    ):
        return _json_error("所选文件信息不完整，请重新选择资源")

    try:
        target_fid = _normalize_fid(
            data.get("target_fid") or "0"
        )
    except ValueError as exc:
        return _json_error(str(exc))

    storage_manager = _services()["storage_targets"]
    storage_target_id = str(
        data.get("storage_target_id")
        or candidate.get("storage_target_id")
        or ""
    ).strip()
    if not storage_target_id and candidate.get("resource_type"):
        storage_target_id = storage_manager.select_target_id(candidate)
    storage_target = storage_manager.get(storage_target_id or None)
    if storage_target_id and not storage_target:
        return _json_error("指定的存储目标不存在或已停用", 400)

    resource_type = str(candidate.get("resource_type") or "").strip().lower()
    if resource_type and not storage_target:
        return _json_error(
            "没有已启用的存储卡片支持此资源类型，请安装或启用兼容的存储卡片",
            400,
        )
    if resource_type and storage_target:
        required_capability = f"storage.accepts.{resource_type}"
        if (
            required_capability not in storage_target.capabilities
            and "storage.accepts.*" not in storage_target.capabilities
        ):
            return _json_error(
                f"存储卡片「{storage_target.manifest.name}」不支持此资源类型，"
                "请在资源选择窗口中切换到兼容的存储卡片",
                400,
            )

    candidate = {
        **candidate,
        "storage_target_id": storage_target_id,
    }
    payload = {
        "movie": movie,
        "candidate": candidate,
        "target_fid": target_fid,
        "storage_target_id": storage_target_id,
        "destination_listing_supported": (
            "storage.list_destination_files"
            in set(getattr(storage_target, "capabilities", ()) or ())
        ),
    }

    # 任务在线程池中异步执行，后台线程没有 Flask 的默认请求上下文。
    # 显式捕获真实 Flask App，并在 runner 中建立应用上下文。
    app = current_app._get_current_object()

    def runner(progress):
        with app.app_context():
            service = _services()["search"]
            return service.transfer_selected_resource_with_progress(
                movie,
                candidate,
                target_fid,
                {},
                progress,
            )

    task = _services()["tasks"].create_transfer_task(payload, runner)
    return jsonify({
        "success": True,
        "task": {key: value for key, value in task.items() if key not in {"retry_payload", "dedupe_key"}},
        "message": "转存任务已创建",
    })


@api.get("/tasks")
def get_tasks():
    manager = _services()["tasks"]
    transfer_tasks = manager.list_tasks()
    subscriptions = _services()["subscriptions"].get_subscriptions()
    items = []

    for task in transfer_tasks:
        items.append({
            key: value for key, value in task.items()
            if key != "retry_payload"
        })

    for sub in subscriptions:
        history = sub.get("run_history", []) or []
        task_state = _subscription_task_state(sub)
        items.append({
            "id": "subscription:" + str(sub.get("id")),
            "type": "subscription",
            "kind": "智能追剧",
            "title": sub.get("title", "未命名任务"),
            "media_type": sub.get("media_type", ""),
            "tag": sub.get("tag", ""),
            "status": task_state["status"],
            "progress": task_state["progress"],
            "total": len(sub.get("tracked_file_keys", []) or []),
            "success_count": len(sub.get("saved_episodes", []) or []),
            "skipped_count": 0,
            "failed_count": 1 if sub.get("last_error") else 0,
            "message": sub.get("last_error") or sub.get("last_check", "等待检查"),
            "next_run_at": sub.get("next_run_at"),
            "updated_at": sub.get("last_check_at") or 0,
            "run_history": history[-10:],
            "last_diagnostics": sub.get("last_diagnostics", {}),
            "cover": sub.get("cover", ""),
            "source_channel": sub.get("channel_name") or sub.get("channel", ""),
            "tracked_file_count": len(sub.get("tracked_file_keys", []) or []),
            "subscription_id": sub.get("id"),
            "retry_count": sub.get("retry_count", 0),
            "pending_save_keys": sub.get("pending_save_keys", []) or [],
            "pending_save_uncertain": bool(sub.get("pending_save_uncertain")),
            "phase": task_state["phase"],
            "phase_label": task_state["phase_label"],
        })

    items = [_sanitize_persisted_numbers(item) for item in items]
    items.sort(key=_task_sort_timestamp, reverse=True)
    return jsonify({"success": True, "tasks": items})


@api.delete("/tasks/<task_id>")
@require_csrf
def delete_task(task_id):
    task_id = str(task_id)
    if task_id.startswith("subscription:"):
        sub_id = task_id.split(":", 1)[1]
        if not _services()["subscriptions"].delete_subscription(sub_id):
            return _json_error("未找到追剧任务", 404)
        return jsonify({"success": True, "message": "追剧任务已删除"})
    ok, message = _services()["tasks"].delete_task(task_id)
    if not ok:
        return _json_error(message, 400 if message != "任务不存在" else 404)
    return jsonify({"success": True, "message": message})


@api.post("/tasks/clear-history")
@require_csrf
def clear_task_history():
    removed_tasks = _services()["tasks"].clear_history()
    removed_runs = _services()["subscriptions"].clear_run_history()
    removed = removed_tasks + removed_runs
    return jsonify({"success": True, "removed": removed, "message": f"已清理 {removed} 条历史记录"})


@api.get("/tasks/<task_id>")
def get_task_detail(task_id):
    if str(task_id).startswith("subscription:"):
        sub_id = str(task_id).split(":", 1)[1]
        sub = next(
            (item for item in _services()["subscriptions"].get_subscriptions()
             if str(item.get("id")) == sub_id),
            None,
        )
        if not sub:
            return _json_error("未找到任务", 404)
        task_state = _subscription_task_state(sub)
        task = {
            "id": "subscription:" + sub_id,
            "type": "subscription",
            "kind": "智能追剧",
            "title": sub.get("title", "未命名任务"),
            "status": task_state["status"],
            "phase": task_state["phase"],
            "phase_label": task_state["phase_label"],
            "progress": task_state["progress"],
            "total": len(sub.get("tracked_file_keys", []) or []),
            "success_count": len(sub.get("saved_episodes", []) or []),
            "skipped_count": 0,
            "failed_count": 1 if sub.get("last_error") else 0,
            "message": sub.get("last_error") or sub.get("last_check", "等待检查"),
            "next_run_at": sub.get("next_run_at"),
            "updated_at": sub.get("last_check_at") or 0,
            "run_history": (sub.get("run_history", []) or [])[-20:],
            "last_diagnostics": sub.get("last_diagnostics", {}),
            "episode_coverage": build_episode_coverage(sub),
            "subscription_id": sub_id,
            "pending_save_keys": sub.get("pending_save_keys", []) or [],
            "cover": sub.get("cover", ""),
            "source_channel": sub.get("channel", ""),
            "tracked_file_count": len(sub.get("tracked_file_keys", []) or []),
        }
        return jsonify({"success": True, "task": task})
    task = _services()["tasks"].get_task(task_id)
    if not task:
        return _json_error("未找到任务", 404)
    task = _sanitize_persisted_numbers(task)
    return jsonify({
        "success": True,
        "task": {key: value for key, value in task.items() if key != "retry_payload"},
    })


@api.post("/tasks/<task_id>/retry")
@require_csrf
def retry_task(task_id):
    manager = _services()["tasks"]
    old = manager.get_task(task_id)
    if not old:
        return _json_error("未找到任务", 404)
    if old.get("type") == "subscription":
        sub_id = old.get("subscription_id")
        ok, message = _services()["subscriptions"].check_subscription_now(str(sub_id or ""))
        return jsonify({"success": ok, "message": message})
    if old.get("type") != "transfer":
        return _json_error("不支持重试的任务类型")

    app = current_app._get_current_object()

    def runner(progress):
        with app.app_context():
            payload = old.get("retry_payload") or {}
            movie = payload.get("movie") or {}
            candidate = payload.get("candidate") or {}
            service = _services()["search"]
            return service.transfer_selected_resource_with_progress(
                movie,
                candidate,
                payload.get("target_fid") or "0",
                {},
                progress,
            )

    task = manager.retry_transfer(task_id, runner)
    if not task:
        return _json_error("任务不存在或无法重试", 400)
    return jsonify({
        "success": True,
        "task": {key: value for key, value in task.items() if key != "retry_payload"},
        "message": "已创建重试任务",
    })


@api.route(
    "/subscriptions",
    methods=[
        "GET",
        "POST",
        "DELETE",
    ],
)
@require_csrf
def subscriptions():
    manager = _services()[
        "subscriptions"
    ]

    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "subscriptions": (
                    manager.get_subscriptions()
                ),
            }
        )

    if request.method == "POST":
        data = (
            request.get_json(
                silent=True
            )
            or {}
        )

        try:
            target_fid = _normalize_fid(data.get("target_fid") or "0")
            cover = str(data.get("cover") or "").strip()[:1000]
            if not cover:
                try:
                    title_for_cover = str(data.get("title") or "").strip()
                    if title_for_cover:
                        matches = _services()["metadata"].search(title_for_cover)
                        if matches:
                            cover = str(matches[0].get("cover") or "").strip()[:1000]
                except Exception:
                    _services()["logger"].debug("获取追剧任务海报失败", exc_info=True)

            sub = manager.add_subscription(
                title=data.get(
                    "title"
                ),
                pwd_id=data.get("pwd_id", ""),
                resource_id=data.get("resource_id", ""),
                resource_type=data.get("resource_type", ""),
                target_fid=target_fid,
                interval_hours=int(
                    data.get(
                        "interval_hours",
                        6,
                    )
                ),
                start_ep=int(
                    data.get(
                        "start_ep",
                        0,
                    )
                ),
                channel=data.get(
                    "channel",
                    "",
                ),
                channel_name=data.get(
                    "channel_name",
                    "",
                ),
                files=data.get(
                    "files",
                    [],
                ),
                storage_target_id=data.get(
                    "storage_target_id",
                    "",
                ),
                cover=cover,
                source_id=data.get("source_id", ""),
                media_type=data.get("media_type", ""),
                tag=data.get("tag", ""),


            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            return _json_error(
                str(exc)
            )

        return jsonify(
            {
                "success": True,
                "subscription": sub,
                "message": (
                    "智能追剧任务添加成功！"
                ),
            }
        )

    sub_id = request.args.get(
        "id"
    ) or str(
        (
            request.get_json(
                silent=True
            )
            or {}
        ).get("id")
        or ""
    )

    if not sub_id:
        return _json_error(
            "未提供订阅 ID"
        )

    if not manager.delete_subscription(
        sub_id
    ):
        return _json_error(
            "未找到订阅任务",
            404,
        )

    return jsonify(
        {
            "success": True,
            "message": "已删除订阅",
        }
    )


@api.post("/subscriptions/run-now")
@require_csrf
def run_subscription_now():
    sub_id = str(
        (
            request.get_json(
                silent=True
            )
            or {}
        ).get("id")
        or ""
    )

    if not sub_id:
        return _json_error(
            "未提供订阅 ID"
        )

    success, message = (
        _services()[
            "subscriptions"
        ].check_subscription_now(
            sub_id
        )
    )

    return jsonify(
        {
            "success": success,
            "message": message,
        }
    )


@api.post("/subscriptions/resolve-pending")
@require_csrf
def resolve_pending_subscription():
    data = request.get_json(silent=True) or {}
    sub_id = str(data.get("id") or "").strip()
    action = str(data.get("action") or "").strip()
    if not sub_id:
        return _json_error("未提供订阅 ID")
    if action not in {"saved", "not_saved"}:
        return _json_error("确认操作无效")
    success, message = _services()["subscriptions"].resolve_pending_save(sub_id, action)
    return jsonify({"success": success, "message": message})


def _metadata_image_policy(target: str, metadata_cards) -> dict[str, str] | None:
    """Resolve image hosts from loaded metadata-card manifests, not provider IDs."""
    try:
        parsed = urlparse(target)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return None

    host = host.lower()
    for card in metadata_cards:
        manifest = getattr(card, "manifest", None)
        for allowed_suffix in getattr(manifest, "image_hosts", ()):
            suffix = str(allowed_suffix or "").lower().strip(".")
            if suffix and (host == suffix or host.endswith("." + suffix)):
                return {"referer": str(getattr(manifest, "image_referer", "") or "")}
    return None


@api.get("/proxy-img")
def proxy_img():
    target = request.args.get("url", "").strip()
    metadata_cards = _services()["card_registry"].find_by_type("metadata_provider")
    policy = _metadata_image_policy(target, metadata_cards)
    if policy is None:
        return Response("Host not allowed", status=403)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }
    if policy["referer"]:
        headers["Referer"] = policy["referer"]

    try:
        with _services()["http"].session.get(
            target,
            headers=headers,
            timeout=(3, 8),
            stream=True,
        ) as response:
            if response.status_code != 200:
                host = urlparse(target).hostname or "unknown"
                _services()["logger"].warning(
                    "元数据海报上游请求失败: host=%s status=%s",
                    host,
                    response.status_code,
                )
                return Response(
                    f"Image upstream HTTP {response.status_code}",
                    status=502,
                    headers={"X-MovieSync-Image-Error": f"upstream_http_{response.status_code}"},
                )

            content_type = (
                response.headers.get("Content-Type", "image/jpeg")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            allowed_image_types = {
                "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif",
            }
            if content_type not in allowed_image_types:
                host = urlparse(target).hostname or "unknown"
                _services()["logger"].warning(
                    "元数据海报上游返回非图片内容: host=%s content_type=%s status=%s",
                    host,
                    content_type or "unknown",
                    response.status_code,
                )
                return Response(
                    f"Upstream content is not an image ({content_type or 'unknown'})",
                    status=415,
                    headers={"X-MovieSync-Image-Error": "upstream_not_image"},
                )

            max_image_bytes = 12 * 1024 * 1024
            try:
                declared_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > max_image_bytes:
                return Response("Image too large", status=413)

            chunks = []
            total_size = 0
            for chunk in response.iter_content(chunk_size=16 * 1024):
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > max_image_bytes:
                    return Response("Image too large", status=413)
                chunks.append(chunk)

            return Response(
                b"".join(chunks),
                content_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        host = urlparse(target).hostname or "unknown"
        _services()["logger"].warning("代理元数据海报请求异常: host=%s", host, exc_info=True)
        return Response(
            "Image proxy request failed",
            status=502,
            headers={"X-MovieSync-Image-Error": "proxy_request_failed"},
        )


@api.post("/change-username")
@require_csrf
def change_username():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    current_password = str(
        data.get(
            "current_password"
        )
        or ""
    )

    new_username = str(
        data.get(
            "new_username"
        )
        or ""
    )

    current_username = str(
        session.get(
            "username"
        )
        or ""
    )

    if not current_username:
        return _json_error(
            "当前会话无效，请重新登录",
            401,
        )

    try:
        _services()["auth"].change_username(
            current_username,
            current_password,
            new_username,
        )

    except ValueError as exc:
        return _json_error(
            str(exc)
        )

    _services()["logger"].info(
        "管理员修改用户名: %s -> %s",
        current_username,
        new_username,
    )

    session.clear()

    return jsonify(
        {
            "success": True,
            "message": (
                "用户名修改成功，请重新登录"
            ),
        }
    )


@api.post("/change-password")
@require_csrf
def change_password():
    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    current_password = str(
        data.get(
            "current_password"
        )
        or ""
    )

    new_password = str(
        data.get(
            "new_password"
        )
        or ""
    )

    username = str(
        session.get(
            "username"
        )
        or ""
    )

    if not username:
        return _json_error(
            "当前会话无效，请重新登录",
            401,
        )

    try:
        _services()["auth"].change_password(
            username,
            current_password,
            new_password,
        )

    except ValueError as exc:
        return _json_error(
            str(exc)
        )

    _services()["logger"].info(
        "管理员修改密码成功: %s",
        username,
    )

    session.clear()

    return jsonify(
        {
            "success": True,
            "message": (
                "密码修改成功，请重新登录"
            ),
        }
    )
