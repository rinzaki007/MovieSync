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
    assert "grid-template-columns:minmax(250px,.65fr) minmax(0,1.8fr)!important" in source
    assert "function activatePlaybackSource(source, stopCurrent = true)" in source
    assert "activatePlaybackSource('online', true)" in source
    assert "window.MovieSyncPlayback.activateOnline" in source
    assert "function stopPlaybackForSourceSwitch()" in source
    assert "player.onerror = null;" in source
    assert "window.MovieSyncPlayback.playOnline = playOnline" in source


def test_playback_template_supports_douban_autoplay_and_fallback():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "playback.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "maccms-playback.js").read_text(encoding="utf-8")
    assert "playbackQuery.get('title')" in template
    # Query-driven activation must happen after the shared-player const bindings and setup.
    assert template.index("const playerTitle =") < template.index("if (playbackQuery.get('title'))")
    assert template.index("loadDirectory('');") < template.index("if (playbackQuery.get('title'))")
    assert "onOnlinePlaybackFailure" in template
    assert "window.location.assign('/playback?'" in script
    assert "function startAutoPlayback(candidates)" in script
    assert "function tryNextAutoResource(generation)" in script
    assert "window.MovieSyncPlayback.cancelAutoPlayback = () =>" in script
