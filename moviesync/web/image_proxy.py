"""Metadata image proxy with per-hop allowlist validation."""
from __future__ import annotations

import ipaddress
from urllib.parse import urljoin, urlparse

from flask import Blueprint, Response, current_app, request

proxy_api = Blueprint("image_proxy", __name__, url_prefix="/api")


def _services():
    return current_app.extensions["moviesync"]


def _metadata_image_policy(target: str, metadata_cards) -> dict[str, str] | None:
    """Resolve image hosts from loaded metadata-card manifests, not provider IDs."""
    try:
        parsed = urlparse(target)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        return None
    host = host.lower()
    try:
        import ipaddress
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return None
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
        # Disable automatic redirects: every hop must pass the same host policy.
        for redirect_count in range(4):
            with _services()["http"].session.get(
                target, headers=headers, timeout=(3, 8), stream=True, allow_redirects=False
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "").strip()
                    if not location or redirect_count >= 3:
                        return Response("Image redirect rejected", status=502)
                    target = urljoin(target, location)
                    policy = _metadata_image_policy(target, metadata_cards)
                    if policy is None:
                        _services()["logger"].warning("元数据海报重定向目标不在白名单内，已拒绝")
                        return Response("Image redirect host not allowed", status=403)
                    if policy["referer"]:
                        headers["Referer"] = policy["referer"]
                    else:
                        headers.pop("Referer", None)
                    continue
                if response.status_code != 200:
                    host = urlparse(target).hostname or "unknown"
                    _services()["logger"].warning("元数据海报上游请求失败: host=%s status=%s", host, response.status_code)
                    return Response(
                        f"Image upstream HTTP {response.status_code}", status=502,
                        headers={"X-MovieSync-Image-Error": f"upstream_http_{response.status_code}"},
                    )
                content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0].strip().lower()
                allowed_image_types = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
                if content_type not in allowed_image_types:
                    host = urlparse(target).hostname or "unknown"
                    _services()["logger"].warning(
                        "元数据海报上游返回非图片内容: host=%s content_type=%s status=%s",
                        host, content_type or "unknown", response.status_code,
                    )
                    return Response(
                        f"Upstream content is not an image ({content_type or 'unknown'})", status=415,
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
                    b"".join(chunks), content_type=content_type,
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        return Response("Too many image redirects", status=502)
    except Exception:
        host = urlparse(target).hostname or "unknown"
        _services()["logger"].warning("代理元数据海报请求异常: host=%s", host, exc_info=True)
        return Response(
            "Image proxy request failed", status=502,
            headers={"X-MovieSync-Image-Error": "proxy_request_failed"},
        )
