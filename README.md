# Beauty Soul — Game Beautification Pipeline

Automated pipeline that transforms AI-generated single-file HTML games into polished, branded mobile game templates. Each game gets a consistent 3-screen structure, a unique visual theme, generated image assets, and sound effects.

---

## What It Does

Takes a raw HTML game (single `index.html` file) and produces a fully themed version with:

- **3-screen template**: Title screen → Gameplay → Game Over
- **Generated visual assets**: background, title logo, game preview, play button, and optional game sprites — all AI-generated and themed
- **Sound effects**: up to 5 SFX generated via ElevenLabs
- **Responsive layout**: portrait (9:16) or landscape (16:9) depending on game type, letterboxed on any screen
- **Playability fixes**: correct screen routing, no localStorage game resumption, DOM measurement timing fixes

---

## Pipeline Architecture

The pipeline runs **6 passes** sequentially per game:

| Pass | Name | What it does |
|---|---|---|
| **Pass 0** | Theme + Layout | Analyses the game and decides visual theme, colour palette, font style, and aspect ratio |
| **Pass 1** | Restructure | Wraps the game into a clean 3-screen HTML template with image asset placeholders already wired |
| **Pass 2** | Logic Fix | Conservative bug fixes — null references, broken event listeners, score reset |
| **Pass 3** | Visual Design | Full CSS redesign using the approved theme; image containers styled to avoid CSS conflicts |
| **Pass 4** | Asset Generation | Generates background, game preview, title logo, play button, optional sprites, and SFX audio |
| **Pass 5** | Playability QA | Fixes screen routing, DOM measurement timing, localStorage game resumption, audio unlock |

---

## Setup

### Requirements

```bash
pip install openai pillow requests rembg
```

### Secrets

Create `code/secrets.json`:
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
# Process one game
python3 code/beautify_games.py --game 2048

# Process top N games by score
python3 code/beautify_games.py --pilot 10

# Process all 300 selected games
python3 code/beautify_games.py --all

# Resume — skip games already through pass 4+5
python3 code/beautify_games.py --pilot 50 --resume

# Skip first N games (for parallel batches)
python3 code/beautify_games.py --pilot 30 --offset 20
```

---

## Output Structure

```
beautified/
  <game_name>/
    index.html              ← Final beautified game
    index_original.html     ← Original untouched
    index_pass1.html        ← After restructure
    index_pass2.html        ← After logic fixes
    index_pass3.html        ← After CSS redesign
    .progress.json          ← Pass completion state (for resume)
    assets/
      images/
        background.png      ← Full-screen atmospheric background
        title.png           ← Styled game title logo (transparent)
        game_preview.png    ← Gameplay preview illustration (transparent)
        btn_play.png        ← PLAY button image (transparent)
        [sprites...]        ← Optional game-specific sprites
        manifest.json       ← Asset manifest from Pass 4A
      audio/
        sfx_*.mp3           ← Generated sound effects
```

---

## Game Selection

Games are selected and scored from the source pool using `code/select_games.py` and `code/extract_features.py`. The scoring weights are configured in `config.json` under `"weights"`. Selected games live in `selected_300_new/scored_games.json`.

---

## Key Design Decisions

**Placeholder approach (Pass 1)**: Title screen assets (`title.png`, `btn_play.png`, `game_preview.png`) are wired as `<img>` tags in Pass 1 before CSS is generated. This ensures Pass 3 styles them as image containers — not as text elements — preventing unwanted glows, drop-shadows, and font properties being applied to images.

**Aspect ratio**: Only 9:16 portrait or 16:9 landscape. The LLM chooses based on game mechanics. Container is letterboxed on any screen size.

**CSS isolation**: Pass 3 generates only decorative CSS (colours, fonts, theming). Structural CSS (screen layout, overflow, container sizing) is locked in the Pass 1 template and cannot be overridden.

**Image model**: Uses `openai/gpt-5-image` via OpenRouter with native `background: transparent` for sprite images, followed by bounding-box crop to remove residual transparent borders.

**Resume support**: Each game tracks pass completion in `.progress.json`. Any interrupted run can be resumed from where it left off with `--resume`.
