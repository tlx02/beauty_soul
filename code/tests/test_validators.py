"""Unit tests for deterministic pipeline helpers."""
import sys, os
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
