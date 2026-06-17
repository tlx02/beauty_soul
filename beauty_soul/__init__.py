"""beauty_soul — KiX game beautification library entry point."""

import os
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path


class _TeeWriter:
    """Tees stdout writes to the original stream AND an on_progress callback."""
    def __init__(self, orig, callback):
        self._orig = orig
        self._cb = callback
        self._buf = ""

    def write(self, s):
        self._orig.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                try:
                    self._cb(line)
                except Exception:
                    pass
        return len(s)

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


@contextmanager
def _tee_stdout(callback):
    orig = sys.stdout
    sys.stdout = _TeeWriter(orig, callback)
    try:
        yield
    finally:
        sys.stdout = orig


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

    ctx = _tee_stdout(on_progress) if on_progress else _no_op()

    try:
        with ctx:
            # Inject keys into env so trinity_protocol SDK picks them up too
            if config.get("openrouter_key"):
                os.environ["OPENROUTER_API_KEY"] = config["openrouter_key"]
            if config.get("elevenlabs_key"):
                os.environ["ELEVENLABS_API_KEY"] = config["elevenlabs_key"]

            from beauty_soul import beautify_games as bg
            bg.configure(config)

            print(f"beauty_soul: start {game_dir} -> {out_dir}", flush=True)
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


from contextlib import contextmanager as _cm

@_cm
def _no_op():
    yield
