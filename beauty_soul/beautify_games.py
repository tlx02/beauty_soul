#!/usr/bin/env python3
"""
Game Beautification Pipeline

  Pass 0   : determine visual theme + screen size (Trinity Protocol)
  Pass 1   : restructure HTML into 3-screen layout with required IDs
  Pass 2   : fix game logic (conservative — no regressions)
  Pass 3   : improve visuals using approved theme (CSS-only)
  StyleLock: freeze art-direction JSON for consistent image generation
  Pass 4a  : generate asset manifest (LLM decides what images/audio to create)
  Pass 4d  : wire manifest assets into HTML (before generation — no broken images yet)
  Pass 4b/c: generate images (parallel) and audio assets
  Pass 5   : playability QA (orphaned elements, overlay positioning, layout)
  Pass 6   : Playwright smoke test + error-driven retry

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
# (Trinity Protocol import deferred until after secrets are loaded — see below)
CODE_DIR   = Path(__file__).parent
ROOT_DIR   = CODE_DIR.parent
SELECTED   = ROOT_DIR / "selected_300_new"
BEAUTIFIED = ROOT_DIR / "beautified"

# ─── Config & secrets ─────────────────────────────────────────────────────────
# Load defaults from config.json if present; all values overridable via configure()
import os as _os
_config_path = CODE_DIR / "config.json"
_config_defaults = json.loads(_config_path.read_text()) if _config_path.exists() else {}
config = _config_defaults  # keep module-level `config` for genre_art_direction lookups

LLM_MODEL   = _config_defaults.get("llm_model",   "anthropic/claude-sonnet-4-5")
IMAGE_MODEL = _config_defaults.get("image_model", "openai/gpt-5-image")
MAX_TOKENS  = _config_defaults.get("max_tokens",  32768)

# Secrets: try secrets.json first (local dev), then env vars — no hard crash
_secrets_path = CODE_DIR / "secrets.json"
if _secrets_path.exists():
    _s = json.loads(_secrets_path.read_text())
    OPENROUTER_KEY = _s.get("openrouter", "")
    ELEVENLABS_KEY = _s.get("elevenlabs", "")
else:
    OPENROUTER_KEY = _os.environ.get("OPENROUTER_API_KEY", "")
    ELEVENLABS_KEY = _os.environ.get("ELEVENLABS_API_KEY", "")

# Pipeline feature flags (overridable via configure())
_SKIP_AUDIO = False
_SKIP_SMOKE = False
_last_report: dict = {}

# ─── Trinity Protocol SDK ─────────────────────────────────────────────────────
_os.environ.setdefault("OPENROUTER_API_KEY", OPENROUTER_KEY)
try:
    from trinity_protocol import solve
    _TP_AVAILABLE = True
except ImportError:
    _TP_AVAILABLE = False
    print("⚠  trinity_protocol not installed — analytical passes will use call_llm fallback")

# ─── OpenRouter client (handles both LLM and image calls) ─────────────────────
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY or "placeholder",
    default_headers={"X-Title": "800 Games Beautifier"},
    timeout=300,
)


def configure(cfg: dict) -> None:
    """Inject runtime config overrides — called by run_beautify before beautify().
    Updates module globals so the pipeline uses the right keys and models."""
    global client, LLM_MODEL, IMAGE_MODEL, MAX_TOKENS
    global OPENROUTER_KEY, ELEVENLABS_KEY, _SKIP_AUDIO, _SKIP_SMOKE

    OPENROUTER_KEY = (cfg.get("openrouter_key")
                      or _os.environ.get("OPENROUTER_API_KEY", OPENROUTER_KEY))
    ELEVENLABS_KEY = (cfg.get("elevenlabs_key")
                      or _os.environ.get("ELEVENLABS_API_KEY", ELEVENLABS_KEY))
    LLM_MODEL   = cfg.get("llm_model",    LLM_MODEL)
    IMAGE_MODEL = cfg.get("image_model",  IMAGE_MODEL)
    MAX_TOKENS  = int(cfg.get("max_tokens", MAX_TOKENS))
    _SKIP_AUDIO = cfg.get("skip_audio",      False)
    _SKIP_SMOKE = cfg.get("skip_smoke_test", False)

    _os.environ["OPENROUTER_API_KEY"] = OPENROUTER_KEY
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_KEY or "placeholder",
        default_headers={"X-Title": "800 Games Beautifier"},
        timeout=300,
    )


def last_report() -> dict:
    """Return the pipeline report from the most recent beautify() call."""
    return _last_report

# ─── Prompts ──────────────────────────────────────────────────────────────────
PASS0 = """\
Analyse this HTML game (structure + game logic provided) and decide on the single best visual theme and layout.

You will receive the HTML structure AND a truncated excerpt of the game's JavaScript so you can understand
the actual mechanics — use this to make an informed aspect ratio decision.

Consider: the game's genre, mechanics, tone, and target audience. Pick a specific, evocative theme
(e.g. "deep-sea adventure", "retro neon arcade", "cozy autumn café", "minimalist zen garden",
"space odyssey", "tropical island resort") — not a generic one like "colorful" or "fun".

Also decide the best aspect ratio and container width for this game:
- "9/16" portrait, max_width 480 — for most games: puzzle, card, casual, board, grid, or vertical games
- "16/9" landscape, max_width 800 — only for side-scrollers, racing, or games that require a wide canvas
Choose whichever best matches how the game is naturally played. Read the JS mechanics to decide.

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

STYLE_LOCK = """\
Generate a terse art direction brief for this game's image assets.
This brief will be prepended verbatim to EVERY image generation prompt, so all assets must share one consistent visual language.

If a cover image is shown above, use it as the PRIMARY reference — extract the exact rendering style,
colour palette, line weight, and mood directly from what you see. The cover is authoritative.
If no cover image is shown, derive the style from the theme and CSS instead.

Return ONLY valid JSON:
{
  "art_style": "<one precise phrase describing the visual style seen in the cover, e.g. 'flat vector illustration, 2px black stroke, warm earthy palette'>",
  "line_weight": "<e.g. 'clean 2px stroke, no texture'>",
  "shadow_style": "<e.g. 'soft drop-shadow only, no hard shadows'>",
  "background_treatment": "<e.g. 'bokeh depth blur, warm ambient light'>",
  "icon_shape": "<e.g. 'rounded rectangle, 12px corner radius, badge style'>",
  "negative_terms": "<e.g. 'no photorealism, no gradients, no lens flare, no text baked in'>"
}"""

COVER_EXTRACT = """\
Analyse this game cover image and extract its visual style as a precise art direction brief.
This becomes the single source of truth for ALL assets — CSS colours, UI panels, backgrounds, and sprites.

Return ONLY valid JSON, no markdown:
{
  "art_style": "<concise image-generation phrase: rendering technique + line style + palette descriptor, e.g. 'glossy 3D cartoon, bold outlines, vibrant saturated palette'>",
  "hex_palette": ["#RRGGBB", "#RRGGBB", "#RRGGBB", "#RRGGBB", "#RRGGBB"],
  "rendering": "<specific rendering description, e.g. 'glossy 3D renders with soft specular highlights and subtle drop shadows'>",
  "mood": "<lighting and atmosphere, e.g. 'warm golden ambient light, high saturation, slight vignette'>",
  "negative_terms": "<what to avoid in all assets, e.g. 'no photorealism, no thin strokes, no desaturated colours, no baked-in text'>",
  "css_primary": "#RRGGBB",
  "css_accent": "#RRGGBB",
  "css_text": "#RRGGBB"
}"""

PASS1 = """\
You are a game developer restructuring an HTML game into a clean 3-screen brandable template.

GOAL: The game must have EXACTLY these 3 screens — no more, no less.

━━━ SCREEN 1 — Title Screen ━━━
Give its outer div: id="screen-home" class="screen"
Required child elements (in this order, vertically centred):
  <img class="game-title" src="assets/images/title.png" alt="[Game Name]">
  <img class="game-preview" src="assets/images/game_preview.png" alt="preview">
  <button id="btn-start"><img src="assets/images/btn_play.png" alt="PLAY"></button>
If a title screen exists, reshape it to include the above. If not, create one.
The button id="btn-start" must call the same JS function that starts the game.

━━━ SCREEN 2 — Gameplay Screen ━━━
Give its outer div: id="screen-game" class="screen"
Keep ALL existing game logic, canvas, elements, and event listeners exactly as-is.
IMPORTANT: If the game uses <canvas>, add style="background:transparent" to the canvas element
so the shared background image shows through behind it.

The gameplay screen MUST always have a home button (id="btn-home-game") and a pause button
(id="btn-pause-game"). Analyze the game's existing layout and place them naturally:
  - If the game already has a HUD bar, score row, or header inside #screen-game → add the buttons
    INTO that existing container (e.g. at the edges of the bar). Do NOT add a new wrapper.
  - Only if there is NO existing bar/header → add a minimal <div id="game-topbar"> as the
    first child of #screen-game to hold them.
  Keep whatever wrapper makes sense for the layout. The IDs must be exactly btn-home-game and
  btn-pause-game. Use placeholder content (&#8962; for home, &#9646;&#9646; for pause) — images
  will be wired in a later pass.

Add a pause overlay INSIDE .app-container as a sibling of the screen divs:
  <div id="pause-overlay">
    <div id="pause-card">
      <div id="pause-title">Paused</div>
      <button id="btn-resume-game">Resume</button>
      <button id="btn-restart-pause">Restart</button>
      <button id="btn-home-pause">Home</button>
    </div>
  </div>

━━━ SCREEN 3 — Game Over Screen ━━━
Give its outer div: id="screen-gameover" class="screen"
Must contain: final score/result display + a PLAY AGAIN button + a HOME button that returns to screen-home.
The PLAY AGAIN button MUST be exactly:
  <button id="btn-playagain"><img src="assets/images/btn_playagain.png" alt="PLAY AGAIN"></button>
Wire btn-playagain to call the same JS function that restarts the game after a game over.
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
    background: #111;
    padding: max(env(safe-area-inset-top, 0px), 0px)
             max(env(safe-area-inset-right, 0px), 0px)
             max(env(safe-area-inset-bottom, 0px), 0px)
             max(env(safe-area-inset-left, 0px), 0px);
    box-sizing: border-box;
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
  #screen-game > * {{ max-width: 100%; overflow: hidden; }}
  #screen-home {{ background-image: url('assets/images/background_home.png'); background-size: cover; background-position: center; }}
  #screen-game {{ background-image: url('assets/images/background_game.png'); background-size: cover; background-position: center; }}
  #screen-gameover {{ background-image: url('assets/images/background_gameover.png'); background-size: cover; background-position: center; }}

━━━ RULES ━━━
- Remove: debug panels, settings, help screens, share buttons, tutorial overlays
- Remove any pre-existing text title element on the title screen (h1, h2, .title, #title, any element
  whose sole content is the game name as text) — the <img class="game-title"> replaces it entirely.
  Never leave both a text heading and the image title on screen-home simultaneously.
- LEVEL-BASED GAMES — CRITICAL: if the game has a level system (a LEVELS/levelConfigs array,
  a function that takes a level index to start a specific puzzle, difficulty tables), you MUST:
  1. Preserve ALL level data arrays and configs in the JS — never delete them
  2. Preserve the level-start function intact — never simplify or remove it
  3. Wire the #btn-start PLAY button to call that function with a RANDOM level index so the
     player gets a different puzzle each time. Find the total number of levels from the array
     length or config, then pick Math.floor(Math.random() * totalLevels).
  Level select UI screens can be removed — the random start replaces them.
  Do NOT remove the underlying level data or generation functions.
- Do NOT change game logic, scoring, physics, or timers
- Preserve all existing IDs and class names that JS references
- Preserve level-progression buttons (e.g. "Next Level", "Continue") — move into screen-gameover

Return ONLY the complete modified HTML. No explanation."""

