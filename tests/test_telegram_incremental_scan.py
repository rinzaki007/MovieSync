from moviesync.clients.telegram import TelegramClient


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, timeout=8):
        self.urls.append(url)
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse("")


def page(*posts):
    chunks = []
    for post_id, text in posts:
        chunks.append(
            '<div class="tgme_widget_message" data-post="demo_channel/'
            + str(post_id)
            + '"><div class="tgme_widget_message_text">'
            + text
            + "</div></div>"
        )
    return "<html>" + "".join(chunks) + "</html>"


def make_client(responses):
    client = TelegramClient()
    client.http._local.session = FakeSession(responses)
    return client


def test_initial_incremental_scan_returns_latest_cursor_after_successful_scan():
    client = make_client([
        FakeResponse(page((105, "Show S01E05 https://pan.quark.cn/s/share105"),
                          (104, "Show S01E04 https://pan.quark.cn/s/share104"))),
        FakeResponse(page()),
    ])

    state = client.search_channel_updates("demo_channel", "Show")

    assert [item["pwd_id"] for item in state["items"]] == ["share105", "share104"]
    assert state["cursor"] == 105
    assert state["complete"] is True


def test_incremental_scan_stops_at_saved_cursor_and_returns_only_new_posts():
    client = make_client([
        FakeResponse(page(
            (107, "Show S01E07 https://pan.quark.cn/s/share107"),
            (106, "Show S01E06 https://pan.quark.cn/s/share106"),
            (105, "Show S01E05 https://pan.quark.cn/s/share105"),
            (104, "Show S01E04 https://pan.quark.cn/s/share104"),
            (103, "Show S01E03 https://pan.quark.cn/s/share103"),
            (102, "Show S01E02 https://pan.quark.cn/s/share102"),
        )),
    ])

    state = client.search_channel_updates("demo_channel", "Show", after_post_id=103)

    assert [item["pwd_id"] for item in state["items"]] == [
        "share107", "share106", "share105", "share104"
    ]
    assert state["cursor"] == 107
    assert state["complete"] is True


def test_incremental_scan_does_not_advance_cursor_when_saved_cursor_was_not_reached():
    client = make_client([
        FakeResponse(page(
            (107, "Show S01E07 https://pan.quark.cn/s/share107"),
            (106, "Show S01E06 https://pan.quark.cn/s/share106"),
        )),
    ])

    state = client.search_channel_updates("demo_channel", "Show", after_post_id=103)

    assert state["items"]
    assert state["cursor"] is None
    assert state["complete"] is False



def test_initial_scan_does_not_advance_cursor_when_page_limit_is_reached():
    from moviesync.clients.telegram import MAX_CHANNEL_PAGES

    responses = [
        FakeResponse(page(
            (1000 - index * 10, f"Show S01E{index + 1:02d} https://pan.quark.cn/s/share{index}")
        ))
        for index in range(MAX_CHANNEL_PAGES)
    ]
    client = make_client(responses)

    state = client.search_channel_updates("demo_channel", "Show")

    assert state["items"]
    assert state["cursor"] is None
    assert state["complete"] is False
    assert len(client.http.session.urls) == MAX_CHANNEL_PAGES


def test_history_backfill_scans_older_posts_and_marks_end_of_history():
    client = make_client([
        FakeResponse(page(
            (10, "Show S01E10 https://pan.quark.cn/s/share10"),
            (9, "Show S01E09 https://pan.quark.cn/s/share9"),
        )),
        FakeResponse(page()),
    ])

    state = client.search_channel_history("demo_channel", "Show", before_post_id=11)

    assert [item["pwd_id"] for item in state["items"]] == ["share10", "share9"]
    assert state["cursor"] == 9
    assert state["complete"] is True
    assert "before=11" in client.http.session.urls[0]
    assert "before=9" in client.http.session.urls[1]


def test_history_backfill_does_not_advance_cursor_on_http_error():
    client = make_client([FakeResponse("blocked", status_code=503)])

    state = client.search_channel_history("demo_channel", "Show", before_post_id=100)

    assert state["items"] == []
    assert state["cursor"] is None
    assert state["complete"] is False
