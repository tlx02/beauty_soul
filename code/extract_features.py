#!/usr/bin/env python3
"""Stage 1: Extract quality signals from each game's index.html → features.json"""

import os
import re
import json

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..")
GAMES_DIR = os.path.join(ROOT_DIR, "source_games")
OUTPUT = os.path.join(ROOT_DIR, "selected_300", "features.json")

GENRE_RULES = [
    ("match3",              r"match3|bejeweled|candy_drop|jelly_splash|bubble_shooter|bubble_pop|toon_blast|toy_blast|cookie_jam|jewel_blast|gem_fusion|bubbletea_match|scratch_3match|sushi_striker|three_in_row|tile_master|triple_tile|color_splash|color_switch|link_3|florist_link|connect_dots"),
    ("tetris_block",        r"tetris|block_blast|electronics_tetris|lumines"),
    ("2048_merge",          r"2048|merge_drop|merge_pets|merge_puzzle|merge_idle|hex_merge|threes|suika|number_merge|bank_2048|bubbletea_merge|triple_town"),
    ("idle_clicker",        r"idle_|cookie_clicker|tap_titan|incremental|prestige|tap_tycoon|casino_chip|number_go_up|egg_inc|loop_hero|ant_idle|dungeon_idle|business_idle"),
    ("tower_defense",       r"^td_|tower_defense"),
    ("roguelike",           r"roguelike|rogue_legacy|dead_cells|daily_roguelike|daily_dungeon|dungeon_crawl|spire_ascent|reverse_roguelike|deck_builder_roguelike|inscryption|bad_north|room_crawler"),
    ("solitaire",           r"solitaire|freecell|clock_patience|golf_solitaire|pyramid_solitaire|hotel_solitaire"),
    ("poker_cards",         r"poker|blackjack|baccarat|texas_holdem|hearts_passing|spades_bidding|crazy_eights|chicago|mexico|war_card|speed_card|card_discard|card_pack|card_merge|predict_winner|hand_management|liars_dice|andar_bahar|ultimate_texas"),
    ("slots_casino",        r"slot_machine|bar_slots|roulette|keno|pachinko|spin_wheel|bingo|plinko|gacha_pull|gacha_collector|convenience_scratch|coffee_spin|birthday_double_spin|lucky_number|coin_flip|dice_higher|dice_predict"),
    ("word_puzzle",         r"wordle|word_search|hangman|anagram|crossword|cryptogram|typeshift|boggle|word_connect|word_cookies|word_crush|word_duel|word_ladder|word_spawn|spelltower|optical_word|pharmacy_crossword"),
    ("sudoku_logic",        r"sudoku|nonogram|picross|hitori|nurikabe|kakuro|futoshiki|fillomino|hidato|hashiwokakero|masyu|slitherlink|shikaku|skyscraper|thermometers|tent_puzzle|star_battle|numbrix|binary_puzzle|logic_circuit|logic_gates|logic_puzzle|ken_ken|circuit_builder|circuit_logic|circuit_sandbox|pipe_puzzle|flow_free|mirror_puzzle|lights_out|gear_puzzle|magnet_puzzle|paper_fold|origami|spa_one_line_draw"),
    ("quiz_trivia",         r"quiz|trivia|jeopardy|flag_quiz|geography_quiz|anatomy_quiz|animal_quiz|math_quiz|history_timeline|music_quiz|language_quiz|emoji_quiz|iq_test|odd_one_out|this_or_that|true_false|family_feud|speed_quiz|category_speed|around_world"),
    ("simulation_tycoon",   r"tycoon|restaurant_tycoon|shop_manager|hotel_manager|hospital_tycoon|game_dev_tycoon|airline_tycoon|school_tycoon|farm_tycoon|zoo_tycoon|two_point|city_sim|disaster_sim|economics_sim|ecosystem_sim|queue_simulator|airport_tycoon|car_dealer|car_workshop|car_wash|train_manager|train_network"),
    ("farm_garden",         r"farm_harvest|farm_seasons|garden_zen|garden_escape|garden_designer|garden_remix|cozy_grove|stardew|plant_growing|yoga_plant|mushroom_farm|creature_ranch|fish_tank|aquarium|coral_reef|bonsai|terrarium|butterfly_garden|flower_shop|flower_breeze|mini_magic_forest"),
    ("cooking_food",        r"cooking|bakery|pizza|sushi|food_truck|burger|juice_maker|ice_cream|coffee_shop|coffeeshop|latte_art|barista|restaurant_sushi|diner_dash|papa_s|hotpot|recipe|roguelike_cooking|bakery_cake|bakery_sim"),
    ("rhythm_music",        r"rhythm|music_|piano_tiles|guitar_hero|beat_catch|beat_sync|drum_pad|karaoke|taiko|audiosurf|rocksmith|dance_|loopstation|music_tiles|music_memory|music_puzzle|music_shop|dj_mix|dj_mixer|sound_studio|chord_trainer|mario_paint|ktv_tap|tap_stop"),
    ("racing",              r"racing|kart_racer|car_drive|bike_racing|top_down_racing|road_fighter|boat_racing"),
    ("sports",              r"soccer|basketball|tennis|cricket|volleyball|archery|boxing|swimming|javelin|hammer_throw|long_jump|pole_vault|ski|disc_golf|fencing|curling|arm_wrestling|bowling|skee_ball|table_tennis|penalty_kick|baseball|snail_race|horse_race|tug_of_war|martial_arts"),
    ("physics_puzzle",      r"physics_|bridge_builder|bridge_constructor|rope_|cut_the_rope|poly_bridge|gravity_|water_flow|water_ripple|balance_scale|marble_blast|marble_race|demolition|elastic_man|bottle_flip|jenga"),
    ("platformer",          r"platformer|celeste|hollow_knight|color_shift_platform|obby_runner|colossus_climb"),
    ("shooter",             r"space_invaders|galaga|galaxian|asteroids|bullet_hell|fps_arena|river_raid|defender|tempest|aerial_combat|missile_command|contra"),
    ("arcade_classic",      r"pac_man|frogger|breakout|centipede|donkey_kong|qbert|joust|metal_slug|dig_dug|phoenix|robotron|kids_breakout|brick_breaker"),
    ("flappy",              r"flappy"),
    ("chess_strategy",      r"chess|chinese_checkers|围棋|reversi|othello|checkers_jump|国际象棋|象棋"),
    ("board_game",          r"ludo|backgammon|domino|snakes_ladder|kids_ludo|macao|machi_koro|diplomacy|roll_write|territory_ink"),
    ("social_party",        r"charades|codenames|pictionary|telestration|just_one|decrypto|one_night|wavelength|concept|telephone_drawing|crowd_vote|debate_arena"),
    ("escape_room",         r"escape_room|cipher_decode|code_breaker|detective|room_escape|coop_countdown|uv_reveal|key_escape|cinema_escape"),
    ("rpg_combat",          r"hack_slash|action_arena|samurai_duel|arena_survival|arena_ladder|hades_dash|devil_may_cry|vampire_horde|necromancer|platform_brawl|parry_counter|boss_rush|sekiro|mark_of_ninja|stealth_action|dragon_ball|crafting_rpg"),
    ("adventure_explore",   r"old_man_journey|ori_forest|journey_pilgrimage|silhouette_journey|samorost|outer_wilds|a_short_hike|toem_camera|isometric_journey|open_world|botanicula|frog_detective|inverted_world|her_story|lifeline|color_grief|feelsy|paper_story|underwater_explore"),
    ("creative_art",        r"coloring_book|pixel_art|art_style|city_painter|world_painter|paint_by|emoji_maker|font_designer|flag_designer|animation_studio|character_creator|photo_studio|shadow_puppet"),
    ("pet_virtual",         r"tamagotchi|virtual_pet|pet_battle|pet_evolution|pet_evolve|pet_shop|neko_atsume|nintendogs|dog_sim|petstore|hamster|creature_tamer"),
    ("puzzle_logic",        r"baba_is_you|portal_2d|the_swapper|monument_valley|the_witness|machinarium|cursor_clone|perspective_maze|time_rewind|gravity_flip|rule_push|rule_word"),
    ("runner",              r"endless_runner|crowd_runner|crossy_road|procedural_runner|swipe_runner"),
    ("dice_board",          r"yahtzee|craps|bunco|bar_dice|shut_the_box|left_right|drop_dead"),
    ("mahjong",             r"mahjong|麻将"),
    ("memory_match",        r"memory_flip|memory_match|memory_matrix|dental_memory|concentration|spot_difference|spot_it|jigsaw|hidden_folks"),
    ("snake",               r"^snake$|slither_io"),
    ("pinball",             r"pinball|pachinko_ball"),
    ("connect_flow",        r"connect4|connection_puzzle|flow_free|link_3_chain"),
    ("management_strategy", r"city_builder|base_builder|kingdom|ant_colony|frostpunk|banner_saga|swipe_kingdom|reigns|this_war|objective_control|bad_north"),
    ("whack",               r"whack"),
    ("catch_slash",         r"fruit_ninja|slash_fruit|icecream_catch|hotpot_fruit"),
    ("salon_fashion",       r"hair_salon|hair_studio|makeup_studio|fashion_|dress_up|petstore_pet_salon"),
    ("tower_stack",         r"stack_tower|tower_of_hanoi|helix_jump|stack_ball|ball_sort"),
    ("bubble_pop",          r"bubble_pop|bubble_wrap|pediatric_bubble"),
    ("scratch_collect",     r"loot_box|gacha|sticker_album|sticker_book|trophy_room|mount_collect|coffee_collection|collect_set|achievement_hunt"),
    ("chinese_games",       r"升级|德州|西洋双陆|桥牌|斗地主|围棋|麻将|扫雷|消消乐"),
    ("medical_themed",      r"clinic_|dental_|pharmacy_|surgery_sim|pediatric_"),
    ("educational_kids",    r"kids_ludo|sign_language|language_learn|abc_trace|shape_sorter|counting_game|animal_sounds|go_fish_kids|pediatric_snakes"),
    ("space_sci",           r"space_colony|space_explorer|space_race|subnautica|oxygen_not|spacechem|space_idle|space_invaders"),
    ("incremental_rpg",     r"equipment_enhance|rune_system|loot_pickup|extraction_loot|moba_shop|rocket_upgrade|capitalist_invest|money_print"),
    ("lifestyle",           r"pottery|woodwork|bookstore|laundromat|post_office|hotel_check|grocery|salon_chair"),
    ("misc",                r".*"),
]

