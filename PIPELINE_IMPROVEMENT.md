# 800 Games Beautification Pipeline — Trinity Protocol Improvement Analysis

> 分诊: code  
> Scope: all 1,567 lines across the pipeline, with focus on root causes behind "AI-generated look", logic errors surviving all 5 passes, and visual incoherence across generated assets.

---

## 1. Diagnosis — What the Pipeline Gets Right vs. Wrong

### Works well
- 3-screen template structure is clean and enforced
- CSS-only Pass 3 avoids truncation (smart)
- Cover image as visual reference improves theme accuracy
- `analyze_game()` gives free static signals (canvas/DOM/touch/levels)
- Progress tracking with `.progress.json` allows safe resumption

### Core failures (root causes follow)

| Symptom | Where it shows up |
|---------|------------------|
| Assets look like different artists made them | Title logo vs. HUD panel vs. background have incompatible styles |
| Background looks decorative, not game-native | DALL-E text prompt ≠ actual game art |
| game_preview.png shows a fictional scene | AI illustration of what the game "might look like" |
| Logic errors survive Pass 2 + Pass 5 | Games crash on first click, blank on load, break on restart |
| CSS and assets fight each other | Colors in Pass 3 CSS don't match colors in Pass 4 images |
| One background on 3 screens looks static | Title screen, gameplay, and game-over share identical bg |

---

## 2. Root Cause Map

### Why games look AI-generated

**RC-1: No shared visual vocabulary across asset generation.**  
Each of 11+ images is generated from an independent text description. DALL-E sees no prior images from the same game. The title logo, PLAY button, background, and pause card are made by "11 different artists with the same brief." Coherence requires a style lock — a single reference image fed to every subsequent generation call.

**RC-2: Text-to-image only; the cover image is never used as image input.**  
The cover image is passed to Pass 0 (theme naming) and Pass 3 (CSS vision), but never to the image generation API. DALL-E receives a *text description* derived from the cover, not the cover itself. This introduces a distortion layer: the description flattens visual specifics (lighting, linestyle, saturation curve) that an image-to-image pass would preserve.

**RC-3: DALL-E's default aesthetic is "soft-gradient colorful photorealism."**  
Without explicit art-direction parameters (flat vector / pixel art / hand-drawn / Art Deco), every game gets the same DALL-E look regardless of genre. The pipeline already detects 50+ genres in `extract_features.py` — this signal is unused at image generation time.

**RC-4: game_preview.png is fictional.**  
It's an AI illustration of what the game *might* look like, not what it actually looks like. The title screen preview never matches the real gameplay.

**RC-5: CSS and images are designed by separate LLM calls with no handoff.**  
Pass 3 locks the CSS color palette. Pass 4A describes images. These two passes don't see each other's output. The CSS might pick teal and gold; the generated background might be purple and red.

**RC-6: One background across all 3 screens.**  
`background.png` is set on `.app-container` and never changes. Title, gameplay, and game-over all look the same. No visual storytelling, no mood shift.

### Why logic errors survive 5 passes

**RC-7: No feedback loop — the game is never run.**  
The entire pipeline is LLM → file → LLM → file. Every fix is probabilistic. A crash on line 47 won't be caught until a human opens the game. There is zero automated execution testing anywhere in the pipeline.

**RC-8: Pass 1 is the highest-risk operation with the least verification.**  
Pass 1 rewrites the entire HTML structure — wrapping divs, renaming IDs, restructuring the DOM tree. The LLM can silently break JS by: changing an element ID that a game function references, moving a DOM element that JavaScript queries by position, removing event listeners that were on deleted elements. Passes 2 and 5 then try to *probabilistically repair* damage that Pass 1 may have caused. The right fix is to prevent Pass 1 from breaking invariants, not to hope Pass 2/5 catch it.

**RC-9: Errors compound across passes with no rollback.**  
If Pass 1 breaks a game, Pass 2 modifies already-broken HTML. Pass 3 styles it. By Pass 5, the original damage is buried under 4 layers of modification and very hard for an LLM to reason about. There's no per-pass "did this pass make it worse?" check.

