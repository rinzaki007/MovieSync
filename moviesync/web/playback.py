"""Optional in-app playback page backed by playback-provider cards."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from ..cards import PlaybackProviderCard
from .routes import require_csrf

playback_pages = Blueprint("playback_pages", __name__)
playback_api = Blueprint("playback_api", __name__, url_prefix="/api/playback")


def _provider():
    services = current_app.extensions["moviesync"]
    registry = services["card_registry"]
    config_store = services["config"]
    try:
        config = config_store.load()
    except Exception:
        current_app.logger.exception("读取播放卡片配置失败")
        return None, "无法读取播放卡片配置"
    cards_config = config.get("cards", {}) if isinstance(config, dict) else {}
    if not isinstance(cards_config, dict):
        cards_config = {}

    for card in registry.find_by_type("playback_provider"):
        if not isinstance(card, PlaybackProviderCard):
            continue
        saved = cards_config.get(card.card_id, {})
        if not isinstance(saved, dict) or saved.get("enabled", True) is False:
            continue
        card_config = saved.get("config", {})
        if not isinstance(card_config, dict) or not card.is_configured(card_config):
            continue
        if "playback.list_files" not in card.capabilities or "playback.resolve" not in card.capabilities:
            continue
        return card, ""
    return None, "尚未安装并配置可用的播放卡片。请在「设置 → 卡片管理」安装 OpenList 播放卡片并完成配置。"


@playback_pages.get("/playback")
def playback_page():
    return render_template("playback.html")


@playback_api.get("/providers")
def playback_providers():
    services = current_app.extensions["moviesync"]
    providers = []
    for card in services["card_registry"].find_by_type("playback_provider"):
        saved = services["config"].load().get("cards", {}).get(card.card_id, {})
        enabled = not isinstance(saved, dict) or saved.get("enabled", True) is not False
        config = saved.get("config", {}) if isinstance(saved, dict) else {}
        configured = bool(isinstance(config, dict) and card.is_configured(config))
        providers.append({
            "id": card.card_id,
            "name": card.manifest.name,
            "enabled": enabled,
            "configured": configured,
            "available": enabled and configured
                and "playback.list_files" in card.capabilities
                and "playback.resolve" in card.capabilities,
        })
    return jsonify({"success": True, "providers": providers})


@playback_api.get("/files")
def playback_files():
    card, error = _provider()
    if card is None:
        return jsonify({"success": False, "message": error, "items": []}), 400
    path = str(request.args.get("path") or "").strip()
    try:
        force_refresh = "_refresh" in request.args
        if force_refresh and card.card_id == "openlist":
            items = card.list_files(path, refresh=True)
        else:
            items = card.list_files(path)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"success": False, "message": str(exc)[:240], "items": []}), 502
    except Exception:
        current_app.logger.exception("播放卡片目录读取失败: %s", card.card_id)
        return jsonify({"success": False, "message": "读取媒体目录失败，请检查卡片配置和服务日志", "items": []}), 502
    return jsonify({"success": True, "items": items, "path": path})


@playback_api.get("/resolve")
def playback_resolve():
    card, error = _provider()
    if card is None:
        return jsonify({"success": False, "message": error}), 400
    path = str(request.args.get("path") or "").strip()
    if not path:
        return jsonify({"success": False, "message": "缺少视频路径"}), 400
    try:
        result = card.resolve_playback(path)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"success": False, "message": str(exc)[:240]}), 502
    except Exception:
        current_app.logger.exception("播放地址解析失败: %s", card.card_id)
        return jsonify({"success": False, "message": "解析播放地址失败，请检查卡片配置和服务日志"}), 502
    url = str(result.get("play_url") or result.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return jsonify({"success": False, "message": "播放卡片没有返回有效的 HTTP/HTTPS 地址"}), 502
    return jsonify({
        "success": True,
        "name": result.get("name") or path.rsplit("/", 1)[-1],
        "url": url,
        "mime_type": result.get("mime_type") or "",
    })



@playback_api.post("/remove")
@require_csrf
def playback_remove():
    """Remove one explicitly confirmed file through the configured OpenList provider."""
    card, error = _provider()
    if card is None:
        return jsonify({"success": False, "message": error}), 400

    if (
        card.card_id != "openlist"
        or "playback.delete_files" not in card.capabilities
        or not callable(getattr(card, "delete_file", None))
    ):
        return jsonify({
            "success": False,
            "message": "当前播放卡片不支持文件删除",
        }), 501

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "请求数据格式无效"}), 400
    if data.get("confirmed") is not True:
        return jsonify({
            "success": False,
            "message": "删除操作未确认；请在播放中心逐个确认后重试",
        }), 400

    path = str(data.get("path") or "").strip()
    if not path:
        return jsonify({"success": False, "message": "缺少待删除文件路径"}), 400

    try:
        deleted = card.delete_file(path)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)[:240]}), 400
    except RuntimeError as exc:
        current_app.logger.warning("OpenList 文件删除失败: %s", str(exc)[:240])
        return jsonify({"success": False, "message": str(exc)[:240]}), 502
    except Exception:
        current_app.logger.exception("OpenList 文件删除异常")
        return jsonify({
            "success": False,
            "message": "OpenList 删除失败，请检查服务状态、Token 权限和服务日志",
        }), 502

    return jsonify({
        "success": True,
        "message": "文件已通过 OpenList 删除",
        "path": deleted.get("path", path) if isinstance(deleted, dict) else path,
    })
