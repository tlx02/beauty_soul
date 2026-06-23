"""Output asset-integrity scanner — spec §7.4 (P0 contract).

Scans the final HTML (with inline CSS and inline JS) for *local relative*
resource references and verifies each referenced file exists in the output
directory. External URLs (http/https/protocol-relative), ``data:`` and
``blob:`` URIs, and in-page anchors are ignored.

This covers the asset-reference patterns common to KiX games:
  - HTML attributes:  src="assets/x.png", href="assets/x.css"
  - inline CSS:        url(assets/x.png), url('assets/x.png')
  - inline JS strings: const ASSETS = {...}, const INGREDIENTS = [{src:"assets/x.png"}]

A JS/string token only counts as an asset when it ends in a known media/font/
script/style extension, which keeps false positives out of the scan.
"""
from __future__ import annotations

import re
from pathlib import Path

# Extensions that mark a quoted token as a real local resource reference.
ASSET_EXTS = (
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif",
    "mp3", "wav", "ogg", "m4a", "aac", "flac",
    "mp4", "webm", "mov",
    "woff", "woff2", "ttf", "otf", "eot",
    "js", "css", "json",
)
_EXT_GROUP = "|".join(ASSET_EXTS)

# Any token that ends in a known asset extension, captured from inside quotes,
# url(...) wrappers, or bare. The capture group is the path itself.
_TOKEN_RE = re.compile(
    r"""["'(]?\s*([^"'()\s]+?\.(?:%s))\b""" % _EXT_GROUP,
    re.IGNORECASE,
)

# Refs we must NOT treat as local files.
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "blob:", "mailto:", "tel:", "#")


def _is_local(path: str) -> bool:
    p = path.strip()
    if not p:
        return False
    low = p.lower()
    if any(low.startswith(pre) for pre in _EXTERNAL_PREFIXES):
        return False
    # Absolute filesystem paths are not local relative refs (and are a §7.2 violation).
    if p.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", p):
        return False
    return True


def _normalize(path: str) -> str:
    """Strip query string / fragment and a leading ./ so refs compare cleanly."""
    p = path.split("?", 1)[0].split("#", 1)[0]
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_external(path: str) -> bool:
    low = path.strip().lower()
    return any(low.startswith(pre) for pre in _EXTERNAL_PREFIXES)


def _is_absolute(path: str) -> bool:
    p = path.strip()
    # protocol-relative (//host) is external, not an absolute local path
    if p.startswith("//"):
        return False
    return p.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", p))


def extract_local_refs(html: str) -> set[str]:
    """Return the set of local relative resource paths referenced by ``html``."""
    refs: set[str] = set()
    for raw in _TOKEN_RE.findall(html or ""):
        if not _is_local(raw):
            continue
        norm = _normalize(raw)
        if norm and _is_local(norm):
            refs.add(norm)
    return refs


def find_path_violations(html: str) -> list[str]:
    """Return forbidden output refs (§7.2): absolute local paths or ``../`` escapes.

    External URLs, ``data:``/``blob:`` and clean relative paths are not violations.
    """
    violations: set[str] = set()
    for raw in _TOKEN_RE.findall(html or ""):
        tok = raw.strip()
        if _is_external(tok):
            continue
        if _is_absolute(tok) or "../" in tok.replace("\\", "/"):
            violations.add(tok)
    return sorted(violations)


def check_integrity(out_dir: Path, html: str) -> dict:
    """Verify every local ref in ``html`` exists under ``out_dir``.

    Returns ``{"ok": bool, "missing_assets": [sorted paths], "checked": int}``.
    """
    out_dir = Path(out_dir)
    refs = extract_local_refs(html)
    missing = sorted(r for r in refs if not (out_dir / r).is_file())
    violations = find_path_violations(html)
    return {
        "ok": not missing and not violations,
        "missing_assets": missing,
        "path_violations": violations,
        "checked": len(refs),
    }
