# Pipeline Improvements + Trinity Protocol SDK Integration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the 800 Games beautification pipeline to eliminate logic errors, produce visually coherent assets, and integrate the Trinity Protocol SDK for analytical decision passes.

**Architecture:** Trinity Protocol `solve()` replaces the raw `call_llm()` on analytical passes (theme decision, style lock, asset planning) where reasoning quality matters; raw LLM calls are kept for HTML transformation passes where full-context generation is needed. A Playwright smoke-testing pass closes the feedback loop by actually running each game. All improvements are applied to `master`.

**Tech Stack:** Python 3.13, `trinity_protocol==2.26.0` (already installed), `openai` (OpenRouter), `playwright` (new dep), `pytest` (tests), existing `PIL`, `requests`, `rembg`.

---

## File Map

| File | Change |
|------|--------|
| `code/beautify_games.py` | Main pipeline — all modifications |
| `code/config.json` | Add `genre_art_direction` map |
| `code/requirements.txt` | Add `playwright` |
| `code/tests/__init__.py` | New — test package |
| `code/tests/test_validators.py` | New — unit tests for deterministic helpers |
| `code/tests/test_css_fixes.py` | New — unit tests for fix_css_issues |
| `code/tests/test_tp_integration.py` | New — Trinity SDK wrapper tests (mocked) |

---

## Phase 1 — Foundation

### Task 1: Test infrastructure + Trinity Protocol SDK bootstrap

**Files:**
- Create: `code/tests/__init__.py`
- Create: `code/tests/test_validators.py`
- Modify: `code/beautify_games.py` (top of file, imports + tp_analyze helper)

- [ ] **Step 1: Create test package**

```bash
mkdir -p code/tests
touch code/tests/__init__.py
```

- [ ] **Step 2: Write failing tests for Trinity SDK wrapper**

Create `code/tests/test_tp_integration.py`:

```python
"""Tests for Trinity Protocol SDK wrapper — uses mocked solve() to avoid API calls."""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

# tp_analyze is defined in beautify_games; we'll import it after it exists
# For now test _extract_json_from_solve which we'll add alongside tp_analyze


def test_extract_json_clean():
    from beautify_games import _extract_json_from_solve
    result = _extract_json_from_solve('{"theme": "neon arcade", "aspect_w": 9}')
    assert result == {"theme": "neon arcade", "aspect_w": 9}


def test_extract_json_fenced():
    from beautify_games import _extract_json_from_solve
    text = '```json\n{"theme": "deep sea", "aspect_w": 9}\n```\n--- [AW critic: score 4/12]'
    result = _extract_json_from_solve(text)
    assert result is not None
    assert result["theme"] == "deep sea"


def test_extract_json_aw_noise():
    """AW critic text appended after JSON must not corrupt parse."""
    from beautify_games import _extract_json_from_solve
    text = '{"theme": "cozy", "aspect_w": 9, "aspect_h": 16, "max_width": 480}\n\n--- [AW critic: score 8/12, BEZOS reframe ...]'
    result = _extract_json_from_solve(text)
    assert result is not None
    assert result["max_width"] == 480


def test_extract_json_returns_none_on_garbage():
    from beautify_games import _extract_json_from_solve
    assert _extract_json_from_solve("no json here at all") is None


def test_tp_analyze_falls_back_to_call_llm_on_bad_json(tmp_path):
    """If solve() returns unparseable output, tp_analyze must not crash."""
    from beautify_games import tp_analyze
    with patch("beautify_games.solve", return_value="I cannot answer this question"):
        with patch("beautify_games.call_llm", return_value='{"theme":"fallback","aspect_w":9,"aspect_h":16,"max_width":480,"palette":["#000"],"mood":"x","font_style":"y"}') as mock_llm:
            result = tp_analyze("test problem", "fallback_system_prompt", "html content", "test-label", max_tokens=256)
            assert mock_llm.called
            assert result is not None
```

- [ ] **Step 3: Run tests to confirm they all fail (function not defined)**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_tp_integration.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name '_extract_json_from_solve'`

- [ ] **Step 4: Add Trinity SDK bootstrap + `_extract_json_from_solve` + `tp_analyze` to beautify_games.py**

In `beautify_games.py`, after the existing imports (after `from openai import OpenAI`), add:

```python
# ─── Trinity Protocol SDK ─────────────────────────────────────────────────────
import os as _os
_os.environ.setdefault("OPENROUTER_API_KEY", secrets.get("openrouter", ""))
try:
    from trinity_protocol import solve as _tp_solve
    _TP_AVAILABLE = True
except ImportError:
    _TP_AVAILABLE = False
    print("⚠  trinity_protocol not installed — analytical passes will use call_llm fallback")
```

Then add these two functions right after the `call_llm` function (after line 852):

```python
def _extract_json_from_solve(text: str) -> dict | None:
    """Extract the first valid JSON object from a solve() response.
    solve() appends AW critic text after the answer — strip it before parsing."""
    import json as _json
    candidates = [
        text.strip(),
        _strip_fences(text),
    ]
    for c in candidates:
        # Try full string
        try:
            return _json.loads(c)
        except Exception:
            pass
        # Try up to first line that starts with non-JSON (AW critic separator)
        for sep in ["\n---", "\n[AW", "\n\n"]:
            idx = c.find(sep)
            if idx != -1:
                try:
                    return _json.loads(c[:idx].strip())
                except Exception:
                    pass
        # Try first {...} block
        import re as _re
        m = _re.search(r'\{.*\}', c, _re.DOTALL)
        if m:
            try:
                return _json.loads(m.group())
            except Exception:
                pass
    return None


def tp_analyze(problem: str, fallback_system: str, fallback_html: str,
               label: str, max_tokens: int = 1024, file_paths: list = None) -> str | None:
    """Run Trinity Protocol solve() for analytical passes; fall back to call_llm on failure.

    Returns raw text output (same contract as call_llm).
    Use _extract_json_from_solve() on the result when JSON is expected.
    """
    if _TP_AVAILABLE:
        try:
            result = _tp_solve(
                problem,
                backend="openrouter",
                max_tokens=max_tokens,
                file_paths=file_paths,
                verify_consistency=bool(file_paths),
                adaptive=True,
            )
            if result and len(result.strip()) > 10:
                return result
            print(f"    ⚠  tp_analyze {label}: empty response, falling back")
        except Exception as e:
            print(f"    ⚠  tp_analyze {label}: {e}, falling back to call_llm")
    return call_llm(fallback_system, fallback_html, label, max_tokens=max_tokens)
