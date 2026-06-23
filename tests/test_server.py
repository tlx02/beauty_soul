"""HTTP service tests — encodes the §16 acceptance cases.

Real LLM/Chromium calls are avoided by injecting a fake ``run_beautify`` that
simulates the pipeline (overwrites index.html, adds a generated asset). This
lets us test the orchestration contract: source-asset preservation (§7.3),
output integrity (§7.4), structured errors (§8), and graceful ElevenLabs
fallback (§9) — deterministically and offline.
"""
import asyncio
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import beauty_soul.server as server
from beauty_soul.server import app, build_health


# ── helpers ─────────────────────────────────────────────────────────────────--

def make_bundle(*, manifest=None, index_html="<html>game</html>", source_assets=None) -> bytes:
    """Build a KiX-standard input bundle zip."""
    manifest = manifest if manifest is not None else {
        "schema_version": "soul-input/v1",
        "request_id": "kix-order-1",
        "game_slug": "match3_ab12",
        "game_name": "Match 3",
        "vertical": "coffee",
        "locale": "zh-Hans-SG",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest is not None:
            z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("game/index.html", index_html)
        for rel, data in (source_assets or {}).items():
            z.writestr(f"game/{rel}", data)
    return buf.getvalue()


def fake_runner_factory(*, out_index_html, new_assets=None):
    """Return a fake run_beautify that overwrites index.html + writes new assets."""
    def _fake(game_dir, out_dir, *, config=None, on_progress=None):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(out_index_html, encoding="utf-8")
        for rel, data in (new_assets or {}).items():
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return {
            "ok": True, "html_path": str(out / "index.html"), "out_dir": str(out),
            "assets_dir": str(out / "assets"),
            "report": {"passes_completed": ["pass1", "pass5"], "smoke_test": {"passed": True}, "visual_audit": None},
            "error": None, "error_class": None,
        }
    return _fake


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    # deterministic health
    monkeypatch.setattr(server, "collect_checks", lambda: {
        "openrouter_key": True, "elevenlabs_key": True,
        "playwright": True, "chromium": True, "trinity_protocol": True, "core_ok": True,
    })
    return TestClient(app)


# ── build_health (pure) ───────────────────────────────────────────────────────

def test_build_health_ready():
    body, status = build_health({"trinity_protocol": True, "chromium": True, "core_ok": True})
    assert status == 200 and body["ready"] is True and body["ok"] is True


def test_build_health_503_when_chromium_unavailable():
    """§5.4 / §16-11: a key dep unavailable → 503, structured, never a hang."""
    body, status = build_health({"trinity_protocol": True, "chromium": False, "core_ok": True})
    assert status == 503 and body["ready"] is False


def test_build_health_missing_openrouter_does_not_503():
    """§5.4: missing OPENROUTER_API_KEY must NOT make health unready."""
    body, status = build_health({"trinity_protocol": True, "chromium": True, "core_ok": True, "openrouter_key": False})
    assert status == 200


# ── §16-1: health endpoint ─────────────────────────────────────────────────---

def test_health_endpoint(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "beauty_soul"
    for k in ("openrouter_key", "elevenlabs_key", "playwright", "chromium", "trinity_protocol"):
        assert k in body["checks"]


# ── §16-2/6: beautify success → zip with root index.html + soul_report.json ────

def test_beautify_success(client, monkeypatch):
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html="<html>pretty</html>"))
    # manifest request_id ("kix-order-1") is primary; the header is only a fallback (§6.5)
    r = client.post("/v1/beautify",
                    files={"file": ("input_bundle.zip", make_bundle(), "application/zip")},
                    headers={"X-Kix-Request-Id": "req-123"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["x-soul-engine"] == "beauty_soul"
    assert r.headers["x-soul-request-id"] == "kix-order-1"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert "index.html" in names
    assert "soul_report.json" in names


# ── §16-3: missing game/index.html → missing_game_entry ───────────────────────-

def test_missing_game_entry(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({
            "schema_version": "soul-input/v1", "game_slug": "g", "game_name": "G"}))
        z.writestr("game/other.txt", "x")  # no index.html
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["error_class"] == "missing_game_entry"


# ── §16-4: missing OPENROUTER_API_KEY → no_api_key ────────────────────────────-

def test_no_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", make_bundle(), "application/zip")})
    assert r.status_code == 503
    assert r.json()["error_class"] == "no_api_key"


# ── §16-5/9: missing ELEVENLABS_API_KEY → still succeeds, report warns ─────────

def test_elevenlabs_fallback(client, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html="<html>pretty</html>"))
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", make_bundle(), "application/zip")})
    assert r.status_code == 200
    report = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("soul_report.json"))
    assert report["sound"]["generated"] is False
    assert report["sound"]["skipped_reason"] == "missing_elevenlabs_key"


