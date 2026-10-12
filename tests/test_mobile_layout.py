"""Mobile portrait layout asset regression checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STYLE = ROOT / "static" / "css" / "style.css"
MOBILE = ROOT / "static" / "css" / "mobile.css"


def test_mobile_stylesheet_is_loaded_by_shared_styles():
    stylesheet = STYLE.read_text(encoding="utf-8")
    assert stylesheet.startswith('@import url("mobile.css");')
    assert MOBILE.is_file()


def test_mobile_stylesheet_covers_core_portrait_layouts():
    stylesheet = MOBILE.read_text(encoding="utf-8")
    assert "@media (max-width: 640px)" in stylesheet
    assert "#movie-grid" in stylesheet
    assert "#resource-list" in stylesheet
    assert "#task-list" in stylesheet
    assert "overflow-x: hidden" in stylesheet
    assert "100dvh" in stylesheet


def test_login_page_loads_mobile_stylesheet():
    login = (TEMPLATES / "login.html").read_text(encoding="utf-8")
    assert "filename='css/mobile.css'" in login