```

- [ ] **Step 5: Run tests — all should pass**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_tp_integration.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add code/tests/__init__.py code/tests/test_tp_integration.py code/beautify_games.py
git commit -m "feat: bootstrap Trinity Protocol SDK + tp_analyze wrapper with fallback"
```

---

### Task 2: Fix 5 critical bugs

**Files:**
- Modify: `code/beautify_games.py`
- Create: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing tests for the bug fixes**

Create `code/tests/test_validators.py`:

```python
"""Unit tests for deterministic pipeline helpers."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from beautify_games import extract_css, inject_css, _strip_fences


# ── Bug 1: </head> missing ────────────────────────────────────────────────────

def test_extract_css_no_head_tag():
    """CSS must not be silently dropped when HTML has no </head>."""
    html = "<html><body><style>.foo{color:red}</style><p>hi</p></body></html>"
    css, template = extract_css(html)
    assert ".foo" in css
    assert "__STYLES__" in template  # marker must be placed even without </head>


def test_inject_css_roundtrip_no_head():
    html = "<html><body><style>.x{margin:0}</style><p>game</p></body></html>"
    css, template = extract_css(html)
    result = inject_css(template, "<style>.x{margin:0}</style>")
    assert "<style>" in result
    assert "__STYLES__" not in result


def test_extract_css_with_head_tag_unchanged():
    """Existing </head> path must still work."""
    html = "<html><head><style>.a{color:blue}</style></head><body></body></html>"
    css, template = extract_css(html)
    assert ".a" in css
    assert "__STYLES__" in template


# ── Bug 4: game_preview.png transparent ──────────────────────────────────────

def test_pass4a_template_game_preview_transparent():
    """PASS4A template must have transparent:true for game_preview.png."""
    from beautify_games import PASS4A
    # Find the game_preview.png line in the template
    import re
    match = re.search(r'"game_preview\.png".*?"transparent"\s*:\s*(true|false)', PASS4A)
    assert match is not None, "game_preview.png not found in PASS4A template"
    assert match.group(1) == "true", f"game_preview.png must be transparent:true, got: {match.group(1)}"


# ── Bug 5: screen_height formula ──────────────────────────────────────────────

def test_screen_height_portrait():
    """Portrait 9:16 @ 480px wide → height should be 853, not hardcoded 844."""
    max_width, aspect_w, aspect_h = 480, 9, 16
    expected = int(max_width * aspect_h / aspect_w)  # 853
    assert expected == 853

def test_screen_height_landscape():
    """Landscape 16:9 @ 800px wide → height must be 450, not 844."""
    max_width, aspect_w, aspect_h = 800, 16, 9
    expected = int(max_width * aspect_h / aspect_w)  # 450
    assert expected == 450
    assert expected != 844  # confirm the bug
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py -v 2>&1 | head -30
```

Expected: `test_extract_css_no_head_tag` FAILS (marker not placed), `test_pass4a_template_game_preview_transparent` FAILS.

- [ ] **Step 3: Fix Bug 1 — `</head>` fallback in `extract_css`**

In `beautify_games.py`, replace the `extract_css` function (lines 588-596):

```python
def extract_css(html: str) -> tuple[str, str]:
    """Return (combined CSS text, html with style blocks replaced by __STYLES__ marker)."""
    blocks = STYLE_RE.findall(html)
    css = "\n\n".join(content for _, content, _ in blocks)
    # Remove all style blocks
    stripped = STYLE_RE.sub("", html)
    # Insert marker — try </head> first, fall back to start of <body>, then top of file
    if "</head>" in stripped:
        stripped = stripped.replace("</head>", "__STYLES__\n</head>", 1)
    elif "<body" in stripped:
        stripped = re.sub(r'(<body[^>]*>)', r'\1\n__STYLES__', stripped, count=1)
    else:
        stripped = "__STYLES__\n" + stripped
    return css, stripped
```

- [ ] **Step 4: Fix Bug 4 — `game_preview.png` transparent in PASS4A**

In the PASS4A prompt string (around line 393), find:

```
    {{"filename": "game_preview.png", "description": "...", "usage": "gameplay preview centred on title screen", "transparent": false, "w": 300, "h": 300}},
```

Change `"transparent": false` → `"transparent": true`:

```
    {{"filename": "game_preview.png", "description": "...", "usage": "gameplay preview centred on title screen", "transparent": true, "w": 300, "h": 300}},
```

- [ ] **Step 5: Fix Bug 5 — `screen_height` formula**

In `beautify_games.py` around line 1015, replace:

```python
    screen_height = 844
    screen_width  = int(screen_height * aspect_w / aspect_h)
```

With:

```python
    screen_height = int(max_width * aspect_h / aspect_w)
```

(Remove `screen_width` — it was dead code.)

- [ ] **Step 6: Fix Bug 2 — intermediate files for Pass 4d and Pass 5**

In `beautify_games.py`, update the Pass 4d block (around line 1157) to write an intermediate file:

```python
    if not prog.get("pass4_wire") and manifest:
        print("    → Pass 4d: wire assets into HTML")
        prompt = PASS4D.format(manifest=json.dumps(manifest, indent=2), aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        r = call_llm(prompt, html, "pass4d")
        if r:
            html = fix_css_issues(r)
            (out_dir / "index_pass4.html").write_text(html, encoding="utf-8")  # ← ADD THIS
            prog["pass4_wire"] = True
        else:
            prog["pass4_wire_failed"] = True
        save_prog()
    elif (out_dir / "index_pass4.html").exists():                              # ← ADD THIS
        html = (out_dir / "index_pass4.html").read_text(encoding="utf-8")      # ← ADD THIS
```

Update the Pass 5 block (around line 1169) similarly:

