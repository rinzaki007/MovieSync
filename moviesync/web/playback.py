"""Optional in-app playback page backed by playback-provider cards."""
from __future__ import annotations

import inspect

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from ..cards import PlaybackProviderCard
from .hdr import probe_hdr, transcoder
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


def _list_provider_files(card, path: str, force_refresh: bool) -> list[dict]:
    """Call the card's directory API while keeping older installed cards compatible.

    Card files are user-managed and may survive application image updates. Newer
    OpenList cards accept refresh=True; older installed copies may only expose
    list_files(path). Only pass the optional keyword when the method declares
    support for it, so a stale card cannot break directory browsing entirely.
    """
    if force_refresh:
        try:
            parameters = inspect.signature(card.list_files).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        supports_refresh = any(
            parameter.name == "refresh"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_refresh:
            return card.list_files(path, refresh=True)
        current_app.logger.debug(
            "播放卡片 %s 尚不支持强制刷新参数，使用兼容目录读取接口",
            card.card_id,
        )
    return card.list_files(path)


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
        items = _list_provider_files(card, path, force_refresh)
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
    # Automatic HDR -> SDR conversion is opportunistic. If FFmpeg/filters are
    # unavailable or the server is at its transcode limit, preserve direct playback.
    hdr_detected = None
    tone_mapped = False
    hdr_mode = __import__("os").environ.get("MOVIESYNC_HDR_TONEMAP", "auto").strip().lower()
    if hdr_mode not in {"off", "false", "0", "direct"}:
        hdr_detected = probe_hdr(url)
        if hdr_detected is True:
            job_id = transcoder.start(url)
            if job_id and transcoder.ready(job_id, wait_seconds=6):
                url = f"/api/playback/hdr/{job_id}/index.m3u8"
                tone_mapped = True
            elif job_id:
                transcoder.discard(job_id)

    # Subtitle metadata is optional: providers that expose tracks may return
    # a list of {src, srclang, label, kind, default} entries. Existing providers
    # remain compatible and simply produce an empty list.
    subtitles = result.get("subtitles", [])
    valid_subtitles = []
    if isinstance(subtitles, list):
        for item in subtitles:
            if not isinstance(item, dict):
                continue
            subtitle_url = str(item.get("src") or "").strip()
            if not subtitle_url.startswith(("http://", "https://")):
                continue
            srclang = str(item.get("srclang") or "und")[:16]
            label = str(item.get("label") or srclang)[:80]
            kind = "captions" if item.get("kind") == "captions" else "subtitles"
            valid_subtitles.append({
                "src": subtitle_url,
                "srclang": srclang,
                "label": label,
                "kind": kind,
                "default": item.get("default") is True,
            })

    return jsonify({
        "success": True,
        "name": result.get("name") or path.rsplit("/", 1)[-1],
        "url": url,
        "mime_type": "application/vnd.apple.mpegurl" if tone_mapped else (result.get("mime_type") or ""),
        "hdr_detected": hdr_detected,
        "tone_mapped": tone_mapped,
        "subtitles": valid_subtitles,
    })


@playback_api.get("/hdr/<job_id>/<filename>")
def playback_hdr_stream(job_id: str, filename: str):
    """Serve only generated HLS playlists/segments for an active conversion job."""
    path = transcoder.file_path(job_id, filename)
    if path is None:
        return jsonify({"success": False, "message": "转码片段不存在或已过期"}), 404
    mimetype = "application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/mp2t"
    response = send_file(path, mimetype=mimetype, conditional=False, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@playback_api.post("/remove")
@require_csrf
def playback_remove():
    """Remove one explicitly confirmed item through a provider that supports deletion."""
    card, error = _provider()
    if card is None:
        return jsonify({"success": False, "message": error}), 400

    supports_items = (
        "playback.delete_items" in card.capabilities
        and callable(getattr(card, "delete_item", None))
    )
    supports_legacy_files = (
        "playback.delete_files" in card.capabilities
        and callable(getattr(card, "delete_file", None))
    )
    if not (supports_items or supports_legacy_files):
        return jsonify({
            "success": False,
            "message": "当前播放卡片不支持删除文件或文件夹",
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
        return jsonify({"success": False, "message": "缺少待删除路径"}), 400
    is_dir = data.get("is_dir", False)
    if not isinstance(is_dir, bool):
        return jsonify({"success": False, "message": "目标类型无效"}), 400
    if is_dir and not supports_items:
        return jsonify({
            "success": False,
            "message": "当前播放卡片不支持删除文件夹",
        }), 501

    try:
        deleted = card.delete_item(path, expected_is_dir=is_dir) if supports_items else card.delete_file(path)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)[:240]}), 400
    except RuntimeError as exc:
        current_app.logger.warning("播放卡片删除失败: %s", str(exc)[:240])
        return jsonify({"success": False, "message": str(exc)[:240]}), 502
    except Exception:
        current_app.logger.exception("OpenList 文件删除异常")
        return jsonify({
            "success": False,
            "message": "删除失败，请检查播放卡片服务状态、权限和服务日志",
        }), 502

    return jsonify({
        "success": True,
        "message": ("文件夹及其内容已删除" if is_dir else "文件已删除"),
        "path": deleted.get("path", path) if isinstance(deleted, dict) else path,
    })
