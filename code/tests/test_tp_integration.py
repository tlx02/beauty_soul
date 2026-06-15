"""Tests for Trinity Protocol SDK wrapper — uses mocked solve() to avoid API calls."""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch


def test_extract_json_clean():
    from beautify_games import _extract_json_from_solve
    result = _extract_json_from_solve('{"theme": "neon arcade", "aspect_w": 9}')
    assert result == {"theme": "neon arcade", "aspect_w": 9}


def test_extract_json_fenced():
    from beautify_games import _extract_json_from_solve
    text = '```json\n{"theme": "deep sea", "aspect_w": 9}\n```\n--- [AW critic: score 4/12]'
    result = _extract_json_from_solve(text)
    assert result is not None
    assert result["theme"] == "deep sea"


def test_extract_json_aw_noise():
    """AW critic text appended after JSON must not corrupt parse."""
    from beautify_games import _extract_json_from_solve
    text = '{"theme": "cozy", "aspect_w": 9, "aspect_h": 16, "max_width": 480}\n\n--- [AW critic: score 8/12, BEZOS reframe ...]'
    result = _extract_json_from_solve(text)
    assert result is not None
    assert result["max_width"] == 480


def test_extract_json_returns_none_on_garbage():
    from beautify_games import _extract_json_from_solve
    assert _extract_json_from_solve("no json here at all") is None


def test_tp_analyze_falls_back_to_call_llm_on_bad_json(tmp_path):
    """If solve() returns unparseable output, tp_analyze must not crash."""
    from beautify_games import tp_analyze
    with patch("beautify_games.solve", return_value="I cannot answer this question"):
        with patch("beautify_games.call_llm", return_value='{"theme":"fallback","aspect_w":9,"aspect_h":16,"max_width":480,"palette":["#000"],"mood":"x","font_style":"y"}') as mock_llm:
            result = tp_analyze("test problem", "fallback_system_prompt", "html content", "test-label", max_tokens=256)
            assert mock_llm.called
            assert result is not None


def test_pass0_theme_via_tp_analyze_parses_to_valid_schema(tmp_path):
    """tp_analyze for Pass 0 must produce parseable JSON matching the theme schema."""
    from beautify_games import _extract_json_from_solve
    mock_output = '```json\n{"theme":"deep sea adventure","palette":["#0a2342","#1b6ca8","#f0c040","#ffffff"],"mood":"Mysterious and calming","font_style":"rounded playful","aspect_w":9,"aspect_h":16,"max_width":480}\n```'
    result = _extract_json_from_solve(mock_output)
    assert result is not None
    required_keys = {"theme", "palette", "mood", "font_style", "aspect_w", "aspect_h", "max_width"}
    assert required_keys.issubset(result.keys())
    assert isinstance(result["palette"], list)
    assert isinstance(result["aspect_w"], int)


def test_extract_style_lock_from_solve():
    """Style lock must parse to a dict with required art direction keys."""
    from beautify_games import _extract_json_from_solve
    mock = '```json\n{"art_style":"flat vector, 2px stroke","line_weight":"clean 2px","shadow_style":"soft drop-shadow","background_treatment":"bokeh blur","icon_shape":"rounded rect 12px","negative_terms":"no photorealism"}\n```'
    result = _extract_json_from_solve(mock)
    assert result is not None
    assert "art_style" in result
    assert "negative_terms" in result
