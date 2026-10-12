from types import SimpleNamespace

from card_templates.tmdb import TMDBMetadataCard
from moviesync.cards import MetadataProviderCard


class FakeConfigStore:
    def __init__(self, config=None):
        self.config = config or {}

    def get_card_config(self, card_id):
        assert card_id == "tmdb"
        return dict(self.config)


class FakeHttpClient:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=self.status_code), self.payload


def test_tmdb_card_is_a_standalone_metadata_provider_with_manifest_config():
    card = TMDBMetadataCard(FakeConfigStore())

    assert isinstance(card, MetadataProviderCard)
    assert card.card_id == "tmdb"
    assert card.manifest.type == "metadata_provider"
    assert "metadata.search" in card.capabilities
    assert "metadata.detail" in card.capabilities
    assert card.manifest.image_hosts == ("image.tmdb.org",)
    assert card.manifest.config_fields[0]["key"] == "api_read_access_token"
    assert card.manifest.config_fields[0]["required"] is False
    assert card.manifest.config_fields[1]["key"] == "api_key"
    assert card.is_configured({"api_read_access_token": "token"}) is True
    assert card.is_configured({"api_key": "v3-key"}) is True
    assert card.is_configured({}) is False


def test_tmdb_search_uses_bearer_auth_and_normalizes_results():
    payload = {
        "results": [
            {
                "id": 27205,
                "media_type": "movie",
                "title": "盗梦空间",
                "original_title": "Inception",
                "poster_path": "/poster.jpg",
                "backdrop_path": "/backdrop.jpg",
                "release_date": "2010-07-15",
                "vote_average": 8.4,
                "vote_count": 30000,
                "overview": "梦境中的任务",
                "genre_ids": [878, 53],
            },
            {"id": 999, "media_type": "person", "name": "Ignored"},
        ]
    }
    http = FakeHttpClient(payload)
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "private-token", "language": "zh-CN"}),
        http,
    )

    result = card.search("盗梦空间")

    assert len(result) == 1
    assert result[0]["id"] == "movie:27205"
    assert result[0]["tmdb_id"] == "27205"
    assert result[0]["media_type"] == "movie"
    assert result[0]["title"] == "盗梦空间"
    assert result[0]["original_title"] == "Inception"
    assert result[0]["cover"] == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert result[0]["backdrop"] == "https://image.tmdb.org/t/p/w1280/backdrop.jpg"
    assert result[0]["year"] == "2010"
    assert result[0]["rate"] == "8.4"
    assert result[0]["provider_id"] == "tmdb"
    assert result[0]["provider_name"] == "TMDB"
    assert http.calls[0][1] == "https://api.themoviedb.org/3/search/multi"
    assert http.calls[0][2]["headers"]["Authorization"] == "Bearer private-token"
    assert http.calls[0][2]["params"]["language"] == "zh-CN"
    assert "private-token" not in http.calls[0][1]


def test_tmdb_search_caches_results_and_does_not_request_without_token():
    empty_http = FakeHttpClient({"results": []})
    empty_card = TMDBMetadataCard(FakeConfigStore(), empty_http)
    assert empty_card.search("test") == []
    assert empty_http.calls == []

    http = FakeHttpClient({"results": [{"id": 1, "media_type": "tv", "name": "测试剧"}]})
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "private-token"}),
        http,
    )

    assert card.search("测试剧")[0]["media_type"] == "tv"
    assert card.search("测试剧")[0]["title"] == "测试剧"
    assert len(http.calls) == 1


def test_tmdb_list_maps_categories_and_rating_sort():
    http = FakeHttpClient({"results": [{"id": 2, "title": "测试电影", "release_date": "2024-01-01"}]})
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "token"}),
        http,
    )

    result = card.list_movies("电影", "R")

    assert result[0]["title"] == "测试电影"
    assert http.calls[0][1] == "https://api.themoviedb.org/3/discover/movie"
    assert http.calls[0][2]["params"]["sort_by"] == "vote_average.desc"
    assert http.calls[0][2]["params"]["vote_count.gte"] == "100"


def test_tmdb_details_return_external_ids_and_health_check_never_exposes_token():
    http = FakeHttpClient({
        "id": 123,
        "name": "测试剧",
        "first_air_date": "2022-05-01",
        "external_ids": {"imdb_id": "tt123"},
    })
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "secret-token"}),
        http,
    )

    detail = card.get_detail("tv:123")

    assert detail["id"] == "tv:123"
    assert detail["external_ids"] == {"imdb_id": "tt123"}
    assert card.check({}) == {
        "status": "unconfigured",
        "message": "请填写 API Read Access Token 或 API Key",
    }
    assert "secret-token" not in str(card.check({"api_read_access_token": "secret-token"}))


