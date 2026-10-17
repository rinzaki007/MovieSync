"""Resource completeness indicators remain category-aware and user-readable."""

from pathlib import Path


def test_resource_selector_renders_claimed_episode_and_scan_warnings():
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "resource-select.js").read_text(encoding="utf-8")

    assert "标题宣称 '+claimed+' 集" in source
    assert "多集打包或命名未识别，请核实" in source
    assert "分享扫描可能不完整" in source
    assert "mediaCategoryLabel()" in source


def test_movie_cards_do_not_receive_series_episode_completeness_warning():
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "resource-select.js").read_text(encoding="utf-8")

    assert "if(series&&Number(c.declared_episode_count)>0)" in source


def test_resource_selector_shows_only_clickable_share_code():
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "resource-select.js").read_text(encoding="utf-8")

    assert "function shareCodeLink(c)" in source
    assert "分享码：'+shareCodeLink(c)" in source
    assert "resource_type||'').toLowerCase()==='quark_share'" in source
    assert "'https://pan.quark.cn/s/'+encodeURIComponent(quarkId)" in source
    assert "原分享路径（用于核对实际集数）" not in source
    assert " · 默认不选择文件</p>" not in source
    assert "href=\"'+escapeHtml(parsed.href)+'\"" in source
