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



def test_douban_detail_rejects_non_douban_urls():
    client = DoubanClient()
    assert client.get_detail("https://example.com/subject/123/") is None
    assert client.get_detail("../123") is None


def test_douban_detail_extracts_subject_fields():
    client = DoubanClient()

    class HtmlResponse:
        status_code = 200
        text = """
        <html><head><meta property="og:image" content="https://img1.doubanio.com/poster.jpg"></head>
        <body><div id="content"><h1><span property="v:itemreviewed">示例电影</span></h1>
        <div id="mainpic"><img src="https://img1.doubanio.com/poster.jpg"></div>
        <div id="info">
          <span class="pl">导演</span><span class="attrs"><a>张三</a></span><br/>
          <span class="pl">编剧</span><span class="attrs"><a>李四</a></span><br/>
          <span class="pl">主演</span><span class="attrs"><a>王五</a><a>赵六</a></span><br/>
          <span class="pl">制片国家/地区:</span> 中国大陆<br/>
          <span class="pl">上映日期:</span> 2024-01-02<br/>
        </div>
        <span property="v:genre">剧情</span><span property="v:average">8.1</span>
        <span property="v:summary">一段测试剧情。</span></div></body></html>
        """
    class FakeSession:
        def get(self, url, timeout):
            assert url == "https://movie.douban.com/subject/12345/"
            return HtmlResponse()

    client.http.session = FakeSession()
    detail = client.get_detail("12345")
    assert detail["id"] == "12345"
    assert detail["title"] == "示例电影"
    assert detail["directors"] == ["张三"]
    assert detail["writers"] == ["李四"]
    assert detail["casts"] == ["王五", "赵六"]
    assert detail["year"] == "2024"
    assert detail["rate"] == "8.1"
    assert detail["summary"] == "一段测试剧情。"
