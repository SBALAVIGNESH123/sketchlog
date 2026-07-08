"""Tests for the SketchLog marketing website."""
import pathlib, re, html, pytest

ROOT = pathlib.Path(__file__).parent.parent / "website"
INDEX = ROOT / "index.html"
STYLE = ROOT / "assets" / "style.css"
JS    = ROOT / "assets" / "app.js"


# ── File existence ─────────────────────────────────────────────────────────────

def test_index_exists():
    assert INDEX.exists(), "website/index.html must exist"

def test_style_exists():
    assert STYLE.exists(), "website/assets/style.css must exist"

def test_js_exists():
    assert JS.exists(), "website/assets/app.js must exist"


# ── HTML structure ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def html_src():
    return INDEX.read_text(encoding="utf-8")

def test_doctype(html_src):
    assert html_src.lstrip().startswith("<!DOCTYPE html>")

def test_charset_meta(html_src):
    assert "charset" in html_src.lower()

def test_viewport_meta(html_src):
    assert "viewport" in html_src

def test_title_tag(html_src):
    assert "<title>" in html_src and "SketchLog" in html_src

def test_meta_description(html_src):
    assert 'name="description"' in html_src

def test_og_title(html_src):
    assert 'og:title' in html_src

def test_twitter_card(html_src):
    assert 'twitter:card' in html_src

def test_stylesheet_link(html_src):
    assert "assets/style.css" in html_src

def test_js_script_tag(html_src):
    assert "assets/app.js" in html_src

def test_nav_present(html_src):
    assert "<nav" in html_src

def test_hero_section(html_src):
    assert 'id="hero"' in html_src

def test_features_section(html_src):
    assert 'id="features"' in html_src

def test_playground_section(html_src):
    assert 'id="playground"' in html_src
    assert "Open the playground" in html_src

def test_how_it_works_section(html_src):
    assert 'id="how-it-works"' in html_src

def test_integrations_section(html_src):
    assert 'id="integrations"' in html_src

def test_quickstart_section(html_src):
    assert 'id="quickstart"' in html_src

def test_faq_section(html_src):
    assert 'id="faq"' in html_src

def test_footer_present(html_src):
    assert "<footer" in html_src

def test_cta_section(html_src):
    assert 'class="cta"' in html_src

def test_github_link(html_src):
    assert "SBALAVIGNESH123/sketchlog" in html_src

def test_get_started_cta(html_src):
    assert "Get started" in html_src

def test_no_placeholder_text(html_src):
    for placeholder in ["TODO", "FIXME", "Lorem ipsum", "placeholder"]:
        assert placeholder not in html_src, f"Found placeholder: {placeholder}"

def test_mit_licence_mentioned(html_src):
    assert "MIT" in html_src

def test_hero_headline_present(html_src):
    assert "hero__headline" in html_src

def test_hero_stats_present(html_src):
    assert "hero__stats" in html_src

def test_stat_latency(html_src):
    assert "1ms" in html_src

def test_stat_quantile_error(html_src):
    assert "0.1%" in html_src

def test_stat_storage_reduction(html_src):
    assert "100" in html_src

def test_feature_cards_count(html_src):
    count = html_src.count("feature-card__icon")
    assert count >= 6, f"Expected ≥6 feature cards, got {count}"

def test_how_steps_count(html_src):
    count = html_src.count("how__step-num")
    assert count >= 4, f"Expected ≥4 how-it-works steps, got {count}"

def test_integration_cards_count(html_src):
    count = html_src.count("integration-card__logo")
    assert count >= 5, f"Expected ≥5 integration cards, got {count}"

def test_faq_items_count(html_src):
    count = html_src.count("<details")
    assert count >= 4, f"Expected ≥4 FAQ items, got {count}"

def test_code_demo_present(html_src):
    assert "demo__code" in html_src

def test_pip_install_mentioned(html_src):
    assert "pip install sketchlog" in html_src


# ── CSS ────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def css_src():
    return STYLE.read_text(encoding="utf-8")

def test_css_variables(css_src):
    assert "--brand:" in css_src

def test_css_responsive_breakpoint(css_src):
    assert "@media" in css_src

def test_css_nav_styles(css_src):
    assert ".nav" in css_src

def test_css_hero_styles(css_src):
    assert ".hero" in css_src

def test_css_btn_styles(css_src):
    assert ".btn" in css_src

def test_css_feature_card(css_src):
    assert ".feature-card" in css_src

def test_css_faq_styles(css_src):
    assert ".faq__item" in css_src

def test_css_footer_styles(css_src):
    assert ".footer" in css_src


# ── JS ─────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def js_src():
    return JS.read_text(encoding="utf-8")

def test_js_nav_scroll(js_src):
    assert "scroll" in js_src

def test_js_burger_menu(js_src):
    assert "burger" in js_src

def test_js_intersection_observer(js_src):
    assert "IntersectionObserver" in js_src

def test_js_fade_up(js_src):
    assert "fade-up" in js_src

def test_js_no_eval(js_src):
    assert "eval(" not in js_src

def test_js_no_document_write(js_src):
    assert "document.write" not in js_src
