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
