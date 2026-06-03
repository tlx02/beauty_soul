#!/usr/bin/env python3
"""
Game Beautification Pipeline — 5-pass automated process
  Pass 0: determine visual theme
  Pass 1: strip non-core UI + enforce 3-screen centred layout
  Pass 2: fix game logic (conservative)
  Pass 3: improve visuals using approved theme
  Pass 4: generate + wire external image/audio assets

Output goes to beautified/<game_name>/ — selected_300 is never modified.

Usage:
  python3 code/beautify_games.py --pilot 10
  python3 code/beautify_games.py --game flappy_bird
  python3 code/beautify_games.py --all
  python3 code/beautify_games.py --pilot 10 --resume   # skip already-completed
"""

import re
import json
import time
import base64
import shutil
import argparse
import requests
from io import BytesIO
from pathlib import Path
from PIL import Image as PILImage
from openai import OpenAI

# ─── Paths ────────────────────────────────────────────────────────────────────
CODE_DIR   = Path(__file__).parent
ROOT_DIR   = CODE_DIR.parent
SELECTED   = ROOT_DIR / "selected_300_new"
BEAUTIFIED = ROOT_DIR / "beautified"

# ─── Config & secrets ─────────────────────────────────────────────────────────
config  = json.loads((CODE_DIR / "config.json").read_text())
secrets = json.loads((CODE_DIR / "secrets.json").read_text())

OPENROUTER_KEY = secrets["openrouter"]
ELEVENLABS_KEY = secrets["elevenlabs"]
LLM_MODEL      = config.get("llm_model",   "anthropic/claude-sonnet-4-5")
IMAGE_MODEL    = config.get("image_model", "openai/dall-e-3")
MAX_TOKENS     = config.get("max_tokens",  32768)

# ─── OpenRouter client (handles both LLM and image calls) ─────────────────────
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
    default_headers={"X-Title": "800 Games Beautifier"}
)

# ─── Prompts ──────────────────────────────────────────────────────────────────
PASS0 = """\
Analyse this HTML game and decide on the single best visual theme and layout for it.

Consider: the game's genre, mechanics, tone, and target audience. Pick a specific, evocative theme
(e.g. "deep-sea adventure", "retro neon arcade", "cozy autumn café", "minimalist zen garden",
"space odyssey", "tropical island resort") — not a generic one like "colorful" or "fun".

Also decide the best aspect ratio and container width for this game:
- "9/16" portrait, max_width 480 — for touch puzzle, card, casual, or vertical scroll games
- "1/1" square, max_width 520 — for board games, grid games, or symmetrical games
- "16/9" landscape, max_width 800 — for side-scrollers, racing, or wide-canvas games
Choose whichever best matches how the game is naturally played.

Return ONLY valid JSON — no markdown, no explanation:
{
  "theme": "<one concise theme name, 2-5 words>",
  "palette": ["<hex>", "<hex>", "<hex>", "<hex>"],
  "mood": "<one sentence describing the visual mood and why it fits this game>",
  "font_style": "<e.g. rounded playful, sharp geometric, elegant serif, bold display>",
  "aspect_w": <integer, e.g. 9>,
  "aspect_h": <integer, e.g. 16>,
  "max_width": <integer px, e.g. 480>
}"""