PASS2 = """\
You are a game developer doing a conservative bug-fix pass on an HTML game.

CONTEXT: The game has been restructured into a 3-screen template:
  - id="screen-home" (title screen) — shown on load, PLAY button starts game
  - id="screen-game" (gameplay screen) — shown after PLAY is clicked
  - id="screen-gameover" (game over screen) — shown when game ends
  Screens are toggled via classList with class="screen active". Inactive screens have display:none.

Fix ONLY issues that would prevent the game from running:
  - Undefined variables causing runtime errors
  - Broken or missing event listeners on interactive elements
  - Missing game-over/win condition (only if clearly absent and game is unfinishable)
  - Null/undefined reference errors — especially any that would crash on startup before the
    game is playable (e.g. getElementById returning null because the element was removed, or
    accessing .classList/.style on a null element). Add null guards: `if (el) el.classList...`
  - Instant game-over on start: if a death/fail condition can trigger on the very first frame
    before the player has any chance to act (e.g. a platform color mismatch on tick 1), add a
    brief grace period counter: skip the fail check for the first 3-5 frames after game start
  - Score or timer not resetting on restart
  - JS references to IDs that no longer exist in the DOM (e.g. removed settings/help panels)

PUZZLE SOLVABILITY — if the game generates puzzles by randomly distributing or shuffling pieces
(tiles, balls, blocks, cards) into a starting state, check whether generation guarantees
solvability. A plain Fisher-Yates shuffle does NOT guarantee a solvable state.
  If generation is shuffle-based with no solvability check: replace it with REVERSE SIMULATION:
    1. Construct the fully solved/winning state of the puzzle (whatever the goal state is)
    2. Apply N random VALID moves in reverse — moves that are legal in the forward game —
       to scramble from solved into a starting state
    3. The result is guaranteed solvable because it was reached backward from the goal
  This pattern works for any constraint-based puzzle: sliding tiles, sorted containers,
  connected pipes, filled grids, etc.
  Do NOT apply this if: the game already uses constraint-based or reverse-simulation generation,
  or if levels are pre-authored fixed configs (hardcoded arrays of piece positions).
  Only replace when generation is clearly a random shuffle with no solvability guarantee.

HOME + PAUSE CONTROLS — wire up the injected buttons:
  Add `let paused = false;` near the top of the script (with other state variables).
  - #btn-pause-game ONLY: set paused = true; add class "active" to #pause-overlay
  - #btn-home-game: MUST call showScreen('home') and stop the game loop directly — do NOT show
    the pause overlay. Stop loop: rAF games → cancelAnimationFrame; interval games → clearInterval.
    Also set paused = false and remove "active" from #pause-overlay if it was open.
    NEVER wire #btn-home-game to the same handler as #btn-pause-game.
  - #btn-resume-game: set paused = false; remove class "active" from #pause-overlay; resume loop:
      canvas/rAF games → requestAnimationFrame(gameLoop)
      setInterval games → restart the interval
      input-driven games → no loop to resume (paused flag is enough)
  - #btn-restart-pause: set paused = false; remove "active" from #pause-overlay; call restart/newGame
  - #btn-home-pause: set paused = false; remove "active"; stop game loop; showScreen('home')
  CRITICAL — guard ALL input handlers with `if (paused) return;` at the top:
    keyboard handlers, pointerdown/up/move handlers, touch handlers, click handlers on game elements.
    This prevents moves/clicks registering while the pause overlay is visible.
  Add null guards on all getElementById calls.

PLAY AGAIN / RESTART — the handler wired to #btn-playagain (and any restart button on the
  game-over screen) MUST fully reset ALL mutable game state before starting a new game:
  score, lives, coins, timer, moves, level, streak, and any other game-specific counters.
  Do NOT just call showScreen('game') — reinitialize state first, then start fresh.

BEST SCORE TRACKING — if the game tracks a best/high score:
  - On page load, read from localStorage: best = parseInt(localStorage.getItem('best') || '0', 10)
  - Save prevBest = best immediately before each game starts (before any score is earned)
  - Show "New Best Score!" only when score > prevBest (never when score >= best, because
    best may already equal score due to real-time updates during gameplay)
  - Whenever best is updated: localStorage.setItem('best', best)
  - Display the persisted best on both the title screen and game-over screen

GAME OVER CONDITION — showScreen('gameover') must ONLY be called when a genuine terminal
  condition is met (lives = 0, coins = 0, time expired, no moves left, puzzle complete, etc.).
  It must NEVER be called unconditionally after every action (e.g., after every spin, every move).
  If no clear game-over condition exists in the original code, add one appropriate to the genre.

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
All sizing inside screens should use relative units — not fixed pixels. Use %, em, rem for elements inside the container. Avoid vw/vh for elements inside screens — those scale with the viewport, not the container, and will overflow on wide displays.

Apply this theme consistently across all 3 screens. Every colour, font, and spacing choice
must feel intentional and part of this specific theme — not generic.

━━━ LAYOUT RULES (apply to all screens) ━━━
- Every screen must use the full viewport height — spread content vertically across the screen with generous spacing, never cluster everything in the centre; content is horizontally centred
- Max content width: {max_width}px, centred with margin: 0 auto — prevents content stretching on wide displays
- Do NOT set different width or height values on individual screen IDs — the .screen class handles all screen sizing uniformly; all screens must be the same size
- Do NOT override .screen padding — it is already set by the template
- All interactive elements must have clear visual affordance (hover states, active states)
- All text must be clearly legible against the background image — ensure sufficient contrast through colour choice, text-shadow, or backdrop as appropriate

━━━ ASSET-AWARE RULES (title screen) ━━━
  The title screen already has THREE <img> elements — style them as image containers only:
  - .game-title: an <img> tag showing the game title logo. Style as: display:block, max-width
    proportional to the screen width, height:auto, margin:0 auto. Do NOT set font-size, color,
    text properties, filter, drop-shadow, or glow effects — the image has its own visual design.
  - .game-preview: an <img> tag showing a gameplay preview. Style with a proportional size
    (around 35-45% of the container width), object-fit:contain, centred. Do NOT use box-shadow
    — it draws a visible rectangle around the element regardless of image transparency. If a
    shadow is needed for depth, use filter:drop-shadow() which follows the image alpha channel.
  - #btn-start: a <button> containing an <img>. Style as: background:transparent, border:none,
    padding:0, cursor:pointer, -webkit-appearance:none, appearance:none, max-width proportional
    to the screen, display:block, margin:0 auto. Add hover scale transform only.
    Do NOT add background colour, box-shadow, glow, filter, or drop-shadow — the button image
    has its own visual design. The appearance reset is critical to remove browser default button styling.
  - #btn-playagain: a <button> containing an <img> on the game-over screen. Style identically to
    #btn-start: background:transparent, border:none, padding:0, cursor:pointer,
    -webkit-appearance:none, appearance:none, max-width proportional to the screen, display:block,
    margin:0 auto. Add hover scale transform only. Do NOT add background colour or shadows.
  - .btn-secondary (HOME button on game-over, any subdued action button): style as a smaller text
    button — themed colour, padding, border-radius, clearly less prominent than image buttons.

━━━ SCREEN-SPECIFIC RULES ━━━
  #screen-home (Title):
    - Space the three elements (.game-title, .game-preview, #btn-start) evenly and generously

  #screen-game (Gameplay):
    - Do NOT override background or add decorative chrome that competes with gameplay
    - Use justify-content: space-evenly so content is distributed across the full screen height —
      never justify-content: flex-start which clusters everything at the top
    - #btn-home-game and #btn-pause-game: style as small unobtrusive icon buttons — sized to feel
      like secondary controls, not primary game elements. They should be visually compact so
      gameplay content dominates the screen. Size proportionally to the game's HUD bar — if the
      HUD bar is dense with stats, keep them very small; if the HUD is spacious, slightly larger
      is fine. Style as transparent image containers (background:transparent, border:none, padding:0,
      cursor:pointer). Do NOT create a separate #game-topbar if the game already has a HUD bar
      — integrate into it.
    - #pause-overlay: fullscreen overlay covering the app container (position:absolute, inset:0, z-index:9999).
      Hidden by default (display:none), shown with class "active" (display:flex).
      Semi-transparent dark backdrop. #pause-card centred inside it via flex (align-items:center; justify-content:center).
      IMPORTANT: #pause-card must NOT use position:absolute or position:fixed — it must be a plain
      flex child so the overlay's align-items:center centers it correctly. Width: 85%; max-width: 360px.
      Never leave max-width unconstrained.
      Style #pause-card to match the game theme — the background image will be wired later,
      so set background:transparent. Buttons inside should be clearly readable.
      BUTTON CONSISTENCY: all 3 buttons (#btn-resume-game, #btn-restart-pause, #btn-home-pause)
      must have consistent visual weight — don't make one a tiny icon while the others are
      full-width styled buttons. Style them in a way that feels balanced for the game's theme.
    - In-game overlays (win popup, gameover popup) that appear OVER the play area must use
      position:absolute; inset:0 — never position:fixed (which escapes the app container)
    - When applying a background-image to a HUD/score panel, make direct child elements
      transparent (no background-color) so the panel image shows through
    - Use filter:drop-shadow() instead of box-shadow on any element with a panel background-image
    - #screen-game must keep align-items: center — all child elements must be horizontally centred
    - Canvas: background transparent; centred with display:block margin:0 auto
    - All game containers (boards, grids, canvas wrappers, buttons) must be centred — use margin:0 auto or align-self:center; never leave them left-aligned
    - Game elements (canvas, board, grid) must use max-width/max-height or % units to scale down and fit within the viewport — never use fixed pixel sizes that exceed the screen
    - Every game container that holds game objects (board, play-area, grid, reel window, lane container, tile area) MUST have overflow:hidden in its CSS rule — this is mandatory to prevent game elements from rendering outside the container bounds
    - HUD / score bar: should feel like a compact strip, leaving the large majority of the screen
      for gameplay. If the game only needs a few stats, prefer a single horizontal row of small
      badges over multiple stacked card panels. The HUD is a support element — not the focal point.
    - CONSOLIDATION: if the game has multiple separate stat elements (a score row, then a separate
      moves/combo row, then a secondary info strip), consolidate them. Multiple stacked strips
      create a dominant HUD even when each strip is individually modest. Merge secondary stats
      into the main score row when they fit, or arrange them as a compact inline group — avoid
      giving each stat its own full-width row.
    - PROPORTION: after all HUD elements are laid out, the game board/canvas/play area must be
      the single tallest element visible during gameplay. If the combined height of all HUD strips
      (topbar + score row + any secondary info bar) rivals the game board, the HUD is dominating
      — consolidate rows and reduce padding until the play area clearly dominates the screen.
    - Resource/progress bars (health, mana, energy, stamina, time): must be clearly visible at a
      glance — thick enough to read, fill colour clearly contrasting with the track.
    - Enough padding around the game area so nothing touches screen edges

  #screen-gameover (Game Over):
    - Centred, breathable — emotionally impactful (defeat or triumph moment for the player)
    - Primary score/result: displayed at LARGE size — at least 2× the secondary stat font-size —
      bold weight, theme accent colour, visually dominant
    - Secondary stats compact and below the primary score — smaller font, subdued colour
    - #btn-playagain: image container styled identically to #btn-start (see ASSET-AWARE RULES)
    - HOME button (.btn-secondary): smaller, more subdued than #btn-playagain
    - Space elements generously across the full screen height (justify-content: space-evenly)

━━━ VISUAL QUALITY ━━━
- Import a Google Font that matches font_style from the theme (one font, 2 weights max)
- The result must look like a professionally designed indie game, NOT an AI-generated template
- Avoid: rainbow gradients, excessive glow effects, mismatched font weights, clashing colours
- Transitions: subtle fade or slide between screens (opacity/transform, 0.25-0.35s ease)
- #screen-home, #screen-game, and #screen-gameover already have background-image set — PRESERVE these rules exactly as-is; do NOT remove or modify them. Do NOT add background-image to the base .screen class (without an ID)

Keep all existing class names and IDs — only change visual properties."""

