"""
GAL (Game Asset Library) search for the beautification pipeline.

Before AI-generating each manifest image, this module searches the pre-built GAL
index for a matching asset. On a hit, the asset is downloaded from S3 and resized
to the manifest's target dimensions, skipping the expensive generation call.

Search pipeline per asset:
  1. Map manifest filename → GAL image category
  2. Hard filter index assets by category (+ optional style narrowing)
  3. Rank filtered candidates by description keyword overlap
  4. LLM selects the single best candidate (or rejects all)
  5. S3 download → PIL resize → save as PNG

Requirements:
  boto3            pip install boto3
  AWS credentials  env vars or ~/.aws/credentials with read access to lobah-game-ai

Config key: "gal_index_path" in code/config.json
  e.g. "/Users/you/dev/stylepedia_ai/GAL/index.json"
  If absent or unreadable, GAL is silently disabled and generation proceeds normally.
"""

from __future__ import annotations

import re
import json
import tempfile
from pathlib import Path
from typing import Callable, Optional

try:
    import boto3 as _boto3_mod
    _BOTO3 = True
except ImportError:
    _boto3_mod = None
    _BOTO3 = False

_GAL_BUCKET = "lobah-game-ai"
_GAL_REGION = "ap-southeast-1"

# Manifest filename stem → primary GAL image category (first match wins)
_CATEGORY_RULES: list[tuple[str, str]] = [
    (r"^background",                                       "scene"),
    (r"^game_preview",                                     "scene"),
    (r"^title",                                            "logo"),
    (r"^btn_",                                             "ui_control"),
    # Buttons named without btn_ prefix
    (r"^play$|^home$|^pause$|^restart$|^start$",           "ui_control"),
    # UI panels / containers (broad match — panel, card, box, frame, hud)
    (r"card|panel|frame|box|container|overlay|hud",        "ui_container"),
    (r"sprite|entity|player|enemy|character|creature",     "entity"),
    (r"coin|gem|item|pickup|powerup|key|star|ball|bubble|balloon", "object"),
    (r"icon|symbol|badge|emblem",                          "symbol"),
]

# art_style string → GAL ALLOWED_STYLES value
_STYLE_ALIASES: dict[str, str] = {
    "cartoon":      "cartoon",
    "pixel art":    "pixel_art",
    "pixel_art":    "pixel_art",
    "pixelart":     "pixel_art",
    "painted":      "painted",
    "hand drawn":   "hand_drawn",
    "hand_drawn":   "hand_drawn",
    "realistic":    "realistic",
    "flat":         "flat",
    "flat design":  "flat",
    "stylized":     "stylized",
    "isometric":    "isometric",
    "low poly":     "low_poly",
    "low_poly":     "low_poly",
    "neon":         "neon",
    "magical":      "magical",
    "cel shaded":   "cel_shaded",
    "cel_shaded":   "cel_shaded",
}

# Words too common to be useful for matching
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on",
    "with", "this", "that", "is", "are", "be", "as", "by", "it", "its",
    "at", "from", "no", "not", "but", "has", "have", "been", "was", "use",
    "used", "used", "game", "screen", "image", "asset", "png", "size",
    "full", "card", "show", "style", "color", "colour", "design", "background",
})


