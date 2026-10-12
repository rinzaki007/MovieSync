from card_templates.douban import DoubanMetadataCard
from moviesync.cards import MetadataProviderCard


class FakeDouban:
    def get_movies(self, tag, sort_type):
        return [{"title": tag, "sort": sort_type}]

    def search(self, query):
        return [{"title": query}]


def test_douban_metadata_card_uses_standard_interface():
    card = DoubanMetadataCard(FakeDouban())

    assert isinstance(card, MetadataProviderCard)
    assert card.card_id == "douban"
    assert "metadata.list" in card.capabilities
    assert card.list_movies("电影", "U") == [
        {"title": "电影", "sort": "U", "provider_id": "douban", "provider_name": "豆瓣"}
    ]
    assert card.search("测试") == [
        {"title": "测试", "provider_id": "douban", "provider_name": "豆瓣"}
    ]
    assert card.get_detail("123") is None


def test_metadata_manager_falls_back_when_one_card_raises_or_returns_empty():
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services.metadata import MetadataProviderManager

    class BrokenProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="broken-provider",
            name="Broken",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search"),
        )

        def list_movies(self, tag, sort_type):
            raise RuntimeError("upstream unavailable")

        def search(self, query):
            return []

        def get_detail(self, item_id):
            return None

    class HealthyProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="healthy-provider",
            name="Healthy",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search", "metadata.detail"),
        )

        def list_movies(self, tag, sort_type):
            return [{"title": "healthy"}]

        def search(self, query):
            return [{"title": query}]

        def get_detail(self, item_id):
            return {"id": item_id}

    registry = CardRegistry()
    registry.register(BrokenProvider())
    registry.register(HealthyProvider())
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata"))

    assert manager.list_movies() == [{"title": "healthy"}]
    assert manager.search("测试") == [{"title": "测试"}]
    assert manager.get_detail("123") == {"id": "123"}


def test_metadata_manager_keeps_explicit_provider_selection_and_validates_results():
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services.metadata import MetadataProviderManager

    class MalformedProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="malformed-provider",
            name="Malformed",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search", "metadata.detail"),
        )

        def list_movies(self, tag, sort_type):
            return {"title": "not-a-list"}

        def search(self, query):
            raise RuntimeError("secret cookie")

        def get_detail(self, item_id):
            return ["not", "a", "detail"]

    class HealthyProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="healthy-provider",
            name="Healthy",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search", "metadata.detail"),
        )

        def list_movies(self, tag, sort_type):
            return [{"title": "healthy"}]

        def search(self, query):
            return [{"title": query}]

        def get_detail(self, item_id):
            return {"id": item_id}

    registry = CardRegistry()
    registry.register(MalformedProvider())
    registry.register(HealthyProvider())
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata"))

    # An explicitly selected provider is authoritative: never silently swap providers.
    assert manager.list_movies(provider_id="malformed-provider") == []
    assert manager.search("测试", provider_id="malformed-provider") == []
    assert manager.get_detail("123", provider_id="malformed-provider") is None

    # Without explicit selection, invalid result contracts are isolated and fallback works.
    assert manager.list_movies() == [{"title": "healthy"}]
    assert manager.search("测试") == [{"title": "测试"}]
    assert manager.get_detail("123") == {"id": "123"}


def test_metadata_manager_respects_card_capabilities():
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services.metadata import MetadataProviderManager

    class ListOnlyProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="list-only-provider",
            name="List Only",
            type="metadata_provider",
            capabilities=("metadata.list",),
        )

        def list_movies(self, tag, sort_type):
            return [{"title": tag}]

        def search(self, query):
            raise AssertionError("unsupported capability must not be called")

        def get_detail(self, item_id):
            raise AssertionError("unsupported capability must not be called")

    registry = CardRegistry()
    registry.register(ListOnlyProvider())
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata"))

    assert manager.list_movies() == [{"title": "电影"}]
    assert manager.search("测试") == []
    assert manager.get_detail("123") is None


def test_metadata_manager_loads_installed_card_plugins_and_isolates_failures(monkeypatch):
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services import metadata as metadata_module
    from moviesync.services.metadata import MetadataProviderManager

    received_context = {}

    class PluginProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="plugin-metadata",
            name="Plugin Metadata",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search"),
        )

        def list_movies(self, tag, sort_type):
            return [{"title": "plugin"}]

        def search(self, query):
            return [{"title": query}]

        def get_detail(self, item_id):
            return None

    class EntryPoint:
        def __init__(self, name, factory):
            self.name = name
            self.factory = factory

        def load(self):
            return self.factory

    def factory(context):
        received_context.update(context)
        return PluginProvider()

    monkeypatch.setattr(
        metadata_module,
        "entry_points",
        lambda group: (
            [
                EntryPoint("good-provider", factory),
                EntryPoint("bad-provider", lambda context: object()),
            ]
            if group == "moviesync.metadata_providers"
            else [],
        )[0],
    )

    registry = CardRegistry()
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata.plugins"))
    context = {"config_store": object(), "logger": logging.getLogger("plugin")}
    assert manager.load_plugins(context) == ["plugin-metadata"]
    assert registry.get("plugin-metadata").card_id == "plugin-metadata"
    assert received_context == context
    assert manager.list_movies() == [{"title": "plugin"}]


