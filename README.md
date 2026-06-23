# Beauty Soul — Game Beautification Pipeline (v1.0)

Automated pipeline that transforms AI-generated single-file HTML games into polished, branded mobile game templates. Each game gets a consistent 3-screen structure, a unique visual theme, AI-generated image assets, and sound effects.

---

## What It Does

Takes a raw HTML game (single `index.html` file) and produces a fully themed version with:

- **3-screen template**: Title screen → Gameplay → Game Over + Pause overlay
- **Cover-driven style**: Visual style is extracted from the game's cover art and propagated consistently to all assets — CSS colours, UI panels, backgrounds, and sprites
- **Generated visual assets**: background, title logo, game preview, play button, UI panels (score, pause card, gameover card), optional game sprites — all AI-generated and on-theme
- **Sound effects**: up to 5 SFX generated via ElevenLabs
- **Responsive layout**: portrait (9:16) or landscape (16:9) depending on game type, letterboxed on any screen
- **Visual QA**: screenshot-based audit checks all 4 screens (home, game, pause, gameover) and auto-fixes layout issues
- **Smoke test**: Playwright headless browser test confirms the game launches and screens route correctly

---

## Pipeline Architecture

The pipeline runs **12 passes** sequentially per game:

| Pass | Name | What it does |
|---|---|---|
| **Cover Extract** | Style Extraction | Analyses the cover image and extracts a `cover_style` JSON (palette, rendering, mood, art style) — single source of truth for all downstream assets |
| **Pass 0** | Theme + Layout | Analyses the game and decides visual theme, colour palette, font style, and aspect ratio |
| **Pass 1** | Restructure | Wraps the game into a clean 3-screen HTML template with placeholder image tags already wired |
| **Pass 2** | Logic Fix | Conservative bug fixes — null references, broken event listeners, score reset |
| **Pass 3** | Visual Design | Full CSS redesign using the approved theme; image containers styled to avoid CSS conflicts |
| **Style Lock** | Style Freeze | Builds a `style_lock` from `cover_style` — art direction brief used to keep all generated images consistent |
| **Pass 4A** | Manifest | LLM generates the full asset manifest: filenames, descriptions, sizes, usage targets |
| **Pass 4D** | Asset Wiring | Wires all manifest assets into the HTML (background-image, src, z-index stacking) |
| **Pass 4B/C** | Asset Generation | Generates all images (parallel, 4 workers) + SFX audio; post-processes each image (crop transparent padding, resize to manifest dimensions) |
| **Pass 4.5** | Visual Audit | Screenshots all 4 screens in a real browser; LLM reviews for layout defects; auto-applies CSS fixes; loops up to 3× until clean |
| **Pass 5** | Playability QA | Fixes screen routing, DOM measurement timing, localStorage game resumption, audio unlock, z-index stacking |
| **Pass 6** | Smoke Test | Playwright headless browser confirms home screen renders, start button works, game screen appears |

---

## Setup

### Requirements

```bash
pip install openai pillow requests rembg playwright
python3 -m playwright install chromium
```

### Secrets

Create `code/secrets.json` (never commit this file):
```json
{
  "openrouter": "sk-or-...",
  "elevenlabs": "sk_..."
}
```

### Config

`code/config.json` controls the models used:
```json
{
  "llm_model": "anthropic/claude-sonnet-4-5",
  "image_model": "openai/gpt-5-image",
  "max_tokens": 32768
}
```

---

## Usage

```bash
# Process any game from an arbitrary folder path (recommended for external use)
python3 code/beautify_games.py --path /path/to/my_game

# --- The following flags require the internal game library (selected_300_new/) ---

# Process one game by name
python3 code/beautify_games.py --game 2048

# Process top N games by score
python3 code/beautify_games.py --pilot 10

# Process all selected games
python3 code/beautify_games.py --all

# Resume — skip passes already completed
python3 code/beautify_games.py --pilot 50 --resume

# Skip first N games (for parallel batches)
python3 code/beautify_games.py --pilot 30 --offset 20
```

---

## HTTP Service (KiX integration)

`beauty_soul.server:app` is a FastAPI service that wraps the pipeline for KiX's
`soul-worker`. It is **zip-in / zip-out**: POST a KiX standard input bundle, get
a beautified, fully-runnable game zip back. See the interface spec
`03-beauty_soul-HTTP接口需求.md` for the full contract.

```bash
# install service deps (already in pyproject dependencies)
pip install -e .
python -m playwright install chromium     # for visual audit + smoke test

# run the service
export OPENROUTER_API_KEY=sk-or-...        # required; missing key -> /v1/beautify fails fast
export ELEVENLABS_API_KEY=...              # optional; absent -> sound skipped, no failure
uvicorn beauty_soul.server:app --host 0.0.0.0 --port 9102
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/v1/health`   | Liveness + dependency probe. `200` ready, `503` if a key dep (core import / trinity / chromium) is unavailable. Missing `OPENROUTER_API_KEY` does **not** 503. |
| `POST` | `/v1/beautify` | `multipart/form-data` field `file` = input bundle zip → `application/zip` output. Failures return structured JSON (never a zip). |
| `GET`  | `/v1/version`  | Engine/version info. |