PASS4A = """\
Analyse this HTML game and produce an asset manifest for its 3-screen template.
The approved visual theme is: {theme_json}
The screen is {aspect_w}:{aspect_h} aspect ratio, max width {max_width}px — factor this into image descriptions and proportions.

The game has:
  - Title screen (id="screen-home"): shows background + icon image + title logo + PLAY button
  - Gameplay screen (id="screen-game"): shows background + game elements
  - Game Over screen (id="screen-gameover"): shows background + score + PLAY AGAIN

REQUIRED — always include all ten:
  1. background_home.png — full-screen atmospheric background for the TITLE screen.
     Rich and detailed — establishes the game world. Soft gradients or painterly illustration.
  2. background_game.png — background for the GAMEPLAY screen.
     Same palette as background_home.png but darker (30-40% darker) and more desaturated.
     IMPORTANT: "darker" must not result in near-black — the background must still read as a
     recognisable colour (muted blue, dark green, warm brown, etc.), never close to pure black.
     If the home background is already moody or dark, reduce saturation and detail instead of
     darkening further. The goal is reduced visual noise, not blackness.
     Minimal detail — must not compete with game elements. Subtle texture or gradient only.
  3. background_gameover.png — background for the GAME OVER screen.
     Dramatic darker variant of background_home.png. Slightly ominous or reflective mood.
  4. game_preview.png — gameplay preview on title screen. Key game elements mid-play, 1:1 ratio,
     transparent background, no text or UI chrome.
  5. title.png — styled game title logo. Bold themed lettering, transparent background.
  6. btn_play.png — PLAY button for the title screen. Pill/badge shape with "PLAY" text,
     transparent background.
  7. btn_home.png — home icon button for the in-game topbar (#btn-home-game). Small, themed,
     icon only (no text), transparent background.
  8. btn_pause.png — pause icon button for the in-game topbar (#btn-pause-game). Same style as
     btn_home.png, two vertical bars, transparent background.
  9. pause_card.png — decorative background panel for the pause overlay card (#pause-card).
     Styled to match the game theme. NO text, NO buttons baked in. Transparent background.
     Must be perfectly front-facing and flat — no perspective tilt or 3D angle.
  10. btn_playagain.png — PLAY AGAIN button for the game-over screen (#btn-playagain). Same
      visual language as btn_play.png but reads "PLAY AGAIN" or shows a replay icon + text.
      Pill/badge shape, transparent background. The primary CTA on game-over — bold and prominent.

OPTIONAL UI CHROME — up to 4 additional panel/frame images. These make the game feel real.
  Generate decorative background images for the most impactful UI containers — prioritise:
  score/HUD panel, game-over card, win card, then any other prominent frame.
  Look for these in the HTML structure and generate one image per container:
  - Score/HUD panel: the box showing score, timer, lives, level — a styled frame/badge background
  - Game-over popup panel: the card/modal that shows "Game Over" + final score + replay button
  - Win/congratulations popup panel: same treatment as game-over if distinct
  - Pause menu panel: if the game has one
  - Any other prominent UI container that would benefit from a polished frame
  CRITICAL RULES for UI chrome:
  - DECORATIVE FRAME ONLY — describe the shape, texture, colour. NO text, NO numbers, NO icons
    baked into the image. Live data (score values, button labels) is rendered by HTML on top.
  - FRONT-FACING ONLY — every card/panel must be rendered perfectly flat and straight-on, as if
    the viewer is looking directly at it head-on. NO perspective tilt, NO 3D rotation, NO angle.
    The card edges must be parallel to the image edges. A tilted or angled card breaks layout.
  - transparent: true always (these overlay on the background)
  - "usage" field must name the HTML element ID or class it wraps (e.g. "#score-panel", ".gameover-card")
  - NEVER generate an image for the game board, grid, play area, tile container, canvas wrapper,
    or any element whose children are JS-positioned game objects. These elements are #grid-wrap,
    #board, #game-area, #tiles, #cells, canvas, and any equivalent. An image on these elements
    conflicts with JS tile/sprite positioning and breaks the game. No exceptions.

OPTIONAL game sprites — be selective.
  Only replace elements that are visually generic (plain shapes, solid colours) and would benefit
  from an illustrated version. Never replace elements whose visual variety IS the game mechanic.
  If different instances need different colours (coloured balls, gems, tiles), skip it — runtime
  tinting is unreliable. Leave colour-differentiated elements as CSS.
  NEVER generate: empty grid cells, blank tile placeholders, board cell backgrounds — CSS handles
  these. Only generate sprites for game pieces with actual content (character, projectile, collectible).
  Describe each with:
  - Exact neutral orientation (the game rotates at runtime — generate axis-aligned)
  - Colour palette consistent with the approved theme
  - Clean edges suitable for transparent-background PNG

AUDIO — up to 5 sound effects. Minimum duration 1.0 seconds each.

For each image include:
  - "transparent": true for logos, buttons, UI panels, sprites; false for background.png only
  - "w" and "h": the pixel dimensions that make sense for this asset's role. Choose what looks
    best — background is the full screen, icon buttons are small squares, popup cards are
    portrait rectangles, HUD panels match the width of the container they frame.

Return ONLY valid JSON — no markdown fences, no explanation:
{{
  "game_title": "...",
  "images": [
    {{"filename": "background_home.png", "description": "...", "usage": "#screen-home background, rich and atmospheric", "transparent": false, "w": {max_width}, "h": {screen_h}}},
    {{"filename": "background_game.png", "description": "...", "usage": "#screen-game background, darker desaturated variant", "transparent": false, "w": {max_width}, "h": {screen_h}}},
    {{"filename": "background_gameover.png", "description": "...", "usage": "#screen-gameover background, dramatic darker variant", "transparent": false, "w": {max_width}, "h": {screen_h}}},
    {{"filename": "game_preview.png", "description": "...", "usage": "gameplay preview centred on title screen", "transparent": true, "w": 300, "h": 300}},
    {{"filename": "title.png", "description": "...", "usage": "game title logo on title screen", "transparent": true, "w": 400, "h": 160}},
    {{"filename": "btn_play.png", "description": "...", "usage": "PLAY button on title screen", "transparent": true, "w": 280, "h": 100}},
    {{"filename": "btn_home.png", "description": "...", "usage": "#btn-home-game", "transparent": true, "w": 80, "h": 80}},
    {{"filename": "btn_pause.png", "description": "...", "usage": "#btn-pause-game", "transparent": true, "w": 80, "h": 80}},
    {{"filename": "pause_card.png", "description": "...(front-facing flat panel, no perspective tilt)...", "usage": "#pause-card", "transparent": true, "w": 360, "h": 440}},
    {{"filename": "btn_playagain.png", "description": "...", "usage": "#btn-playagain on game-over screen", "transparent": true, "w": 320, "h": 100}},
    ...optional UI chrome and sprites...
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
  - assets/images/background_home.png    (CSS #screen-home background-image)
  - assets/images/background_game.png    (CSS #screen-game background-image)
  - assets/images/background_gameover.png (CSS #screen-gameover background-image)
  - assets/images/game_preview.png (referenced as <img class="game-preview"> on the title screen)
  - assets/images/title.png        (referenced as <img class="game-title"> on the title screen)
  - assets/images/btn_play.png     (referenced as <img> inside <button id="btn-start"> on the title screen)
  - assets/images/btn_playagain.png (referenced as <img> inside <button id="btn-playagain"> on the game-over screen)

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

UI CHROME PANELS (HUD frames, popups, score cards):
  For each panel/HUD image in the manifest, wire it as CSS on the target element in "usage".

  EXACT SELECTOR RULE (strictly enforced):
  The "usage" field is the EXACT CSS selector to target. Apply each image to ONLY that selector — never
  to a parent, sibling, child, or semantically related element.
  - If "usage" says "#pause-card" → apply ONLY to #pause-card. Never to .overlay, #pause-overlay,
    or any other element, even if those elements visually surround or contain the card.
  - If "usage" says ".score-row" → apply ONLY to .score-row. Never to .score-box or individual children.
  - If "usage" says ".result-card" → apply ONLY to .result-card. Never to a parent wrapper or sibling.
  - DANGER: Never apply a background-image to a shared class selector (a class used by more than one element
    in the HTML) unless the usage field explicitly names that class. A background-image on a shared class
    will corrupt ALL elements that share it. If the target needs a background, apply it via an ID selector
    or a more specific selector that matches only the intended element.

  HUD / score bar panels (small bars at top of game screen):
  - background-image + background-size: 100% 100% + background-repeat: no-repeat
  - position: relative; box-sizing: border-box
  - padding: enough that score text sits comfortably inside the panel frame — judge by the
    image border thickness, not a fixed number
  - Remove conflicting background-color and border
  - Add min-height so the panel is always visible even if children are small

  Popup panels (game-over card, win card — anything INSIDE the game play area):
  EXCEPTION: #pause-card is NOT a popup panel — it is a flex child inside #pause-overlay.
  Do NOT apply these popup rules to #pause-card. See "TOPBAR + PAUSE CARD" section below.
  - Do NOT use position:absolute with inset:0 — that stretches the panel across the entire
    parent container, distorting the image. Instead, create a centred absolutely-positioned popup:
      position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
      width: sized to match the panel image's natural proportions — typically 70-90% of container;
      z-index: 100;
  - background-image + background-size: 100% 100% + background-repeat: no-repeat
  - padding: enough that no content touches the panel image's frame edges — judge by eye
  - box-sizing: border-box; border-radius matching the panel image corners
  - display: flex; flex-direction: column; align-items: center; gap appropriate for the theme
  - Remove conflicting background-color and border
  - If the popup was previously an overlay with inset:0, move it OUT of the game container
    and wire it as an absolutely-positioned element instead

  Result / stats cards (on game-over screen):
  - background-image + background-size: 100% 100% + background-repeat: no-repeat
  - padding: enough that rows sit inside the frame — judge by the panel image's border
  - width: sized to match the panel image's natural proportions; box-sizing: border-box
  - Remove conflicting background-color and border

  ALL panel types:
  - Keep ALL child elements (text, scores, buttons) fully visible on top — never hide them
  - Ensure enough padding that no text touches the panel edges
  - HEIGHT: always use height: auto so the card's height is driven by its content + padding.
    Do NOT set an explicit height based on the panel image's pixel dimensions (e.g. do NOT
    write height: 480px just because the panel PNG is 480px tall). background-size: 100% 100%
    will stretch the panel image to cover whatever height the content requires. Setting an
    explicit height that is larger than the content creates a large empty white/blank area.
  - CRITICAL: when applying a background-image panel to a container, remove background-color
    and background from ALL direct children that would cover the panel image — children must
    be transparent so the panel shows through
  - Use filter:drop-shadow(...) instead of box-shadow on any element with a panel background-image
    so the shadow follows the image shape rather than the element's bounding rectangle
  - In-game overlays (win message, gameover popup inside the game play area): NEVER use
    position:fixed — this escapes the app container on wide-viewport layouts. Use
    position:absolute instead. If the overlay is a full-screen dimming backdrop (inset:0
    with semi-transparent background, no panel image), use inset:0. If the manifest provides
    a background-image for this element (meaning it is a styled popup card), treat it as a
    Popup panel — position it as a centred card (top:50%;left:50%;transform:translate(-50%,-50%);
    width sized to the image proportions) and apply the background-image as specified above.
    Do NOT skip applying the background-image just because the element was previously full-screen.

TOPBAR + PAUSE CARD:
  - btn_home.png → put <img src="assets/images/btn_home.png"> inside #btn-home-game, remove text
  - btn_pause.png → put <img src="assets/images/btn_pause.png"> inside #btn-pause-game, remove text
  - #btn-home-pause (inside the pause card): do NOT replace its text with an image — keep it
    as a styled text button so it matches the visual weight of #btn-resume-game and
    #btn-restart-pause. The pause card buttons must all be full-width, same size.
  - pause_card.png → apply as background-image on #pause-card, background-size: 100% 100%,
    keep all child elements (title, buttons) visible on top, remove conflicting background-color
  - #pause-overlay (the dark backdrop behind #pause-card): must use a semi-transparent
    background — e.g. rgba(0,0,0,0.55). NEVER opaque black or any solid colour. The game
    world must remain dimly visible behind the pause menu. If #pause-overlay currently has
    an opaque background, change it to rgba(0,0,0,0.55).
  - #pause-overlay positioning: use `position: absolute; inset: 0` so it covers exactly the
    app container (the game box). NEVER use position:fixed — that makes the overlay span the
    full desktop viewport, causing the card's percentage width to become enormous.
  - #pause-card must be a plain flex child (position: relative or static, NOT absolute/fixed).
    The overlay's `display: flex; align-items: center; justify-content: center` centers it.
    Set width: 85%; max-width: 360px on #pause-card. Never leave max-width unconstrained.
  - Pause menu buttons (#btn-resume-game, #btn-restart-pause, #btn-home-pause): apply a
    background-color from the theme palette (primary or secondary colour). They must look
    like the game's own buttons, not browser-default grey. Add border-radius, font-weight:bold,
    and a contrasting text colour. All three buttons must be the same size, full-width within
    the card. Never leave these buttons unstyled.

Z-INDEX STACKING (strictly enforced):
  Every game uses this layering — apply it as you wire assets, do not leave z-index unset:
  - Decorative background elements (floating shapes, scattered 3D pieces, ambient particles
    that are purely visual decoration and are separate DOM elements): z-index: 0.
    These must NEVER appear above any UI panel, overlay, or button.
    NEVER apply z-index:0 to the main game canvas, game board, grid, or any element
    whose children are JS-positioned game objects — those stay at z-index: 1.
  - Game board / play area / canvas: z-index: 1
  - HUD / topbar row: z-index: 10
  - In-game popup panels (win card, gameover card inside the play area): z-index: 100
  - #pause-overlay (the full-screen dim + pause card): z-index: 9999
  If you see any decorative DOM element that could overlap a panel or overlay, explicitly set
  its z-index to 0 and its parent to position:relative so the stacking context is correct.

ALIGNMENT:
  - All wired sprites must appear visually centred on their game object — never offset or clipped
  - DOM sprite <img> tags: set display:block, object-fit:contain, and match the container's dimensions
  - Canvas sprites: draw centred on the object's (x, y) position using (x - drawW/2, y - drawH/2)

CSS CONFLICT RESOLUTION:
  For any DOM element receiving a sprite or panel image, remove conflicting CSS background-color
  and border so the pre-existing styles do not show behind the transparent image.

Do NOT restructure screens, change game logic, or re-add background/game_preview.
Return ONLY the complete modified HTML. No explanation."""