PASS1 = """\
You are a game developer restructuring an HTML game into a clean 3-screen brandable template.

GOAL: The game must have EXACTLY these 3 screens — no more, no less.

━━━ SCREEN 1 — Title Screen ━━━
Give its outer div: id="screen-home" class="screen"
Required child elements (in this order, vertically centred):
  <h1 class="game-title">[Game Name]</h1>
  <img class="game-preview" src="assets/images/game_preview.png" alt="icon">
  <button id="btn-start" class="btn-primary">PLAY</button>
If a title screen exists, reshape it to include the above. If not, create one.
The PLAY button must call the same JS function that starts the game.

━━━ SCREEN 2 — Gameplay Screen ━━━
Give its outer div: id="screen-game" class="screen"
Keep ALL existing game logic, canvas, elements, and event listeners exactly as-is.
IMPORTANT: If the game uses <canvas>, add style="background:transparent" to the canvas element
so the shared background image shows through behind it.

━━━ SCREEN 3 — Game Over Screen ━━━
Give its outer div: id="screen-gameover" class="screen"
Must contain: final score/result display + a PLAY AGAIN button that restarts the game.
If one exists, reshape it. If not, create a minimal one.

━━━ SHARED LAYOUT ━━━
Wrap ALL three screen divs inside a single <div class="app-container"> inside <body>.
Add this to the CSS (alongside existing styles — do not remove anything):
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    height: 100dvh;
    overflow: hidden;
    display: flex;
    justify-content: center;
    align-items: center;
    background: #000;
  }}
  .app-container {{
    width: min(100vw, calc(100dvh * {aspect_w} / {aspect_h}), {max_width}px);
    height: 100dvh;
    position: relative;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    background-image: url('assets/images/background.png');
    background-size: cover;
    background-position: center;
    background-repeat: no-repeat;
  }}
  .screen {{
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: space-evenly;
    text-align: center;
    flex: 1;
    width: 100%;
    padding: 24px;
    overflow: hidden;
  }}
  .screen.active {{ display: flex; }}

━━━ RULES ━━━
- Remove: debug panels, settings, help screens, share buttons, extra navigation, tutorial overlays
- Do NOT change game logic, scoring, physics, or timers
- Preserve all existing IDs and class names that JS references — just add the new wrapper structure around them

Return ONLY the complete modified HTML. No explanation."""

PASS2 = """\
You are a game developer doing a conservative bug-fix pass on an HTML game.
Fix ONLY issues that would prevent the game from running:
  - Undefined variables causing runtime errors
  - Broken or missing event listeners on interactive elements
  - Missing game-over/win condition (only if clearly absent and game is unfinishable)
  - Null/undefined reference errors
  - Score or timer not resetting on restart

Leave anything ambiguous untouched. Do NOT change mechanics, difficulty, or intentional behaviour.
Return ONLY the complete modified HTML. No explanation."""

