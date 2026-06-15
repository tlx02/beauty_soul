"""Tests for Playwright smoke testing — uses a minimal known-good game fixture."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
from pathlib import Path
from beautify_games import smoke_test


_GOOD_GAME = """<!DOCTYPE html>
<html><head><style>
.screen { display: none; } .screen.active { display: block; }
</style></head>
<body>
<div class="app-container">
  <div id="screen-home" class="screen active">
    <button id="btn-start" onclick="showScreen('screen-game')">PLAY</button>
  </div>
  <div id="screen-game" class="screen"></div>
  <div id="screen-gameover" class="screen"></div>
</div>
<script>
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
</script>
</body></html>"""


def test_smoke_test_good_game(tmp_path):
    (tmp_path / "index.html").write_text(_GOOD_GAME)
    result = smoke_test(tmp_path)
    assert result["home_active"] is True
    assert result["game_active"] is True
    assert result["js_errors"] == []
    assert result["passed"] is True


def test_smoke_test_broken_game(tmp_path):
    broken = _GOOD_GAME.replace("showScreen('screen-game')", "brokenFunction()")
    (tmp_path / "index.html").write_text(broken)
    result = smoke_test(tmp_path)
    assert result["passed"] is False