def detect_genre(name: str) -> str:
    name_lower = name.lower()
    for genre, pattern in GENRE_RULES:
        if re.search(pattern, name_lower):
            return genre
    return "misc"

def extract_features(game_dir: str, name: str) -> dict:
    html_path = os.path.join(game_dir, "index.html")
    if not os.path.exists(html_path):
        return None

    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        src = f.read()

    src_lower = src.lower()
    char_count = len(src)

    # Code size score (log-scaled, max 20)
    if char_count < 2000:
        size_score = 2
    elif char_count < 5000:
        size_score = 6
    elif char_count < 15000:
        size_score = 11
    elif char_count < 40000:
        size_score = 16
    elif char_count < 80000:
        size_score = 19
    else:
        size_score = 20

    has_game_loop = bool(re.search(r"requestAnimationFrame|setInterval\s*\(", src))
    has_start_screen = bool(
        re.search(r'class=["\'][^"\']*\b(home|start|intro|title|menu)\b|id=["\'][^"\']*\b(home|start|intro|title|menu)\b|\.scr\b', src_lower)
        and re.search(r'display\s*:\s*none|\.on\b|show\s*\(|classList', src_lower)
    )
    has_game_over = bool(re.search(r'game.?over|gameover|you.?lose|you.?win|game.?end|round.?over|level.?complete|you.?died|mission.?complete', src_lower))
    has_score = bool(re.search(r'\bscore\b|\bpoints\b|\bhigh.?score\b', src_lower))
    has_audio = bool(re.search(r'AudioContext|webkitAudioContext|new Audio\s*\(|playTone|__kixPlay|\.play\s*\(\)', src))
    has_animations = bool(re.search(r'@keyframes|animation\s*:|\.animate\s*\(|transition\s*:', src))
    has_particles = bool(re.search(r'particle|confetti|spawnBurst|firework|sparkle', src_lower))
    has_local_storage = bool(re.search(r'localStorage', src))
    has_levels = bool(re.search(r'\blevel\s*[+><=]|\bnext.?level\b|\badvance\b|\bdifficulty\b|\bwave\s*[+><=]', src_lower))
    has_canvas = bool(re.search(r'<canvas|getContext\s*\(', src))
    has_touch = bool(re.search(r'touchstart|touchend|touchmove|touch-action|pointerdown', src_lower))
    screen_count = len(re.findall(r'class=["\'][^"\']*\bscr\b', src_lower))

    genre = detect_genre(name)

    return {
        "name": name,
        "char_count": char_count,
        "size_score": size_score,
        "has_game_loop": has_game_loop,
        "has_start_screen": has_start_screen,
        "has_game_over": has_game_over,
        "has_score": has_score,
        "has_audio": has_audio,
        "has_animations": has_animations,
        "has_particles": has_particles,
        "has_local_storage": has_local_storage,
        "has_levels": has_levels,
        "has_canvas": has_canvas,
        "has_touch": has_touch,
        "screen_count": screen_count,
        "genre": genre,
    }

def main():
    results = []
    skipped = []

    entries = sorted(os.listdir(GAMES_DIR))
    for name in entries:
        game_dir = os.path.join(GAMES_DIR, name)
        if not os.path.isdir(game_dir) or name.startswith("__"):
            continue
        feat = extract_features(game_dir, name)
        if feat is None:
            skipped.append(name)
        else:
            results.append(feat)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Extracted features for {len(results)} games ({len(skipped)} skipped)")
    if skipped:
        print(f"Skipped: {skipped}")

if __name__ == "__main__":
    main()
