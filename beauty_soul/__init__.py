"""beauty_soul — KiX game beautification library entry point."""

import os
import traceback
from pathlib import Path


def run_beautify(
    game_dir: str,
    out_dir: str,
    *,
    config: dict | None = None,
    on_progress=None,
) -> dict:
    """
    Beautify a game directory and write output to out_dir.

    Returns a dict with fields: ok, html_path, out_dir, assets_dir, report, error, error_class.
    Never raises — any failure is returned as ok=False.
    """
    config = config or {}

    def _log(m: str) -> None:
        print(m, flush=True)
        if on_progress:
            try:
                on_progress(m)
            except Exception:
                pass

    try:
        # Inject keys into env so trinity_protocol SDK picks them up
        if config.get("openrouter_key"):
            os.environ["OPENROUTER_API_KEY"] = config["openrouter_key"]
        os.environ.setdefault("OPENROUTER_API_KEY",
                              os.environ.get("OPENROUTER_API_KEY", ""))
        if config.get("elevenlabs_key"):
            os.environ["ELEVENLABS_API_KEY"] = config["elevenlabs_key"]

        from beauty_soul import beautify_games as bg
        bg.configure(config)

        _log(f"beauty_soul: start {game_dir} -> {out_dir}")
        ok = bg.beautify(Path(game_dir), out_dir=Path(out_dir))

        html = Path(out_dir) / "index.html"
        if not ok or not html.exists():
            return {
                "ok": False,
                "html_path": None,
                "out_dir": str(out_dir),
                "assets_dir": None,
                "report": None,
                "error": "beautify did not produce index.html",
                "error_class": "no_output",
            }

        return {
            "ok": True,
            "html_path": str(html.resolve()),
            "out_dir": str(Path(out_dir).resolve()),
            "assets_dir": str((Path(out_dir) / "assets").resolve()),
            "report": bg.last_report(),
            "error": None,
            "error_class": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "html_path": None,
            "out_dir": str(out_dir),
            "assets_dir": None,
            "report": None,
            "error": f"{e}\n{traceback.format_exc()}"[:2000],
            "error_class": type(e).__name__,
        }