---

## 3. Improvement Recommendations

Organized by impact tier. Each recommendation includes: what to change, why it works mechanically, and implementation sketch.

---

### TIER 1 — Close the feedback loop (biggest ROI)

#### I-1. Playwright smoke testing as Pass 6

**What:** After Pass 5, run the game in headless Chrome (Playwright) and check 4 invariants:
1. Page loads without uncaught JS errors (console.error count = 0)
2. `#screen-home` has class `active` at page load
3. Click `#btn-start` → `#screen-game` gains class `active` within 2s
4. Game runs for 3 seconds without uncaught errors

If any check fails → extract console errors + stack trace → send to a targeted **Pass 6** (error-driven fix) with the specific failure as context. Retry up to 2 times.

**Why it works:** This catches the most common failure modes (blank screen, JS crash, broken PLAY routing) with zero LLM cost when it passes and targeted context when it fails. The LLM in Pass 6 sees `TypeError: cannot read property 'style' of null at line 234` — that's a solvable problem. "Fix any bugs" is not.

**Implementation sketch:**
```python
from playwright.sync_api import sync_playwright

def smoke_test(out_dir: Path) -> dict:
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"file://{out_dir}/index.html")
        page.wait_for_timeout(500)
        
        # Check 1: home screen visible
        home_active = page.evaluate(
            "document.getElementById('screen-home')?.classList.contains('active')"
        )
        
        # Check 2: PLAY button navigates to game
        page.click("#btn-start")
        page.wait_for_timeout(2000)
        game_active = page.evaluate(
            "document.getElementById('screen-game')?.classList.contains('active')"
        )
        
        # Check 3: 3 seconds of gameplay without errors
        page.wait_for_timeout(3000)
        browser.close()
    
    return {
        "home_active": home_active,
        "game_active": game_active, 
        "js_errors": errors,
        "passed": home_active and game_active and len(errors) == 0
    }
```

Add to `requirements.txt`: `playwright`. Run `playwright install chromium` once.

---

#### I-2. Per-pass structural validation with error-aware retry

**What:** After each LLM pass, run a deterministic validator. If it fails, retry with the specific missing elements listed in the retry prompt.

```python
PASS1_REQUIRED_IDS = [
    "screen-home", "screen-game", "screen-gameover", 
    "btn-start", "btn-home-game", "btn-pause-game", "pause-overlay"
]

def validate_pass1(html: str) -> list[str]:
    missing = []
    for id_ in PASS1_REQUIRED_IDS:
        if f'id="{id_}"' not in html and f"id='{id_}'" not in html:
            missing.append(id_)
    return missing  # empty = valid

# In beautify():
r = call_llm(pass1_prompt, html, "pass1", ...)
missing = validate_pass1(r)
if missing:
    retry_prompt = pass1_prompt + f"\n\nRETRY: Your output was missing these required IDs: {missing}. They must be present."
    r = call_llm(retry_prompt, html, "pass1-retry", ...)
```

Similar validators for Pass 2 (`let paused` present, `#btn-resume-game` wired) and Pass 5 (`screen-home` has `active` in static HTML attribute).

**Why it works:** Structural failures are the most common and easiest to detect. A regex check costs nothing and eliminates the "Pass 1 silently produced a non-3-screen game" failure mode entirely.

---

#### I-3. JS invariant guard on Pass 1

**What:** Before Pass 1, extract critical JS symbols. After Pass 1, verify they survived.

```python
def extract_js_symbols(html: str) -> set[str]:
    functions = set(re.findall(r'function\s+(\w+)\s*\(', html))
    # Also extract functions assigned to variables: const foo = function...
    arrow_fns = set(re.findall(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\()', html))
    return functions | arrow_fns

before_symbols = extract_js_symbols(html)
r = call_llm(pass1_prompt, html, "pass1")
after_symbols = extract_js_symbols(r)
removed = before_symbols - after_symbols
if removed:
    print(f"    ⚠  Pass 1 removed {len(removed)} JS functions: {removed}")
    # If critical functions removed, retry with explicit list
    retry_note = f"\n\nCRITICAL: do NOT remove these JS functions: {sorted(removed)}"
    r = call_llm(pass1_prompt + retry_note, html, "pass1-retry")
```