PASS3 = """\
You are a UI/UX designer improving an HTML game's CSS.
You will receive the game's existing CSS, a structural HTML summary, and an approved visual theme.
Return ONLY a single improved <style> block — nothing else, no explanation.

━━━ APPROVED THEME ━━━
{theme_json}

━━━ SCREEN LAYOUT ━━━
The game uses a responsive container: aspect ratio {aspect_w}:{aspect_h}, max width {max_width}px, full viewport height.
Design all layout, font sizes, and element sizes to fill and suit this shape.
All sizing inside screens should use relative units (%, em, rem, vw, vh, or flex) — not fixed pixels.

Apply this theme consistently across all 3 screens. Every colour, font, and spacing choice
must feel intentional and part of this specific theme — not generic.

━━━ LAYOUT RULES (apply to all screens) ━━━
- Every screen must use the full viewport height — spread content vertically across the screen with generous spacing, never cluster everything in the centre; content is horizontally centred
- Max content width: {max_width}px, centred with margin: 0 auto — prevents content stretching on wide displays
- Do NOT set different width or height values on individual screen IDs — the .screen class handles all screen sizing uniformly; all screens must be the same size
- All interactive elements must have clear visual affordance (hover states, active states)
- All text must be clearly legible against the background image — ensure sufficient contrast through colour choice, text-shadow, or backdrop as appropriate

━━━ ASSET-AWARE RULES (title screen) ━━━
  The title screen has THREE image assets already handled externally — style them as image containers:
  - .game-title: will be replaced by title.png (a logo image). Style as an image container:
    display:block, max-width proportional to the screen width, height:auto, margin:0 auto,
    subtle drop-shadow. Do NOT set font-size, color, or text properties on it.
  - .game-preview: displays game_preview.png (a square gameplay illustration). Style with a fixed
    size (around 35-45% of the container width), object-fit:contain, centred, subtle drop-shadow.
  - #btn-start ONLY: will be replaced by btn_play.png (a button image). Style #btn-start as a
    transparent image button: background:transparent, border:none, padding:0, cursor:pointer,
    max-width proportional to the screen, display:block, margin:0 auto. Add hover scale transform.
    Do NOT add background colour, border-radius fill, or padding to #btn-start.
  - .btn-primary (other buttons, e.g. PLAY AGAIN): style as a normal themed button with background
    colour, padding, border-radius — these do NOT have image replacements.

━━━ SCREEN-SPECIFIC RULES ━━━
  #screen-home (Title):
    - Space the three elements (.game-title, .game-preview, #btn-start) evenly and generously

  #screen-game (Gameplay):
    - Do NOT override background or add decorative chrome that competes with gameplay
    - Canvas: background transparent; centred with display:block margin:0 auto
    - Game elements (canvas, board, grid) must use max-width/max-height or vw/vh units to scale down and fit within the viewport — never use fixed pixel sizes that exceed the screen
    - HUD / score bar: compact, proportionally small relative to the screen height, semi-transparent overlay — never obscures play area
    - Enough padding around the game area so nothing touches screen edges

  #screen-gameover (Game Over):
    - Centred, breathable — final score large and prominent
    - PLAY AGAIN button uses .btn-primary styling (themed background, padding, border-radius) — same visual weight as #btn-start but as a full styled button, not a transparent image container

━━━ VISUAL QUALITY ━━━
- Import a Google Font that matches font_style from the theme (one font, 2 weights max)
- The result must look like a professionally designed indie game, NOT an AI-generated template
- Avoid: rainbow gradients, excessive glow effects, mismatched font weights, clashing colours
- Transitions: subtle fade or slide between screens (opacity/transform, 0.25-0.35s ease)
- .screen: already has background-image — do NOT override it; just ensure background-size: cover

Keep all existing class names and IDs — only change visual properties."""

PASS4A = """\
Analyse this HTML game and produce an asset manifest for its 3-screen template.
The approved visual theme is: {theme_json}
The screen is {aspect_w}:{aspect_h} aspect ratio, max width {max_width}px — factor this into image descriptions and proportions.

The game has:
  - Title screen (id="screen-home"): shows background + icon image + title logo + PLAY button
  - Gameplay screen (id="screen-game"): shows background + game elements
  - Game Over screen (id="screen-gameover"): shows background + score + PLAY AGAIN

REQUIRED — always include all four:
  1. background.png — a SIMPLE, ATMOSPHERIC background used on all 3 screens.
     CRITICAL: it must be a subtle scene that DOES NOT compete with gameplay elements.
     Use soft gradients, gentle depth-of-field bokeh, or a minimalist environment scene.
     No busy patterns, no detailed textures, no bright focal points in the centre.
     The game's UI and sprites must read clearly on top of it.
     Style: painterly illustration or soft digital art, consistent with the approved theme.
  2. game_preview.png — a gameplay preview shown on the title screen.
     Show key game elements mid-play (e.g. gems mid-combo, ball mid-flight, snake curving).
     Style: clean illustration, transparent background, 1:1 ratio. No text or UI chrome.
     Must match the approved theme's colour palette.
  3. title.png — a stylised logo/title image for the game name, shown on the title screen.
     Style: bold themed lettering or illustrated logo, transparent background.
     Must feel like a polished game title, consistent with the approved theme.
  4. btn_play.png — a themed PLAY button image for the title screen.
     Style: an attractive pill or badge shape with "PLAY" text, consistent with the approved theme.
     Transparent background, clean edges.

OPTIONAL game sprites — include as many as genuinely improve the visual quality.
  Be selective — only include a sprite when an image genuinely improves the visual quality.
  Ask: would replacing this element with a single image make it look better, or would it destroy
  information the player needs (e.g. colour coding, number labels, state differentiation)?
  Only replace elements that are visually generic (plain shapes, solid colours) and would benefit
  from an illustrated version. Never replace elements whose visual variety IS the game mechanic.
  IMPORTANT: if different instances of the same element need different colours (e.g. coloured balls,
  coloured gems, coloured tiles), do NOT generate a sprite for it — even if you think runtime tinting
  could work. Runtime colour tinting is not reliably implemented. Leave colour-differentiated elements
  as native canvas/CSS rendering.
  Only include sprites the game actually draws/renders. Describe each with:
  - Exact neutral orientation (the game rotates at runtime — generate axis-aligned)
  - Colour palette consistent with the approved theme
  - Clean edges suitable for transparent-background PNG

AUDIO — up to 5 sound effects. Minimum duration 1.0 seconds each.

Return ONLY valid JSON — no markdown fences, no explanation:
{{
  "game_title": "...",
  "images": [
    {{"filename": "background.png", "description": "...", "usage": "full-screen background on all 3 screens"}},
    {{"filename": "game_preview.png", "description": "...", "usage": "gameplay preview centred on title screen"}},
    {{"filename": "title.png", "description": "...", "usage": "stylised game title logo on title screen"}},
    {{"filename": "btn_play.png", "description": "...", "usage": "PLAY button on title screen"}},
    ...optional game sprites...
  ],
  "audio": [
    {{"filename": "sfx_name.mp3", "description": "...", "duration_seconds": 1.0}}
  ]
}}"""

