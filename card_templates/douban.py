"""Standalone Douban metadata-provider card shipped as one Python file."""
from __future__ import annotations

from typing import Any

from moviesync.cards import CardManifest, MetadataProviderCard
from moviesync.clients.douban import DoubanClient


class DoubanMetadataCard(MetadataProviderCard):
    manifest = CardManifest(
        id="douban",
        name="Douban",
        version="1.1.0",
        type="metadata_provider",
        description="豆瓣影视元数据与搜索",
        capabilities=("metadata.list", "metadata.search", "metadata.detail"),
        image_hosts=("doubanio.com", "douban.com"),
        image_referer="https://movie.douban.com/",
    )

    def __init__(self, client: DoubanClient):
        self.client = client

    @staticmethod
    def _annotate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**item, "provider_id": "douban", "provider_name": "豆瓣"}
            for item in items
            if isinstance(item, dict)
        ]

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def list_movies(self, tag: str, sort_type: str) -> list[dict[str, Any]]:
        return self._annotate(self.client.get_movies(tag, sort_type))

    def search(self, query: str) -> list[dict[str, Any]]:
        return self._annotate(self.client.search(query))

    def get_detail(self, item_id: str) -> dict[str, Any] | None:
        detail = self.client.get_detail(item_id)
        if not isinstance(detail, dict):
            return None
        return {**detail, "provider_id": "douban", "provider_name": "豆瓣"}


def create_card(context):
    """Create an independent Douban provider; the platform injects no provider client."""
    return DoubanMetadataCard(DoubanClient())
