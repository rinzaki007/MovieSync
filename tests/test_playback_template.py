from pathlib import Path

from jinja2 import Environment


def test_playback_template_compiles_as_jinja():
    template_path = Path(__file__).resolve().parents[1] / "templates" / "playback.html"
    source = template_path.read_text(encoding="utf-8")

    # CSS selectors such as `{#video-player` collide with Jinja's comment opener.
    Environment().parse(source)
    assert "async function deleteItem(item, button)" in source
    assert "is_dir: Boolean(item.is_dir)" in source
    assert 'href="/tasks"' in source
    assert "Range 请求" in source
    assert "播放地址已获取，正在尝试开始播放。" in source
    assert 'id="playback-source-switch"' in source
    assert 'id="maccms-inline-panel"' in source
    assert 'id="player-workspace-panel"' in source
    assert "playback-online-layout" in source
    assert "window.MovieSyncPlayback.playOnline = playOnline" in source


def test_playback_template_supports_douban_autoplay_and_fallback():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "playback.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "maccms-playback.js").read_text(encoding="utf-8")
    assert "playbackQuery.get('title')" in template
    assert "onOnlinePlaybackFailure" in template
    assert "window.location.assign('/playback?'" in script
    assert "function startAutoPlayback(candidates)" in script
    assert "function tryNextAutoResource(generation)" in script