PASS5 = """\
You are a QA engineer doing a final playability pass on a fully assembled HTML game.
The game has 3 screens: screen-home (title), screen-game (gameplay), screen-gameover (game over).

Fix the following classes of bugs and visual issues. Do not change game logic or asset filenames/paths:

1. SCREEN ROUTING CORRECTNESS
   a) On load — static HTML: ensure the screen-home div has class="screen active" in the HTML
      attribute itself, not just via JS. If the div only has class="screen" without "active",
      add "active" directly to the class attribute in the HTML.
   b) On load — JS: remove any JS that auto-navigates away from screen-home on page load.
   c) PLAY button — direct navigation: the #btn-start click/pointerdown handler must navigate
      DIRECTLY to screen-game in ONE step. If it currently shows an intermediate screen first
      (e.g., a betting screen, difficulty select, category select, or any screen that is not
      screen-game), change the handler to skip all intermediate screens and call the game's
      start function + show screen-game directly. Apply sensible defaults for any parameters
      the intermediate screen was collecting (e.g., default bet=10, default difficulty='normal').
      LEVEL-BASED GAMES (has_level_select_system=true): verify the PLAY button starts a random
      level index, not always index 0. If it hardcodes 0, fix it to use
      Math.floor(Math.random() * totalLevels) where totalLevels comes from the level array.
      Never modify the level generation algorithm itself.
   d) PLAY button — ID convention: check how the game's showScreen/goto/nav helper function
      constructs element IDs. If it prepends 'screen-' to its argument (e.g., function goto(name)
      { getElementById('screen-'+name) }), then ALL calls to it must pass the bare name
      ('game', 'home', 'gameover'), NOT the full ID ('screen-game'). Fix any double-prefix bugs
      where the caller passes 'screen-game' but the helper already adds 'screen-'.
   e) Game-over: ensure the game-over transition calls the navigation helper with the correct
      argument that resolves to id="screen-gameover".

2. DOM MEASUREMENTS ON HIDDEN ELEMENTS
   Any function that reads layout dimensions (offsetWidth, offsetHeight, getBoundingClientRect,
   clientWidth, clientHeight) to calculate sizes must run AFTER the relevant screen is visible.
   Common pattern to fix: if newGame() / startGame() calls renderTray(), buildBoard(), or similar
   sizing functions BEFORE calling showScreen('screen-game'), reorder so showScreen is called first,
   then the sizing functions are called (use requestAnimationFrame or setTimeout(fn, 0) if needed).
   CRITICAL: Remove any buildGrid() / buildBoard() / calculateDimensions() calls from image
   onload / onerror / complete handlers at the top level (page load). These fire before any screen
   is visible and measure 0px. Instead, ensure the PLAY button handler calls buildGrid() (or
   equivalent) INSIDE the requestAnimationFrame, right before newGame()/startGame():
     showScreen('game');
     requestAnimationFrame(() => { buildGrid(); newGame(); });
   Also apply this to ALL restart paths — any button (Restart, Play Again, Try Again, Next Level)
   that reinitialises the game while screen-game is already visible must also call buildGrid()
   (or equivalent layout-measuring init) before newGame(). Use requestAnimationFrame if needed.

3. IMAGE LOAD PROMISES BLOCKING INIT
   If buildBoardDOM(), renderTray(), or equivalent init functions are gated inside a Promise.all
   waiting for images, add .onerror handlers so a failed image load does not hang the game forever.
   Do NOT call layout-measuring init functions at page load — they will measure 0px on hidden
   screens. Only call them after the relevant screen is shown (inside the PLAY button rAF).

4. PLAY BUTTON ALWAYS STARTS FRESH
   The PLAY button must always call newGame() or equivalent fresh-start function.
   Remove any continueGame() / loadGame() / resume logic from the PLAY button handler.
   Best-score localStorage is fine to keep; only remove saved board/progress state.

5. CANVAS BACKGROUND VISIBILITY
   If the game uses a canvas and clears it each frame with ctx.fillRect(0, 0, W, H) using a solid
   colour, replace that line with ctx.clearRect(0, 0, W, H) so the background.png behind the canvas
   shows through. If a semi-transparent overlay is needed for readability, use fillStyle with alpha
   (e.g. 'rgba(0,0,0,0.3)') instead of a solid colour.

6. AUDIO UNLOCK ON FIRST GESTURE
   If the game uses AudioContext or webkitAudioContext, add an audio unlock handler if not already
   present. At the end of the script, add a self-executing function that listens for the first
   pointerdown event, resumes any suspended AudioContext, and removes itself. Use "once: true" on
   the event listener. Find the existing AudioContext variable name in the code and call .resume() on it,
   OR create a temporary AudioContext just to unlock it.

7. GAME-OVER SCREEN COMPLETENESS
   Inspect the STATIC HTML of #screen-gameover right now. If it contains NO interactive element
   (no <button>, no <div onclick>, no <div onpointerdown>, no <a>) that restarts or advances the
   game, inject one: <button class="btn-primary" onclick="[restart_fn]()">PLAY AGAIN</button>
   where [restart_fn] is the game's restart/new-game function name (find it in the JS). Do this
   even if the game-over screen has not been triggered during testing — check the static DOM.
   Also ensure: (a) a visible final score or result display exists, (b) if the game had level
   progression buttons, preserve them.

8. POINTER EVENT CONSISTENCY
   Replace all inline `onclick="..."` on game buttons with `onpointerdown="..."` for consistent
   low-latency response on both mobile and desktop. The only exception is `<a>` links.
   This applies to all buttons: Play Again, Restart, Home, Next Level, etc.

9. VISUAL LAYOUT AUDIT — self-critique each screen and fix what looks wrong.
   For each screen below, ask yourself: "Does this look like a real polished mobile game, or like
   an AI-generated template?" Then fix the CSS and HTML to close the gap. You may edit <style>
   blocks and tweak element structure (class names, wrapper divs, inline styles) — but do NOT
   change game logic, asset filenames, or element IDs.

   TITLE SCREEN (#screen-home):
   - Is the title image visually dominant (largest element)? If it is tiny or same size as
     other elements, increase its max-width to 70–80% of the container.
   - Is the preview image clearly a "gameplay preview"? Should be ~35–45% container width.
   - Is the PLAY button obviously the primary CTA? Should be larger than secondary elements.
   - Are the three elements spread generously across the full screen height? If they cluster
     at the top or center, add justify-content: space-evenly to #screen-home.
   - Is there any leftover raw text (game name as plain <h1>/<h2>, or a small text badge/pill
     element) alongside the title image? If a text element shows the game's name and the title
     image already shows it, the text element is redundant — hide it with display:none.
   - Is there any plain white or solid-coloured rectangular element that has no content and
     no background-image? This is an orphaned container from the original HTML. Make it
     background: transparent so it doesn't appear as a white block over the background.

   GAME SCREEN (#screen-game):
   - Is the score/HUD visible and legible without squinting? If font-size is under 0.9em or
     the score element has no background panel, increase its visual prominence.
   - Count every strip above the game board: topbar row, score/stat row, any secondary info
     row (moves counter, combo bar, mana strip). If their combined height rivals or exceeds
     the game board/canvas height, the HUD is dominating — this must be fixed. Merge secondary
     rows into the main score row, reduce padding on existing rows, or remove redundant stat
     displays. The game board must be the single tallest element on the screen.
   - Do #btn-home-game and #btn-pause-game feel like small secondary controls, or do they
     visually compete with the game content? If they look dominant, reduce them.
   - Are resource/progress bars (health, mana, energy, stamina) clearly readable — thick enough
     to communicate state at a glance? A hairline bar is not useful to a player.
   - Does the game board/grid/canvas fill a natural proportion of the screen (~60–75% height)?
     If it is tiny (under 40%) or overflows its container, fix the sizing.
   - Does anything overflow its container? Add overflow:hidden where needed.
   - Are there any orphaned visual artifacts — elements with visible rounded-corner borders,
     faint background shapes, or placeholder ovals/capsules/rectangles that have no game
     content and no interactive purpose? These are often leftover from the original HTML
     before it was restructured. Hide them with display:none. Common locations: below the
     game board, beside the topbar, or as empty floats around the play area.
   - Pause overlay: `#pause-overlay` must use `position: absolute; inset: 0` (NOT fixed — fixed
     makes the overlay cover the full desktop viewport and causes percentage widths on the pause
     card to become enormous). Ensure `#pause-overlay` has `display: flex; align-items: center;
     justify-content: center` when active. `#pause-card` must be a plain flex child (NOT
     `position: absolute` or `position: fixed`) with `width: 85%; max-width: 360px`. If the card
     currently has `position: absolute; top: 50%; left: 50%`, remove that and let the flex parent
     center it. If `max-width` is missing or unconstrained (e.g. `max-width: 90%`), add
     `max-width: 360px`.
   - HUD consolidation: If there are more than two horizontal rows of HUD elements stacked above
     the game board, and their combined height consumes more than ~25% of the screen, consolidate
     them. Any plain text-only row (no background-image panel) that contains only counters or
     action labels should be merged into the adjacent HUD row rather than occupying its own strip.
     Navigation buttons (home, pause) belong in the topbar row, not in a separate row.
     EXCEPTION: do NOT merge or remove any row that has a `background-image` applied to it —
     that image was intentionally wired in a previous pass and must stay on its target element.
   - Play area fills available height: The container directly holding game elements MUST use
     `flex: 1; min-height: 0`. Additionally, if a DOM game board/grid has a fixed pixel CSS size
     that leaves large empty margins above and below it, replace the fixed CSS dimensions with
     container-relative sizing (percentage widths, `aspect-ratio`, or JS that reads
     `container.offsetWidth`/`offsetHeight` after the screen is shown via requestAnimationFrame).
     EXCEPTION: do NOT modify `<canvas>` element `width`/`height` attributes or the JS that sets
     them — canvas pixel dimensions define the coordinate system and must not be changed via CSS.
   - Game mechanic container backgrounds: Repeating game element containers (reels, grid cells,
     sortable slots, etc.) must not have a white or near-white background when the game theme is
     dark or richly coloured. A white fill breaks immersion against a dark background. Replace
     with a semi-transparent or theme-appropriate dark fill.
   - Z-index stacking: Decorative background elements (floating shapes, scattered 3D pieces,
     ambient particles — purely visual, not interactive) must have z-index: 0. If any such
     element appears visually on top of a UI panel, overlay, or button, fix it by setting
     z-index: 0 on the decorative element (and z-index: 9999 on #pause-overlay if not already set).
     The stacking order must always be: decorations (0) → game board (1) → HUD (10) →
     popups (100) → pause overlay (9999).

   GAME-OVER SCREEN (#screen-gameover):
   - Is the primary score/result displayed at LARGE size — clearly bigger than secondary stats?
     If all stats are the same font-size, make the primary score at least 2× larger.
   - Does the screen feel emotionally impactful (celebratory win or dramatic loss), or does
     it look like a plain data table? If bland: bold the palette, add the theme accent colour
     to the primary score, increase spacing.
   - Is there visible, prominent empty space between the score area and the buttons?
     If buttons are crammed directly under the stats, add margin or padding.
   - Are there any plain unstyled text buttons that should look like the theme? Style them.

   GLOBAL:
   - Any element with a `background-image` panel that has visible browser-default background-color
     or border bleeding through: remove that background-color/border.
   - Any button that still shows default browser button styling (grey background, system font,
     visible border): apply the game's theme styling to it.
   - Text that is unreadable against its background: add text-shadow or change colour.

Return ONLY the complete modified HTML. No explanation."""