def test_metadata_plugin_factory_closes_card_when_registration_fails(monkeypatch):
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services import metadata as module
    from moviesync.services.metadata import MetadataProviderManager

    class ExistingProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="duplicate-provider",
            name="Existing",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search", "metadata.detail"),
        )

        def list_movies(self, tag, sort_type):
            return []

        def search(self, query):
            return []

        def get_detail(self, item_id):
            return None

    class CandidateProvider(ExistingProvider):
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    candidate = CandidateProvider()

    class EntryPoint:
        name = "duplicate-provider-plugin"

        def load(self):
            return lambda context: candidate

    monkeypatch.setattr(module, "entry_points", lambda group: [EntryPoint()])
    registry = CardRegistry()
    registry.register(ExistingProvider())
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata.cleanup"))

    assert manager.load_plugins() == []
    assert candidate.closed is True
    assert registry.get("duplicate-provider") is not candidate



def test_metadata_image_policy_uses_card_manifest_hosts_not_provider_ids():
    from types import SimpleNamespace

    from moviesync.web.routes import _metadata_image_policy

    card = SimpleNamespace(
        manifest=SimpleNamespace(
            image_hosts=("example-cdn.test",),
            image_referer="https://metadata.example.test/",
        )
    )

    assert _metadata_image_policy("https://img.example-cdn.test/poster.jpg", [card]) == {
        "referer": "https://metadata.example.test/"
    }
    douban_card = SimpleNamespace(
        manifest=SimpleNamespace(
            image_hosts=("doubanio.com", "douban.com"),
            image_referer="https://movie.douban.com/",
        )
    )
    assert _metadata_image_policy("https://img1.doubanio.com/poster.jpg", [douban_card]) == {
        "referer": "https://movie.douban.com/"
    }
    assert _metadata_image_policy("https://img3.douban.com/poster.jpg", [douban_card]) == {
        "referer": "https://movie.douban.com/"
    }
    assert _metadata_image_policy("http://img.example-cdn.test/poster.jpg", [card]) is None
    assert _metadata_image_policy("https://example-cdn.test:8443/poster.jpg", [card]) is None
    assert _metadata_image_policy("https://evil.test/poster.jpg", [card]) is None