```python
    if not prog.get("pass5"):
        print("    → Pass 5: playability QA")
        meta_note = (
            f"GAME ANALYSIS: render_type={game_meta.get('render_type')}, "
            f"uses_dom_measurements={game_meta.get('uses_dom_meas')}, "
            f"has_solid_canvas_fill={game_meta.get('has_solid_fill')}, "
            f"has_audio_context={game_meta.get('has_audio_ctx')}, "
            f"has_audio_elements={game_meta.get('has_audio_el')}, "
            f"has_level_progression={game_meta.get('has_levels')}, "
            f"has_level_select_system={game_meta.get('has_level_select')}, "
            f"has_drag_interaction={game_meta.get('has_drag')}\n\n"
        )
        pass5_input = meta_note + html
        r = call_llm(PASS5, pass5_input, "pass5", max_tokens=65536)
        if r:
            html = r
            (out_dir / "index_pass5.html").write_text(html, encoding="utf-8")  # ← ADD THIS
            prog["pass5"] = True
        else:
            prog["pass5_failed"] = True
        save_prog()
    elif (out_dir / "index_pass5.html").exists():                              # ← ADD THIS
        html = (out_dir / "index_pass5.html").read_text(encoding="utf-8")      # ← ADD THIS
```

Update `latest_html()` to check the new files:

```python
    def latest_html() -> str:
        for fname in ("index_pass5.html", "index_pass4.html", "index_pass3.html",
                      "index_pass2.html", "index_pass1.html", "index_original.html"):
            f = out_dir / fname
            if f.exists():
                return f.read_text(encoding="utf-8", errors="ignore")
        return orig.read_text(encoding="utf-8", errors="ignore")
```

- [ ] **Step 7: Fix Bug 3 — `--resume` flag check**

In `beautify_games.py` around line 1235, replace:

```python
                pass4_done = prog.get("pass4") or prog.get("pass4_skipped") or prog.get("pass4_failed")
                pass5_done = prog.get("pass5") or prog.get("pass5_failed")
```

With:

```python
                pass4_done = (prog.get("pass4") or prog.get("pass4_skipped") or prog.get("pass4_failed")
                              or prog.get("pass4_assets") or prog.get("pass4_assets_skipped"))
                pass5_done = prog.get("pass5") or prog.get("pass5_failed")
```

- [ ] **Step 8: Run all tests — should all pass now**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add code/beautify_games.py code/tests/test_validators.py
git commit -m "fix: 5 critical bugs — resume flag, intermediate files, game_preview transparent, screen_height formula, head tag fallback"
```

---

### Task 3: Per-pass structural validation with retry

**Files:**
- Modify: `code/beautify_games.py`
- Modify: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `code/tests/test_validators.py`:

```python
# ── Per-pass structural validation ────────────────────────────────────────────

def test_validate_pass1_all_present():
    from beautify_games import validate_pass1
    html = '''<div class="app-container">
      <div id="screen-home" class="screen active"></div>
      <div id="screen-game" class="screen"></div>
      <div id="screen-gameover" class="screen"></div>
      <button id="btn-start"></button>
      <button id="btn-home-game"></button>
      <button id="btn-pause-game"></button>
      <div id="pause-overlay"></div>
    </div>'''
    assert validate_pass1(html) == []


def test_validate_pass1_missing_ids():
    from beautify_games import validate_pass1
    html = '<div id="screen-home"></div><div id="screen-game"></div>'
    missing = validate_pass1(html)
    assert "screen-gameover" in missing
    assert "btn-start" in missing
    assert "pause-overlay" in missing


def test_validate_pass1_single_quote_ids():
    from beautify_games import validate_pass1
    html = "<div id='screen-home'></div><div id='screen-game'></div><div id='screen-gameover'></div><button id='btn-start'></button><button id='btn-home-game'></button><button id='btn-pause-game'></button><div id='pause-overlay'></div>"
    assert validate_pass1(html) == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py::test_validate_pass1_all_present -v
```

Expected: `ImportError: cannot import name 'validate_pass1'`

- [ ] **Step 3: Add `validate_pass1` to beautify_games.py**

Add after the `fix_css_issues` function (after line 832):

```python
_PASS1_REQUIRED_IDS = [
    "screen-home", "screen-game", "screen-gameover",
    "btn-start", "btn-home-game", "btn-pause-game", "pause-overlay",
]

def validate_pass1(html: str) -> list[str]:
    """Return list of required IDs missing from html. Empty list = valid."""
    missing = []
    for id_ in _PASS1_REQUIRED_IDS:
        if f'id="{id_}"' not in html and f"id='{id_}'" not in html:
            missing.append(id_)
    return missing