PASS4D = """\
Wire the following external assets into this HTML game.
Paths: images at "assets/images/<filename>", audio at "assets/audio/<filename>".
The screen is {aspect_w}:{aspect_h} aspect ratio, max width {max_width}px — size all assets proportionally to this.

ALREADY WIRED BY TEMPLATE — do NOT add again:
  - assets/images/background.png  (referenced via CSS .screen background-image)
  - assets/images/game_preview.png        (referenced as <img class="game-preview"> on the title screen)

WIRE THESE INTO THE TITLE SCREEN (screen-home):
  - assets/images/title.png — replace the <h1 class="game-title"> text with an <img> tag pointing to the file. Size it with max-width and height:auto so it displays at a natural readable size.
  - assets/images/btn_play.png — replace the <button id="btn-start"> content with an <img> tag pointing to the file. Make the button itself background:transparent, border:none, padding:0, cursor:pointer. Size the img using max-width and height:auto so it displays at a natural readable size without relying on percentage widths against an unsized parent.

WHAT YOU MUST WIRE — game-specific sprites and audio only:
  Asset manifest: {manifest}

RULES:
  Canvas games:
    - For each sprite image: declare a module-level Image object (e.g. const spriteImg = new Image(); spriteImg.src = "assets/images/sprite.png";)
    - Replace the corresponding canvas drawRect / fillRect / emoji drawImage calls with ctx.drawImage(spriteImg, ...)
    - ASPECT RATIO: NEVER stretch a sprite to the game object's raw hitbox dimensions —
      hitboxes are often much narrower or shorter than the visual should be.
      For every ctx.drawImage call, compute the draw size from the image's natural ratio:
        use the game object's dominant axis (whichever is larger) as the base,
        then derive the other axis as: base * (img.naturalWidth / img.naturalHeight)  OR
                                       base * (img.naturalHeight / img.naturalWidth).
      Centre the drawn sprite on the object's position so collision feel stays correct.
    - Wrap game start so it waits for all images to load (Promise.all or a counter)
  DOM games:
    - Replace emoji characters or CSS-only shapes used as game objects with <img> tags referencing the sprite files
    - Keep existing positioning/sizing CSS; just swap the element content
  Audio (both game types):
    - For each audio file: declare const snd = new Audio("assets/audio/filename.mp3");
    - Call snd.play() / snd.currentTime = 0; snd.play() at the appropriate game events
    - For background music: set snd.loop = true; snd.play() when gameplay starts

ALIGNMENT:
  - All wired sprites must appear visually centred on their game object — never offset or clipped
  - DOM sprite <img> tags: set display:block, object-fit:contain, and match the container's dimensions
  - Canvas sprites: draw centred on the object's (x, y) position using (x - drawW/2, y - drawH/2)

CSS CONFLICT RESOLUTION:
  Whenever an image asset replaces a DOM element, remove any CSS that would visually conflict with it:
  - For any element receiving an image: set background:transparent, border:none, padding:0 on that element
    so the pre-existing CSS background/border does not show behind the transparent image
  - This applies to all image replacements — buttons, headings, containers, any DOM element

Do NOT restructure screens, change game logic, or re-add background/game_preview.
Return ONLY the complete modified HTML. No explanation."""