PASS_VISUAL_AUDIT = """\
You are a strict visual QA engineer reviewing a mobile HTML game rendered in a real browser.
Screenshots are labelled HOME, GAME, PAUSE, GAMEOVER. COVER ART (if shown) is the quality reference.

Be critical. Err on the side of reporting issues — a false positive is better than a miss.
Report everything that looks wrong, broken, misaligned, or would frustrate a real player.

PRIORITY ISSUES — always report if present:
1. Game board tiles, pieces, or decorative 3D objects appearing OVER UI panels, overlays,
   buttons, or text on any screen. These should be behind UI, never visually on top of it.
2. Pause card not cleanly centred in the overlay — off-centre, pushed to a corner,
   partially cut off, or overlapped by game objects.
3. Score/stat labels with no visible value, or text illegible against its background.
4. Buttons partially hidden, overlapped, or with no readable label.
5. Game board occupying less than 50% of the screen height on the game screen.
6. Large blank or white areas with no content (orphaned containers).
7. Content clipped by its container.

ALSO REPORT:
- Elements that should be centred but are visibly off to one side
- HUD rows whose combined height rivals the game board
- Flex children not filling available space

DO NOT report:
- Subjective style preferences ("looks generic", "could be more polished")
- Canvas solid fill colour (handled separately by a later pass)

Return ONLY valid JSON, no markdown:
{
  "home": ["<specific issue>", ...],
  "game": ["<specific issue>", ...],
  "pause": ["<specific issue>", ...],
  "gameover": ["<specific issue>", ...],
  "overall": ["<cross-screen CSS issue>", ...]
}
If a screen is genuinely correct with no fixable defects, return an empty array for it."""

# ─── CSS extraction helpers (for pass 3) ─────────────────────────────────────
STYLE_RE = re.compile(r'(<style[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)

def extract_css(html: str) -> tuple[str, str]:
    """Return (combined CSS text, html with style blocks replaced by __STYLES__ marker)."""
    blocks = STYLE_RE.findall(html)
    css = "\n\n".join(content for _, content, _ in blocks)
    stripped = STYLE_RE.sub("", html)
    # Insert marker — try </head> first, fall back to start of <body>, then top of file
    if "</head>" in stripped:
        stripped = stripped.replace("</head>", "__STYLES__\n</head>", 1)
    elif re.search(r'<body', stripped, re.IGNORECASE):
        stripped = re.sub(r'(<body[^>]*>)', r'\1\n__STYLES__', stripped, count=1, flags=re.IGNORECASE)
    else:
        stripped = "__STYLES__\n" + stripped
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
    skeleton = re.sub(r'\n\s*\n', '\n', no_styles).strip()
    if len(skeleton) > 6000:
        skeleton = skeleton[:6000] + "\n...[truncated for brevity]"
    return skeleton

def analyze_game(html: str) -> dict:
    """Static analysis — deterministic game type classification, zero LLM cost."""
    has_canvas     = bool(re.search(r'<canvas\b', html, re.IGNORECASE))
    has_getcontext = bool(re.search(r"getContext\s*\(\s*['\"]2d['\"]", html))
    uses_dom_meas  = bool(re.search(r'offsetWidth|offsetHeight|getBoundingClientRect|clientWidth|clientHeight', html))
    has_touch      = bool(re.search(r'touchstart|touchend|touchmove|pointerdown|pointermove|pointerup', html))
    has_levels     = bool(re.search(r'next.?level|nextLevel|nextStage|levelUp|level\s*\+\+|advance', html, re.IGNORECASE))
    has_level_select = bool(re.search(r'startLevel|loadLevel|selectLevel|levelIndex|levelIdx|LEVELS\s*=|levels\s*=\s*\[', html, re.IGNORECASE))
    has_solid_fill = bool(re.search(r'ctx\.fillRect\s*\(\s*0\s*,\s*0\s*,', html))
    has_audio_ctx  = bool(re.search(r'AudioContext|webkitAudioContext', html))
    has_audio_el   = bool(re.search(r'new Audio\s*\(', html))
    has_drag       = bool(re.search(r'pointerdown.*drag|drag.*pointerdown|mousedown.*drag|addEventListener.*drag', html, re.IGNORECASE | re.DOTALL))

    if has_canvas and has_getcontext and uses_dom_meas:
        render_type = "mixed"
    elif has_canvas and has_getcontext:
        render_type = "canvas"
    else:
        render_type = "dom"

    return {
        "render_type":       render_type,
        "has_canvas":        has_canvas,
        "uses_dom_meas":     uses_dom_meas,
        "has_touch":         has_touch,
        "has_levels":        has_levels,
        "has_level_select":  has_level_select,
        "has_solid_fill":    has_solid_fill,
        "has_audio_ctx":     has_audio_ctx,
        "has_audio_el":      has_audio_el,
        "has_drag":          has_drag,
    }

def extract_js_symbols(html: str) -> set:
    """Extract JS function names from all script tags."""
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    js = "\n".join(scripts)
    named = set(re.findall(r'function\s+(\w+)\s*\(', js))
    arrow = set(re.findall(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>|\w+\s*=>)', js))
    return named | arrow


def validate_js_syntax(html: str) -> list:
    """Extract JS and check syntax via `node --check`. Returns list of error lines."""
    import subprocess, tempfile, os as _os
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    if not scripts:
        return []
    js = "\n".join(scripts)
    tmp = None
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
    finally:
        if tmp:
            try:
                _os.unlink(tmp)
            except OSError:
                pass


def game_context_for_pass0(html: str) -> str:
    """Build richer Pass 0 input: structure + first 12000 chars of JS for mechanic understanding."""
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    js = '\n'.join(scripts)[:12000]
    struct = html_skeleton(html)
    return f"=== HTML STRUCTURE ===\n{struct}\n\n=== GAME LOGIC (truncated) ===\n{js}"

# ─── Cover image helpers ──────────────────────────────────────────────────────
COVER_DIRS = [
    ROOT_DIR / "开头字母A-J游戏封面图",
    ROOT_DIR / "开头字母K-Z游戏封面图",
]

def find_cover_image(name: str) -> Path | None:
    for d in COVER_DIRS:
        p = d / f"{name}.png"
        if p.exists():
            return p
    return None

def encode_image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()

def call_llm_vision(system: str, text: str, image_b64: str, label: str,
                    max_tokens: int = MAX_TOKENS) -> str | None:
    try:
        res = client.chat.completions.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": f"{system}\n\n{text}"}
                ]
            }]
        )
        if res.choices[0].finish_reason == "length":
            print(f"    ⚠  {label}: truncated")
            return None
        result = _strip_fences(res.choices[0].message.content)
        result = _fix_screen_backgrounds(result)
        return result
    except Exception as e:
        print(f"    ✗  {label}: {e}")
        return None

def call_llm_vision_multi(system: str, text: str, images: list,
                           label: str, max_tokens: int = 2048) -> str | None:
    """Call LLM with multiple labelled images. images: list of (label_str, base64_png) tuples."""
    if not images:
        return None
    try:
        content = []
        for img_label, img_b64 in images:
            if not img_b64:
                continue
            content.append({"type": "text", "text": img_label})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
        if not content:
            return None
        content.append({"type": "text", "text": f"{system}\n\n{text}"})
        res = client.chat.completions.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}]
        )
        if res.choices[0].finish_reason == "length":
            print(f"    ⚠  {label}: truncated")
            return None
        return _strip_fences(res.choices[0].message.content)
    except Exception as e:
        print(f"    ✗  {label}: {e}")
        return None

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
    r'(#screen-(?:home|game|gameover)\s*\{[^}]*?)'
    r'(?:background(?:-color)?\s*:\s*(?!url\([\'"]?assets/images/background_(?:home|game|gameover))[^;]+;[ \t]*\n?)'
    r'([^}]*?\})',
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

