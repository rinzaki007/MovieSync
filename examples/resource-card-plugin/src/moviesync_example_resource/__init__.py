"""Minimal third-party MovieSync resource-source plugin example.

This example intentionally returns no share links. It demonstrates discovery,
configuration and health checks without pretending to be a real resource provider.
"""

from moviesync.cards import CardManifest, ResourceSourceCard


class ExampleResourceSource(ResourceSourceCard):
    manifest = CardManifest(
        id="example-resource",
        name="示例资源来源（仅演示）",
        type="resource_source",
        version="0.1.0",
        description="用于验证 MovieSync 资源卡片接入流程，不会搜索真实资源。",
        capabilities=("resource.search", "resource.health_check"),
    )

    def search(self, movie, config):
        """A real plugin would query its provider and return normalized resources."""
        return []

    def check(self, config):
        return {
            "status": "healthy",
            "message": "示例资源卡片已成功加载；此示例不连接外部资源服务。",
        }


def create_card(context):
    """Entry-point factory required by MovieSync."""
    return ExampleResourceSource()
