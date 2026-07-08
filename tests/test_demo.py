"""
Tests for #234 — Hosted demo and interactive playground.
All checks are structural/content — no network calls.
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

def read(p): return (ROOT / p).read_text(encoding="utf-8")


class TestDemoHTML:
    def setup_method(self):
        self.html = read("demo/index.html")

    def test_file_exists(self):
        assert (ROOT / "demo/index.html").exists()

    def test_doctype(self):
        assert "<!DOCTYPE html>" in self.html

    def test_charset(self):
        assert 'charset="UTF-8"' in self.html

    def test_viewport(self):
        assert "viewport" in self.html

    def test_title(self):
        assert "SketchLog" in self.html
        assert "Playground" in self.html

    def test_meta_description(self):
        assert 'name="description"' in self.html

    def test_nav_present(self):
        assert 'class="nav"' in self.html

    def test_hero_present(self):
        assert 'class="hero"' in self.html

    def test_sketch_section(self):
        assert "sketch-demo" in self.html
        assert "DDSketch" in self.html

    def test_stream_section(self):
        assert "stream-demo" in self.html
        assert "Stream Operations" in self.html

    def test_export_section(self):
        assert "export-demo" in self.html
        assert "Loki" in self.html
        assert "Datadog" in self.html
        assert "New Relic" in self.html

    def test_snippet_section(self):
        assert "Python API Cheatsheet" in self.html

    def test_cta_section(self):
        assert "cta-section" in self.html
        assert "GitHub" in self.html

    def test_footer(self):
        assert "footer" in self.html
        assert "MIT" in self.html

    def test_css_link(self):
        assert "demo.css" in self.html

    def test_js_link(self):
        assert "demo.js" in self.html

    def test_interactive_ids(self):
        for id_ in ["sketch-add-btn", "sketch-reset-btn", "stream-write-btn",
                    "stream-read-btn", "stream-reset-btn", "stream-log",
                    "s-count", "s-p50", "s-p95", "s-p99"]:
            assert id_ in self.html

    def test_loki_panel(self):
        assert "loki-panel" in self.html

    def test_dd_panel(self):
        assert "dd-panel" in self.html

    def test_nr_panel(self):
        assert "nr-panel" in self.html

    def test_no_external_scripts(self):
        # No CDN JS frameworks
        for cdn in ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com"]:
            assert cdn not in self.html

    def test_no_inline_sensitive(self):
        assert "api_key" not in self.html.lower()
        assert "secret" not in self.html.lower()


class TestDemoCSS:
    def setup_method(self):
        self.css = read("demo/assets/demo.css")

    def test_file_exists(self):
        assert (ROOT / "demo/assets/demo.css").exists()

    def test_css_variables(self):
        assert "--bg:" in self.css
        assert "--accent:" in self.css
        assert "--border:" in self.css

    def test_responsive(self):
        assert "@media" in self.css

    def test_nav_styles(self):
        assert ".nav" in self.css

    def test_hero_styles(self):
        assert ".hero" in self.css

    def test_panel_styles(self):
        assert ".panel" in self.css

    def test_stat_grid_styles(self):
        assert ".stat-grid" in self.css

    def test_log_box_styles(self):
        assert ".log-box" in self.css

    def test_btn_styles(self):
        assert ".btn-primary" in self.css
        assert ".btn-ghost" in self.css

    def test_code_block_styles(self):
        assert ".code-block" in self.css

    def test_tab_styles(self):
        assert ".tab-btn" in self.css
        assert ".tab-panel" in self.css


class TestDemoJS:
    def setup_method(self):
        self.js = read("demo/assets/demo.js")

    def test_file_exists(self):
        assert (ROOT / "demo/assets/demo.js").exists()

    def test_use_strict(self):
        assert "'use strict'" in self.js

    def test_ddsketch_class(self):
        assert "class DDSketch" in self.js

    def test_ddsketch_add(self):
        assert "add(value)" in self.js

    def test_ddsketch_quantile(self):
        assert "quantile(q)" in self.js

    def test_sketch_reset(self):
        assert "sketch.reset()" in self.js

    def test_stream_store(self):
        assert "streamStore" in self.js

    def test_stream_write_handler(self):
        assert "stream-write-btn" in self.js

    def test_stream_read_handler(self):
        assert "stream-read-btn" in self.js

    def test_export_preview(self):
        assert "previewExport" in self.js
        assert "loki" in self.js
        assert "datadog" in self.js
        assert "newrelic" in self.js

    def test_snippets(self):
        assert "SNIPPETS" in self.js
        assert "basic" in self.js
        assert "stream" in self.js
        assert "agent" in self.js
        assert "loki" in self.js

    def test_tab_switching(self):
        assert "tab-btn" in self.js
        assert "classList" in self.js

    def test_no_eval(self):
        # Should not use eval()
        assert "eval(" not in self.js

    def test_no_document_write(self):
        assert "document.write(" not in self.js


class TestDemoDoc:
    def setup_method(self):
        self.md = read("docs/demo.md")

    def test_file_exists(self):
        assert (ROOT / "docs/demo.md").exists()

    def test_title(self):
        assert "demo" in self.md.lower() or "playground" in self.md.lower()

    def test_sections(self):
        assert "##" in self.md

    def test_github_pages(self):
        assert "GitHub Pages" in self.md

    def test_features_documented(self):
        for feature in ["DDSketch", "Stream", "Export"]:
            assert feature in self.md
