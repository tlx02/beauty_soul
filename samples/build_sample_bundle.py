#!/usr/bin/env python3
"""Build a runnable beauty_soul input bundle (spec §6.3) from samples/sample_game.

Generates the source sprite PNG (so no binary is committed), then zips the
manifest + game/ tree into an input_bundle.zip ready for /v1/beautify.

    python samples/build_sample_bundle.py            # -> samples/input_bundle.zip
    python samples/build_sample_bundle.py out.zip    # custom output path

Then:
    curl -sS -X POST http://localhost:9102/v1/beautify \
      -H 'X-Kix-Engine: beauty_soul' \
      -F 'file=@samples/input_bundle.zip' -o output.zip
"""
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "sample_game"


def _write_sprite(path: Path) -> None:
    """Write a tiny solid PNG so the bundle has a real, referenced source asset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        Image.new("RGBA", (96, 64), (245, 166, 35, 255)).save(path)
    except ImportError:
        # Minimal 1x1 PNG fallback if Pillow is unavailable.
        path.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000d49444154789c6360000002000100ffff03000006000557bfabd400"
            "00000049454e44ae426082"
        ))


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "input_bundle.zip"
    _write_sprite(SRC / "game" / "assets" / "sprite_patty.png")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(SRC.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(SRC).as_posix())
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
