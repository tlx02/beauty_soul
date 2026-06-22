"""Input-bundle handling and output packaging for the beauty_soul HTTP service.

Responsibilities:
  - safe_extract:            unzip an input bundle, rejecting zip-slip (§13).
  - load_manifest:           read manifest.json (§6.4 / §13).
  - validate_manifest:       enforce required manifest fields (§6.5).
  - find_game_entry:         locate game/index.html (§13).
  - backfill_source_assets:  copy un-overwritten source game files into the
                             output dir so the result is complete (§7.3 P0).
  - package_output:          build output.zip with index.html at the root,
                             excluding internal pipeline scratch (§7.2).
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path


class BundleError(Exception):
    """A structured, client-facing failure mapped to a spec error_class + status."""

    def __init__(self, error_class: str, message: str, status: int = 400):
        super().__init__(message)
        self.error_class = error_class
        self.message = message
        self.status = status


# Pipeline scratch artifacts that must never ship inside output.zip (§7.2).
_SCRATCH_NAMES = {".progress.json", "index_original.html"}
_SCRATCH_RE = re.compile(r"^index_pass.*\.html$")


def _is_scratch(rel: str) -> bool:
    name = Path(rel).name
    return name in _SCRATCH_NAMES or bool(_SCRATCH_RE.match(name))


def safe_extract(zip_bytes: bytes, dest: Path) -> None:
    """Extract a zip archive to ``dest``, refusing any entry that would escape it.

    Rejects path traversal (``../``) and absolute paths — i.e. zip-slip (§13).
    Raises ``BundleError('bad_request')`` on a malformed archive or unsafe entry.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise BundleError("bad_request", f"input is not a valid zip: {e}", 400)

    with zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue  # directory entry
            target = (dest / name).resolve()
            if os.path.isabs(name) or not str(target).startswith(str(dest_root) + os.sep):
                raise BundleError("bad_request", f"unsafe path in zip: {name!r}", 400)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def load_manifest(bundle_dir: Path) -> dict:
    """Read and parse ``manifest.json`` from the extracted bundle root (§13)."""
    mf = Path(bundle_dir) / "manifest.json"
    if not mf.is_file():
        raise BundleError("bad_request", "manifest.json missing from bundle", 400)
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BundleError("bad_request", f"manifest.json is not valid JSON: {e}", 400)
    if not isinstance(data, dict):
        raise BundleError("bad_request", "manifest.json must be a JSON object", 400)
    return data


# Fields that must be present in the manifest itself. request_id is required by
# §6.5 but may be supplied via the X-Kix-Request-Id header, so it is resolved by
# the caller rather than enforced here.
_REQUIRED_FIELDS = ("schema_version", "game_slug", "game_name")


def validate_manifest(manifest: dict) -> None:
    """Enforce required manifest fields. Missing field → 422 (§6.5 / §8)."""
    missing = [f for f in _REQUIRED_FIELDS if not manifest.get(f)]
    if missing:
        raise BundleError(
            "bad_request",
            f"manifest missing required field(s): {', '.join(missing)}",
            422,
        )


def find_game_entry(bundle_dir: Path) -> Path:
    """Return the ``game/`` dir, ensuring ``game/index.html`` exists (§13)."""
    game_dir = Path(bundle_dir) / "game"
    if not (game_dir / "index.html").is_file():
        raise BundleError("missing_game_entry", "game/index.html not found in bundle", 400)
    return game_dir


def backfill_source_assets(game_dir: Path, out_dir: Path) -> list[str]:
    """Copy every source file under ``game_dir`` that is absent from ``out_dir``.

    Files already present in ``out_dir`` (the beautified index.html, newly
    generated assets) are left untouched — backfill never clobbers pipeline
    output. Returns the sorted list of relative paths copied (§7.3).
    """
    game_dir = Path(game_dir)
    out_dir = Path(out_dir)
    copied: list[str] = []
    for src in game_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(game_dir)
        dst = out_dir / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel.as_posix())
    return sorted(copied)


def package_output(out_dir: Path) -> bytes:
    """Zip ``out_dir`` into output.zip bytes, index.html at the root (§7.2).

    Excludes internal pipeline scratch files (progress, per-pass HTML snapshots).
    """
    out_dir = Path(out_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(out_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(out_dir).as_posix()
            if _is_scratch(rel):
                continue
            z.write(path, rel)
    return buf.getvalue()
