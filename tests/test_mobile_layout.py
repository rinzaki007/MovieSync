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


def test_page_specific_mobile_rules_are_scoped_to_their_pages():
    stylesheet = MOBILE.read_text(encoding="utf-8")
    assert "body:has(#movie-grid) > header.glass-nav" in stylesheet
    assert "body:has(#task-list) > header.glass-nav > div" in stylesheet


def test_all_html_pages_load_mobile_styles_directly_or_through_shared_styles():
    for template in TEMPLATES.glob("*.html"):
        html = template.read_text(encoding="utf-8")
        if "css/style.css" in html:
            continue
        assert "css/mobile.css" in html, f"{template.name} does not load mobile styles"


def test_admin_and_resource_pages_have_dedicated_mobile_layouts():
    stylesheet = MOBILE.read_text(encoding="utf-8")
    assert "body:has(#panel-global) > div.flex-1.flex.overflow-hidden" in stylesheet
    assert "body:has(#panel-global) > div.flex-1.flex.overflow-hidden > aside.w-60" in stylesheet
    assert "body:has(#resource-list) > header.glass-nav > div" in stylesheet
    assert "body:has(#resource-list) > main" in stylesheet



def test_mobile_task_admin_and_openlist_controls_are_compact():
    stylesheet = MOBILE.read_text(encoding="utf-8")
    assert "body:has(#movie-grid) > div.glass-subnav a" in stylesheet
    assert "body:has(#task-list) > main > section:nth-child(2)" in stylesheet
    assert "body:has(#panel-global) > header.glass-nav > div:last-child" in stylesheet
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in stylesheet
    assert stylesheet.rstrip().endswith("}")