**Why it works:** Pass 1's most dangerous failure is silently removing or renaming JS functions the game depends on. This check is zero-cost and catches it before it compounds through later passes.

---

### TIER 2 — Visual coherence

#### I-4. Style lock: generate a shared visual vocabulary before image generation

**What:** Add a new step between Pass 0 (theme) and Pass 4A (asset manifest). This step generates a concise "art direction brief" that is prepended to every subsequent image generation prompt.

```python
STYLE_LOCK_PROMPT = """\
Given this game's approved theme:
{theme_json}

And this game genre: {genre}

Generate a terse art direction brief for all assets in this game.
Return ONLY valid JSON:
{{
  "art_style": "one precise phrase: e.g. 'flat vector illustration, 2px black stroke, pastel palette'",
  "line_weight": "e.g. 'clean 2px stroke, no texture'",
  "shadow_style": "e.g. 'soft drop-shadow only, no hard shadows'",
  "background_treatment": "e.g. 'bokeh depth blur, warm ambient light'",
  "icon_shape": "e.g. 'rounded rectangle, 12px corner radius'",
  "negative_terms": "e.g. 'no photorealism, no gradients, no lens flare'"
}}"""
```

Prepend the style lock to every image description in Pass 4B:
```python
style_brief = f"{art_direction['art_style']}. {art_direction['negative_terms']}. "
desc = style_brief + img["description"]
```

**Why it works:** All 11 images now share a common linestyle, shadow treatment, and shape language. The difference between "coherent UI kit" and "11 independent AI images" is exactly this shared constraint.

---

#### I-5. Use cover image as image-to-image style reference

**What:** For `background.png`, `game_preview.png`, and panel images, pass the cover image directly to the generation API as a style reference instead of only using text description.

OpenRouter's API supports image input in the generation message. For background.png:

```python
def gen_image_with_reference(description: str, reference_b64: str, 
                               out: Path, transparent: bool = False) -> bool:
    """Generate image using a reference image for style consistency."""
    try:
        res = client.chat.completions.create(
            model=IMAGE_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{reference_b64}"}
                    },
                    {
                        "type": "text",
                        "text": f"Using the style and color palette of this reference image: {description}"
                    }
                ]
            }],
            extra_body={"modalities": ["image"], "background": "transparent" if transparent else "opaque"}
        )
        # ... rest of gen_image
```

Call `gen_image_with_reference(desc, cover_b64, out)` when `cover_b64` is available. Fall back to text-only generation when it isn't.

**Why it works:** The cover image already embeds the game's visual DNA: color temperature, illustration style, saturation level, complexity. Passing it as a reference is the closest thing to img2img style transfer available without a dedicated API.

---

#### I-6. Screenshot-based game_preview.png

**What:** After Pass 2 (logic fixed), take an actual screenshot of the gameplay screen to use as `game_preview.png` instead of generating one from a text description.

```python
def screenshot_gameplay(out_dir: Path) -> Path | None:
    """Run game in headless browser, click PLAY, screenshot gameplay."""
    from playwright.sync_api import sync_playwright
    out_path = out_dir / "assets" / "images" / "game_preview_screenshot.png"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 480, "height": 854})
            page.goto(f"file://{out_dir}/index_pass2.html")
            page.wait_for_timeout(300)
            page.click("#btn-start", timeout=2000)
            page.wait_for_timeout(1500)  # let game initialize
            # Screenshot just the game screen region
            screen = page.query_selector("#screen-game")
            if screen:
                screen.screenshot(path=str(out_path))
                browser.close()
                return out_path
    except Exception as e:
        print(f"    ⚠  screenshot failed: {e}")
    return None
```