# ── §16-7/8: source assets preserved without relying on KiX backfill ──────────-

def test_source_assets_preserved(client, monkeypatch):
    # Pipeline rewrites index.html to reference a DYNAMIC source asset and adds a new one.
    html = '<script>const INGREDIENTS=[{src:"assets/sprite_patty.png"}]</script><img src="assets/bg.png">'
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html=html, new_assets={"assets/bg.png": b"NEWBG"}))
    bundle = make_bundle(index_html=html, source_assets={"assets/sprite_patty.png": b"PATTY"})
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", bundle, "application/zip")})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.read("assets/sprite_patty.png") == b"PATTY"   # source asset carried through
    assert z.read("assets/bg.png") == b"NEWBG"             # generated asset present


# ── §16-9: referenced asset missing everywhere → asset_integrity_failed ───────-

def test_asset_integrity_failure(client, monkeypatch):
    # final HTML references assets/ghost.png that is neither in source nor generated
    html = '<img src="assets/ghost.png">'
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html=html))
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", make_bundle(index_html=html), "application/zip")})
    assert r.status_code == 500
    body = r.json()
    assert body["error_class"] == "asset_integrity_failed"
    assert "assets/ghost.png" in body["report"]["missing_assets"]


# ── §16-10: concurrent requests use isolated temp dirs ────────────────────────-

def test_requests_isolated(client, monkeypatch):
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html="<html>ok</html>"))
    man_a = {"schema_version": "soul-input/v1", "request_id": "A", "game_slug": "g", "game_name": "G"}
    man_b = {"schema_version": "soul-input/v1", "request_id": "B", "game_slug": "g", "game_name": "G"}
    r1 = client.post("/v1/beautify",
                     files={"file": ("b.zip", make_bundle(manifest=man_a), "application/zip")})
    r2 = client.post("/v1/beautify",
                     files={"file": ("b.zip", make_bundle(manifest=man_b), "application/zip")})
    assert r1.headers["x-soul-request-id"] == "A"
    assert r2.headers["x-soul-request-id"] == "B"
    # temp dirs cleaned up after each request
    assert list(Path(server.WORK_ROOT).glob("*")) == []


def test_request_id_falls_back_to_header(client, monkeypatch):
    """§6.5: when the manifest omits request_id, the X-Kix-Request-Id header is used."""
    monkeypatch.setattr(server, "run_beautify",
                        fake_runner_factory(out_index_html="<html>ok</html>"))
    man = {"schema_version": "soul-input/v1", "game_slug": "g", "game_name": "G"}  # no request_id
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", make_bundle(manifest=man), "application/zip")},
                    headers={"X-Kix-Order-Id": "from-header"})
    assert r.status_code == 200
    assert r.headers["x-soul-request-id"] == "from-header"


# ── §7.2: absolute / escape path refs in output → asset_integrity_failed ──────-

