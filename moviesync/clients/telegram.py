"""Telegram 公开频道检索客户端。

用途：检查频道可用性、检索频道消息，并从消息文本/链接中提取夸克分享短码。
维护说明：频道 ID 会先规范化；检索结果只返回频道、分享 ID 等必要信息，不保存上游敏感内容。
"""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from .http import HttpClient

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{2,64}$")
QUARK_RE = re.compile(r"(?:https?://)?(?:pan\.)?quark\.cn/s/([A-Za-z0-9]{1,128})(?![A-Za-z0-9])", re.IGNORECASE)

MAX_SEARCH_MESSAGES = 100
MAX_SHARES_PER_CHANNEL = 50
MAX_CHANNEL_PAGES = 6
TG_HEADERS = {"User-Agent": "Mozilla/5.0"}


def normalize_channel_id(value: object) -> str:
    channel_id = str(value or "").strip().lstrip("@")
    return channel_id if CHANNEL_RE.fullmatch(channel_id) else ""


class TelegramClient:
    def __init__(self):
        self.http = HttpClient(TG_HEADERS)

    def check_channel_detail(self, channel: object) -> dict:
        channel_id = normalize_channel_id(
            channel.get("id", "") if isinstance(channel, dict) else channel
        )
        if not channel_id:
            return {
                "status": "unavailable",
                "message": "频道 ID 格式无效",
            }

        try:
            response = self.http.session.get(
                f"https://t.me/s/{quote(channel_id)}",
                timeout=4,
            )
        except Exception as exc:
            return {
                "status": "unavailable",
                "message": f"连接失败：{type(exc).__name__}",
            }

        if response.status_code == 404:
            return {
                "status": "unavailable",
                "message": "频道不存在或无法访问",
            }
        if response.status_code == 403:
            return {
                "status": "unavailable",
                "message": "访问被拒绝，可能无法公开访问",
            }
        if response.status_code != 200:
            return {
                "status": "unavailable",
                "message": f"Telegram 返回 HTTP {response.status_code}",
            }
        if "tgme_channel_info" not in response.text:
            return {
                "status": "degraded",
                "message": "页面可访问，但未识别为公开频道",
            }

        return {
            "status": "healthy",
            "message": "频道连接正常",
        }

    def check_channel(self, channel: object) -> bool:
        return self.check_channel_detail(channel)["status"] == "healthy"

    def search_channel(self, channel: object, title: str, scan_all: bool = False) -> list[dict]:
        """Search channel shares; retain the list-based compatibility API."""
        return self._search_channel(channel, title, scan_all=scan_all)

    def search_channel_updates(
        self,
        channel: object,
        title: str,
        after_post_id: int | None = None,
    ) -> dict:
        """Scan recent posts incrementally, stopping at the persisted post watermark."""
        return self._search_channel(
            channel,
            title,
            scan_all=True,
            after_post_id=after_post_id,
            include_scan_state=True,
        )

    def _search_channel(
        self,
        channel: object,
        title: str,
        scan_all: bool = False,
        after_post_id: int | None = None,
        include_scan_state: bool = False,
    ):
        if isinstance(channel, dict):
            channel_id = normalize_channel_id(channel.get("id"))
            channel_name = str(channel.get("name") or channel_id).strip()
        else:
            channel_id = normalize_channel_id(channel)
            channel_name = channel_id
        if not channel_id or not str(title or "").strip():
            return (
                {"items": [], "cursor": None, "complete": False}
                if include_scan_state
                else []
            )

        if after_post_id is not None:
            try:
                after_post_id = int(after_post_id)
            except (TypeError, ValueError, OverflowError):
                after_post_id = None

        title = str(title).strip()
        simple_target = self._simplify(title)
        results: list[dict] = []
        seen_shares: set[str] = set()
        before: str | None = None
        latest_post_id: int | None = None
        reached_cursor = False
        scan_complete = True

        def finish():
            if not include_scan_state:
                return results
            cursor_safe = scan_complete and (
                after_post_id is None or reached_cursor
            )
            return {
                "items": results,
                "cursor": latest_post_id if cursor_safe else None,
                "complete": cursor_safe,
            }

        for page_index in range(MAX_CHANNEL_PAGES):
            url = f"https://t.me/s/{quote(channel_id)}"
            params = []
            if not scan_all and page_index == 0:
                params.append(("q", title))
            if before:
                params.append(("before", before))
            if params:
                query = "&".join(
                    f"{key}={quote(value)}"
                    for key, value in params
                )
                url += f"?{query}"

            try:
                response = self.http.session.get(url, timeout=8)
            except Exception:
                scan_complete = False
                return finish()

            if response.status_code != 200:
                scan_complete = False
                return finish()

            soup = BeautifulSoup(response.text, "html.parser")
            messages = soup.select("div.tgme_widget_message")
            if not messages and not scan_all and page_index == 0:
                # Telegram 的频道搜索参数在部分公开频道/缓存页面上可能失效；
                # 普通资源搜索首屏无结果时回退到频道页，再由本地标题匹配筛选。
                try:
                    response = self.http.session.get(
                        f"https://t.me/s/{quote(channel_id)}",
                        timeout=8,
                    )
                except Exception:
                    scan_complete = False
                    return finish()
                if response.status_code != 200:
                    scan_complete = False
                    return finish()
                soup = BeautifulSoup(response.text, "html.parser")
                messages = soup.select("div.tgme_widget_message")
            if not messages:
                if after_post_id is not None:
                    scan_complete = False
                return finish()

            oldest_post_id = None
            for message in messages:
                data_post = str(message.get("data-post") or "")
                match = re.search(r"/(\d+)$", data_post)
                if match:
                    post_id = int(match.group(1))
                    latest_post_id = (
                        post_id
                        if latest_post_id is None
                        else max(latest_post_id, post_id)
                    )
                    if after_post_id is not None and post_id <= after_post_id:
                        reached_cursor = True
                        break
                    oldest_post_id = (
                        post_id
                        if oldest_post_id is None
                        else min(oldest_post_id, post_id)
                    )

                text_node = message.select_one("div.tgme_widget_message_text")
                if not text_node:
                    continue

                plain_text = text_node.get_text(" ", strip=True)
                simple_plain = self._simplify(plain_text)
                if (
                    title not in plain_text
                    and (not simple_target or simple_target not in simple_plain)
                ):
                    continue

                links = [
                    anchor.get("href", "")
                    for anchor in message.select("a[href]")
                ]
                raw_sources = links + [plain_text]
                for raw in raw_sources:
                    for pwd_id in QUARK_RE.findall(raw or ""):
                        if pwd_id in seen_shares:
                            continue
                        seen_shares.add(pwd_id)
                        results.append(
                            {
                                "channel": channel_name,
                                "channel_id": channel_id,
                                "pwd_id": pwd_id,
                            }
                        )
                        if (
                            len(results) >= MAX_SHARES_PER_CHANNEL
                            and not include_scan_state
                        ):
                            return finish()

            if reached_cursor:
                break

            if oldest_post_id is None:
                return finish()

            next_before = str(oldest_post_id)
            if next_before == before:
                return finish()
            before = next_before

        # Reaching the hard page limit is not proof that the history was fully
        # scanned. Do not persist a watermark for an incomplete initial scan:
        # that could skip older shares on channels with deep histories.
        scan_complete = False
        return finish()

    @staticmethod
    def _simplify(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", value or "")