class GALSearch:
    """Search the GAL index for reusable images before falling back to AI generation."""

    def __init__(
        self,
        index_path: str | Path,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> None:
        """
        Load the GAL index. Sets self.enabled = False if loading fails.

        Args:
            index_path:  Path to GAL/index.json
            top_k:       Max candidates to show the LLM for selection
            threshold:   Min keyword-overlap score (0.0 = no cutoff)
        """
        self.enabled = False
        self._images: list[dict] = []
        self.top_k = max(1, top_k)
        self.threshold = threshold

        try:
            raw = Path(index_path).read_text(encoding="utf-8")
            data = json.loads(raw)
            self._images = [
                a for a in data.get("assets", [])
                if a.get("type") == "image" and not a.get("is_meaningless", False)
            ]
            print(f"  [GAL] Loaded {len(self._images):,} images from {index_path}")
            self.enabled = bool(self._images)
        except Exception as exc:
            print(f"  [GAL] Index load failed ({exc}) — GAL disabled")

    # ── Category / style mapping ───────────────────────────────────────────────

    def _map_category(self, filename: str) -> str:
        stem = Path(filename).stem.lower()
        for pattern, cat in _CATEGORY_RULES:
            if re.search(pattern, stem):
                return cat
        return "misc"

    def _map_style(self, art_style: str) -> Optional[str]:
        lower = (art_style or "").lower().strip()
        if lower in _STYLE_ALIASES:
            return _STYLE_ALIASES[lower]
        for key, val in _STYLE_ALIASES.items():
            if key in lower:
                return val
        return None

    # ── Search ────────────────────────────────────────────────────────────────

    def find_image(
        self,
        manifest_entry: dict,
        style_lock: dict | None = None,
        llm_call_fn: Callable[[str], Optional[str]] | None = None,
    ) -> Optional[dict]:
        """
        Find the best-matching GAL asset for *manifest_entry*.

        Returns the GAL asset dict on a hit, or None if nothing suitable found.
        Caller is responsible for downloading and post-processing the asset.
        """
        if not self.enabled:
            return None

        filename = manifest_entry.get("filename", "")
        desc     = manifest_entry.get("description", "")
        category = self._map_category(filename)

        # ── Step 1: Hard filter by category ───────────────────────────────────
        candidates = [
            a for a in self._images
            if category in (a.get("categories") or [])
        ]

        if not candidates:
            return None

        # ── Step 2: Optional style narrowing ──────────────────────────────────
        gal_style = self._map_style(style_lock.get("art_style", "") if style_lock else "")
        if gal_style and len(candidates) >= 20:
            style_filtered = [
                a for a in candidates
                if gal_style in (a.get("styles") or [])
            ]
            if len(style_filtered) >= 5:
                candidates = style_filtered

        # ── Step 3: Keyword score + take top_k ────────────────────────────────
        scored = self._score(desc, candidates)
        top = [asset for _, asset in scored[: self.top_k]]
        if not top:
            return None

        # ── Step 4: LLM selection ─────────────────────────────────────────────
        if llm_call_fn and len(top) > 1:
            return self._llm_pick(filename, desc, style_lock, top, llm_call_fn)

        # No LLM: accept top candidate only if score meets threshold
        best_score = scored[0][0] if scored else 0.0
        return scored[0][1] if best_score > self.threshold else None

    def _score(self, query: str, candidates: list[dict]) -> list[tuple[float, dict]]:
        """Rank candidates by keyword overlap with query. Returns sorted (score, asset) list."""
        words = set(re.findall(r"\b[a-z]{3,}\b", query.lower())) - _STOPWORDS
        if not words:
            return [(0.0, c) for c in candidates]

        scored: list[tuple[float, dict]] = []
        for c in candidates:
            desc_words = set(re.findall(r"\b[a-z]{3,}\b", c.get("description", "").lower()))
            tag_words  = {t.lower() for t in (c.get("tags") or [])}
            overlap    = len(words & (desc_words | tag_words))
            scored.append((overlap / len(words), c))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _llm_pick(
        self,
        filename: str,
        desc: str,
        style_lock: dict | None,
        candidates: list[dict],
        llm_call_fn: Callable[[str], Optional[str]],
    ) -> Optional[dict]:
        """Ask the LLM to pick the best candidate. Returns asset dict or None."""
        style_note = ""
        if style_lock:
            art     = style_lock.get("art_style", "")
            mood    = style_lock.get("mood", "")
            palette = style_lock.get("hex_palette", [])
            if art:
                style_note = f"Art style: {art}."
                if mood:
                    style_note += f" Mood: {mood}."
                if palette:
                    style_note += f" Palette: {', '.join(palette[:4])}."

        lines = [
            "Select a pre-made game asset to reuse (avoids generating a new one).",
            "",
            f"Asset needed: {filename}",
            f"Description: {desc}",
        ]
        if style_note:
            lines.append(f"Style context: {style_note}")
        lines += ["", "Candidates from the asset library:"]

        for i, c in enumerate(candidates, 1):
            cdesc   = (c.get("description") or "")[:120]
            cstyle  = ", ".join(c.get("styles") or [])
            ccolors = ", ".join(c.get("colors") or [])
            ctags   = ", ".join(c.get("tags") or [])
            dim     = c.get("dimensions") or {}
            lines.append(
                f"{i}. {cdesc} "
                f"[style:{cstyle} | colors:{ccolors} | tags:{ctags} | "
                f"{dim.get('width','?')}x{dim.get('height','?')}]"
            )

        lines += [
            "",
            "Reply with ONLY the number of the best match (1–N).",
            "If none are visually appropriate for this asset, reply 0.",
            "Be lenient for backgrounds and UI panels (similar theme is fine).",
            "Be strict for logos, title text, and game-specific sprites.",
        ]

        try:
            result = llm_call_fn("\n".join(lines)) or ""
            m = re.search(r"\b(\d+)\b", result)
            if m:
                num = int(m.group(1))
                if 1 <= num <= len(candidates):
                    return candidates[num - 1]
        except Exception:
            pass
        return None

    # ── Download ──────────────────────────────────────────────────────────────

    def download_image(
        self,
        asset: dict,
        target_path: Path,
        target_w: int | None = None,
        target_h: int | None = None,
        is_transparent: bool = False,
    ) -> bool:
        """
        Download the GAL image from S3, resize to target dimensions, save as PNG.

        For opaque assets (e.g. backgrounds): scale-to-fill + center-crop.
        For transparent assets (e.g. buttons, sprites): scale-to-fit centered on
        a transparent canvas of exactly target_w × target_h.

        Returns True on success, False on any error (caller falls back to generation).
        """
        if not _BOTO3:
            print("  [GAL] boto3 unavailable — cannot download")
            return False

        asset_id = asset.get("id", "")
        fmt      = asset.get("format", "png")
        s3_key   = f"gal/images/{asset_id}.{fmt}"
        tmp_path = Path(tempfile.mktemp(suffix=f".{fmt}"))

        try:
            s3 = _boto3_mod.client("s3", region_name=_GAL_REGION)
            s3.download_file(_GAL_BUCKET, s3_key, str(tmp_path))

            from PIL import Image as PILImage  # deferred — already in pipeline deps
            img = PILImage.open(tmp_path).convert("RGBA")

            if target_w and target_h:
                if is_transparent:
                    # Scale to fit within target (preserve aspect ratio)
                    img.thumbnail((target_w, target_h), PILImage.LANCZOS)
                    canvas = PILImage.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                    x = (target_w - img.width) // 2
                    y = (target_h - img.height) // 2
                    canvas.paste(img, (x, y), img)
                    img = canvas
                else:
                    # Scale to fill + center-crop (background-style)
                    scale = max(target_w / img.width, target_h / img.height)
                    nw = max(int(img.width * scale), target_w)
                    nh = max(int(img.height * scale), target_h)
                    img = img.resize((nw, nh), PILImage.LANCZOS)
                    left = (nw - target_w) // 2
                    top  = (nh - target_h) // 2
                    img  = img.crop((left, top, left + target_w, top + target_h))

            target_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(target_path, format="PNG")
            return True

        except Exception as exc:
            print(f"  [GAL] Download failed ({asset_id}): {exc}")
            return False

        finally:
            tmp_path.unlink(missing_ok=True)

    # ── Convenience ───────────────────────────────────────────────────────────

    def stats_line(self, hits: int, total: int) -> str:
        if total == 0:
            return "[GAL] 0/0 assets"
        pct = hits * 100 // total
        return f"[GAL] {hits}/{total} assets from library ({pct}% hit rate)"