def fix_css_issues(html: str) -> str:
    """Regex-based post-processor: remove known bad CSS patterns after Pass 3."""
    css, html_template = extract_css(html)
    if not css:
        return html

    # 1. Remove box-shadow from image asset containers (creates visible rectangles on transparent images)
    for sel in [r'\.game-title', r'\.game-preview', r'#btn-start']:
        css = re.sub(rf'({sel}\s*\{{[^}}]*?)box-shadow\s*:[^;]+;', r'\1', css, flags=re.DOTALL)

    # 2. Remove filter with drop-shadow/glow from image title/preview (amplifies existing glow in image)
    for sel in [r'\.game-title', r'\.game-preview']:
        css = re.sub(rf'({sel}\s*\{{[^}}]*?)filter\s*:[^;]+;', r'\1', css, flags=re.DOTALL)

    # 3. Replace vw in font-size clamp() with % so it scales with container not viewport
    css = re.sub(r'(font-size\s*:\s*clamp\([^,]+,\s*)(\d+\.?\d*)vw', r'\1\2%', css)
    css = re.sub(r'(min-width\s*:\s*clamp\([^,]+,\s*)(\d+\.?\d*)vw',  r'\1\2%', css)

    # 4. Remove opacity:0 from .screen base rule (makes initial active screen invisible)
    css = re.sub(r'(\.screen\s*\{[^}]*?)opacity\s*:\s*0\s*;', r'\1', css, flags=re.DOTALL)

    # 5. Remove padding overrides on .screen (PASS1 template already sets padding:24px)
    css = re.sub(r'((?:^|\})\s*\.screen\s*\{[^}]*?)padding\s*:[^;]+;', r'\1', css, flags=re.DOTALL | re.MULTILINE)

    # 6. Replace explicit pixel widths > 480px on non-canvas containers with max-width.
    #    canvas rules are excluded — their pixel dimensions define the render surface and
    #    must not be changed, or the game's coordinate system breaks.
    _CANVAS_RULE_RE = re.compile(
        r'(?:^|\})\s*(?:canvas\b|#canvas\b|\.canvas\b)[^{]*\{[^}]*\}', re.DOTALL | re.MULTILINE
    )
    # Extract canvas rules, replace in non-canvas portions only
    canvas_rules = _CANVAS_RULE_RE.findall(css)
    css_no_canvas = _CANVAS_RULE_RE.sub('\0CANVAS\0', css)

    def replace_wide_widths(m):
        val = int(m.group(1))
        if val > 480:
            return 'max-width: 100%; width: 100%'
        return m.group(0)
    css_no_canvas = re.sub(r'(?<!\-)width\s*:\s*(\d+)px', replace_wide_widths, css_no_canvas)

    # Restore canvas rules untouched
    for rule in canvas_rules:
        css_no_canvas = css_no_canvas.replace('\0CANVAS\0', rule, 1)
    css = css_no_canvas

    # 7. Ensure grid/calendar containers don't overflow — add max-width:100% to common grid wrappers
    for grid_sel in [r'\.calendar', r'\.grid', r'\.days-grid', r'\.card-grid', r'\.board-grid']:
        css = re.sub(
            rf'({grid_sel}\s*\{{)',
            r'\1 max-width: 100%; overflow: hidden;',
            css, flags=re.DOTALL
        )

    # 7b. Fix overlay layers (#tiles, #tile-layer, #pieces-layer, etc.) that must overlap #cells
    #     PASS3 often assigns position:relative to both layers, stacking them vertically.
    #     Replace position:relative on any *-layer or #tiles selector with absolute overlay.
    for overlay_sel in [r'#tiles\b', r'#tile-layer\b', r'#pieces-layer\b', r'#overlay\b', r'#animations\b']:
        css = re.sub(
            rf'({overlay_sel}\s*\{{[^}}]*?)position\s*:\s*relative\s*;',
            r'\1position: absolute; top: 0; left: 0;',
            css, flags=re.DOTALL
        )

    # 8. Strip solid background-color from elements that also have a background-image panel.
    #    A solid child background covers the panel image, making it invisible.
    #    Find CSS rules that have both background-image and background-color, remove the color.
    css = re.sub(
        r'((?:[^{}]+\{[^}]*?)background-image\s*:[^;]+;[^}]*?)background(?:-color)?\s*:\s*(?!transparent)[^;]+;',
        r'\1background: transparent;',
        css, flags=re.DOTALL
    )

    # 9. Remove background-image from game board containers — breaks JS tile measurement/positioning
    for board_sel in [r'#grid-wrap', r'#board\b', r'#game-area\b', r'#play-area\b',
                      r'#game-board', r'#gameBoard', r'#world\b', r'#canvas-wrap',
                      r'#tiles\b', r'#cells\b']:
        css = re.sub(
            rf'({board_sel}\s*\{{[^}}]*?)background-image\s*:[^;]+;',
            r'\1', css, flags=re.DOTALL
        )

    # 9b. Fix #pause-overlay: must be position:absolute (not fixed) so it stays within .app-container.
    #     position:fixed makes percentage widths on #pause-card calculate against the full viewport.
    css = re.sub(
        r'(#pause-overlay\s*\{[^}]*?)position\s*:\s*fixed\s*;',
        r'\1position: absolute;',
        css, flags=re.DOTALL
    )

    # 9c. Fix #pause-card: remove position:absolute/fixed so it centers via flex parent.
    #     LLMs sometimes add `position:absolute; top:50%; left:50%; transform:translate(-50%,-50%)`
    #     which takes the card OUT of flex flow, making the overlay's align-items:center useless.
    css = re.sub(
        r'(#pause-card\s*\{[^}]*?)position\s*:\s*(?:absolute|fixed)\s*;',
        r'\1position: relative;',
        css, flags=re.DOTALL
    )
    # Also strip top/left/transform centering that only made sense when position:absolute
    css = re.sub(
        r'(#pause-card\s*\{[^}]*?)top\s*:\s*50%\s*;',
        r'\1',
        css, flags=re.DOTALL
    )
    css = re.sub(
        r'(#pause-card\s*\{[^}]*?)left\s*:\s*50%\s*;',
        r'\1',
        css, flags=re.DOTALL
    )
    # Match any transform: containing translate (handles compound values, no-space variants,
    # translateX/Y shorthands) since the only reason these exist on #pause-card is absolute centering.
    css = re.sub(
        r'(#pause-card\s*\{[^}]*?)transform\s*:\s*[^;}]*translate[^;}]*;',
        r'\1',
        css, flags=re.DOTALL
    )

    # 10. Remove any duplicate #screen-game > * rules Pass 3 may have generated
    #     (keeps only the last occurrence; our guardrail below will be the authoritative one)
    seen_game_rule = False
    clean_blocks = []
    for block in re.split(r'(?=\n?#screen-game\s*>\s*\*)', css):
        if re.match(r'\s*#screen-game\s*>\s*\*', block):
            if seen_game_rule:
                continue  # drop duplicate
            seen_game_rule = True
        clean_blocks.append(block)
    css = ''.join(clean_blocks)

    # Always append structural guardrail last — only once (fix_css_issues runs after Pass3, Pass4D, and Pass5)
    if "structural guardrail" not in css:
        css += "\n/* ── structural guardrail ── */\n"
        css += "#screen-game { position: relative; overflow: hidden !important; }\n"
        css += "#screen-game > * { max-width: 100% !important; overflow: hidden !important; }\n"
        css += "#pause-overlay { z-index: 9999 !important; }\n"
    # Also strip any overflow:visible declarations from within screen-game scope
    css = re.sub(
        r'(#screen-game[^{]*\{[^}]*?)overflow\s*:\s*visible\s*;',
        r'\1', css, flags=re.DOTALL
    )

    return inject_css(html_template, f"<style>\n{css.strip()}\n</style>")

_PASS1_REQUIRED_IDS = [
    "screen-home", "screen-game", "screen-gameover",
    "btn-start", "btn-playagain", "btn-home-game", "btn-pause-game", "pause-overlay",
]

def validate_pass1(html: str) -> list[str]:
    """Return list of required IDs missing from html. Empty list = valid."""
    missing = []
    for id_ in _PASS1_REQUIRED_IDS:
        if not re.search(r'id\s*=\s*["\']' + re.escape(id_) + r'["\']', html):
            missing.append(id_)
    return missing

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


def _extract_json_from_solve(text: str) -> dict | None:
    """Extract the first valid JSON object from a solve() response.
    solve() appends AW critic text after the answer — strip it before parsing."""
    import json as _json
    candidates = [
        text.strip(),
        _strip_fences(text),
    ]
    for c in candidates:
        try:
            return _json.loads(c)
        except Exception:
            pass
        for sep in ["\n---", "\n[AW", "\n\n"]:
            idx = c.find(sep)
            if idx != -1:
                try:
                    return _json.loads(c[:idx].strip())
                except Exception:
                    pass
        import re as _re
        m = _re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', c)
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
            result = solve(
                problem,
                backend="openrouter",
                max_tokens=max_tokens,
                file_paths=file_paths,
                verify_consistency=bool(file_paths),
                adaptive=True,
            )
            stripped_r = result.strip() if result else ""
            probe = _strip_fences(stripped_r).lstrip()
            if stripped_r and len(stripped_r) > 10 and probe.startswith("{"):
                return result
            print(f"    ⚠  tp_analyze {label}: empty or non-JSON response, falling back")
        except Exception as e:
            print(f"    ⚠  tp_analyze {label}: {e}, falling back to call_llm")
    return call_llm(fallback_system, fallback_html, label, max_tokens=max_tokens)


# ─── Image generation ─────────────────────────────────────────────────────────
def _strip_background(img_bytes: bytes) -> bytes:
    """Remove solid/white background if present, then crop to content bounding box."""
    try:
        img = PILImage.open(BytesIO(img_bytes)).convert("RGBA")
        pixels = list(img.getdata())
        # Check if image has any real transparency (alpha < 250)
        has_transparency = any(p[3] < 250 for p in pixels)
        if not has_transparency:
            # All pixels are opaque — likely white background; use rembg to remove it
            try:
                from rembg import remove as rembg_remove
                img_bytes = rembg_remove(img_bytes)
                img = PILImage.open(BytesIO(img_bytes)).convert("RGBA")
            except Exception as e:
                print(f"    ⚠  rembg failed ({e}), keeping original")
        # Crop to bounding box to remove residual transparent border
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        print(f"    ⚠  background removal failed ({e}), keeping original")
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

def generate_images_parallel(images: list, img_dir: Path, max_workers: int = 4,
                              style_lock: dict = None, genre_direction: str = "") -> set:
    """Generate images concurrently. Returns set of failed filenames."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _build_desc(img: dict) -> tuple[str, bool]:
        fname = img["filename"]
        is_bg = fname.startswith("background")
        transparent = img.get("transparent", not is_bg)
        desc = img["description"]
        if style_lock:
            art = style_lock.get("art_style", "")
            neg = style_lock.get("negative_terms", "")
            palette = style_lock.get("hex_palette", [])
            rendering = style_lock.get("rendering", "")
            mood = style_lock.get("mood", "")
            if isinstance(neg, list):
                neg = ", ".join(neg)
            if art:
                prefix = f"Art style: {art}."
                if palette:
                    prefix += f" Colour palette: {', '.join(palette[:5])}."
                if rendering and rendering != art:
                    prefix += f" {rendering}."
                if mood:
                    prefix += f" {mood}."
                desc = f"{prefix} {desc}"
            if neg and art:
                desc = f"{desc} {neg}."
        if genre_direction:
            if not style_lock:
                desc = f"Art style: {genre_direction}. {desc}"
            else:
                desc = f"{desc} Genre-specific style: {genre_direction}."
        if transparent:
            desc += " Isolated on a fully transparent background — no white fill, no background color, PNG with alpha channel."
        return desc, transparent

    def _post_process(path: Path, target_w: int | None, target_h: int | None) -> None:
        """Crop transparent padding then resize to manifest dimensions. Operates on saved file."""
        try:
            img = PILImage.open(path).convert("RGBA")
            bbox = img.getbbox()
            if bbox and bbox != (0, 0, img.width, img.height):
                img = img.crop(bbox)
            if target_w and target_h and (img.width != target_w or img.height != target_h):
                img = img.resize((target_w, target_h), PILImage.LANCZOS)
            img.save(path)
        except Exception as e:
            print(f"    ⚠  _post_process {path.name}: {e}")

    def _gen_one(img: dict) -> tuple[str, bool]:
        fname = img["filename"]
        out_path = img_dir / fname
        target_w = img.get("w")
        target_h = img.get("h")
        if out_path.exists():
            # Manifest w/h is source of truth — crop padding + resize cached file if needed
            _post_process(out_path, target_w, target_h)
            return fname, True
        desc, transparent = _build_desc(img)
        ok = gen_image(desc, out_path, transparent=transparent)
        if not ok:
            time.sleep(2)
            ok = gen_image(desc, out_path, transparent=transparent)
        if ok and out_path.exists():
            # Crop transparent padding then resize to declared manifest dimensions
            _post_process(out_path, target_w, target_h)
        return fname, ok

    failed = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_gen_one, img): img for img in images}
        for f in as_completed(futures):
            img = futures[f]
            try:
                fname, ok = f.result()
                if not ok:
                    failed.add(fname)
                    print(f"    ✗  image {fname}: failed after retry")
                else:
                    print(f"    ✓  image {fname}")
            except Exception as e:
                fname = img["filename"]
                failed.add(fname)
                print(f"    ✗  image {fname}: exception — {e}")

    # Final verification: re-run _post_process on all transparent images to catch any
    # silent failures during parallel generation (thread-safety edge cases in PIL save)
    for img_meta in images:
        if not img_meta.get("transparent"):
            continue
        p = img_dir / img_meta["filename"]
        if p not in failed and p.exists():
            _post_process(p, img_meta.get("w"), img_meta.get("h"))

    return failed


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


# ─── Playwright smoke test ─────────────────────────────────────────────────────
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

            if game_active:
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


def screenshot_screens(out_dir: Path, html: str) -> dict:
    """Render the game in headless Chromium and capture screenshots of all 3 screens.
    Writes a temporary file so the screenshots reflect the current in-memory HTML.
    Returns dict with keys: home_b64, game_b64, gameover_b64 (base64 PNG strings).
    Missing keys mean that screen could not be captured."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}

    import base64
    tmp_path = out_dir / "_screenshot_tmp.html"
    tmp_path.write_text(html, encoding="utf-8")

    result = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: None)  # suppress errors during capture
            page.goto(f"file://{tmp_path}", wait_until="domcontentloaded")
            page.wait_for_timeout(800)

            # Home screen
            result["home_b64"] = base64.b64encode(page.screenshot()).decode()

            # Game screen — click PLAY button
            try:
                page.click("#btn-start", timeout=3000)
                page.wait_for_timeout(1500)
                result["game_b64"] = base64.b64encode(page.screenshot()).decode()
            except Exception:
                pass

            # Pause screen — force-show #pause-overlay while on game screen
            if result.get("game_b64"):
                try:
                    page.evaluate("""() => {
                        const overlay = document.getElementById('pause-overlay');
                        if (overlay) { overlay.style.display = 'flex'; overlay.classList.add('active'); }
                    }""")
                    page.wait_for_timeout(400)
                    result["pause_b64"] = base64.b64encode(page.screenshot()).decode()
                    # Dismiss overlay before gameover screenshot
                    page.evaluate("""() => {
                        const overlay = document.getElementById('pause-overlay');
                        if (overlay) { overlay.style.display = ''; overlay.classList.remove('active'); }
                    }""")
                except Exception:
                    pass

            # Gameover screen — force-show it via JS (bypass needing to actually play)
            try:
                page.evaluate("""() => {
                    document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
                    const go = document.getElementById('screen-gameover');
                    if (go) go.classList.add('active');
                }""")
                page.wait_for_timeout(500)
                result["gameover_b64"] = base64.b64encode(page.screenshot()).decode()
            except Exception:
                pass

            browser.close()
    except Exception as e:
        print(f"    ⚠  screenshot: {e}")
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    return result


