"""Tests for the output asset-integrity scanner (spec §7.4).

The scanner must find local relative resource references in the final HTML
(including inline CSS and inline JS dynamic-asset arrays) and confirm every
referenced file exists in the output dir. External / data: / blob: refs are
left alone. This is the P0 contract that prevents the historical PNG-404 bug.
"""
from pathlib import Path

from beauty_soul.asset_integrity import extract_local_refs, check_integrity, find_path_violations


def test_extract_src_attribute():
    refs = extract_local_refs('<img src="assets/logo.png">')
    assert "assets/logo.png" in refs


def test_extract_css_url():
    html = "<style>.bg{background:url('assets/bg_fast_food_counter.png')}</style>"
    refs = extract_local_refs(html)
    assert "assets/bg_fast_food_counter.png" in refs


def test_extract_js_dynamic_asset_array():
    """The exact pattern from spec §7.4: const INGREDIENTS = [{src: "assets/..."}]."""
    html = '<script>const INGREDIENTS=[{src:"assets/sprite_patty.png"},{src:\'assets/sprite_bun.png\'}];</script>'
    refs = extract_local_refs(html)
    assert "assets/sprite_patty.png" in refs
    assert "assets/sprite_bun.png" in refs


def test_external_and_data_refs_excluded():
    html = (
        '<img src="https://cdn.example.com/x.png">'
        '<img src="data:image/png;base64,AAAA">'
        '<img src="//proto-relative.com/y.png">'
        '<a href="#section">jump</a>'
    )
    refs = extract_local_refs(html)
    assert refs == set()


def test_query_string_and_fragment_stripped():
    refs = extract_local_refs('<img src="assets/sprite.png?v=2#frag">')
    assert "assets/sprite.png" in refs


def test_check_integrity_all_present(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "patty.png").write_bytes(b"x")
    html = '<script>const A=[{src:"assets/patty.png"}]</script>'
    result = check_integrity(tmp_path, html)
    assert result["ok"] is True
    assert result["missing_assets"] == []


def test_check_integrity_reports_missing(tmp_path: Path):
    html = '<img src="assets/patty.png"><img src="assets/bun.png">'
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "patty.png").write_bytes(b"x")  # bun.png missing
    result = check_integrity(tmp_path, html)
    assert result["ok"] is False
    assert result["missing_assets"] == ["assets/bun.png"]


# ── §7.2 path violations: absolute / escape refs are forbidden in output ──────-

def test_find_path_violations_absolute_unix():
    assert "/tmp/sprite.png" in find_path_violations('<img src="/tmp/sprite.png">')


def test_find_path_violations_absolute_windows():
    assert any("x.png" in v for v in find_path_violations(r'<img src="C:\game\x.png">'))


def test_find_path_violations_escape():
    assert "../secret.png" in find_path_violations('<img src="../secret.png">')


def test_find_path_violations_clean_and_external():
    assert find_path_violations('<img src="assets/x.png"><img src="https://cdn/y.png">') == []


def test_check_integrity_fails_on_path_violation(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "x.png").write_bytes(b"x")
    result = check_integrity(tmp_path, '<img src="assets/x.png"><img src="/etc/passwd.png">')
    assert result["ok"] is False
    assert "/etc/passwd.png" in result["path_violations"]