PASS5 = """\
You are a QA engineer doing a final playability pass on a fully assembled HTML game.
The game has 3 screens: screen-home (title), screen-game (gameplay), screen-gameover (game over).

Fix ONLY the following classes of bugs — do not change visuals, game logic, or asset references:

1. SCREEN SHOWN ON LOAD
   The very first screen shown must be screen-home. If any JS initialisation auto-starts the game
   or calls showScreen('screen-game') / equivalent on page load, remove or replace it with
   showScreen for the home screen (or ensure screen-home has class="screen active" and nothing overrides it).

2. DOM MEASUREMENTS ON HIDDEN ELEMENTS
   Any function that reads layout dimensions (offsetWidth, offsetHeight, getBoundingClientRect,
   clientWidth, clientHeight) to calculate sizes must run AFTER the relevant screen is visible.
   Common pattern to fix: if newGame() / startGame() calls renderTray(), buildBoard(), or similar
   sizing functions BEFORE calling showScreen('screen-game'), reorder so showScreen is called first,
   then the sizing functions are called (use requestAnimationFrame or setTimeout(fn, 0) if needed).

3. IMAGE LOAD PROMISES BLOCKING INIT
   If buildBoardDOM(), renderTray(), or equivalent init functions are gated inside a Promise.all
   waiting for images, add .onerror handlers so a failed image load does not hang the game forever.
   Also ensure these init functions are called on page load unconditionally (not only after images load)
   so the game board is always ready when the user clicks PLAY.

4. PLAY BUTTON ALWAYS STARTS FRESH
   The PLAY button must always call newGame() or equivalent fresh-start function.
   Remove any continueGame() / loadGame() / resume logic from the PLAY button handler.
   Best-score localStorage is fine to keep; only remove saved board/progress state.

Return ONLY the complete modified HTML. No explanation."""

