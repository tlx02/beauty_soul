#!/usr/bin/env python3
"""Stage 2: Score games, apply genre cap, copy top N to selected_300/, write report."""

import os
import json
import shutil
from collections import defaultdict
from datetime import date

CODE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.join(CODE_DIR, "..")
SOURCE_DIR = os.path.join(ROOT_DIR, "source_games")
OUTPUT_DIR = os.path.join(ROOT_DIR, "selected_300")
FEATURES_FILE = os.path.join(OUTPUT_DIR, "features.json")
CONFIG_FILE = os.path.join(CODE_DIR, "config.json")
REPORT_FILE = os.path.join(OUTPUT_DIR, "selection_report.md")

def score_game(feat: dict, weights: dict) -> float:
    total = feat["size_score"] * (weights["size_score"] / 20)
    for key, w in weights.items():
        if key == "size_score":
            continue
        val = feat.get(key, False)
        if isinstance(val, bool):
            total += w if val else 0
        else:
            total += val * w
    return round(total, 2)

def main():
    with open(FEATURES_FILE, encoding="utf-8") as f:
        games = json.load(f)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = json.load(f)

    weights = config["weights"]
    target = config["target_count"]
    genre_cap = config["genre_cap"]

    # Score all games
    for g in games:
        g["score"] = score_game(g, weights)

    games.sort(key=lambda g: g["score"], reverse=True)

    # Greedy selection: pick top-scored games, respecting genre cap.
    # If target can't be met with current cap, raise cap until it can.
    def select(games, cap):
        genre_counts = defaultdict(int)
        selected = []
        for g in games:
            genre = g["genre"]
            if genre_counts[genre] < cap:
                selected.append(g)
                genre_counts[genre] += 1
            if len(selected) == target:
                break
        return selected, genre_counts

    selected, genre_counts = select(games, genre_cap)
    actual_cap = genre_cap
    while len(selected) < target and actual_cap < 20:
        actual_cap += 1
        selected, genre_counts = select(games, actual_cap)

    print(f"Selected {len(selected)} games (genre cap used: {actual_cap})")

    # Copy game folders to output dir (preserve non-game files like features.json and report)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    RESERVED = {"features.json", "selection_report.md"}
    for entry in os.listdir(OUTPUT_DIR):
        if entry not in RESERVED:
            shutil.rmtree(os.path.join(OUTPUT_DIR, entry))

    for g in selected:
        src = os.path.join(SOURCE_DIR, g["name"])
        dst = os.path.join(OUTPUT_DIR, g["name"])
        shutil.copytree(src, dst)

    # --- Write markdown report ---
    genre_summary = defaultdict(list)
    for g in selected:
        genre_summary[g["genre"]].append(g)

    lines = []
    lines.append(f"# Game Selection Report")
    lines.append(f"\nGenerated: {date.today()}  ")
    lines.append(f"Total candidates: {len(games)}  ")
    lines.append(f"Selected: {len(selected)}  ")
    lines.append(f"Genre cap applied: {actual_cap} per genre  ")
    lines.append(f"Genres represented: {len(genre_summary)}  ")

    lines.append("\n---\n")
    lines.append("## Score Distribution\n")
    scores = [g["score"] for g in games]
    sel_scores = [g["score"] for g in selected]
    lines.append(f"| Metric | All 845 | Selected {len(selected)} |")
    lines.append("|--------|---------|----------|")
    lines.append(f"| Max score | {max(scores):.1f} | {max(sel_scores):.1f} |")
    lines.append(f"| Min score | {min(scores):.1f} | {min(sel_scores):.1f} |")
    lines.append(f"| Avg score | {sum(scores)/len(scores):.1f} | {sum(sel_scores)/len(sel_scores):.1f} |")

    lines.append("\n---\n")
    lines.append("## Genre Breakdown\n")
    lines.append("| Genre | Count | Top Game | Top Score |")
    lines.append("|-------|-------|----------|-----------|")
    for genre, genre_games in sorted(genre_summary.items(), key=lambda x: -len(x[1])):
        top = genre_games[0]
        lines.append(f"| {genre} | {len(genre_games)} | {top['name']} | {top['score']:.1f} |")

    lines.append("\n---\n")
    lines.append("## Full Ranked List\n")
    lines.append("| Rank | Game | Genre | Score | Size | Loop | Start | GameOver | Score | Audio | Anim | Particles | Storage | Levels | Canvas |")
    lines.append("|------|------|-------|-------|------|------|-------|----------|-------|-------|------|-----------|---------|--------|--------|")
    for i, g in enumerate(selected, 1):
        def b(v): return "✓" if v else "·"
        lines.append(
            f"| {i} | {g['name']} | {g['genre']} | {g['score']:.1f} "
            f"| {g['char_count']:,} "
            f"| {b(g['has_game_loop'])} | {b(g['has_start_screen'])} | {b(g['has_game_over'])} "
            f"| {b(g['has_score'])} | {b(g['has_audio'])} | {b(g['has_animations'])} "
            f"| {b(g['has_particles'])} | {b(g['has_local_storage'])} | {b(g['has_levels'])} "
            f"| {b(g['has_canvas'])} |"
        )

    lines.append("\n---\n")
    lines.append("## Excluded Games\n")
    lines.append("Games that did not make the cut (sorted by score descending):\n")
    lines.append("| Game | Genre | Score |")
    lines.append("|------|-------|-------|")
    selected_names = {g["name"] for g in selected}
    excluded = [g for g in games if g["name"] not in selected_names]
    for g in excluded:
        lines.append(f"| {g['name']} | {g['genre']} | {g['score']:.1f} |")

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Save scored list for beautify pipeline
    scored_list = [{"name": g["name"], "score": g["score"], "genre": g["genre"]} for g in selected]
    scored_path = os.path.join(OUTPUT_DIR, "scored_games.json")
    with open(scored_path, "w", encoding="utf-8") as f:
        json.dump(scored_list, f, indent=2, ensure_ascii=False)

    print(f"Report written to: {REPORT_FILE}")
    print(f"Scored list written to: {scored_path}")
    print(f"Games copied to:   {OUTPUT_DIR}/")
    print(f"\nGenre distribution:")
    for genre, genre_games in sorted(genre_summary.items(), key=lambda x: -len(x[1])):
        print(f"  {genre:<25} {len(genre_games)}")

if __name__ == "__main__":
    main()