If screenshot succeeds, use it as `game_preview.png` directly (skip DALL-E generation for that asset). The manifest still includes `game_preview.png` for wiring — just source it from the screenshot.

**Why it works:** The title screen preview will actually match the game. This single change eliminates the most visible sign that the assets are AI-generated.

---

#### I-7. Genre-specific art direction

**What:** The pipeline already detects genre (`extract_features.py`, 50+ genres). Use it to inject concrete art direction into every image prompt. Add a mapping table to `config.json`:

```json
"genre_art_direction": {
  "tetris_block":    "pixel art, 16-bit retro, crisp edges, no antialiasing",
  "match3":          "jewel illustration, faceted gems, rich saturated colors, subtle sparkle",
  "puzzle_logic":    "minimal flat design, monochrome with one accent color, geometric",
  "platformer":      "pixel art, dithered shading, 32x32 sprite language",
  "slots_casino":    "Art Deco, gold filigree, velvet textures, ornate frames",
  "word_puzzle":     "newspaper/typewriter aesthetic, aged cream paper, ink bleed",
  "chess_strategy":  "woodblock print, classical, high contrast black and cream",
  "racing":          "speed lines, chrome reflections, bold graphic design",
  "farm_garden":     "soft watercolor, hand-painted, botanical illustration style",
  "rhythm_music":    "neon glow, chromatic aberration, dark background, light emission",
  "solitaire":       "classic playing card illustration style, clean and formal"
}
```

In Pass 4B, prepend the genre art direction to each image description:
```python
genre_art = config.get("genre_art_direction", {}).get(game_meta.get("genre", ""), "")
if genre_art:
    desc = f"Art style: {genre_art}. {desc}"
```

**Why it works:** "Retro neon arcade" describes a mood. "Pixel art, 16-bit, crisp edges, no antialiasing" is a concrete visual instruction. The difference between a generic DALL-E output and a genre-authentic result is the specificity of the art direction.

---

#### I-8. Per-screen backgrounds

**What:** Generate 3 backgrounds instead of 1. Update CSS to assign backgrounds per screen.

Add to Pass 4A manifest template:
```json
{"filename": "background_home.png", "description": "..., rich detailed establishing shot of the game world, full atmosphere", "usage": "#screen-home", ...},
{"filename": "background_game.png", "description": "..., same palette but darker and desaturated, minimal detail to avoid competing with gameplay", "usage": "#screen-game", ...},
{"filename": "background_gameover.png", "description": "..., dramatic darker variant, slightly foreboding", "usage": "#screen-gameover", ...}
```

Update Pass 3 CSS template to reference per-screen backgrounds:
```css
#screen-home    { background-image: url('assets/images/background_home.png'); }
#screen-game    { background-image: url('assets/images/background_game.png'); }
#screen-gameover { background-image: url('assets/images/background_gameover.png'); }
```

Remove `background-image` from `.app-container` (no longer needed as a shared fallback).

**Why it works:** Visual storytelling is the difference between a "finished game" and "an app." The title screen should feel welcoming, gameplay should not compete with UI, game-over should feel momentous. One background cannot do all three.

---

#### I-9. Consistent button/panel family

