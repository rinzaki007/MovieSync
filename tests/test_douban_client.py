from moviesync.clients.douban import DoubanClient


class FakeResponse:
    status_code = 200


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(), self.payload


def test_douban_search_accepts_subject_suggest_array_payload():
    client = DoubanClient()
    client.http = FakeHttp([
        {
            "id": "1292052",
            "title": "肖申克的救赎",
            "img": "https://img.example.test/poster.jpg",
            "rate": "9.7",
            "year": "1994",
        }
    ])

    results = client.search("肖申克的救赎")

    assert len(results) == 1
    assert results[0]["title"] == "肖申克的救赎"
    assert results[0]["url"] == "https://movie.douban.com/subject/1292052/"
    assert results[0]["rate"] == "9.7"
    assert client.http.calls[0][2]["allow_non_dict"] is True



def test_douban_client_close_releases_http_client():
    client = DoubanClient()
    calls = []
    client.http.close = lambda: calls.append("closed")

    client.close()

    assert calls == ["closed"]