def test_metadata_search_exposes_generic_route_and_keeps_legacy_alias(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    routes = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/api/search-metadata" in routes
    assert "/api/search-douban" in routes


def test_metadata_routes_forward_explicit_provider_selection(tmp_path):
    from moviesync.app import create_app

    class RecordingMetadata:
        def __init__(self):
            self.calls = []

        def search(self, query, provider_id=None, category=None):
            self.calls.append(("search", query, provider_id, category))
            return [{"title": query, "provider_id": provider_id}]

        def list_movies(self, tag, sort_type, provider_id=None):
            self.calls.append(("list", tag, sort_type, provider_id))
            return [{"title": tag, "provider_id": provider_id}]

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    app.extensions["moviesync"]["auth"].setup("admin", "test-password-123")
    metadata = RecordingMetadata()
    app.extensions["moviesync"]["metadata"] = metadata
    client = app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["csrf_token"] = "test-csrf"

    search = client.get("/api/search-metadata?q=Arrival&provider_id=tmdb")
    listing = client.get("/api/get-movies?tag=电影&sort=U&provider_id=douban")

    assert search.status_code == 200
    assert search.get_json()["movies"] == [{
        "title": "Arrival",
        "provider_id": "tmdb",
        "tag": "电影",
        "media_type": "movie",
    }]
    assert listing.status_code == 200
    assert listing.get_json()["movies"] == [{
        "title": "电影",
        "provider_id": "douban",
        "tag": "电影",
        "media_type": "movie",
    }]
    assert metadata.calls == [
        ("search", "Arrival", "tmdb", "电影"),
        ("list", "电影", "U", "douban"),
    ]



def test_douban_cover_urls_are_normalized_for_proxy():
    from moviesync.clients.douban import DoubanClient

    normalize = DoubanClient._normalize_cover_url
    assert normalize("//img3.douban.com/poster.jpg") == "https://img3.douban.com/poster.jpg"
    assert normalize("http://img2.doubanio.com/poster.jpg") == "https://img2.doubanio.com/poster.jpg"
    assert normalize("https://img2.doubanio.com/poster.jpg") == "https://img2.doubanio.com/poster.jpg"
    # Do not rewrite unrelated hosts.
    assert normalize("http://example.com/poster.jpg") == "http://example.com/poster.jpg"


def test_douban_normalizer_preserves_string_shaped_cover_fields():
    from moviesync.clients.douban import DoubanClient

    item_cover = DoubanClient._normalize_item({
        "id": "123",
        "title": "电影 A",
        "cover": "//img3.doubanio.com/view/photo/s_ratio_poster/public/p123.jpg",
    })
    target_cover = DoubanClient._normalize_item({
        "target": {
            "id": "456",
            "title": "电影 B",
            "cover": "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p456.jpg",
        }
    })

    assert item_cover["cover"] == "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p123.jpg"
    assert target_cover["cover"] == "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p456.jpg"



def test_douban_normalizer_extracts_nested_poster_url_shapes():
    from moviesync.clients.douban import DoubanClient

    normalize = DoubanClient._normalize_item

    nested_pic = normalize({
        "id": "123",
        "title": "嵌套 pic 海报",
        "pic": {
            "normal": {
                "url": "//img3.doubanio.com/view/photo/s_ratio_poster/public/p123.webp",
            },
        },
    })
    nested_cover = normalize({
        "target": {
            "id": "456",
            "title": "嵌套 cover 海报",
            "cover": {
                "large": {
                    "url": "http://img1.doubanio.com/view/photo/s_ratio_poster/public/p456.jpg",
                },
            },
        },
    })
    fallback_cover = normalize({
        "id": "789",
        "title": "回退 img 海报",
        "pic": {"normal": {"not_a_url": True}},
        "img": {"url": "https://img2.doubanio.com/view/photo/s_ratio_poster/public/p789.jpg"},
    })

    assert nested_pic["cover"] == "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p123.webp"
    assert nested_cover["cover"] == "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p456.jpg"
    assert fallback_cover["cover"] == "https://img2.doubanio.com/view/photo/s_ratio_poster/public/p789.jpg"


def test_douban_cover_normalizer_rejects_non_string_values():
    from moviesync.clients.douban import DoubanClient

    assert DoubanClient._normalize_cover_url({"url": "https://img1.doubanio.com/poster.jpg"}) == ""
    assert DoubanClient._extract_cover_url({"url": {"normal": "https://img1.doubanio.com/poster.jpg"}}) == "https://img1.doubanio.com/poster.jpg"



def test_metadata_manager_config_load_failure_does_not_break_home_or_search():
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services.metadata import MetadataProviderManager

    class ConfigStoreThatFails:
        def load(self):
            raise OSError("simulated config read failure")

    class HealthyProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="healthy-after-config-error",
            name="Healthy After Config Error",
            type="metadata_provider",
            capabilities=("metadata.list", "metadata.search"),
        )

        def list_movies(self, tag, sort_type):
            return [{"title": tag}]

        def search(self, query):
            return [{"title": query}]

        def get_detail(self, item_id):
            return None

    registry = CardRegistry()
    registry.register(HealthyProvider())
    manager = MetadataProviderManager(
        registry,
        logging.getLogger("test.metadata.config-failure"),
        ConfigStoreThatFails(),
    )

    # If config storage is temporarily unreadable, default-enabled cards remain
    # usable; a failed config read must not escape into the home/search routes.
    assert manager.list_movies() == [{"title": "电影"}]
    assert manager.search("测试") == [{"title": "测试"}]



def test_metadata_manager_uses_category_aware_search_when_provider_supports_it():
    import logging

    from moviesync.cards import CardManifest, CardRegistry, MetadataProviderCard
    from moviesync.services.metadata import MetadataProviderManager

    class CategoryProvider(MetadataProviderCard):
        manifest = CardManifest(
            id="category-provider",
            name="Category Provider",
            type="metadata_provider",
            capabilities=("metadata.search",),
        )

        def search(self, query):
            return [{"title": "mixed result"}]

        def search_by_category(self, query, category):
            return [{"title": query, "tag": category, "media_type": "movie"}]

        def list_movies(self, tag, sort_type):
            return []

        def get_detail(self, item_id):
            return None

    registry = CardRegistry()
    registry.register(CategoryProvider())
    manager = MetadataProviderManager(registry, logging.getLogger("test.metadata"))

    assert manager.search("潜伏6", category="电影") == [
        {"title": "潜伏6", "tag": "电影", "media_type": "movie"}
    ]
    assert manager.search("潜伏6") == [{"title": "mixed result"}]
