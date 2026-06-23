"""beauty_soul HTTP service — KiX integration entry point.

Exposes an ASGI ``app`` with:

    GET  /v1/health    liveness + dependency probe (§5)
    POST /v1/beautify  zip-in / zip-out one-shot beautify (§6, §7)
    GET  /v1/version   build/version info (optional, §4)

Run with:

    uvicorn beauty_soul.server:app --host 0.0.0.0 --port 9102

The HTTP layer wraps the existing library entry ``beauty_soul.run_beautify``
(kept intact for rollback/debugging, §19). It owns the contracts the library
does not: safe zip extraction, source-asset preservation (§7.3), output asset
integrity (§7.4), per-request temp isolation (§10/§11), and structured JSON
failures (§8).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from beauty_soul import run_beautify
from beauty_soul.asset_integrity import check_integrity
from beauty_soul.bundle import (
    BundleError,
    safe_extract,
    load_manifest,
    validate_manifest,
    find_game_entry,
    backfill_source_assets,
    package_output,
)

ENGINE = "beauty_soul"
VERSION = "0.1.0"
REPORT_SCHEMA = "soul-output-report/v1"

# Per-request scratch lives here; each request gets its own uuid subdir (§11).
WORK_ROOT = os.environ.get("BEAUTY_SOUL_WORK_ROOT", os.path.join(tempfile.gettempdir(), "beauty_soul_work"))

# Serializes the pipeline: run_beautify -> beautify_games.configure() mutates module
# globals, so concurrent jobs would corrupt each other (single-concurrency, §11).
_PIPELINE_LOCK = threading.Lock()

app = FastAPI(title="beauty_soul", version=VERSION)


# ── health ──────────────────────────────────────────────────────────────────--

def collect_checks() -> dict:
    """Probe runtime dependencies for /v1/health (best-effort, never raises)."""
    checks = {
        "openrouter_key": bool(os.environ.get("OPENROUTER_API_KEY")),
        "elevenlabs_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "playwright": False,
        "chromium": False,
        "trinity_protocol": False,
        "core_ok": False,
    }
    try:
        import beauty_soul.beautify_games  # noqa: F401  core pipeline importable
        checks["core_ok"] = True
    except Exception:
        checks["core_ok"] = False
    try:
        import trinity_protocol  # noqa: F401
        checks["trinity_protocol"] = True
    except Exception:
        checks["trinity_protocol"] = False
    try:
        from playwright.sync_api import sync_playwright
        checks["playwright"] = True
        try:
            with sync_playwright() as p:
                checks["chromium"] = Path(p.chromium.executable_path).exists()
        except Exception:
            checks["chromium"] = False
    except Exception:
        checks["playwright"] = False
        checks["chromium"] = False
    return checks


def build_health(checks: dict) -> tuple[dict, int]:
    """Map a checks dict to a health body + status (§5.3/§5.4).

    Ready (200) requires the core module, trinity_protocol, and chromium.
    A missing OPENROUTER/ELEVENLABS key never makes health unready (§5.4).
    """
    ready = bool(checks.get("core_ok", True) and checks.get("trinity_protocol") and checks.get("chromium"))
    body = {
        "ok": ready,
        "engine": ENGINE,
        "version": VERSION,
        "ready": ready,
        "checks": {k: bool(checks.get(k, False)) for k in
                   ("openrouter_key", "elevenlabs_key", "playwright", "chromium", "trinity_protocol")},
    }
    return body, (200 if ready else 503)


@app.get("/v1/health")
def health():
    body, status = build_health(collect_checks())
    return JSONResponse(body, status_code=status)


@app.get("/v1/version")
def version():
    return {"engine": ENGINE, "version": VERSION, "report_schema": REPORT_SCHEMA}


# ── report ────────────────────────────────────────────────────────────────────

def build_soul_report(*, request_id, pipeline_report, assets_generated, assets_preserved,
                      integrity, sound, warnings, duration_ms) -> dict:
    """Assemble soul_report.json per §7.5 from pipeline + orchestration results."""
    pipeline_report = pipeline_report or {}
    passes = [{"name": name, "ok": True} for name in pipeline_report.get("passes_completed", [])]
    smoke = pipeline_report.get("smoke_test") or {}
    va = pipeline_report.get("visual_audit") or {}
    return {
        "schema_version": REPORT_SCHEMA,
        "engine": ENGINE,
        "engine_version": VERSION,
        "ok": True,
        "request_id": request_id,
        "summary": "Beautified layout, palette, assets and motion polish.",
        "warnings": warnings,
        "passes": passes,
        "assets_generated": assets_generated,
        "assets_preserved": assets_preserved,
        "asset_integrity": {
            "enabled": True,
            "ok": integrity["ok"],
            "source_assets_copied": bool(assets_preserved),
            "missing_assets": integrity["missing_assets"],
        },
        "sound": sound,
        "visual_audit": {"enabled": va != {}, "ok": bool(va.get("ok", va.get("passed"))) if va else False,
                         "screenshots": va.get("screenshots", [])},
        "smoke_test": {"enabled": smoke.get("passed") is not None,
                       "ok": bool(smoke.get("passed"))},
        "timings_ms": {"total": duration_ms},
    }


# ── failure helper ─────────────────────────────────────────────────────────---

def _fail(status: int, error_class: str, message: str, request_id: str | None,
          report: dict | None = None, retryable: bool = False) -> JSONResponse:
    body = {
        "ok": False,
        "engine": ENGINE,
        "request_id": request_id,
        "error_class": error_class,
        "error": message,
        "retryable": retryable,
        "report": report or {},
    }
    return JSONResponse(body, status_code=status)


# ── beautify ───────────────────────────────────────────────────────────────---

@app.post("/v1/beautify")
async def beautify_endpoint(request: Request, file: UploadFile = File(...)):
    # request_id fallback from headers until the manifest is parsed (§6.2/§6.5).
    hdr_request_id = (request.headers.get("X-Kix-Request-Id")
                      or request.headers.get("X-Kix-Order-Id"))
    zip_bytes = await file.read()
    # The pipeline is a blocking, minutes-long job — run it off the event loop so
    # /v1/health and other requests stay responsive during a job (§5.1).
    return await run_in_threadpool(_process_beautify, zip_bytes, hdr_request_id)


def _process_beautify(zip_bytes: bytes, hdr_request_id: str | None) -> Response:
    """Synchronous beautify pipeline — runs in a worker thread, never the loop."""
    request_id = hdr_request_id
    started = time.monotonic()

    os.makedirs(WORK_ROOT, exist_ok=True)
    work = Path(WORK_ROOT) / uuid.uuid4().hex     # isolated per request (§11)
    bundle_dir = work / "bundle"
    out_dir = work / "out"
    try:
        # 1. Safe extract + manifest + game entry (§13)
        safe_extract(zip_bytes, bundle_dir)
        manifest = load_manifest(bundle_dir)
        validate_manifest(manifest)
        game_dir = find_game_entry(bundle_dir)
        request_id = manifest.get("request_id") or hdr_request_id
        if not request_id:
            raise BundleError("bad_request", "request_id missing (manifest + headers)", 422)
        print(f"beauty_soul: request_id={request_id} slug={manifest.get('game_slug')} elevenlabs="
              f"{'on' if os.environ.get('ELEVENLABS_API_KEY') else 'off'}", flush=True)

        # 2. Fast-fail on missing core key before any expensive work (§5.4/§12)
        if not os.environ.get("OPENROUTER_API_KEY"):
            return _fail(503, "no_api_key", "OPENROUTER_API_KEY is not set", request_id, retryable=True)

        # 3. Seed output dir from full source game so source assets survive (§7.3)
        shutil.copytree(game_dir, out_dir)

        # 4. Run the existing pipeline (overwrites index.html, adds new assets)
        config = {"request_id": request_id, "game_slug": manifest.get("game_slug"),
                  "game_name": manifest.get("game_name")}
        # Serialize: the pipeline mutates module globals via configure() (§11).
        with _PIPELINE_LOCK:
            result = run_beautify(game_dir=str(game_dir), out_dir=str(out_dir), config=config)
        if not result.get("ok"):
            ec = result.get("error_class")
            mapped = "no_output" if ec == "no_output" else "internal_error"
            return _fail(500, mapped, result.get("error") or "beautify failed", request_id,
                         report={"stage": "pipeline"}, retryable=True)

        # 5. Guaranteed source-asset backfill before packaging (§7.3 safety net)
        backfill_source_assets(game_dir, out_dir)

        # 6. Output asset-integrity check (§7.4 P0) — never ship a broken zip
        html = (out_dir / "index.html").read_text(encoding="utf-8", errors="ignore")
        integrity = check_integrity(out_dir, html)
        if not integrity["ok"]:
            return _fail(500, "asset_integrity_failed",
                         "final HTML references missing or forbidden local assets",
                         request_id, report={"stage": "asset_integrity",
                                              "missing_assets": integrity["missing_assets"],
                                              "path_violations": integrity["path_violations"]})

        # 7. Build report (incl. ElevenLabs graceful fallback, §9) + package zip
        elevenlabs_on = bool(os.environ.get("ELEVENLABS_API_KEY"))
        warnings = [] if elevenlabs_on else ["ElevenLabs key missing; sound generation skipped."]
        sound = {"enabled": True, "generated": False,
                 "skipped_reason": None if elevenlabs_on else "missing_elevenlabs_key"}
        assets_generated = [{"path": p.relative_to(out_dir).as_posix(), "kind": "image"}
                            for p in (out_dir / "assets" / "images").rglob("*") if p.is_file()] \
            if (out_dir / "assets" / "images").exists() else []
        assets_preserved = [{"path": (Path("assets") / f.relative_to(game_dir / "assets")).as_posix(),
                             "source": "input_game"}
                            for f in (game_dir / "assets").rglob("*") if f.is_file()] \
            if (game_dir / "assets").exists() else []
        duration_ms = int((time.monotonic() - started) * 1000)
        report = build_soul_report(
            request_id=request_id, pipeline_report=result.get("report"),
            assets_generated=assets_generated, assets_preserved=assets_preserved,
            integrity=integrity, sound=sound, warnings=warnings, duration_ms=duration_ms)
        (out_dir / "soul_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

        zip_bytes_out = package_output(out_dir)
        headers = {
            "X-Soul-Engine": ENGINE,
            "X-Soul-Version": VERSION,
            "X-Soul-Request-Id": str(request_id),
            "X-Soul-Duration-Ms": str(duration_ms),
        }
        return Response(content=zip_bytes_out, media_type="application/zip", headers=headers)

    except BundleError as e:
        return _fail(e.status, e.error_class, e.message, request_id)
    except Exception as e:  # noqa: BLE001 — last-resort structured error (§8)
        return _fail(500, "internal_error", f"{type(e).__name__}: {e}", request_id,
                     report={"stage": "internal"}, retryable=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)   # per-request cleanup (§11)