# ─── Single-game pipeline ─────────────────────────────────────────────────────
def beautify(src_dir: Path, out_dir: Path | None = None) -> bool:
    global _last_report
    name    = src_dir.name
    out_dir = Path(out_dir) if out_dir else BEAUTIFIED / name
    prog_f  = out_dir / ".progress.json"

    if not (src_dir / "index.html").exists():
        print(f"    ✗  no index.html"); return False

    out_dir.mkdir(parents=True, exist_ok=True)

    # Preserve original once
    orig = out_dir / "index_original.html"
    if not orig.exists():
        shutil.copy(src_dir / "index.html", orig)

    # Cover image (optional — used as visual reference for Pass 3 + Pass 4A)
    cover_path = find_cover_image(name)
    cover_b64  = encode_image_b64(cover_path) if cover_path else None
    print(f"    ○ cover: {'found ✓' if cover_b64 else 'not found (text-only mode)'}")

    # Progress tracking
    prog = json.loads(prog_f.read_text()) if prog_f.exists() else {}
    def save_prog(): prog_f.write_text(json.dumps(prog, indent=2))

    # Migrate old pass4 flag → new split flags so existing games aren't re-processed
    _old_pass4_done = prog.get("pass4") or prog.get("pass4_skipped") or prog.get("pass4_failed")
    if _old_pass4_done and not prog.get("pass4_assets"):
        prog["pass4_assets"] = True
        prog["pass4_wire"] = True
        prog["pass4_manifest"] = True
        mf = out_dir / "assets" / "manifest.json"
        if mf.exists() and not prog.get("manifest"):
            prog["manifest"] = json.loads(mf.read_text())
        save_prog()
    # Migrate pass4_assets (old meaning: manifest+images) → also mark pass4_manifest done
    if prog.get("pass4_assets") and not prog.get("pass4_manifest"):
        prog["pass4_manifest"] = True
        save_prog()

    # Load from last completed pass
    def latest_html() -> str:
        for fname in ("index_pass5.html", "index_pass_va.html", "index_pass4.html", "index_pass3.html", "index_pass2.html", "index_pass1.html", "index_original.html"):
            f = out_dir / fname
            if f.exists():
                return f.read_text(encoding="utf-8", errors="ignore")
        return orig.read_text(encoding="utf-8", errors="ignore")

    html = latest_html()

    # ── Pre-pass: static game analysis (zero cost, deterministic) ────────────
    game_meta = prog.get("game_meta")
    if not game_meta:
        game_meta = analyze_game(html)
        prog["game_meta"] = game_meta
        save_prog()
    render_type = game_meta.get("render_type", "dom")
    print(f"    ○ game type: {render_type} | canvas={game_meta.get('has_canvas')} | dom_meas={game_meta.get('uses_dom_meas')} | levels={game_meta.get('has_levels')}")

    # ── Pass 0: determine visual theme + screen size ─────────────────────────
    theme_json = prog.get("theme_json")
    if not theme_json:
        print("    → Pass 0: determine visual theme + screen size (Trinity Protocol)")
        pass0_input = game_context_for_pass0(html)
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
        if not theme_json:
            print(f"    ⚠  pass0: using fallback casual-arcade theme")
            theme_json = '{"theme":"casual arcade","palette":["#4A90D9","#F5A623","#7ED321","#D0021B"],"mood":"Bright and energetic.","font_style":"rounded playful","aspect_w":9,"aspect_h":16,"max_width":480}'
            prog["theme_json"] = theme_json
        save_prog()

    # Extract layout decided by Pass 0
    theme_data = json.loads(theme_json)
    aspect_w  = theme_data.get("aspect_w",  9)
    aspect_h  = theme_data.get("aspect_h",  16)
    max_width = theme_data.get("max_width", 480)
    # Derive representative screen dimensions for PASS3 context
    screen_height = int(max_width * aspect_h / aspect_w)

    # ── Pass 1: strip UI ──────────────────────────────────────────────────────
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
                if r2:
                    still_missing = validate_pass1(r2)
                    if still_missing:
                        print(f"    ⚠  pass1-retry still missing {still_missing}, using best attempt")
                    r = r2
                else:
                    print(f"    ⚠  pass1-retry call failed, using original (missing: {missing})")
            # JS invariant check — critical functions must not be removed by restructuring
            before_symbols = extract_js_symbols(html)  # html is still the pre-pass1 original here
            if before_symbols:
                after_symbols = extract_js_symbols(r)
                removed = before_symbols - after_symbols
                if removed:
                    print(f"    ⚠  pass1 removed JS functions: {sorted(removed)[:5]} — retrying")
                    symbol_note = f"\n\nCRITICAL: Do NOT remove or rename these JS functions: {sorted(removed)}. They must remain in your output."
                    r3 = call_llm(pass1_prompt + symbol_note, html, "pass1-js-retry", max_tokens=65536)
                    if r3:
                        r = r3
            html = r
            (out_dir / "index_pass1.html").write_text(html, encoding="utf-8")
            prog["pass1"] = True
        else:
            print("    ⚠  pass1 failed — continuing from original HTML (game may lack 3-screen structure)")
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
            # JS syntax check — catch syntax errors introduced by Pass 2
            js_errors = validate_js_syntax(html)
            if js_errors:
                print(f"    ⚠  pass2 JS syntax errors ({len(js_errors)} lines) — retrying")
                error_note = f"\n\nJS SYNTAX ERRORS in your output:\n" + "\n".join(js_errors[:5]) + "\nFix these errors and return the complete corrected HTML."
                r2 = call_llm(PASS2 + error_note, html, "pass2-syntax-retry")
                if r2 and not validate_js_syntax(r2):
                    html = r2
            (out_dir / "index_pass2.html").write_text(html, encoding="utf-8")
            prog["pass2"] = True
        else:
            prog["pass2_failed"] = True
        save_prog()
    elif (out_dir / "index_pass2.html").exists():
        html = (out_dir / "index_pass2.html").read_text(encoding="utf-8")

    # ── Cover style extraction (once, before PASS3 — single source of truth) ──
    cover_style = prog.get("cover_style")
    if cover_b64 and not cover_style:
        r = call_llm_vision(
            COVER_EXTRACT,
            "Extract the visual style from this cover image.",
            cover_b64, "cover-extract", max_tokens=512
        )
        if r:
            parsed = _extract_json_from_solve(r)
            if parsed:
                cover_style = parsed
                prog["cover_style"] = cover_style
                save_prog()

    # ── Pass 3: improve visuals (CSS-only to avoid truncation) ───────────────
    if not prog.get("pass3"):
        print("    → Pass 3: improve visuals")
        css, html_template = extract_css(html)
        skeleton = html_skeleton(html)
        content = f"=== HTML STRUCTURE (context only) ===\n{skeleton}\n\n=== CURRENT CSS ===\n{css}"
        prompt3 = PASS3.format(theme_json=theme_json, aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        if cover_b64:
            if cover_style:
                palette_str = ", ".join(cover_style.get("hex_palette", []))
                cover_note = (
                    "\n\nCOVER STYLE BRIEF (extracted from cover image — apply these values directly in CSS):\n"
                    f"  CSS primary background: {cover_style.get('css_primary', '')}\n"
                    f"  CSS accent/action colour: {cover_style.get('css_accent', '')}\n"
                    f"  CSS text colour: {cover_style.get('css_text', '')}\n"
                    f"  Full palette: {palette_str}\n"
                    f"  Rendering: {cover_style.get('rendering', '')}\n"
                    f"  Mood/lighting: {cover_style.get('mood', '')}\n"
                    "\nThe cover image above confirms this brief. Use the hex values directly; do not invent new colours."
                )
            else:
                cover_note = "\nThe cover image above shows this game's official visual style. Use it as your PRIMARY reference for colours, typography mood, and overall aesthetic. The theme JSON is a guide — the cover image overrides it where they differ."
            r = call_llm_vision(prompt3 + cover_note, content, cover_b64, "pass3", max_tokens=8192)
        else:
            r = call_llm(prompt3, content, "pass3", max_tokens=8192)
        if r:
            html = inject_css(html_template, r)
            html = fix_css_issues(html)   # remove known bad CSS patterns
            (out_dir / "index_pass3.html").write_text(html, encoding="utf-8")
            prog["pass3"] = True
        else:
            prog["pass3_failed"] = True
        save_prog()
    elif (out_dir / "index_pass3.html").exists():
        html = (out_dir / "index_pass3.html").read_text(encoding="utf-8")

    # ── Pass 0.5: style lock (shared art direction for all image generation) ──
    style_lock = prog.get("style_lock")
    if not style_lock and prog.get("pass3"):
        print("    → Pass 0.5: generate style lock (Trinity Protocol)")
        if cover_style:
            # Cover extraction already ran — build style_lock from it directly, no extra LLM call
            style_lock = {
                "art_style": cover_style.get("art_style", ""),
                "hex_palette": cover_style.get("hex_palette", []),
                "rendering": cover_style.get("rendering", ""),
                "mood": cover_style.get("mood", ""),
                "line_weight": cover_style.get("rendering", ""),
                "shadow_style": cover_style.get("mood", ""),
                "background_treatment": cover_style.get("mood", ""),
                "icon_shape": "",
                "negative_terms": cover_style.get("negative_terms", ""),
            }
            prog["style_lock"] = style_lock
            print(f"    ○ style: {style_lock.get('art_style', '?')} (from cover extract)")
            save_prog()
        else:
            css_sample, _ = extract_css(html)
            genre = game_meta.get("genre", "misc") if game_meta else "misc"
            style_problem = (
                f"Game genre: {genre}\n"
                f"Approved theme: {theme_json}\n\n"
                f"Current CSS (shows committed colours and typography):\n{(css_sample[:3000].rsplit('}', 1)[0] + '}') if css_sample else '/* no styles */'}\n\n"
                f"Return ONLY valid JSON with these fields: art_style, line_weight, shadow_style, background_treatment, icon_shape, negative_terms"
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

    # ── Pass 4a: determine asset manifest ────────────────────────────────────
    manifest = prog.get("manifest")
    if not prog.get("pass4_manifest"):
        print("    → Pass 4a: determine assets")
        prompt4a = PASS4A.format(theme_json=theme_json, aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width, screen_h=screen_height)
        p4a_input = html_skeleton(html)
        if cover_b64:
            if cover_style:
                palette_str = ", ".join(cover_style.get("hex_palette", []))
                cover_note = (
                    "\n\nCOVER STYLE BRIEF (use for ALL asset descriptions):\n"
                    f"  Art style: {cover_style.get('art_style', '')}\n"
                    f"  Rendering: {cover_style.get('rendering', '')}\n"
                    f"  Mood: {cover_style.get('mood', '')}\n"
                    f"  Palette: {palette_str}\n"
                    f"  Avoid: {cover_style.get('negative_terms', '')}\n"
                    "\ngame_preview.png must closely match the cover image composition and style."
                )
            else:
                cover_note = "\nThe cover image above is this game's official artwork. All asset descriptions must match its visual style — colours, illustration style, and mood. game_preview.png should closely match the cover image composition."
            manifest_raw = call_llm_vision(prompt4a + cover_note, p4a_input, cover_b64, "pass4a", max_tokens=4096)
        else:
            manifest_raw = call_llm(prompt4a, p4a_input, "pass4a", max_tokens=4096)
        manifest = None
        if manifest_raw:
            manifest = _extract_json_from_solve(manifest_raw)
            if manifest is None:
                print(f"    ⚠  pass4a: could not parse manifest")
        if manifest:
            (out_dir / "assets" / "images").mkdir(parents=True, exist_ok=True)
            (out_dir / "assets" / "audio").mkdir(parents=True, exist_ok=True)
            (out_dir / "assets" / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False)
            )
            prog["manifest"] = manifest
            prog["pass4_manifest"] = True
        else:
            prog["pass4_manifest_skipped"] = True
        save_prog()

    # Manifest is now the single source of truth — load from progress, never mutate after this
    manifest = manifest or prog.get("manifest")

    # ── Pass 4d: wire assets into HTML (before image generation) ─────────────
    if not prog.get("pass4_wire") and manifest:
        print("    → Pass 4d: wire assets into HTML")
        prompt = PASS4D.format(manifest=json.dumps(manifest, indent=2), aspect_w=aspect_w, aspect_h=aspect_h, max_width=max_width)
        r = call_llm(prompt, html, "pass4d")
        if r:
            html = fix_css_issues(r)
            (out_dir / "index_pass4.html").write_text(html, encoding="utf-8")
            prog["pass4_wire"] = True
        else:
            prog["pass4_wire_failed"] = True
        save_prog()
    elif (out_dir / "index_pass4.html").exists():
        html = (out_dir / "index_pass4.html").read_text(encoding="utf-8")

    # ── Pass 4b-c: generate images and audio ─────────────────────────────────
    if not prog.get("pass4_assets") and manifest:
        img_dir = out_dir / "assets" / "images"
        aud_dir = out_dir / "assets" / "audio"
        img_dir.mkdir(parents=True, exist_ok=True)
        aud_dir.mkdir(parents=True, exist_ok=True)

        genre_direction = config.get("genre_art_direction", {}).get(
            game_meta.get("genre", "misc") if game_meta else "misc", ""
        )
        print(f"    → Pass 4b: generating {len(manifest.get('images', []))} images (parallel, max 4 workers)")
        failed_images = generate_images_parallel(
            manifest.get("images", []),
            img_dir,
            max_workers=4,
            style_lock=style_lock,
            genre_direction=genre_direction,
        )
        if failed_images:
            print(f"    ⚠  {len(failed_images)} image(s) failed to generate: {failed_images}")

        if _SKIP_AUDIO:
            print("    ○ Pass 4c: audio generation skipped (skip_audio=True)")
        elif not ELEVENLABS_KEY:
            print("    ○ Pass 4c: audio generation skipped (no ELEVENLABS_API_KEY)")
        else:
            for aud in manifest.get("audio", []):
                out_path = aud_dir / aud["filename"]
                if out_path.exists():
                    print(f"    → Pass 4c: audio  {aud['filename']} (cached)")
                    continue
                print(f"    → Pass 4c: audio  {aud['filename']}")
                gen_audio(aud["description"], aud.get("duration_seconds", 1.0), out_path)
                time.sleep(0.5)

        prog["pass4_assets"] = True
        save_prog()

    # ── Pass 4.5: screenshot-based visual audit (loops until clean, max 3 iters) ─
    if not prog.get("pass_va"):
        print("    → Pass 4.5: visual audit (screenshot)")
        MAX_VA_ITERS = 3
        va_done = False
        for va_iter in range(MAX_VA_ITERS):
            iter_label = f"{va_iter+1}/{MAX_VA_ITERS}"
            screenshots = screenshot_screens(out_dir, html)
            if not screenshots:
                print("    ○ visual audit skipped (Playwright unavailable)")
                va_done = True
                break

            # Build labelled image list: optional cover first, then all captured screens
            images = []
            if cover_b64:
                images.append(("COVER ART (quality reference):", cover_b64))
            for key, label in [("home_b64", "HOME SCREEN:"), ("game_b64", "GAME SCREEN:"),
                                ("pause_b64", "PAUSE SCREEN:"), ("gameover_b64", "GAMEOVER SCREEN:")]:
                if screenshots.get(key):
                    images.append((label, screenshots[key]))

            if len(images) < (2 if cover_b64 else 1):
                print(f"    ○ visual audit {iter_label}: insufficient screenshots, skipping")
                va_done = True
                break

            skeleton = html_skeleton(html)
            r = call_llm_vision_multi(PASS_VISUAL_AUDIT, skeleton, images,
                                      f"visual-audit-{va_iter+1}", max_tokens=2048)
            issues = _extract_json_from_solve(r) if r else None

            if not issues or not isinstance(issues, dict):
                print(f"    ○ visual audit {iter_label}: could not parse response")
                va_done = True
                break

            all_issues = []
            for screen in ("home", "game", "pause", "gameover", "overall"):
                items = issues.get(screen, [])
                if items:
                    all_issues.append(f"{screen.upper()} SCREEN:" if screen != "overall" else "OVERALL:")
                    all_issues.extend(f"  - {i}" for i in items)

            if not all_issues:
                print(f"    ✓ visual audit {iter_label}: no issues found")
                va_done = True
                break

            total = sum(len(v) for v in issues.values() if isinstance(v, list))
            print(f"    ⚠  visual audit {iter_label}: {total} issue(s) — applying fixes")
            issue_text = "\n".join(all_issues)
            fix_prompt = (
                f"VISUAL AUDIT: The game was rendered in a real browser and the following "
                f"visual defects were found:\n\n{issue_text}\n\n"
                f"Fix ALL of the above by editing only <style> blocks and element "
                f"class/inline-style attributes. Do NOT change game logic, asset "
                f"filenames, or element IDs. Return ONLY the complete fixed HTML."
            )
            r2 = call_llm(fix_prompt, html, f"visual-audit-fix-{va_iter+1}", max_tokens=65536)
            if r2:
                html = fix_css_issues(r2)
                (out_dir / "index_pass_va.html").write_text(html, encoding="utf-8")
            else:
                print(f"    ⚠  visual audit fix LLM failed — will retry next run")
                break  # leave pass_va unset so it retries next run
        else:
            # for-loop exhausted without a clean pass
            print(f"    ⚠  visual audit: reached max {MAX_VA_ITERS} iterations")
            va_done = True

        if va_done:
            prog["pass_va"] = True
        save_prog()
    elif (out_dir / "index_pass_va.html").exists():
        html = (out_dir / "index_pass_va.html").read_text(encoding="utf-8")

    # ── Pass 5: playability QA ────────────────────────────────────────────────
    if not prog.get("pass5"):
        print("    → Pass 5: playability QA")
        # Prepend game_meta context so Pass 5 can apply targeted checks
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
            html = fix_css_issues(r)
            (out_dir / "index_pass5.html").write_text(html, encoding="utf-8")
            prog["pass5"] = True
        else:
            prog["pass5_failed"] = True
        save_prog()
    elif (out_dir / "index_pass5.html").exists():
        html = (out_dir / "index_pass5.html").read_text(encoding="utf-8")

    # ── Pass 6: Playwright smoke test + error-driven retry ────────────────────
    smoke_result = prog.get("smoke_result")
    if _SKIP_SMOKE:
        print("    ○ Pass 6: smoke test skipped (skip_smoke_test=True)")
        smoke_result = smoke_result or {"passed": None, "skip_reason": "skip_smoke_test=True"}
    elif not smoke_result:
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
                    issues = []
                    if not smoke_result.get("home_active"):
                        issues.append("screen-home does not have class 'active' on load")
                    if not smoke_result.get("game_active"):
                        issues.append("screen-game did not become active after clicking #btn-start")
                    if smoke_result.get("js_errors"):
                        issues.extend(smoke_result["js_errors"][:3])
        save_prog()

    # Ensure index.html is written (may have been skipped if smoke was skipped)
    if not (out_dir / "index.html").exists():
        (out_dir / "index.html").write_text(html, encoding="utf-8")

    # Populate last_report for run_beautify
    _last_report = {
        "passes_completed": [k for k, v in prog.items() if v is True],
        "smoke_test": smoke_result,
        "visual_audit": prog.get("va_result"),
    }

    print(f"    ✓ done → {out_dir}/index.html")
    return True

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Beautify selected games")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--pilot", type=int, metavar="N", help="Process top N games by score")
    grp.add_argument("--game",  type=str, metavar="NAME",  help="Process one specific game by name")
    grp.add_argument("--path",  type=str, metavar="PATH",  help="Process a game from an arbitrary folder path")
    grp.add_argument("--all",   action="store_true",       help="Process all 300 games")
    ap.add_argument("--resume", action="store_true", help="Skip games already through pass 3")
    ap.add_argument("--offset", type=int, default=0, metavar="N", help="Skip first N games (for parallel batches)")
    args = ap.parse_args()

    # --path bypasses the scored games list entirely
    if args.path:
        src = Path(args.path).resolve()
        if not src.is_dir():
            print(f"✗ not a directory: {src}")
            return
        if not (src / "index.html").exists():
            print(f"✗ no index.html found in: {src}")
            return
        BEAUTIFIED.mkdir(exist_ok=True)
        ok = beautify(src)
        print(f"\nDone — {'succeeded' if ok else 'failed'}")
        print(f"Output: {BEAUTIFIED / src.name}/")
        return

    scored_path = SELECTED / "scored_games.json"
    if not scored_path.exists():
        print("✗ selected_300/scored_games.json not found — run select_games.py first")
        return

    scored = json.loads(scored_path.read_text())

    if args.game:
        games = [SELECTED / args.game]
    elif args.pilot:
        games = [SELECTED / g["name"] for g in scored[args.offset:args.offset + args.pilot]]
    else:
        games = [SELECTED / g["name"] for g in scored[args.offset:]]

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
                pass4_done = (prog.get("pass4") or prog.get("pass4_skipped") or prog.get("pass4_failed")
                              or prog.get("pass4_assets") or prog.get("pass4_assets_skipped")
                              or prog.get("pass4_wire") or prog.get("pass4_wire_failed"))
                pass5_done = prog.get("pass5") or prog.get("pass5_failed")
                smoke_done = prog.get("smoke_result", {}).get("passed") is not False
                if pass4_done and pass5_done and smoke_done:
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