### Example

```bash
# build the bundled sample game into an input bundle
python samples/build_sample_bundle.py            # -> samples/input_bundle.zip

curl -sS -X POST http://localhost:9102/v1/beautify \
  -H 'X-Kix-Request-Id: test-order-001' \
  -H 'X-Kix-Engine: beauty_soul' \
  -F 'file=@samples/input_bundle.zip' \
  -o output.zip
```

`output.zip` always contains `index.html` at the root, every asset the final HTML
references (including **source assets carried over from the input** — see §7.3),
and `soul_report.json`. Before returning success the service runs an asset-integrity
check (§7.4): if the final HTML/JS/CSS references a local asset missing from the
output, it returns `asset_integrity_failed` JSON instead of a broken zip.

### Docker

```dockerfile
# (excerpt — see Dockerfile)
CMD ["uvicorn", "beauty_soul.server:app", "--host", "0.0.0.0", "--port", "9102"]
```

Run as a sidecar container in the KiX soul image; the worker calls it over the
compose network at `http://beauty-soul-service:9102`.

---

## Output Structure

```
beautified/
  <game_name>/
    index.html              ← Final beautified game (latest passing version)
    index_original.html     ← Original untouched
    index_pass1.html        ← After restructure
    index_pass2.html        ← After logic fixes
    index_pass3.html        ← After CSS redesign
    index_pass4.html        ← After asset wiring
    index_pass5.html        ← After playability QA
    index_pass_va.html      ← After visual audit fixes (if any)
    .progress.json          ← Pass completion state (for resume)
    assets/
      images/
        background_home.png     ← Full-screen home screen background
        background_game.png     ← Game screen background (darker variant)
        background_gameover.png ← Game over screen background
        title.png               ← Styled game title logo (transparent)
        game_preview.png        ← Gameplay preview illustration (transparent)
        btn_play.png            ← PLAY button (transparent)
        btn_home.png            ← Home icon for in-game HUD (transparent)
        btn_pause.png           ← Pause icon for in-game HUD (transparent)
        btn_playagain.png       ← PLAY AGAIN button (transparent)
        pause_card.png          ← Pause overlay panel background (transparent)
        [score_panel.png]       ← Optional HUD score frame (transparent)
        [result_card.png]       ← Optional game-over card background (transparent)
        [sprites...]            ← Optional game-specific sprites
        manifest.json           ← Full asset manifest
      audio/
        sfx_*.mp3               ← Generated sound effects
```

---

## Game Selection

Games are selected and scored from the source pool using `code/select_games.py` and `code/extract_features.py`. The scoring weights are configured in `config.json` under `"weights"`. Selected games live in `selected_300_new/scored_games.json`.

---

## Key Design Decisions

**Cover-driven style consistency**: A dedicated cover extraction pass runs once per game and produces a `cover_style` JSON (hex palette, rendering style, mood, art style). This is the single source of truth — StyleLock is built directly from it, and `_build_desc` prefixes every image generation prompt with the extracted art direction. All assets come out on-theme without per-pass re-interpretation drift.

**Placeholder approach (Pass 1)**: All asset `<img>` tags and background containers are wired in Pass 1 before CSS is generated. Pass 3 then styles them as image containers, preventing font/glow/shadow properties being applied to images.

**Z-index stacking hierarchy**: Enforced in Pass 4D, Pass 5, and as a CSS guardrail in `fix_css_issues`:
- Decorative elements: `z-index: 0`
- Game board / canvas: `z-index: 1`
- HUD / topbar: `z-index: 10`
- In-game popups: `z-index: 100`
- Pause overlay: `z-index: 9999`

**Front-facing UI panels**: All generated card/panel images (pause card, gameover card, score panel) are rendered flat and straight-on — no perspective tilt or 3D rotation. Enforced in the PASS4A manifest prompt.

**Transparent padding removal**: All transparent-background images are post-processed after generation: bounding-box crop to remove empty borders, then resize to manifest dimensions. A final sequential verification pass runs after parallel generation to catch any silent failures.

**Visual audit loop**: Pass 4.5 takes real browser screenshots of all 4 screens, sends them to the LLM for layout review, and applies CSS fixes. It loops up to 3 times until clean or no more fixable issues remain.

**Aspect ratio**: Only 9:16 portrait or 16:9 landscape. The LLM chooses based on game mechanics. Container is letterboxed on any screen size.

**Resume support**: Each game tracks pass completion in `.progress.json`. Any interrupted run resumes from where it left off with `--resume`.
