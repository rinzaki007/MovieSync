"""MacCMS on-demand search API used by the movie detail drawer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, request

from card_templates.vod_source_manager import _normalize_sources, _public_http_url

maccms_api = Blueprint("maccms_api", __name__, url_prefix="/api/maccms")
_TIMEOUT = (3.05, 8)
_MAX_RESULTS_PER_SOURCE = 8


def _split_values(value: object, separator: str = "$$$") -> list[str]:
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(separator) if part.strip()]


def _parse_play_sources(play_from: object, play_url: object) -> list[dict[str, Any]]:
    """Normalize common MacCMS vod_play_from/vod_play_url fields without guessing URLs."""
    lines = _split_values(play_from)
    playlists = _split_values(play_url)
    result: list[dict[str, Any]] = []
    for index, playlist in enumerate(playlists):
        line_name = lines[index] if index < len(lines) else f"线路 {index + 1}"
        episodes: list[dict[str, str]] = []
        for entry in playlist.split("#"):
            if "$" in entry:
                name, url = entry.split("$", 1)
            else:
                name, url = f"播放项 {len(episodes) + 1}", entry
            name = name.strip()[:160]
            url = url.strip()
            if not name or not url or not url.startswith(("http://", "https://")):
                continue
            episodes.append({"name": name, "url": url})
        if episodes:
            result.append({"name": line_name[:100], "episodes": episodes[:100]})
    return result


def _search_one_source(source: dict[str, Any], title: str) -> dict[str, Any]:
    try:
        url = _public_http_url(source["url"], resolve_dns=True)
        response = requests.get(
            url,
            params={"ac": "list", "wd": title, "pg": 1},
            timeout=_TIMEOUT,
            allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "MovieSync-MacCMS-Search/1.0"},
        )
        if 300 <= response.status_code < 400:
            return {"source_id": source["id"], "source_name": source["name"], "items": [],
                    "error": "接口返回重定向，请填写最终 API 地址"}
        if response.status_code != 200:
            return {"source_id": source["id"], "source_name": source["name"], "items": [],
                    "error": f"接口返回 HTTP {response.status_code}"}
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON 顶层不是对象")
        rows = payload.get("list")
        if not isinstance(rows, list):
            data = payload.get("data")
            rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {"source_id": source["id"], "source_name": source["name"], "items": [],
                    "error": "响应中没有可识别的影片列表"}
        items = []
        for row in rows[:_MAX_RESULTS_PER_SOURCE]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("vod_name") or row.get("title") or "").strip()
            vod_id = str(row.get("vod_id") or row.get("id") or "").strip()
            if not name or not vod_id:
                continue
            items.append({
                "id": vod_id[:160],
                "title": name[:240],
                "year": str(row.get("vod_year") or "")[:20],
                "remarks": str(row.get("vod_remarks") or "")[:160],
                "category": str(row.get("type_name") or "")[:100],
                "poster": str(row.get("vod_pic") or "")[:2048],
                "play_sources": _parse_play_sources(row.get("vod_play_from"), row.get("vod_play_url")),
            })
        return {"source_id": source["id"], "source_name": source["name"], "items": items, "error": ""}
    except requests.Timeout:
        message = "连接超时"
    except requests.RequestException:
        message = "请求失败，请检查接口地址或站点状态"
    except (ValueError, TypeError):
        message = "接口响应格式无效"
    except Exception:
        message = "搜索失败，请检查接口地址和服务日志"
    return {"source_id": source.get("id", ""), "source_name": source.get("name", "未知站点"),
            "items": [], "error": message}


@maccms_api.get("/search")
def search_movies():
    title = str(request.args.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "message": "请输入影片名称", "results": []}), 400
    if len(title) > 100:
        return jsonify({"success": False, "message": "影片名称不能超过 100 个字符", "results": []}), 400

    services = current_app.extensions["moviesync"]
    card = services["card_registry"].get("maccms")
    saved_cards = services["config"].load().get("cards", {})
    saved = saved_cards.get("maccms", {}) if isinstance(saved_cards, dict) else {}
    if card is None or not isinstance(saved, dict) or saved.get("enabled", True) is False:
        return jsonify({"success": False, "message": "影视采集源卡片尚未安装或已停用", "results": []}), 400
    config = saved.get("config", {})
    if not isinstance(config, dict):
        config = {}
    try:
        sources = [item for item in _normalize_sources(config.get("sources", [])) if item["enabled"]]
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc), "results": []}), 400
    if not sources:
        return jsonify({"success": False, "message": "没有启用的影视采集源，请先到设置中启用站点", "results": []}), 400

    results = []
    with ThreadPoolExecutor(max_workers=min(6, len(sources))) as pool:
        futures = [pool.submit(_search_one_source, source, title) for source in sources]
        for future in as_completed(futures):
            results.append(future.result())
    order = {source["id"]: index for index, source in enumerate(sources)}
    results.sort(key=lambda item: order.get(item["source_id"], len(sources)))
    found = sum(len(item["items"]) for item in results)
    return jsonify({
        "success": True,
        "message": f"已查询 {len(sources)} 个启用站点，找到 {found} 条候选影片",
        "results": results,
    })



@maccms_api.get("/detail")
def movie_detail():
    source_id = str(request.args.get("source_id") or "").strip()
    vod_id = str(request.args.get("vod_id") or "").strip()
    if not source_id or len(source_id) > 64 or not vod_id or len(vod_id) > 160:
        return jsonify({"success": False, "message": "采集源或影片编号无效"}), 400

    services = current_app.extensions["moviesync"]
    card = services["card_registry"].get("maccms")
    saved_cards = services["config"].load().get("cards", {})
    saved = saved_cards.get("maccms", {}) if isinstance(saved_cards, dict) else {}
    if card is None or not isinstance(saved, dict) or saved.get("enabled", True) is False:
        return jsonify({"success": False, "message": "影视采集源卡片尚未安装或已停用"}), 400
    config = saved.get("config", {})
    try:
        sources = [item for item in _normalize_sources(config.get("sources", []))
                   if item["enabled"] and item["id"] == source_id]
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify({"success": False, "message": str(exc), "detail": None}), 400
    if not sources:
        return jsonify({"success": False, "message": "这个采集源未启用或不存在"}), 404

    source = sources[0]
    try:
        url = _public_http_url(source["url"], resolve_dns=True)
        response = requests.get(
            url, params={"ac": "detail", "ids": vod_id}, timeout=_TIMEOUT,
            allow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "MovieSync-MacCMS-Search/1.0"},
        )
        if 300 <= response.status_code < 400:
            return jsonify({"success": False, "message": "接口返回重定向，请填写最终 API 地址", "detail": None}), 502
        if response.status_code != 200:
            return jsonify({"success": False, "message": f"接口返回 HTTP {response.status_code}", "detail": None}), 502
        payload = response.json()
        rows = payload.get("list") if isinstance(payload, dict) else None
        if not isinstance(rows, list) and isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            rows = payload["data"].get("list")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return jsonify({"success": False, "message": "接口没有返回影片详情", "detail": None}), 502
        row = rows[0]
        detail = {
            "id": str(row.get("vod_id") or row.get("id") or vod_id)[:160],
            "title": str(row.get("vod_name") or row.get("title") or "")[:240],
            "year": str(row.get("vod_year") or "")[:20],
            "remarks": str(row.get("vod_remarks") or "")[:160],
            "poster": str(row.get("vod_pic") or "")[:2048],
            "play_sources": _parse_play_sources(row.get("vod_play_from"), row.get("vod_play_url")),
        }
        if not detail["play_sources"]:
            return jsonify({
                "success": False,
                "message": "该影片没有识别到可用的 HTTP/HTTPS 播放线路",
                "detail": detail,
            }), 422
        return jsonify({"success": True, "source_name": source["name"], "detail": detail})
    except requests.Timeout:
        message = "连接超时"
    except requests.RequestException:
        message = "请求失败，请检查接口地址或站点状态"
    except (ValueError, TypeError):
        message = "接口响应格式无效"
    except Exception:
        message = "读取影片详情失败，请检查接口地址和服务日志"
    return jsonify({"success": False, "message": message, "detail": None}), 502

def register_maccms_api(app) -> None:
    app.register_blueprint(maccms_api)
