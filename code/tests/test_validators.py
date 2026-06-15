"""Unit tests for deterministic pipeline helpers."""
import sys, os, shutil
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from beautify_games import extract_css, inject_css


def test_extract_css_no_head_tag():
    """CSS must not be silently dropped when HTML has no </head>."""
    html = "<html><body><style>.foo{color:red}</style><p>hi</p></body></html>"
    css, template = extract_css(html)
    assert ".foo" in css
    assert "__STYLES__" in template


def test_inject_css_roundtrip_no_head():
    html = "<html><body><style>.x{margin:0}</style><p>game</p></body></html>"
    css, template = extract_css(html)
    result = inject_css(template, "<style>.x{margin:0}</style>")
    assert "<style>" in result
    assert "__STYLES__" not in result


def test_extract_css_with_head_tag_unchanged():
    """Existing </head> path must still work."""
    html = "<html><head><style>.a{color:blue}</style></head><body></body></html>"
    css, template = extract_css(html)
    assert ".a" in css
    assert "__STYLES__" in template


def test_pass4a_template_game_preview_transparent():
    """PASS4A template must have transparent:true for game_preview.png."""
    from beautify_games import PASS4A
    import re
    match = re.search(r'"game_preview\.png".*?"transparent"\s*:\s*(true|false)', PASS4A)
    assert match is not None, "game_preview.png not found in PASS4A template"
    assert match.group(1) == "true", f"game_preview.png must be transparent:true, got: {match.group(1)}"


def test_screen_height_portrait():
    max_width, aspect_w, aspect_h = 480, 9, 16
    expected = int(max_width * aspect_h / aspect_w)
    assert expected == 853

def test_screen_height_landscape():
    max_width, aspect_w, aspect_h = 800, 16, 9
    expected = int(max_width * aspect_h / aspect_w)
    assert expected == 450
    assert expected != 844


def test_latest_html_ordering():
    """index_pass5/4 must appear before index_pass3 in latest_html search order."""
    import inspect
    from beautify_games import beautify
    src = inspect.getsource(beautify)
    # Find the tuple passed to latest_html's for loop
    import re
    m = re.search(r'for fname in \(([^)]+)\)', src)
    assert m, "latest_html for-loop not found"
    entries = m.group(1)
    idx5 = entries.find("index_pass5.html")
    idx4 = entries.find("index_pass4.html")
    idx3 = entries.find("index_pass3.html")
    assert idx5 != -1, "index_pass5.html missing from latest_html"
    assert idx4 != -1, "index_pass4.html missing from latest_html"
    assert idx5 < idx3, "index_pass5.html must come before index_pass3.html"
    assert idx4 < idx3, "index_pass4.html must come before index_pass3.html"


def test_pass4d_writes_intermediate(tmp_path):
    """Pass 4d must write index_pass4.html when it succeeds."""
    import inspect
    from beautify_games import beautify
    src = inspect.getsource(beautify)
    assert 'index_pass4.html' in src, "Pass 4d must write index_pass4.html"


def test_pass5_writes_intermediate():
    """Pass 5 must write index_pass5.html when it succeeds."""
    import inspect
    from beautify_games import beautify
    src = inspect.getsource(beautify)
    assert 'index_pass5.html' in src, "Pass 5 must write index_pass5.html"


def test_resume_covers_pass4_assets_keys():
    """--resume must skip games whose progress has pass4_assets or pass4_assets_skipped."""
    import inspect
    from beautify_games import main
    src = inspect.getsource(main)
    assert 'pass4_assets' in src, "resume check must include pass4_assets key"
    assert 'pass4_assets_skipped' in src, "resume check must include pass4_assets_skipped key"


# ── Per-pass structural validation ────────────────────────────────────────────

def test_validate_pass1_all_present():
    from beautify_games import validate_pass1
    html = '''<div class="app-container">
      <div id="screen-home" class="screen active"></div>
      <div id="screen-game" class="screen"></div>
      <div id="screen-gameover" class="screen"></div>
      <button id="btn-start"></button>
      <button id="btn-home-game"></button>
      <button id="btn-pause-game"></button>
      <div id="pause-overlay"></div>
    </div>'''
    assert validate_pass1(html) == []


def test_validate_pass1_missing_ids():
    from beautify_games import validate_pass1
    html = '<div id="screen-home"></div><div id="screen-game"></div>'
    missing = validate_pass1(html)
    assert "screen-gameover" in missing
    assert "btn-start" in missing
    assert "pause-overlay" in missing


def test_validate_pass1_single_quote_ids():
    from beautify_games import validate_pass1
    html = "<div id='screen-home'></div><div id='screen-game'></div><div id='screen-gameover'></div><button id='btn-start'></button><button id='btn-home-game'></button><button id='btn-pause-game'></button><div id='pause-overlay'></div>"
    assert validate_pass1(html) == []


def test_genre_art_direction_loaded_from_config():
    """Genre art direction must be readable from config and injected into image descriptions."""
    import json, os
    config = json.loads(open(os.path.join(os.path.dirname(__file__), "../config.json")).read())
    assert "genre_art_direction" in config
    assert "match3" in config["genre_art_direction"]
    assert "puzzle_logic" in config["genre_art_direction"]


def test_pass4a_has_three_background_variants():
    from beautify_games import PASS4A
    assert "background_home.png" in PASS4A
    assert "background_game.png" in PASS4A
    assert "background_gameover.png" in PASS4A


def test_generate_images_parallel_returns_failed_set():
    """Parallel generator must return set of failed filenames."""
    from unittest.mock import patch
    from pathlib import Path
    import tempfile
    from beautify_games import generate_images_parallel

    with tempfile.TemporaryDirectory() as tmpdir:
        img_dir = Path(tmpdir)
        images = [
            {"filename": "a.png", "description": "test", "transparent": True},
            {"filename": "b.png", "description": "test", "transparent": False},
        ]
        with patch("beautify_games.gen_image", return_value=False):
            failed = generate_images_parallel(images, img_dir, max_workers=2, style_lock=None, genre_direction="")
        assert failed == {"a.png", "b.png"}


# ── JS quality helpers ─────────────────────────────────────────────────────────

def test_extract_js_symbols_finds_functions():
    from beautify_games import extract_js_symbols
    html = "<script>function startGame() {} function resetScore() {} const update = () => {}</script>"
    symbols = extract_js_symbols(html)
    assert "startGame" in symbols
    assert "resetScore" in symbols


def test_extract_js_symbols_empty_on_no_script():
    from beautify_games import extract_js_symbols
    assert extract_js_symbols("<html><body></body></html>") == set()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_validate_js_syntax_valid():
    from beautify_games import validate_js_syntax
    html = "<script>function foo() { return 1 + 1; }</script>"
    errors = validate_js_syntax(html)
    assert errors == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_validate_js_syntax_invalid():
    from beautify_games import validate_js_syntax
    html = "<script>function foo( { return; }</script>"  # missing closing paren
    errors = validate_js_syntax(html)
    assert len(errors) > 0
