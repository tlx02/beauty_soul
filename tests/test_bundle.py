"""Tests for input-bundle handling and output packaging.

Covers: safe zip extraction (§13 zip-slip guard), manifest validation (§6.5),
game-entry presence (§13), source-asset backfill (§7.3 P0), and output.zip
packaging (§7.2 — index.html at root, internal scratch excluded).
"""
import io
import zipfile
from pathlib import Path

import pytest

from beauty_soul.bundle import (
    BundleError,
    safe_extract,
    load_manifest,
    validate_manifest,
    find_game_entry,
    backfill_source_assets,
    package_output,
)


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


# ── safe_extract ──────────────────────────────────────────────────────────────

def test_safe_extract_writes_files(tmp_path: Path):
    data = _zip({"manifest.json": b"{}", "game/index.html": b"<html>"})
    dest = tmp_path / "b"
    safe_extract(data, dest)
    assert (dest / "manifest.json").read_bytes() == b"{}"
    assert (dest / "game" / "index.html").read_bytes() == b"<html>"


def test_safe_extract_rejects_zip_slip(tmp_path: Path):
    data = _zip({"../evil.txt": b"pwned"})
    with pytest.raises(BundleError) as ei:
        safe_extract(data, tmp_path / "b")
    assert ei.value.error_class == "bad_request"
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_rejects_bad_zip(tmp_path: Path):
    with pytest.raises(BundleError) as ei:
        safe_extract(b"not a zip", tmp_path / "b")
    assert ei.value.error_class == "bad_request"


# ── manifest ────────────────────────────────────────────────────────────────--

def test_load_manifest_missing_is_bad_request(tmp_path: Path):
    (tmp_path / "game").mkdir()
    with pytest.raises(BundleError) as ei:
        load_manifest(tmp_path)
    assert ei.value.error_class == "bad_request"
    assert ei.value.status == 400


def test_validate_manifest_ok():
    m = {"schema_version": "soul-input/v1", "game_slug": "match3_ab", "game_name": "Match 3"}
    validate_manifest(m)  # does not raise


def test_validate_manifest_missing_field_is_422():
    with pytest.raises(BundleError) as ei:
        validate_manifest({"schema_version": "soul-input/v1"})  # no game_slug/game_name
    assert ei.value.status == 422


# ── game entry ─────────────────────────────────────────────────────────────---

def test_find_game_entry_missing(tmp_path: Path):
    (tmp_path / "game").mkdir()
    with pytest.raises(BundleError) as ei:
        find_game_entry(tmp_path)
    assert ei.value.error_class == "missing_game_entry"


def test_find_game_entry_present(tmp_path: Path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "index.html").write_text("<html>")
    assert find_game_entry(tmp_path) == tmp_path / "game"


# ── source-asset backfill (§7.3 P0) ────────────────────────────────────────---

def test_backfill_copies_unmodified_source_assets(tmp_path: Path):
    game = tmp_path / "game"
    (game / "assets").mkdir(parents=True)
    (game / "index.html").write_text("orig")
    (game / "assets" / "patty.png").write_bytes(b"PATTY")

    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    (out / "index.html").write_text("beautified")        # already overwritten by pipeline
    (out / "assets" / "new.png").write_bytes(b"NEW")      # newly generated

    copied = backfill_source_assets(game, out)

    # source-only asset backfilled
    assert (out / "assets" / "patty.png").read_bytes() == b"PATTY"
    assert "assets/patty.png" in copied
    # pipeline output must NOT be clobbered by the source original
    assert (out / "index.html").read_text() == "beautified"
    assert "index.html" not in copied


# ── output packaging (§7.2) ──────────────────────────────────────────────────-

def test_package_output_root_index_and_excludes_scratch(tmp_path: Path):
    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    (out / "index.html").write_text("<html>")
    (out / "assets" / "x.png").write_bytes(b"x")
    (out / "soul_report.json").write_text("{}")
    # pipeline scratch that must NOT ship
    (out / ".progress.json").write_text("{}")
    (out / "index_original.html").write_text("orig")
    (out / "index_pass1.html").write_text("p1")

    data = package_output(out)
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert "index.html" in names
    assert "assets/x.png" in names
    assert "soul_report.json" in names
    assert ".progress.json" not in names
    assert "index_original.html" not in names
    assert "index_pass1.html" not in names
