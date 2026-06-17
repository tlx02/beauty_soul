"""Fail-closed smoke tests for the beauty_soul entry contract.
No real network calls are made — we test that the API surface is correct
and that bad input returns ok=False without raising."""
from beauty_soul import run_beautify


def test_import():
    """Package can be imported and run_beautify is callable."""
    assert callable(run_beautify)


def test_entry_failclosed(tmp_path):
    """Non-existent game_dir returns ok=False, not an exception."""
    r = run_beautify(
        game_dir=str(tmp_path / "nope"),
        out_dir=str(tmp_path / "out"),
        config={"openrouter_key": "test", "skip_smoke_test": True},
    )
    assert r["ok"] is False
    assert r["html_path"] is None
    assert r["out_dir"] == str(tmp_path / "out")
    assert r["error"] is not None


def test_return_shape(tmp_path):
    """Return dict always has all required KiX contract fields."""
    r = run_beautify(
        game_dir=str(tmp_path / "missing"),
        out_dir=str(tmp_path / "out2"),
    )
    required = {"ok", "html_path", "out_dir", "assets_dir", "report", "error", "error_class"}
    assert required.issubset(r.keys()), f"missing fields: {required - r.keys()}"


def test_no_secrets_json_crash(tmp_path):
    """Works without secrets.json — keys come from config param."""
    # game_dir missing → early failure, but no crash from missing secrets.json
    r = run_beautify(
        game_dir=str(tmp_path / "game"),
        out_dir=str(tmp_path / "out3"),
        config={},  # no keys, no secrets.json
    )
    assert isinstance(r, dict)
    assert "ok" in r