def test_output_path_violation_fails(client, monkeypatch):
    html = '<img src="/tmp/leak.png">'
    monkeypatch.setattr(server, "run_beautify", fake_runner_factory(out_index_html=html))
    r = client.post("/v1/beautify",
                    files={"file": ("b.zip", make_bundle(index_html=html), "application/zip")})
    assert r.status_code == 500
    body = r.json()
    assert body["error_class"] == "asset_integrity_failed"
    assert "/tmp/leak.png" in body["report"]["path_violations"]


# ── §11 / service correctness: event loop must not block during a long job ─────

def _deterministic_health(monkeypatch):
    monkeypatch.setattr(server, "collect_checks", lambda: {
        "openrouter_key": True, "elevenlabs_key": False,
        "playwright": True, "chromium": True, "trinity_protocol": True, "core_ok": True})


def test_event_loop_not_blocked_during_beautify(monkeypatch):
    """A minutes-long beautify must not stall the event loop (else /v1/health can't
    answer Docker's healthcheck mid-job, §5.1). Detected via scheduling latency:
    a 50ms ticker must keep ticking while a 1s blocking job runs."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _deterministic_health(monkeypatch)

    def slow(game_dir, out_dir, *, config=None, on_progress=None):
        time.sleep(1.0)                                  # blocking work (LLM/Chromium stand-in)
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html>ok</html>")
        return {"ok": True, "html_path": str(out / "index.html"), "out_dir": str(out),
                "assets_dir": str(out / "assets"),
                "report": {"passes_completed": [], "smoke_test": {"passed": True}, "visual_audit": None},
                "error": None, "error_class": None}
    monkeypatch.setattr(server, "run_beautify", slow)

    async def run():
        loop = asyncio.get_event_loop()
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=10) as ac:
            async def do_beautify():
                r = await ac.post("/v1/beautify",
                                  files={"file": ("b.zip", make_bundle(), "application/zip")})
                return "beautify", r.status_code, loop.time()

            async def do_health():
                await asyncio.sleep(0.2)                  # start once the job is underway
                r = await ac.get("/v1/health")
                return "health", r.status_code, loop.time()

            results = {name: (code, t) for name, code, t in
                       await asyncio.gather(do_beautify(), do_health())}
        assert results["beautify"][0] == 200 and results["health"][0] == 200
        # health (instant) must finish well before the 1s beautify — i.e. the loop
        # was free to serve it mid-job. If run_beautify blocks the loop, health is
        # queued behind it and finishes last.
        assert results["health"][1] < results["beautify"][1], \
            "health was blocked behind beautify — event loop is stalled by run_beautify"
    asyncio.run(run())


def test_concurrent_beautify_serialized(monkeypatch):
    """§11: concurrent jobs must not run simultaneously (global-state pollution)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    state = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def tracking(game_dir, out_dir, *, config=None, on_progress=None):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.3)
        with lock:
            state["cur"] -= 1
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("<html>ok</html>")
        return {"ok": True, "html_path": str(out / "index.html"), "out_dir": str(out),
                "assets_dir": str(out / "assets"),
                "report": {"passes_completed": [], "smoke_test": {"passed": True}, "visual_audit": None},
                "error": None, "error_class": None}
    monkeypatch.setattr(server, "run_beautify", tracking)

    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=10) as ac:
            man_a = {"schema_version": "soul-input/v1", "request_id": "A", "game_slug": "g", "game_name": "G"}
            man_b = {"schema_version": "soul-input/v1", "request_id": "B", "game_slug": "g", "game_name": "G"}
            r1, r2 = await asyncio.gather(
                ac.post("/v1/beautify", files={"file": ("a.zip", make_bundle(manifest=man_a), "application/zip")}),
                ac.post("/v1/beautify", files={"file": ("b.zip", make_bundle(manifest=man_b), "application/zip")}))
            assert r1.status_code == 200 and r2.status_code == 200
            assert state["max"] == 1, f"jobs overlapped (max concurrent={state['max']}) — not serialized"
    asyncio.run(run())