def test_about_page_contains_required_tmdb_attribution(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    app.extensions["moviesync"]["auth"].setup("admin", "test-password-123")
    client = app.test_client()
    with client.session_transaction() as session:
        session["logged_in"] = True

    response = client.get("/about")

    assert response.status_code == 200
    assert b"This product uses the TMDB API but is not endorsed or certified by TMDB." in response.data
    assert b"logos-attribution" in response.data
    assert b"blue_square_2-" in response.data


def test_tmdb_is_seeded_and_can_be_uninstalled_without_affecting_douban(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    plugin_path = tmp_path / "cards" / "tmdb.py"

    assert plugin_path.is_file()
    assert services["card_registry"].get("tmdb") is not None
    assert services["card_registry"].get("douban") is not None

    removed = services["file_card_plugins"].uninstall("tmdb.py")

    assert removed["card_id"] == "tmdb"
    assert services["card_registry"].get("tmdb") is None
    assert services["card_registry"].get("douban") is not None
    assert not plugin_path.exists()


def test_tmdb_api_key_auth_uses_query_parameter_without_bearer_header():
    http = FakeHttpClient({"images": {}})
    card = TMDBMetadataCard(
        FakeConfigStore({"api_key": "v3-api-key", "language": "zh-CN"}),
        http,
    )

    assert card.check({"api_key": "v3-api-key"}) == {
        "status": "healthy",
        "message": "TMDB API 连接正常",
    }
    kwargs = http.calls[0][2]
    assert kwargs["params"]["api_key"] == "v3-api-key"
    assert "Authorization" not in kwargs["headers"]
    assert "v3-api-key" not in http.calls[0][1]


def test_tmdb_health_check_reports_auth_status_without_exposing_credentials():
    http = FakeHttpClient(
        {"status_message": "Invalid API key"},
        status_code=401,
    )
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "secret-token"}),
        http,
    )

    result = card.check({"api_read_access_token": "secret-token"})

    assert result["status"] == "unhealthy"
    assert "HTTP 401" in result["message"]
    assert "secret-token" not in str(result)


def test_tmdb_enabled_config_requires_either_supported_credential():
    card = TMDBMetadataCard(FakeConfigStore())

    try:
        card.validate_enabled_config({})
    except ValueError as exc:
        assert "API Read Access Token 或 API Key" in str(exc)
    else:
        raise AssertionError("TMDB should not be enabled without credentials")

    card.validate_enabled_config({"api_read_access_token": "token"})
    card.validate_enabled_config({"api_key": "v3-key"})



def test_tmdb_category_search_does_not_mix_movies_with_tv_shows():
    http = FakeHttpClient({
        "results": [
            {"id": 101, "title": "潜伏6", "release_date": "2026-01-01", "genre_ids": [27]},
        ]
    })
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "token"}),
        http,
    )

    result = card.search_by_category("潜伏6", "电影")

    assert len(result) == 1
    assert result[0]["title"] == "潜伏6"
    assert result[0]["media_type"] == "movie"
    assert result[0]["tag"] == "电影"
    assert http.calls[0][1] == "https://api.themoviedb.org/3/search/movie"


def test_tmdb_category_search_filters_tv_by_variety_and_animation_genres():
    http = FakeHttpClient({
        "results": [
            {"id": 1, "name": "动画剧", "first_air_date": "2025-01-01", "genre_ids": [16]},
            {"id": 2, "name": "综艺节目", "first_air_date": "2025-01-01", "genre_ids": [10764]},
            {"id": 3, "name": "普通剧集", "first_air_date": "2025-01-01", "genre_ids": [18]},
        ]
    })
    card = TMDBMetadataCard(
        FakeConfigStore({"api_read_access_token": "token"}),
        http,
    )

    anime = card.search_by_category("测试", "动漫")
    assert [item["title"] for item in anime] == ["动画剧"]
    assert anime[0]["media_type"] == "tv"
    assert anime[0]["tag"] == "动漫"

    variety = card.search_by_category("测试", "综艺")
    assert [item["title"] for item in variety] == ["综艺节目"]
    assert variety[0]["tag"] == "综艺"
    assert http.calls[1][1] == "https://api.themoviedb.org/3/search/tv"
