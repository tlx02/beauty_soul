"""Deterministic post-processor tests for the two KiX-reported output defects:
  P1 (§1): window-based fit() scaler conflicts with the centered .app-container
           frame -> clipped on wide viewports. fix_viewport_scaling() neutralizes it.
  P2 (§2): external Google-Fonts @import/<link> blocked by CSP. strip_external_resources()
           removes external resource references.
Behavior of P1 is additionally verified end-to-end with Playwright (see scratchpad probe);
these unit tests pin the structural contract + guard against false positives / non-idempotency.
"""
from beauty_soul.beautify_games import fix_viewport_scaling, strip_external_resources


_BUGGY = """<!doctype html><html><head><style>
#game{position:absolute;left:0;width:100%;height:1280px;transform-origin:top left;}
.app-container{width:min(100vw,480px);height:100dvh;overflow:hidden;}
</style></head><body>
<div class="app-container"><div id="game"></div></div>
<script>
const LW=720, LH=1280;
const game=document.getElementById('game');
function fit(){const s=Math.min(innerWidth/LW,innerHeight/LH);const w=LW*s,h=LH*s;
game.style.transform=`translate(${(innerWidth-w)/2}px,${(innerHeight-h)/2}px) scale(${s})`;}
addEventListener('resize',fit);addEventListener('load',fit);fit();
</script></body></html>"""


# ── P1: fix_viewport_scaling ────────────────────────────────────────────────--

def test_p1_injects_frame_based_corrector():
    out = fix_viewport_scaling(_BUGGY)
    assert "kix-viewport-fix" in out                      # sentinel present
    assert "clientWidth" in out                           # recomputes from frame, not window
    assert "innerWidth" not in out.split("kix-viewport-fix")[1]  # corrector uses frame, never innerWidth
    assert "720" in out                                   # parsed LW used


def test_p1_locks_logical_size():
    """§1.4: lock #game width/height to the logical canvas so the scale basis matches."""
    out = fix_viewport_scaling(_BUGGY)
    assert "width:720px" in out.replace(" ", "")
    assert "height:1280px" in out.replace(" ", "")


def test_p1_idempotent():
    once = fix_viewport_scaling(_BUGGY)
    twice = fix_viewport_scaling(once)
    assert once == twice                                  # second pass is a no-op
    assert once.count("<script>/* kix-viewport-fix */") == 1   # corrector injected exactly once


def test_p1_no_false_positive_when_pattern_absent():
    plain = "<html><head><style>.x{color:red}</style></head><body><div>hi</div></body></html>"
    assert fix_viewport_scaling(plain) == plain


def test_p1_skips_when_logical_dims_unparseable():
    """No LW/LH constants -> can't compute a correct scale -> leave untouched."""
    weird = ("<html><body><div id='game'></div><script>"
             "var game=document.getElementById('game');"
             "game.style.transform=`translate(${innerWidth}px,0) scale(1)`;</script></body></html>")
    assert fix_viewport_scaling(weird) == weird


# ── P2: strip_external_resources ────────────────────────────────────────────--

def test_p2_removes_external_font_import():
    css = ("<style>@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@800&display=swap');"
           ".x{font-family:'Nunito',sans-serif}</style>")
    out = strip_external_resources(css)
    assert "googleapis" not in out
    assert "@import" not in out
    assert "font-family:'Nunito'" in out.replace(" ", "")   # usage preserved (system fallback)


def test_p2_removes_external_link_and_preconnect():
    html = ('<head><link rel="preconnect" href="https://fonts.gstatic.com">'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito">'
            '</head><body></body>')
    out = strip_external_resources(html)
    assert "googleapis" not in out and "gstatic" not in out
    assert "<link" not in out


def test_p2_preserves_local_resources():
    html = ("<style>@import url('assets/fonts/local.css');"
            "@font-face{font-family:'N';src:url('assets/fonts/N.woff2')}</style>"
            '<img src="assets/x.png">')
    out = strip_external_resources(html)
    assert "assets/fonts/local.css" in out
    assert "assets/fonts/N.woff2" in out
    assert "assets/x.png" in out