**What:** Generate `btn_play.png` first (it's the hero button). For all other button/panel images in Pass 4B, pass `btn_play.png` as a reference image with the instruction "match the visual style of this button exactly — same shape, corner radius, material, and shadow treatment."

```python
# Generate btn_play first, then use it as reference for all other buttons
hero_button_path = img_dir / "btn_play.png"
# ... generate hero button ...

# Use hero as reference for family
button_family = {"btn_home.png", "btn_pause.png", "pause_card.png"}
for img in manifest["images"]:
    if img["filename"] in button_family and hero_button_path.exists():
        hero_b64 = encode_image_b64(hero_button_path)
        ok = gen_image_with_reference(img["description"], hero_b64, out_path, transparent=True)
```

**Why it works:** In real UI design, all buttons in a game share a single "button spec." Generating each one independently produces mismatched visual languages. Using the first-generated button as a style anchor costs one extra API parameter and eliminates the mismatch.

---

### TIER 3 — Logic quality improvements

#### I-10. Canvas-game-specific Pass 1 prompt

**What:** Canvas games are the highest-risk case for Pass 1. The LLM must preserve canvas dimensions, drawing coordinate systems, and rendering loops while restructuring the DOM. Add a dedicated Pass 1 variant for canvas games that is more conservative:

```python
PASS1_CANVAS_ADDENDUM = """
CANVAS GAMES — ADDITIONAL CONSTRAINTS:
- Do NOT change the canvas element's width or height attributes — they define the coordinate system
- Do NOT change the canvas element's id or class — JS rendering functions reference it
- Do NOT move the canvas element's position relative to its parent in the DOM
- Do NOT wrap the canvas in any new container element — only wrap the screen div itself
- The canvas MUST remain the direct child of #screen-game (or its existing parent)
- Only add the #game-topbar and pause overlay — do not restructure anything else inside #screen-game
"""

pass1_prompt = PASS1.format(...)
if game_meta.get("has_canvas"):
    pass1_prompt += PASS1_CANVAS_ADDENDUM
```

**Why it works:** Canvas games break in a specific way: the canvas coordinate system is set via HTML attributes (`width=600 height=800`), and any wrapping element with `overflow:hidden` that's smaller than the canvas clips the render. A canvas-specific prompt prevents the 3 most common canvas restructuring failures.

---

#### I-11. Static JS syntax validation after Pass 2 and Pass 5

**What:** After each LLM pass that modifies JS, extract the script content and validate its syntax using `py_mini_racer` (Python-embeddable V8) or by calling `node --check` on the extracted script.

```python
import subprocess
import tempfile

def validate_js(html: str) -> list[str]:
    """Extract JS and check for syntax errors. Returns list of errors."""
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    js = "\n".join(scripts)
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(js)
        tmp = f.name
    result = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    return result.stderr.splitlines() if result.returncode != 0 else []
```

If errors found → pass them as context to a retry: `"Your output has JS syntax errors: {errors}. Fix them and return the complete HTML."`

**Why it works:** JS syntax errors are the easiest logic errors to prevent and the most reliable to detect. This is deterministic, costs no API tokens, and eliminates the class of "game crashes immediately with SyntaxError" failures.

---

#### I-12. LLM-as-judge quality scoring

**What:** After Pass 5 completes (and smoke testing passes), run a final vision-based quality assessment using the LLM with screenshots:

```python
QUALITY_JUDGE_PROMPT = """\
You are a mobile game UI reviewer. Rate this game's visual quality and playability.
Screenshots: title screen (left) and gameplay screen (right).

Return ONLY valid JSON:
{
  "visual_score": <1-10, how polished does this look as a real game>,
  "coherence_score": <1-10, how well do assets fit together visually>,
  "top_issues": ["issue 1", "issue 2", "issue 3"],
  "verdict": "ship" | "needs_work" | "broken"
}"""

def judge_quality(out_dir: Path, cover_b64: str = None) -> dict | None:
    # Take screenshots of title and game screens
    # ... playwright ...
    # Combine into side-by-side image
    # Pass to LLM vision
```

Save score to `.progress.json`. Flag `verdict != "ship"` games for manual review queue. This surfaces the worst outputs without manually opening 300 games.

---

### TIER 4 — Pipeline reliability

#### I-13. Fix the 10 bugs from the code review

Priority order:
1. **`--resume` flag check** (line 1235) — change to `prog.get("pass4_assets") or prog.get("pass4_assets_skipped")`
2. **Pass 4d/5 intermediate files** — write `index_pass4.html` after Pass 4d and `index_pass5.html` after Pass 5; update `latest_html()` to check them
3. **`game_preview.png` transparent** (line 393) — change `"transparent": false` → `"transparent": true` in PASS4A template
4. **`</head>` guard** (line 595) — add fallback: if `</head>` not found, insert before `</body>` or at top of `<body>`
5. **`screen_height` formula** (line 1015) — change to `screen_height = int(max_width * aspect_h / aspect_w)`
6. **`fix_css_issues` rule 8 order** (line 787) — make the regex handle both orderings
7. Remaining 4 medium-severity bugs

---

#### I-14. Parallel image generation

**What:** Replace serial image generation with `ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def generate_images_parallel(images: list, img_dir: Path, max_workers: int = 4) -> set:
    failed = set()
    def gen_one(img):
        fname = img["filename"]
        is_bg = fname == "background.png"
        transparent = img.get("transparent", not is_bg)
        out_path = img_dir / fname
        if out_path.exists():
            return fname, True
        desc = img["description"]
        if transparent:
            desc += " Isolated on fully transparent background. PNG with alpha channel."
        ok = gen_image(desc, out_path, transparent=transparent)
        if not ok:
            ok = gen_image(desc, out_path, transparent=transparent)  # one retry
        return fname, ok
    
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(gen_one, img): img for img in images}
        for f in as_completed(futures):
            fname, ok = f.result()
            if not ok:
                failed.add(fname)
    return failed
```

**Why it works:** 11 images at ~8s each = 88s serial + 10s sleep = ~98s. At 4-parallel: ~24s. For 300 games, this alone saves ~6 hours of wall-clock time.

---

## 4. Priority Stack

Sequence below: do these in order. Each tier unblocks the next.

```
WEEK 1 — Fix what's broken
─────────────────────────────────────────────────────────
[ ] Fix 10 bugs from code review (especially #3, #2, #4)         I-13
[ ] Per-pass structural validation + error-aware retry             I-2
[ ] JS invariant guard on Pass 1                                   I-3
[ ] Static JS syntax check after Pass 2 + Pass 5                   I-11

WEEK 2 — Close the feedback loop
─────────────────────────────────────────────────────────
[ ] Playwright smoke testing (Pass 6) + error-driven retry        I-1
[ ] LLM-as-judge quality scoring                                   I-12
[ ] Canvas-game-specific Pass 1 prompt                             I-10

WEEK 3 — Visual coherence
─────────────────────────────────────────────────────────
[ ] Style lock pre-step (shared visual vocabulary)                 I-4
[ ] Genre-specific art direction from config.json                  I-7
[ ] Cover image as image-to-image reference                        I-5
[ ] Per-screen backgrounds (3 instead of 1)                        I-8
[ ] Consistent button/panel family generation                      I-9

WEEK 4 — Authenticity
─────────────────────────────────────────────────────────
[ ] Screenshot-based game_preview.png                              I-6
[ ] Parallel image generation                                       I-14
[ ] Review 10 pilot games against quality scores
[ ] Tune style lock and genre art direction based on output
```

---

## 5. Expected Impact Estimates

| Improvement | Current state | After |
|-------------|---------------|-------|
| Games with logic errors | ~30-40% (unverified) | <5% with smoke testing + JS linting |
| Assets feeling like same artist | ~20% of games | ~80% with style lock + family generation |
| game_preview.png matching actual game | 0% | ~70% with screenshot approach |
| Background telling visual story | 0% (one bg, 3 screens) | 100% with per-screen bgs |
| --resume working correctly | Broken (0%) | Fixed (100%) |
| Image generation time per game | ~100s | ~25s with parallelism |
| Games needing manual intervention | Unknown (no signal) | Surfaced automatically by quality judge |

---

## 6. The Single Most Impactful Change

**I-1 (Playwright smoke testing) + I-2 (structural validation) together.**

Every other improvement is additive — it makes a working game look better. These two changes close the feedback loop that currently allows broken games to ship undetected. A game that crashes on first click cannot be saved by better assets. Fix the foundation first.

The second most impactful: **I-4 (style lock) + I-7 (genre art direction)** together. These address the root cause of the "AI-generated" aesthetic — not the model, but the lack of a shared visual constraint across asset generation calls.

---

*Document generated via Trinity Protocol analysis of 1,567 lines across 3 pipeline files.*  
*Code review bugs (10 confirmed): see separate audit output.*
