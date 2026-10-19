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
    assert 'id="vod_source_manager-inline-panel"' in source
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
    script = (root / "static" / "js" / "vod-source-manager-playback.js").read_text(encoding="utf-8")
    assert "playbackQuery.get('title')" in template
    # Query-driven activation must happen after the shared-player const bindings and setup.
    assert template.index("const playerTitle =") < template.index("if (playbackQuery.get('title'))")
    assert template.index("loadDirectory('');") < template.index("if (playbackQuery.get('title'))")
    assert "onOnlinePlaybackFailure" in template
    assert "window.location.assign('/playback?'" in script
    assert "function startAutoPlayback(candidates)" in script
    assert "function tryNextAutoResource(generation)" in script
    assert "window.MovieSyncPlayback.cancelAutoPlayback = () =>" in script
    assert 'id="vod_source_manager-watch-progress"' in script
    assert 'data-progress-step="0"' in script
    assert "function setAutoProgress(stage, title, detail = '')" in script
    assert "setAutoProgress('search', '正在搜索在线资源'" in script
    assert "setAutoProgress('match'" in script
    assert "setAutoProgress('play'" in script
    assert "setAutoProgress('success', '视频已开始播放'" in script
    assert "setAutoProgress('searchFailed'" in script
    assert "setAutoProgress('playFailed'" in script

def test_homepage_douban_immediate_watch_navigates_to_playback_center():
    root = Path(__file__).resolve().parents[1]
    homepage = (root / "templates" / "index.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert 'onclick="movieDetailWatch()"' in homepage
    assert "function movieDetailWatch()" in script
    assert "window.location.assign('/playback?' + params.toString())" in script
    assert "title: movie.title || ''" in script
    assert "year: movie.year || movie.release_year || ''" in script
    assert "type: category" in script



def test_playback_source_switch_separates_episode_selector_and_openlist_hint():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "playback.html").read_text(encoding="utf-8")

    assert 'id="playback-openlist-hint"' in template
    assert "const episodePanel = document.getElementById('vod_source_manager-episode-panel')" in template
    assert "episodePanel.classList.toggle('hidden', !online || !episodePanel.childElementCount)" in template
    assert "openlistHint.classList.toggle('hidden', online)" in template