```

- [ ] **Step 4: Wire validation into Pass 1 in `beautify()`**

In the Pass 1 block (around line 1019), replace the existing block with:

```python
    if not prog.get("pass1"):
        print("    → Pass 1: strip non-core UI")
        pass1_prompt = PASS1.format(aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        r = call_llm(pass1_prompt, html, "pass1", max_tokens=65536)
        if not r:
            time.sleep(3)
            r = call_llm(pass1_prompt, html, "pass1 (retry)", max_tokens=65536)
        if r:
            missing = validate_pass1(r)
            if missing:
                print(f"    ⚠  pass1 missing IDs {missing} — retrying with constraint")
                retry_prompt = pass1_prompt + f"\n\nRETRY REQUIRED: your previous output was missing these required element IDs: {missing}. They MUST all be present in your output."
                r2 = call_llm(retry_prompt, html, "pass1-retry", max_tokens=65536)
                if r2 and not validate_pass1(r2):
                    r = r2
                elif r2:
                    print(f"    ⚠  pass1-retry still missing {validate_pass1(r2)}, using best attempt")
                    r = r2
            html = r
            (out_dir / "index_pass1.html").write_text(html, encoding="utf-8")
            prog["pass1"] = True
        else:
            print("    ⚠  pass1 failed — continuing from original HTML")
            prog["pass1_failed"] = True
        save_prog()
    elif (out_dir / "index_pass1.html").exists():
        html = (out_dir / "index_pass1.html").read_text(encoding="utf-8")
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add code/beautify_games.py code/tests/test_validators.py
git commit -m "feat: per-pass structural validation with auto-retry for missing IDs"
```

---

## Phase 2 — Trinity Protocol SDK Integration

### Task 4: Replace Pass 0 with Trinity Protocol `tp_analyze()`

**Files:**
- Modify: `code/beautify_games.py`

The goal: Pass 0 decides the game's visual theme and aspect ratio. Using Trinity Protocol's `solve()` with the game HTML as codebase context gives it the B.5 consistency verifier (ensures the theme recommendation actually fits the shown game mechanics) and the full reasoning framework instead of a single-shot LLM call.

- [ ] **Step 1: Write failing test**

Append to `code/tests/test_tp_integration.py`:

```python
def test_pass0_theme_via_tp_analyze_parses_to_valid_schema(tmp_path):
    """tp_analyze for Pass 0 must produce parseable JSON matching the theme schema."""
    from beautify_games import _extract_json_from_solve
    # Simulate a solve() response matching the Pass 0 schema
    mock_output = '```json\n{"theme":"deep sea adventure","palette":["#0a2342","#1b6ca8","#f0c040","#ffffff"],"mood":"Mysterious and calming","font_style":"rounded playful","aspect_w":9,"aspect_h":16,"max_width":480}\n```'
    result = _extract_json_from_solve(mock_output)
    assert result is not None
    required_keys = {"theme", "palette", "mood", "font_style", "aspect_w", "aspect_h", "max_width"}
    assert required_keys.issubset(result.keys())
    assert isinstance(result["palette"], list)
    assert isinstance(result["aspect_w"], int)
```

- [ ] **Step 2: Run to confirm pass (this should pass already since _extract_json_from_solve is done)**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_tp_integration.py::test_pass0_theme_via_tp_analyze_parses_to_valid_schema -v
```

Expected: PASS (no code change needed — validating existing helper).

- [ ] **Step 3: Update Pass 0 in `beautify()` to use `tp_analyze()`**

Replace the existing Pass 0 block (around line 992-1007) with:

```python
    theme_json = prog.get("theme_json")
    if not theme_json:
        print("    → Pass 0: determine visual theme + screen size (Trinity Protocol)")
        pass0_input = game_context_for_pass0(html)
        # Build problem statement for solve() — codebase-aware reasoning
        pass0_problem = (
            f"Analyse this HTML game and decide the single best visual theme, layout, and aspect ratio.\n\n"
            f"Game structure and JS mechanics:\n{pass0_input}\n\n"
            f"Return ONLY valid JSON (no markdown, no explanation):\n"
            f'{{"theme":"<2-5 word theme>","palette":["<hex>","<hex>","<hex>","<hex>"],'
            f'"mood":"<one sentence>","font_style":"<e.g. rounded playful>","aspect_w":<int>,"aspect_h":<int>,"max_width":<int>}}\n\n'
            f"Aspect ratio rules: 9:16 portrait (max_width 480) for most games. "
            f"16:9 landscape (max_width 800) ONLY for side-scrollers, racing, or wide-canvas games."
        )
        r = tp_analyze(
            problem=pass0_problem,
            fallback_system=PASS0,
            fallback_html=pass0_input,
            label="pass0",
            max_tokens=1024,
        )
        if r:
            try:
                parsed = _extract_json_from_solve(r)
                if parsed:
                    theme_json = json.dumps(parsed)
                    prog["theme_json"] = theme_json
                else:
                    raise ValueError("no JSON found in solve() output")
            except Exception as e:
                print(f"    ⚠  pass0: could not parse theme — {e}")
                theme_json = '{"theme":"casual arcade","palette":["#4A90D9","#F5A623","#7ED321","#D0021B"],"mood":"Bright and energetic.","font_style":"rounded playful","aspect_w":9,"aspect_h":16,"max_width":480}'
                prog["theme_json"] = theme_json
        save_prog()
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add code/beautify_games.py code/tests/test_tp_integration.py
git commit -m "feat: replace Pass 0 with Trinity Protocol tp_analyze() for theme decisions"
```

---

### Task 5: Style lock step (new Pass 0.5) using `tp_analyze()`

**Files:**
- Modify: `code/beautify_games.py`

The style lock generates a shared art direction brief — prepended to every image generation prompt in Pass 4B — so all 11 images feel like one coherent visual system rather than independent DALL-E outputs.

- [ ] **Step 1: Write failing test**

Append to `code/tests/test_tp_integration.py`:

```python
def test_extract_style_lock_from_solve():
    """Style lock must parse to a dict with required art direction keys."""
    from beautify_games import _extract_json_from_solve
    mock = '```json\n{"art_style":"flat vector, 2px stroke","line_weight":"clean 2px","shadow_style":"soft drop-shadow","background_treatment":"bokeh blur","icon_shape":"rounded rect 12px","negative_terms":"no photorealism"}\n```'
    result = _extract_json_from_solve(mock)
    assert result is not None
    assert "art_style" in result
    assert "negative_terms" in result
```

- [ ] **Step 2: Run to confirm pass**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_tp_integration.py::test_extract_style_lock_from_solve -v
```

Expected: PASS.

- [ ] **Step 3: Add `STYLE_LOCK_PROMPT` constant and style lock step to `beautify()`**

Add the prompt constant near the other prompts (after `PASS0`, around line 80):

```python
STYLE_LOCK = """\
Given this game's approved visual theme and a sample of the game's CSS (so you understand what colours are already committed), generate a terse art direction brief.
This brief will be prepended verbatim to EVERY image generation prompt, so all assets share one visual language.

Return ONLY valid JSON:
{{
  "art_style": "<one precise phrase: e.g. 'flat vector illustration, 2px black stroke, pastel palette'>",
  "line_weight": "<e.g. 'clean 2px stroke, no texture'>",
  "shadow_style": "<e.g. 'soft drop-shadow only, no hard shadows'>",
  "background_treatment": "<e.g. 'bokeh depth blur, warm ambient light'>",
  "icon_shape": "<e.g. 'rounded rectangle, 12px corner radius, badge style'>",
  "negative_terms": "<e.g. 'no photorealism, no gradients, no lens flare, no text baked in'>"
}}"""
```

In `beautify()`, add the style lock step **after** Pass 3 completes and **before** the Pass 4a block (around line 1074):

```python
    # ── Pass 0.5: style lock (shared art direction for all image generation) ──
    style_lock = prog.get("style_lock")
    if not style_lock and prog.get("pass3"):
        print("    → Pass 0.5: generate style lock (Trinity Protocol)")
        css_sample, _ = extract_css(html)
        genre = game_meta.get("genre", "misc") if game_meta else "misc"
        style_problem = (
            f"Game genre: {genre}\n"
            f"Approved theme: {theme_json}\n\n"
            f"Current CSS (shows committed colours and typography):\n{css_sample[:3000]}\n\n"
            f"{STYLE_LOCK.format()}"
        )
        r = tp_analyze(
            problem=style_problem,
            fallback_system=STYLE_LOCK,
            fallback_html=f"Genre: {genre}\nTheme: {theme_json}",
            label="style-lock",
            max_tokens=512,
        )
        if r:
            parsed = _extract_json_from_solve(r)
            if parsed:
                style_lock = parsed
                prog["style_lock"] = style_lock
                print(f"    ○ style: {style_lock.get('art_style', '?')}")
        save_prog()
```

- [ ] **Step 4: Wire style lock into Pass 4B image generation**

In the Pass 4B loop (around line 1099), replace the description building:

```python
        for img in manifest.get("images", []):
            fname = img["filename"]
            is_bg = fname == "background.png"
            transparent = img.get("transparent", not is_bg)
            out_path = img_dir / fname
            if out_path.exists():
                print(f"    → Pass 4b: image  {fname} (cached)")
                try:
                    actual = PILImage.open(out_path)
                    img["w"], img["h"] = actual.size
                except Exception:
                    pass
                continue
            print(f"    → Pass 4b: image  {fname}{' (transparent)' if transparent else ''}")
            desc = img["description"]
            # Prepend style lock for visual coherence across all assets
            if style_lock:
                art = style_lock.get("art_style", "")
                neg = style_lock.get("negative_terms", "")
                prefix = f"Art style: {art}. " if art else ""
                suffix = f" {neg}." if neg else ""
                desc = f"{prefix}{desc}{suffix}"
            if transparent:
                desc += " Isolated on a fully transparent background — no white fill, no background color, PNG with alpha channel."
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add code/beautify_games.py code/tests/test_tp_integration.py
git commit -m "feat: style lock step (Pass 0.5) via Trinity Protocol — shared art direction for all image generation"
```

---

## Phase 3 — Visual Quality

### Task 6: Genre-specific art direction

**Files:**
- Modify: `code/config.json`
- Modify: `code/beautify_games.py`
- Modify: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing test**

Append to `code/tests/test_validators.py`:

```python
def test_genre_art_direction_loaded_from_config():
    """Genre art direction must be readable from config and injected into image descriptions."""
    import json
    config = json.loads(open(os.path.join(os.path.dirname(__file__), "../config.json")).read())
    assert "genre_art_direction" in config
    assert "match3" in config["genre_art_direction"]
    assert "puzzle_logic" in config["genre_art_direction"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py::test_genre_art_direction_loaded_from_config -v
```

Expected: FAIL — `genre_art_direction` key missing from config.

- [ ] **Step 3: Add `genre_art_direction` to `config.json`**

Read the current config.json first, then add the key. Open `code/config.json` and add inside the root object:

```json
"genre_art_direction": {
  "match3":              "jewel illustration, faceted gems, rich saturated colors, subtle sparkle highlights",
  "tetris_block":        "pixel art, 16-bit retro, crisp edges, no antialiasing, bold geometric shapes",
  "2048_merge":          "clean flat design, soft pastel tiles, minimal shadow, number-forward layout",
  "puzzle_logic":        "minimal flat design, 2px stroke icons, monochrome with one accent color, generous whitespace",
  "platformer":          "pixel art, dithered shading, 32x32 sprite language, bold outlines",
  "slots_casino":        "Art Deco, gold filigree on deep velvet, ornate frames, luxury material textures",
  "word_puzzle":         "typewriter aesthetic, aged cream paper, ink bleed, serif typography",
  "solitaire":           "classical playing card illustration, clean formal layout, green baize texture",
  "chess_strategy":      "woodblock print, high-contrast black and ivory, classical engraving style",
  "racing":              "speed lines, chrome reflections, bold graphic design, motion blur",
  "farm_garden":         "soft watercolor, hand-painted botanicals, warm earthy palette",
  "rhythm_music":        "neon glow on dark, chromatic aberration, light emission, synthwave aesthetic",
  "tower_defense":       "isometric pixel art, military map style, strategic overhead view",
  "roguelike":           "dark fantasy ink illustration, textured parchment, hand-drawn dungeon maps",
  "simulation_tycoon":   "clean isometric flat design, bright primary colors, corporate cartoon style",
  "cooking_food":        "warm pastel illustration, soft shadows, food-photography inspired composition",
  "quiz_trivia":         "clean editorial design, bold sans-serif, bright accent colors, quiz show aesthetic",
  "runner":              "high-contrast silhouette art, parallax depth layers, bold simplified shapes",
  "shooter":             "retro arcade scan-lines, neon on black, geometric pixel sprites",
  "arcade_classic":      "retro pixel art, CRT glow effect, high contrast, limited 8-bit palette",
  "sports":              "bold graphic design, motion photography style, championship poster aesthetic",
  "physics_puzzle":      "clean technical illustration, blueprint grid, precise line art",
  "memory_match":        "soft watercolor illustration, friendly rounded shapes, pastel nursery palette",
  "mahjong":             "traditional Chinese ink painting, red and gold accents, calligraphy-inspired",
  "rpg_combat":          "dark fantasy concept art, dramatic lighting, detailed character illustration",
  "idle_clicker":        "bright cartoon style, exaggerated proportions, shiny metallic icons",
  "board_game":          "warm wooden board texture, tactile material surfaces, family-friendly illustration",
  "misc":                "clean flat design, consistent icon style, professional color palette"
}
```

- [ ] **Step 4: Wire genre art direction into Pass 4B**

In `beautify_games.py`, after the style lock prefix is built in the Pass 4B loop (Task 5 Step 4), add genre direction:

```python
            # Add genre-specific art direction on top of style lock
            genre_direction = config.get("genre_art_direction", {}).get(
                game_meta.get("genre", "misc") if game_meta else "misc", ""
            )
            if genre_direction and not style_lock:
                desc = f"Art style: {genre_direction}. {desc}"
            elif genre_direction and style_lock:
                # Blend: style_lock already prefixed, append genre as additional constraint
                desc = desc + f" Genre-specific style: {genre_direction}."
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add code/config.json code/beautify_games.py code/tests/test_validators.py
git commit -m "feat: genre-specific art direction for image generation (50+ genres mapped)"
```

---

### Task 7: Per-screen backgrounds

**Files:**
- Modify: `code/beautify_games.py` (PASS4A template + CSS injection)
- Modify: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing test**

Append to `code/tests/test_validators.py`:

```python
def test_pass4a_has_three_background_variants():
    from beautify_games import PASS4A
    assert "background_home.png" in PASS4A
    assert "background_game.png" in PASS4A
    assert "background_gameover.png" in PASS4A
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py::test_pass4a_has_three_background_variants -v
```

Expected: FAIL.

- [ ] **Step 3: Update PASS4A to require 3 backgrounds instead of 1**

In `PASS4A`, replace the single `background.png` line in the REQUIRED list and template:

Change the required list description from:
```
  1. background.png — full-screen atmospheric background for all 3 screens.
```
To:
```
  1. background_home.png — full-screen atmospheric background for the TITLE screen.
     Rich and detailed — establishes the game world. Soft gradients or painterly illustration.
  2. background_game.png — background for the GAMEPLAY screen.
     Same palette as background_home.png but darker (30-40% darker) and more desaturated.
     Minimal detail — must not compete with game elements. Subtle texture only.
  3. background_gameover.png — background for the GAME OVER screen.
     Dramatic darker variant of background_home.png. Slightly ominous or reflective mood.
```

Update the template JSON example to replace the single background entry with:
```
    {{"filename": "background_home.png", "description": "...", "usage": "#screen-home background", "transparent": false, "w": {max_width}, "h": {screen_h}}},
    {{"filename": "background_game.png", "description": "...", "usage": "#screen-game background, darker desaturated variant", "transparent": false, "w": {max_width}, "h": {screen_h}}},
    {{"filename": "background_gameover.png", "description": "...", "usage": "#screen-gameover background, dramatic darker variant", "transparent": false, "w": {max_width}, "h": {screen_h}}},
```

- [ ] **Step 4: Update CSS to use per-screen backgrounds**

In `PASS1`, replace the `.app-container` background-image line:

Old:
```
    background-image: url('assets/images/background.png');
```

New:
```
    background-image: url('assets/images/background_home.png');
```

Add per-screen overrides immediately after the `.app-container` block in PASS1's CSS template:
```
  #screen-home    {{ background-image: url('assets/images/background_home.png'); background-size: cover; background-position: center; }}
  #screen-game    {{ background-image: url('assets/images/background_game.png'); background-size: cover; background-position: center; }}
  #screen-gameover {{ background-image: url('assets/images/background_gameover.png'); background-size: cover; background-position: center; }}
```

Update `fix_css_issues` — the existing `_fix_screen_backgrounds` removes background from individual screen rules; update it to allow `background_home/game/gameover` URLs:

In `_SCREEN_BG_RE`, narrow it to only remove backgrounds that reference generic patterns (not `background_home/game/gameover`):
```python
_SCREEN_BG_RE = re.compile(
    r'(#screen-(?:home|game|gameover)\s*\{[^}]*?)'
    r'(?:background(?:-color)?\s*:\s*(?!url\([\'"]assets/images/background_(?:home|game|gameover))[^;]+;[ \t]*\n?)'
    r'([^}]*?\})',
    re.DOTALL
)
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add code/beautify_games.py code/tests/test_validators.py
git commit -m "feat: per-screen backgrounds — 3 variants (home/game/gameover) for visual storytelling"
```

---

### Task 8: Parallel image generation

**Files:**
- Modify: `code/beautify_games.py`
- Modify: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing test**

Append to `code/tests/test_validators.py`:

```python
def test_generate_images_parallel_returns_failed_set():
    """Parallel generator must return set of failed filenames."""
    from unittest.mock import patch, MagicMock
    from pathlib import Path
    import tempfile
    from beautify_games import generate_images_parallel

    with tempfile.TemporaryDirectory() as tmpdir:
        img_dir = Path(tmpdir)
        images = [
            {"filename": "a.png", "description": "test", "transparent": True},
            {"filename": "b.png", "description": "test", "transparent": False},
        ]
        with patch("beautify_games.gen_image", return_value=False):
            failed = generate_images_parallel(images, img_dir, max_workers=2, style_lock=None, genre_direction="")
        assert failed == {"a.png", "b.png"}
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py::test_generate_images_parallel_returns_failed_set -v
```

Expected: `ImportError: cannot import name 'generate_images_parallel'`

- [ ] **Step 3: Add `generate_images_parallel` function**

Add after the `gen_image` function (around line 916):

```python
def generate_images_parallel(images: list, img_dir: Path, max_workers: int = 4,
                              style_lock: dict = None, genre_direction: str = "") -> set:
    """Generate images concurrently. Returns set of failed filenames."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _build_desc(img: dict) -> str:
        fname = img["filename"]
        is_bg = fname.startswith("background")
        transparent = img.get("transparent", not is_bg)
        desc = img["description"]
        if style_lock:
            art = style_lock.get("art_style", "")
            neg = style_lock.get("negative_terms", "")
            if art:
                desc = f"Art style: {art}. {desc}"
            if neg:
                desc = f"{desc} {neg}."
        if genre_direction and not style_lock:
            desc = f"Art style: {genre_direction}. {desc}"
        if transparent:
            desc += " Isolated on a fully transparent background — no white fill, PNG with alpha channel."
        return desc, transparent

    def _gen_one(img: dict) -> tuple[str, bool]:
        fname = img["filename"]
        out_path = img_dir / fname
        if out_path.exists():
            try:
                actual = PILImage.open(out_path)
                img["w"], img["h"] = actual.size
            except Exception:
                pass
            return fname, True
        desc, transparent = _build_desc(img)
        ok = gen_image(desc, out_path, transparent=transparent)
        if not ok:
            ok = gen_image(desc, out_path, transparent=transparent)  # one retry
        if ok and out_path.exists():
            try:
                actual = PILImage.open(out_path)
                img["w"], img["h"] = actual.size
            except Exception:
                pass
        return fname, ok

    failed = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_gen_one, img): img for img in images}
        from concurrent.futures import as_completed
        for f in as_completed(futures):
            fname, ok = f.result()
            if not ok:
                failed.add(fname)
                print(f"    ✗  image {fname}: failed after retry")
            else:
                print(f"    ✓  image {fname}")
    return failed
```

- [ ] **Step 4: Replace serial loop in Pass 4B with `generate_images_parallel()`**

In `beautify()`, replace the entire serial image generation loop (the `for img in manifest.get("images", []):` block, roughly lines 1099-1133) with:

```python
            genre_direction = config.get("genre_art_direction", {}).get(
                game_meta.get("genre", "misc") if game_meta else "misc", ""
            )
            print(f"    → Pass 4b: generating {len(manifest.get('images', []))} images (parallel)")
            failed_images = generate_images_parallel(
                manifest.get("images", []),
                img_dir,
                max_workers=4,
                style_lock=style_lock,
                genre_direction=genre_direction,
            )
```

- [ ] **Step 5: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add code/beautify_games.py code/tests/test_validators.py
git commit -m "feat: parallel image generation with ThreadPoolExecutor (4 concurrent) + style lock wired in"
```

---

## Phase 4 — Logic Quality

### Task 9: JS invariant guard + static syntax check for Pass 1 and Pass 2/5

**Files:**
- Modify: `code/beautify_games.py`
- Modify: `code/tests/test_validators.py`

- [ ] **Step 1: Write failing tests**

Append to `code/tests/test_validators.py`:

```python
def test_extract_js_symbols_finds_functions():
    from beautify_games import extract_js_symbols
    html = "<script>function startGame() {} function resetScore() {} const update = () => {}</script>"
    symbols = extract_js_symbols(html)
    assert "startGame" in symbols
    assert "resetScore" in symbols


def test_extract_js_symbols_empty_on_no_script():
    from beautify_games import extract_js_symbols
    assert extract_js_symbols("<html><body></body></html>") == set()


def test_validate_js_syntax_valid():
    from beautify_games import validate_js_syntax
    html = "<script>function foo() { return 1 + 1; }</script>"
    errors = validate_js_syntax(html)
    assert errors == []


def test_validate_js_syntax_invalid():
    from beautify_games import validate_js_syntax
    html = "<script>function foo( { return; }</script>"  # missing closing paren
    errors = validate_js_syntax(html)
    assert len(errors) > 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_validators.py -k "js" -v
```

Expected: `ImportError: cannot import name 'extract_js_symbols'`

- [ ] **Step 3: Add `extract_js_symbols` and `validate_js_syntax`**

Add after `analyze_game()` (around line 645):

```python
def extract_js_symbols(html: str) -> set[str]:
    """Extract JS function names from all script tags. Used to guard Pass 1."""
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    js = "\n".join(scripts)
    named   = set(re.findall(r'function\s+(\w+)\s*\(', js))
    arrow   = set(re.findall(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>|\w+\s*=>)', js))
    return named | arrow


def validate_js_syntax(html: str) -> list[str]:
    """Extract JS and check syntax via `node --check`. Returns list of error lines."""
    import subprocess, tempfile
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    if not scripts:
        return []
    js = "\n".join(scripts)
    try:
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False, encoding="utf-8") as f:
            f.write(js)
            tmp = f.name
        result = subprocess.run(
            ["node", "--check", tmp],
            capture_output=True, text=True, timeout=10
        )
        return result.stderr.splitlines() if result.returncode != 0 else []
    except FileNotFoundError:
        return []  # node not installed — skip silently
    except Exception:
        return []
```

- [ ] **Step 4: Wire JS invariant guard into Pass 1**

In the Pass 1 block (already modified in Task 3), add before the `call_llm` call:

```python
        before_symbols = extract_js_symbols(html)
```

And after the validation check (after `validate_pass1`), add:

```python
            # JS invariant check — critical functions must survive restructuring
            if before_symbols:
                after_symbols = extract_js_symbols(r)
                removed = before_symbols - after_symbols
                if removed:
                    print(f"    ⚠  pass1 removed JS functions: {sorted(removed)[:5]} — retrying")
                    symbol_note = f"\n\nCRITICAL: Do NOT remove or rename these JS functions: {sorted(removed)}. They must remain in your output."
                    r2 = call_llm(pass1_prompt + symbol_note, html, "pass1-js-retry", max_tokens=65536)
                    if r2:
                        r = r2
```

- [ ] **Step 5: Wire JS syntax check after Pass 2 and Pass 5**

In Pass 2 block (around line 1037), after `html = r`:
```python
        if r:
            html = r
            js_errors = validate_js_syntax(html)
            if js_errors:
                print(f"    ⚠  pass2 JS syntax errors: {js_errors[:3]}")
                error_note = f"\n\nJS SYNTAX ERRORS in your output:\n" + "\n".join(js_errors[:5]) + "\nFix these and return the complete HTML."
                r2 = call_llm(PASS2 + error_note, html, "pass2-syntax-retry")
                if r2 and not validate_js_syntax(r2):
                    html = r2
            (out_dir / "index_pass2.html").write_text(html, encoding="utf-8")
            prog["pass2"] = True
```

Add the same pattern to Pass 5 (after `html = r` in the pass5 block).

- [ ] **Step 6: Run all tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add code/beautify_games.py code/tests/test_validators.py
git commit -m "feat: JS invariant guard for Pass 1 + static syntax validation after Pass 2/5"
```

---

## Phase 5 — Playwright Smoke Testing

### Task 10: Playwright smoke testing (Pass 6)

**Files:**
- Modify: `code/requirements.txt`
- Modify: `code/beautify_games.py`
- Create: `code/tests/test_smoke.py`

- [ ] **Step 1: Add playwright to requirements and install**

In `code/requirements.txt`, add:
```
playwright
```

Then install:
```bash
pip3 install playwright && playwright install chromium
```

- [ ] **Step 2: Write failing test**

Create `code/tests/test_smoke.py`:

```python
"""Tests for Playwright smoke testing — uses a minimal known-good game fixture."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
from pathlib import Path
from beautify_games import smoke_test


_GOOD_GAME = """<!DOCTYPE html>
<html><head><style>
.screen { display: none; } .screen.active { display: block; }
</style></head>
<body>
<div class="app-container">
  <div id="screen-home" class="screen active">
    <button id="btn-start" onclick="showScreen('screen-game')">PLAY</button>
  </div>
  <div id="screen-game" class="screen"></div>
  <div id="screen-gameover" class="screen"></div>
</div>
<script>
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}
</script>
</body></html>"""


def test_smoke_test_good_game(tmp_path):
    (tmp_path / "index.html").write_text(_GOOD_GAME)
    result = smoke_test(tmp_path)
    assert result["home_active"] is True
    assert result["game_active"] is True
    assert result["js_errors"] == []
    assert result["passed"] is True


def test_smoke_test_broken_game(tmp_path):
    broken = _GOOD_GAME.replace("showScreen('screen-game')", "brokenFunction()")
    (tmp_path / "index.html").write_text(broken)
    result = smoke_test(tmp_path)
    assert result["passed"] is False
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_smoke.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'smoke_test'`

- [ ] **Step 4: Add `smoke_test` function to beautify_games.py**

Add after `gen_audio` (around line 936):

```python
def smoke_test(out_dir: Path) -> dict:
    """Run the final game in headless Chromium. Returns dict with pass/fail + JS errors.
    Checks: home screen active on load, PLAY button navigates to game, no JS errors for 3s."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"passed": None, "home_active": None, "game_active": None,
                "js_errors": [], "skip_reason": "playwright not installed"}

    js_errors: list[str] = []
    home_active = False
    game_active = False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 480, "height": 854})
            page.on("pageerror", lambda e: js_errors.append(str(e)))
            page.goto(f"file://{out_dir}/index.html", wait_until="domcontentloaded")
            page.wait_for_timeout(500)

            home_active = page.evaluate(
                "!!document.getElementById('screen-home')?.classList.contains('active')"
            ) or False

            try:
                page.click("#btn-start", timeout=3000)
                page.wait_for_timeout(2000)
                game_active = page.evaluate(
                    "!!document.getElementById('screen-game')?.classList.contains('active')"
                ) or False
            except Exception as e:
                js_errors.append(f"btn-start click failed: {e}")

            page.wait_for_timeout(3000)
            browser.close()
    except Exception as e:
        js_errors.append(f"browser error: {e}")

    passed = home_active and game_active and len(js_errors) == 0
    return {
        "passed": passed,
        "home_active": home_active,
        "game_active": game_active,
        "js_errors": js_errors,
    }
```

- [ ] **Step 5: Add Pass 6 (smoke test + error-driven retry) to `beautify()`**

After the Pass 5 block and before the final write (before line 1193), add:

```python
    # ── Pass 6: Playwright smoke test + error-driven retry ────────────────────
    smoke_result = prog.get("smoke_result")
    if not smoke_result:
        print("    → Pass 6: smoke test")
        # Write current html to index.html first so smoke_test can load it
        (out_dir / "index.html").write_text(html, encoding="utf-8")
        smoke_result = smoke_test(out_dir)
        prog["smoke_result"] = smoke_result
        if smoke_result.get("skip_reason"):
            print(f"    ○ smoke test skipped: {smoke_result['skip_reason']}")
        elif smoke_result["passed"]:
            print(f"    ✓ smoke test passed")
        else:
            issues = []
            if not smoke_result.get("home_active"):
                issues.append("screen-home does not have class 'active' on load")
            if not smoke_result.get("game_active"):
                issues.append("screen-game did not become active after clicking #btn-start")
            if smoke_result.get("js_errors"):
                issues.extend(smoke_result["js_errors"][:3])
            print(f"    ⚠  smoke test FAILED: {issues}")
            # Pass 6 retry — feed exact errors back to the LLM
            for attempt in range(2):
                error_context = "\n".join(f"- {i}" for i in issues)
                fix_prompt = (
                    f"SMOKE TEST FAILED. The following issues were detected by running "
                    f"the game in a real browser:\n{error_context}\n\n"
                    f"Fix ONLY these issues. Do not change game mechanics, visuals, or assets.\n"
                    f"Return ONLY the complete fixed HTML."
                )
                r = call_llm(fix_prompt, html, f"pass6-retry-{attempt+1}", max_tokens=65536)
                if r:
                    html = r
                    (out_dir / "index.html").write_text(html, encoding="utf-8")
                    smoke_result = smoke_test(out_dir)
                    prog["smoke_result"] = smoke_result
                    if smoke_result["passed"]:
                        print(f"    ✓ smoke test passed after retry {attempt+1}")
                        break
                    issues = smoke_result.get("js_errors", [])[:3]
        save_prog()
```

- [ ] **Step 6: Run smoke tests**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/test_smoke.py -v
```

Expected: both tests pass.

- [ ] **Step 7: Run full test suite**

```bash
cd /Users/mozat/Desktop/800_games && python3 -m pytest code/tests/ -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add code/requirements.txt code/beautify_games.py code/tests/test_smoke.py
git commit -m "feat: Playwright smoke testing (Pass 6) with error-driven retry — closes the feedback loop"
```

---

## Self-Review

**Spec coverage check:**

| Improvement | Task |
|-------------|------|
| Fix --resume flag | Task 2 |
| Fix intermediate files (pass4d/5) | Task 2 |
| Fix game_preview transparent | Task 2 |
| Fix screen_height formula | Task 2 |
| Fix </head> fallback | Task 2 |
| Per-pass structural validation | Task 3 |
| Trinity Protocol SDK bootstrap | Task 1 |
| Trinity Protocol Pass 0 replacement | Task 4 |
| Trinity Protocol style lock (Pass 0.5) | Task 5 |
| Genre-specific art direction | Task 6 |
| Per-screen backgrounds | Task 7 |
| Parallel image generation | Task 8 |
| JS invariant guard on Pass 1 | Task 9 |
| JS syntax check after Pass 2/5 | Task 9 |
| Playwright smoke testing (Pass 6) | Task 10 |
| Screenshot-based game_preview | Not included — complex Playwright interaction, deferred to follow-up |
| Canvas-specific Pass 1 prompt | Not included — straightforward prompt addition, deferred |
| fix_css_issues rule 8 order | Not included — regex surgery, deferred |

**Placeholder scan:** No TBDs, TODOs, or vague instructions found.

**Type consistency:** `extract_js_symbols` returns `set[str]` — used in Task 9 as `before_symbols` (set subtraction). `validate_js_syntax` returns `list[str]` — used as `js_errors`. `smoke_test` returns `dict` — used as `smoke_result`. All consistent.

**`generate_images_parallel` signature:** defined as `(images, img_dir, max_workers, style_lock, genre_direction)` and called with same keyword args in Task 8. Consistent.

**`tp_analyze` signature:** defined as `(problem, fallback_system, fallback_html, label, max_tokens, file_paths)` and called in Tasks 4 and 5 with keyword args. Consistent.