# ─── CSS extraction helpers (for pass 3) ─────────────────────────────────────
STYLE_RE = re.compile(r'(<style[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)

def extract_css(html: str) -> tuple[str, str]:
    """Return (combined CSS text, html with style blocks replaced by __STYLES__ marker)."""
    blocks = STYLE_RE.findall(html)
    css = "\n\n".join(content for _, content, _ in blocks)
    stripped = STYLE_RE.sub("", html, count=1)   # remove first block; marker inserted below
    # Replace all style blocks with a single marker
    stripped = STYLE_RE.sub("", stripped)
    stripped = stripped.replace("</head>", "__STYLES__\n</head>", 1)
    return css, stripped

def inject_css(html_template: str, new_css: str) -> str:
    """Re-insert improved CSS into the marker position."""
    style_block = new_css.strip()
    if not style_block.startswith("<style"):
        style_block = f"<style>\n{style_block}\n</style>"
    return html_template.replace("__STYLES__", style_block, 1)

def html_skeleton(html: str) -> str:
    """Return a condensed structural summary of the HTML for context (no scripts)."""
    no_scripts = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    no_styles  = re.sub(r'<style[^>]*>.*?</style>',  '', no_scripts, flags=re.DOTALL | re.IGNORECASE)
    # Collapse whitespace
    skeleton = re.sub(r'\n\s*\n', '\n', no_styles).strip()
    # Truncate to keep context reasonable
    if len(skeleton) > 6000:
        skeleton = skeleton[:6000] + "\n...[truncated for brevity]"
    return skeleton

# ─── LLM helper ───────────────────────────────────────────────────────────────
def _strip_fences(text: str) -> str:
    """Remove markdown code fences (```html ... ``` or ``` ... ```) from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```[a-zA-Z]*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()

# Regex to find a CSS rule block for a given selector and remove background/background-color from it
_SCREEN_BG_RE = re.compile(
    r'(#screen-(?:home|game|gameover)\s*\{[^}]*?)'   # selector + props before
    r'(?:background(?:-color)?\s*:[^;]+;[ \t]*\n?)'  # background: ... or background-color: ...
    r'([^}]*?\})',                                     # remaining props + closing brace
    re.DOTALL
)

def _fix_screen_backgrounds(html: str) -> str:
    """Remove background / background-color overrides from individual screen rules.
    The .screen class already sets background-image; per-screen overrides hide it."""
    prev = None
    while prev != html:
        prev = html
        html = _SCREEN_BG_RE.sub(r'\1\2', html)
    return html

def call_llm(system: str, html: str, label: str, max_tokens: int = MAX_TOKENS) -> str | None:
    try:
        res = client.chat.completions.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": html}
            ]
        )
        if res.choices[0].finish_reason == "length":
            print(f"    ⚠  {label}: truncated — game too large")
            return None
        result = _strip_fences(res.choices[0].message.content)
        result = _fix_screen_backgrounds(result)
        return result
    except Exception as e:
        print(f"    ✗  {label}: {e}")
        return None

# ─── Image generation ─────────────────────────────────────────────────────────
def _strip_background(img_bytes: bytes) -> bytes:
    """Crop transparent image to its content bounding box."""
    try:
        img = PILImage.open(BytesIO(img_bytes)).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"    ⚠  crop failed ({e}), keeping original")
        return img_bytes


def gen_image(description: str, out: Path, transparent: bool = False) -> bool:
    extra: dict = {"modalities": ["image"]}
    if transparent:
        extra["background"] = "transparent"
    # OpenRouter image generation uses chat completions with modalities:["image"]
    try:
        res = client.chat.completions.create(
            model=IMAGE_MODEL,
            messages=[{"role": "user", "content": description}],
            extra_body=extra
        )
        msg = res.choices[0].message

        # OpenRouter returns images in message.images (not message.content)
        data_url = None
        images = getattr(msg, "images", None) or []
        if images:
            data_url = images[0].get("image_url", {}).get("url") if isinstance(images[0], dict) else None
        # Fallback: some models embed in content
        if not data_url and msg.content and msg.content.startswith("data:"):
            data_url = msg.content

        if data_url:
            img_bytes = base64.b64decode(data_url.split(",", 1)[1])
            if transparent:
                # Crop to bounding box to remove any residual transparent border
                img_bytes = _strip_background(img_bytes)
            out.write_bytes(img_bytes)
            return True

        print(f"    ✗  image {out.name}: unexpected response format")
        return False
    except Exception as e:
        print(f"    ✗  image {out.name}: {e}")
        return False

# ─── Audio generation ─────────────────────────────────────────────────────────
def gen_audio(description: str, duration: float, out: Path) -> bool:
    try:
        res = requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"},
            json={
                "text": description,
                "duration_seconds": min(max(duration, 1.0), 22.0),
                "prompt_influence": 0.3
            },
            timeout=60
        )
        res.raise_for_status()
        out.write_bytes(res.content)
        return True
    except Exception as e:
        print(f"    ✗  audio {out.name}: {e}")
        return False

# ─── Single-game pipeline ─────────────────────────────────────────────────────
def beautify(src_dir: Path) -> bool:
    name    = src_dir.name
    out_dir = BEAUTIFIED / name
    prog_f  = out_dir / ".progress.json"

    if not (src_dir / "index.html").exists():
        print(f"    ✗  no index.html"); return False

    out_dir.mkdir(parents=True, exist_ok=True)

    # Preserve original once
    orig = out_dir / "index_original.html"
    if not orig.exists():
        shutil.copy(src_dir / "index.html", orig)

    # Progress tracking
    prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    def save_prog(): prog_f.write_text(json.dumps(prog, indent=2))

    # Load from last completed pass
    def latest_html() -> str:
        for fname in ("index_pass3.html", "index_pass2.html", "index_pass1.html", "index_original.html"):
            f = out_dir / fname
            if f.exists():
                return f.read_text(encoding="utf-8", errors="ignore")
        return orig.read_text(encoding="utf-8", errors="ignore")

    html = latest_html()

    # ── Pass 0: determine visual theme + screen size ─────────────────────────
    theme_json = prog.get("theme_json")
    if not theme_json:
        print("    → Pass 0: determine visual theme + screen size")
        skeleton = html_skeleton(html)
        r = call_llm(PASS0, skeleton, "pass0", max_tokens=512)
        if r:
            try:
                raw = _strip_fences(r)
                json.loads(raw)  # validate
                theme_json = raw
                prog["theme_json"] = theme_json
            except Exception as e:
                print(f"    ⚠  pass0: could not parse theme — {e}")
                theme_json = '{"theme":"casual arcade","palette":["#4A90D9","#F5A623","#7ED321","#D0021B"],"mood":"Bright and energetic.","font_style":"rounded playful","aspect_w":9,"aspect_h":16,"max_width":480}'
                prog["theme_json"] = theme_json
        save_prog()

    # Extract layout decided by Pass 0
    theme_data = json.loads(theme_json)
    aspect_w  = theme_data.get("aspect_w",  9)
    aspect_h  = theme_data.get("aspect_h",  16)
    max_width = theme_data.get("max_width", 480)
    # Derive representative screen dimensions for PASS3 context
    screen_height = 844
    screen_width  = int(screen_height * aspect_w / aspect_h)

    # ── Pass 1: strip UI ──────────────────────────────────────────────────────
    if not prog.get("pass1"):
        print("    → Pass 1: strip non-core UI")
        r = call_llm(PASS1.format(aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width), html, "pass1")
        if r:
            html = r
            (out_dir / "index_pass1.html").write_text(html, encoding="utf-8")
            prog["pass1"] = True
        else:
            prog["pass1_failed"] = True
        save_prog()
    elif (out_dir / "index_pass1.html").exists():
        html = (out_dir / "index_pass1.html").read_text(encoding="utf-8")

    # ── Pass 2: fix logic ─────────────────────────────────────────────────────
    if not prog.get("pass2"):
        print("    → Pass 2: fix game logic")
        r = call_llm(PASS2, html, "pass2")
        if r:
            html = r
            (out_dir / "index_pass2.html").write_text(html, encoding="utf-8")
            prog["pass2"] = True
        else:
            prog["pass2_failed"] = True
        save_prog()
    elif (out_dir / "index_pass2.html").exists():
        html = (out_dir / "index_pass2.html").read_text(encoding="utf-8")

    # ── Pass 3: improve visuals (CSS-only to avoid truncation) ───────────────
    if not prog.get("pass3"):
        print("    → Pass 3: improve visuals")
        css, html_template = extract_css(html)
        skeleton = html_skeleton(html)
        content = f"=== HTML STRUCTURE (context only) ===\n{skeleton}\n\n=== CURRENT CSS ===\n{css}"
        prompt3 = PASS3.format(theme_json=theme_json, aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        r = call_llm(prompt3, content, "pass3", max_tokens=8192)
        if r:
            html = inject_css(html_template, r)
            (out_dir / "index_pass3.html").write_text(html, encoding="utf-8")
            prog["pass3"] = True
        else:
            prog["pass3_failed"] = True
        save_prog()
    elif (out_dir / "index_pass3.html").exists():
        html = (out_dir / "index_pass3.html").read_text(encoding="utf-8")

    # ── Pass 4: assets ────────────────────────────────────────────────────────
    if not prog.get("pass4"):
        print("    → Pass 4a: determine assets")
        prompt4a = PASS4A.format(theme_json=theme_json, aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        manifest_raw = call_llm(prompt4a, html_skeleton(html), "pass4a", max_tokens=2048)
        manifest = None
        if manifest_raw:
            try:
                raw = _strip_fences(manifest_raw)
                manifest = json.loads(raw)
            except Exception as e:
                print(f"    ⚠  pass4a: could not parse manifest — {e}")

        if manifest:
            img_dir = out_dir / "assets" / "images"
            aud_dir = out_dir / "assets" / "audio"
            img_dir.mkdir(parents=True, exist_ok=True)
            aud_dir.mkdir(parents=True, exist_ok=True)

            for img in manifest.get("images", []):
                fname = img["filename"]
                is_bg = fname == "background.png"
                print(f"    → Pass 4b: image  {fname}{'' if is_bg else ' (transparent)'}")
                gen_image(img["description"], img_dir / fname, transparent=not is_bg)
                time.sleep(1)

            for aud in manifest.get("audio", []):
                print(f"    → Pass 4c: audio  {aud['filename']}")
                gen_audio(aud["description"], aud.get("duration_seconds", 1.0), aud_dir / aud["filename"])
                time.sleep(0.5)

            (out_dir / "assets" / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False)
            )

            print("    → Pass 4d: wire assets into HTML")
            prompt = PASS4D.format(manifest=json.dumps(manifest, indent=2), aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
            r = call_llm(prompt, html, "pass4d")
            if r:
                html = r
                prog["pass4"] = True
            else:
                prog["pass4_failed"] = True
        else:
            prog["pass4_skipped"] = True

        save_prog()

    # ── Pass 5: playability QA ────────────────────────────────────────────────
    if not prog.get("pass5"):
        print("    → Pass 5: playability QA")
        r = call_llm(PASS5, html, "pass5", max_tokens=65536)
        if r:
            html = r
            prog["pass5"] = True
        else:
            prog["pass5_failed"] = True
        save_prog()

    # ── Write final ───────────────────────────────────────────────────────────
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"    ✓ done → beautified/{name}/index.html")
    return True

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Beautify selected games")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--pilot", type=int, metavar="N", help="Process top N games by score")
    grp.add_argument("--game",  type=str, metavar="NAME",  help="Process one specific game")
    grp.add_argument("--all",   action="store_true",       help="Process all 300 games")
    ap.add_argument("--resume", action="store_true", help="Skip games already through pass 3")
    args = ap.parse_args()

    scored_path = SELECTED / "scored_games.json"
    if not scored_path.exists():
        print("✗ selected_300/scored_games.json not found — run select_games.py first")
        return

    scored = json.loads(scored_path.read_text())

    if args.game:
        games = [SELECTED / args.game]
    elif args.pilot:
        games = [SELECTED / g["name"] for g in scored[:args.pilot]]
    else:
        games = [SELECTED / g["name"] for g in scored]

    BEAUTIFIED.mkdir(exist_ok=True)
    ok = fail = 0

    for i, gdir in enumerate(games, 1):
        if not gdir.is_dir():
            print(f"[{i}/{len(games)}] ✗ not found: {gdir.name}\n")
            fail += 1
            continue

        if args.resume:
            prog_f = BEAUTIFIED / gdir.name / ".progress.json"
            if prog_f.exists():
                prog = json.loads(prog_f.read_text())
                pass4_done = prog.get("pass4") or prog.get("pass4_skipped") or prog.get("pass4_failed")
                pass5_done = prog.get("pass5") or prog.get("pass5_failed")
                if pass4_done and pass5_done:
                    print(f"[{i}/{len(games)}] ↷ already done: {gdir.name}\n")
                    ok += 1
                    continue

        print(f"[{i}/{len(games)}] {gdir.name}")
        if beautify(gdir):
            ok += 1
        else:
            fail += 1
        print()

    print(f"Done — {ok} succeeded, {fail} failed")
    if ok:
        print(f"Output: {BEAUTIFIED}/")

if __name__ == "__main__":
    main()
