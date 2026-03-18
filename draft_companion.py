#!/usr/bin/env python3
"""
ESPN Fantasy Baseball Draft Companion
======================================
Real-time draft assistant. Watches picks as they happen, suggests what
to take next, and shows your team's needs after every pick.

Usage:
  python3 draft_companion.py              # auto-detect picks via ESPN API
  python3 draft_companion.py --manual    # you type every pick manually
  python3 draft_companion.py --mock      # simulates a full draft for testing

Setup (one-time, already done):
  - ESPN_S2, ESPN_SWID, ESPN_TEAM_NAME set in .env
  - ANTHROPIC_API_KEY set in .env (for AI advice)
  - pip install -r requirements.txt
  - python3 -m playwright install chromium (only if ESPN API auth fails)

Draft day: just run  python3 draft_companion.py
"""
import os
import sys
import time
import random
import argparse
import threading
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.draft_data import (
    build_player_pool, get_available, mark_drafted, FALLBACK_PLAYERS
)
from espn_agent.draft_suggester import (
    get_top_picks, format_my_turn_display, get_claude_pick_advice
)
from espn_agent.draft_tracker import (
    DraftTracker, build_team_id_map, find_my_team_id,
    get_snake_pick_numbers
)

MY_TEAM_NAME = os.getenv('ESPN_TEAM_NAME', 'Sandy Koufax')
NUM_TEAMS    = 10    # Will be confirmed from ESPN at startup
NUM_ROUNDS   = 23    # Standard 23-round MLB fantasy draft


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clear_line():
    print('\r' + ' ' * 80 + '\r', end='', flush=True)


def print_pick(pick: dict):
    """Print a pick announcement."""
    flag = " ← YOU" if pick.get('is_my_pick') else ""
    print(f"  [R{pick['round']:02d} P{pick['pick_number']:03d}]  "
          f"{pick['player_name']:<25}  →  {pick['team_name']}{flag}")


def prompt_draft_position(num_teams: int) -> int:
    """Ask user for their draft position."""
    while True:
        raw = input(f"\nYour draft position (1-{num_teams})? ").strip()
        try:
            pos = int(raw)
            if 1 <= pos <= num_teams:
                return pos
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {num_teams}.")


def wait_for_my_pick(tracker: DraftTracker, player_pool: dict,
                     manual_mode: bool) -> Optional[str]:
    """
    Called when it's the user's turn.
    Waits for the user to press Enter after picking on ESPN.
    Optionally accepts a typed player name to override auto-detection.

    Returns the player name to mark as my pick (or None to let tracker detect it).
    """
    raw = input("\n[Press Enter after you pick, or type the name]: ").strip()
    if raw:
        return raw
    return None


# ─── Connect to ESPN ──────────────────────────────────────────────────────────

def connect_espn():
    """Connect to ESPN API. Returns (league, num_teams) or raises."""
    print("\nConnecting to ESPN...")
    from espn_agent.espn_client import ESPNClient
    client = ESPNClient()
    num_teams = len(client.league.teams)
    league_name = getattr(client.league.settings, 'name', f"League {os.getenv('ESPN_LEAGUE_ID')}")
    print(f"  League: {league_name}  ({num_teams} teams)")
    print(f"  My team: {client.my_team.team_name}")
    return client, num_teams


# ─── Mock draft ───────────────────────────────────────────────────────────────

def run_mock_draft(player_pool: dict, draft_position: int,
                   num_teams: int = 10, num_rounds: int = 6):
    """
    Simulate a short draft to test all logic end-to-end.
    Uses --mock flag. Simulates num_rounds rounds, stopping each time it's your pick.
    """
    print("\n" + "═" * 52)
    print("  MOCK DRAFT MODE (first 6 rounds simulated)")
    print("═" * 52)

    # Build a simple team map for mock
    mock_teams = [f"Team {i}" for i in range(1, num_teams + 1)]
    mock_teams[draft_position - 1] = MY_TEAM_NAME

    my_pick_numbers = set(get_snake_pick_numbers(draft_position, num_teams, num_rounds))
    all_players = sorted(player_pool.values(), key=lambda p: p['adp'])

    # Manual tracker for mock (no API)
    class MockTracker:
        def __init__(self):
            self.picks_done = 0
        def current_pick_number(self): return self.picks_done + 1
        def current_round(self): return (self.picks_done // num_teams) + 1
        def is_my_turn(self): return self.picks_done + 1 in my_pick_numbers
        def is_draft_complete(self): return self.picks_done >= num_teams * num_rounds
        def get_my_picks(self): return mock_my_roster

    tracker = MockTracker()
    mock_my_roster = []
    available = get_available(player_pool)

    total_picks = num_teams * num_rounds

    while not tracker.is_draft_complete():
        pick_num = tracker.current_pick_number()
        round_num = tracker.current_round()

        if tracker.is_my_turn():
            # Show recommendations
            top = get_top_picks(available, mock_my_roster, round_num, pick_num)
            display = format_my_turn_display(top, mock_my_roster, round_num,
                                             pick_num, total_picks, MY_TEAM_NAME)
            print(display)

            if top:
                advice = get_claude_pick_advice(top, mock_my_roster, round_num, MY_TEAM_NAME)
                if advice and not advice.startswith('['):
                    print(f"\n  AI recommendation: {advice}\n")

            raw = input("[Mock] Type player name to draft (or Enter for top pick): ").strip()
            if not raw and top:
                raw = top[0]['name']

            # Mark as my pick
            key = mark_drafted(player_pool, raw, MY_TEAM_NAME, pick_num, round_num)
            available = get_available(player_pool)
            if key and key in player_pool:
                mock_my_roster.append(player_pool[key])
                print(f"\n  You drafted: {player_pool[key]['name']}\n")
            tracker.picks_done += 1
        else:
            # Simulate another team picking the best available player
            best_available = sorted(available.values(), key=lambda p: p['adp'])
            if not best_available:
                break
            # Add slight randomness — occasionally pick 3-4 picks off ADP
            idx = random.randint(0, min(3, len(best_available) - 1))
            picked = best_available[idx]
            team_idx = (pick_num - 1) % num_teams
            team_name = mock_teams[team_idx]

            key = mark_drafted(player_pool, picked['name'], team_name, pick_num, round_num)
            available = get_available(player_pool)
            print(f"  [R{round_num:02d} P{pick_num:03d}]  {picked['name']:<25}  →  {team_name}")
            tracker.picks_done += 1
            time.sleep(0.05)  # Brief pause so output is readable

    print("\n═" * 52)
    print("  MOCK DRAFT COMPLETE")
    print("  My team:")
    for p in mock_my_roster:
        pos = '/'.join(p['positions'][:2])
        print(f"    {p['name']:<25} {pos}")
    print("═" * 52)


# ─── Main draft loop ──────────────────────────────────────────────────────────

def run_draft(tracker: DraftTracker, player_pool: dict,
              manual_mode: bool, num_teams: int):
    """
    Main draft loop. Runs until the draft is complete or user quits.
    """
    total_picks = num_teams * NUM_ROUNDS
    my_roster = []

    print("\n" + "═" * 52)
    mode_str = "MANUAL" if manual_mode else "AUTO-DETECT"
    print(f"  DRAFT COMPANION READY  [{mode_str}]")
    print(f"  Watching {total_picks} total picks ({num_teams} teams × {NUM_ROUNDS} rounds)")
    if manual_mode:
        print("  Type  'p: Player Name'  to log any pick")
        print("  Type  'mine: Player Name'  to log YOUR pick")
    print("  Press Ctrl+C to exit")
    print("═" * 52 + "\n")

    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    spin_i = 0

    if not manual_mode:
        tracker.start_polling()

    while not tracker.is_draft_complete():
        # ── Manual mode: read stdin ───────────────────────────────────────────
        if manual_mode:
            raw = input("").strip()
            if not raw:
                continue

            if raw.lower().startswith(('p:', 'pick:')):
                name = raw.split(':', 1)[1].strip()
                pick_num = tracker.current_pick_number()
                round_num = tracker.current_round()
                round_pick = ((pick_num - 1) % num_teams) + 1
                is_mine = pick_num in tracker.my_pick_numbers
                pick = tracker.log_manual_pick(name, MY_TEAM_NAME if is_mine else 'Opponent')
                print_pick(pick)

                key = mark_drafted(player_pool, name,
                                   pick['team_name'], pick_num, round_num)
                if is_mine and key and key in player_pool:
                    my_roster.append(player_pool[key])

            elif raw.lower().startswith('mine:'):
                name = raw.split(':', 1)[1].strip()
                pick_num = tracker.current_pick_number()
                round_num = tracker.current_round()
                pick = tracker.log_manual_pick(name, MY_TEAM_NAME)
                pick['is_my_pick'] = True
                print_pick(pick)
                key = mark_drafted(player_pool, name, MY_TEAM_NAME, pick_num, round_num)
                if key and key in player_pool:
                    my_roster.append(player_pool[key])

            elif raw.lower() in ('q', 'quit', 'exit'):
                break
            elif raw.lower() in ('?', 'help'):
                print("  Commands:")
                print("    p: Player Name    — log any pick (auto-detects if it's yours)")
                print("    mine: Player Name  — explicitly log YOUR pick")
                print("    top               — show top picks now")
                print("    q                 — quit")
            elif raw.lower() == 'top':
                pick_num = tracker.current_pick_number()
                round_num = tracker.current_round()
                top = get_top_picks(get_available(player_pool), my_roster, round_num, pick_num)
                display = format_my_turn_display(top, my_roster, round_num,
                                                 pick_num, total_picks, MY_TEAM_NAME)
                print(display)
            else:
                # Bare name — treat as opponent pick (same as "p: Name")
                name = raw
                pick_num = tracker.current_pick_number()
                round_num = tracker.current_round()
                is_mine = pick_num in tracker.my_pick_numbers
                pick = tracker.log_manual_pick(name, MY_TEAM_NAME if is_mine else 'Opponent')
                print_pick(pick)
                key = mark_drafted(player_pool, name,
                                   pick['team_name'], pick_num, round_num)
                if is_mine and key and key in player_pool:
                    my_roster.append(player_pool[key])
            continue

        # ── Auto mode: check for new picks from background thread ─────────────
        new_picks = tracker.pop_new_picks()
        for pick in new_picks:
            print_pick(pick)
            key = mark_drafted(player_pool, pick['player_name'],
                               pick['team_name'], pick['pick_number'], pick['round'])
            if pick.get('is_my_pick') and key and key in player_pool:
                my_roster.append(player_pool[key])

        # ── Show "YOUR TURN" when it's my pick ───────────────────────────────
        if tracker.is_my_turn():
            pick_num  = tracker.current_pick_number()
            round_num = tracker.current_round()
            available = get_available(player_pool)
            top = get_top_picks(available, my_roster, round_num, pick_num)

            display = format_my_turn_display(top, my_roster, round_num,
                                             pick_num, total_picks, MY_TEAM_NAME)
            print(display)

            # Claude AI recommendation (pre-pick — advises before you click on ESPN)
            advice = get_claude_pick_advice(top, my_roster, round_num, MY_TEAM_NAME)
            if advice and not advice.startswith('['):
                print(f"\n  AI recommendation: {advice}")

            # Wait for user to confirm pick
            print()
            raw = input("[Press Enter after picking on ESPN, or type name if missed]: ").strip()

            if raw:
                # User typed the name they picked
                key = mark_drafted(player_pool, raw, MY_TEAM_NAME, pick_num, round_num)
                if key and key in player_pool:
                    my_roster.append(player_pool[key])
                    print(f"  Logged: {player_pool[key]['name']}")
                    # Log it in the tracker too so pick count advances
                    tracker.log_manual_pick(raw, MY_TEAM_NAME)
            else:
                # Will be detected automatically from next API poll
                print("  Watching ESPN for your pick...")
                # Wait for API to catch up
                deadline = time.time() + 60  # 60s timeout
                while tracker.is_my_turn() and time.time() < deadline:
                    new_picks = tracker.pop_new_picks()
                    for pick in new_picks:
                        print_pick(pick)
                        key = mark_drafted(player_pool, pick['player_name'],
                                           pick['team_name'], pick['pick_number'], pick['round'])
                        if pick.get('is_my_pick') and key and key in player_pool:
                            my_roster.append(player_pool[key])
                    time.sleep(2)
            continue

        # ── Spinner while waiting for next pick ───────────────────────────────
        if not tracker._api_available and not manual_mode:
            print("\n  WARNING: ESPN API unreachable. Switch to manual mode:")
            print("  Restart with:  python3 draft_companion.py --manual")
            time.sleep(5)
        else:
            spin_i = (spin_i + 1) % len(spinner)
            pick_num = tracker.current_pick_number()
            round_num = tracker.current_round()
            my_next = min(p for p in tracker.my_pick_numbers if p >= pick_num)
            picks_away = my_next - pick_num
            sys.stdout.write(
                f"\r  {spinner[spin_i]}  Round {round_num}  —  "
                f"Pick {pick_num} of {total_picks}  "
                f"(your next pick in {picks_away})     "
            )
            sys.stdout.flush()
            time.sleep(1)

    tracker.stop_polling()
    print("\n\n" + "═" * 52)
    print("  DRAFT COMPLETE — Your team:")
    print("═" * 52)
    for p in my_roster:
        pos = '/'.join(p['positions'][:2])
        hr_str  = f"  {p['proj_hr']} HR" if p.get('proj_hr') else ''
        era_str = f"  {p['proj_era']:.2f} ERA" if p.get('proj_era') else ''
        print(f"  {p['name']:<25} {pos:<10}{hr_str}{era_str}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='ESPN Fantasy Baseball Draft Companion')
    parser.add_argument('--manual', action='store_true',
                        help='Manual pick entry mode (type picks as they happen)')
    parser.add_argument('--mock', action='store_true',
                        help='Simulate a draft for testing (no ESPN connection needed)')
    parser.add_argument('--rounds', type=int, default=NUM_ROUNDS,
                        help=f'Number of draft rounds (default: {NUM_ROUNDS})')
    parser.add_argument('--teams', type=int, default=None,
                        help='Number of teams (auto-detected from ESPN if not set)')
    parser.add_argument('--pos', type=int, default=None,
                        help='Your draft position 1-10 (skips interactive prompt)')
    args = parser.parse_args()

    print("\n" + "═" * 52)
    print("  ESPN Fantasy Baseball Draft Companion")
    print("═" * 52)

    # ── Load player pool (auto-fetches from Fangraphs + ESPN) ─────────────────
    player_pool = build_player_pool()

    if args.mock:
        # Mock mode: no ESPN connection needed
        draft_pos = args.pos if args.pos else prompt_draft_position(args.teams or 10)
        run_mock_draft(player_pool, draft_pos,
                       num_teams=args.teams or 10,
                       num_rounds=min(args.rounds, 6))
        return

    # ── Connect to ESPN ────────────────────────────────────────────────────────
    try:
        client, num_teams = connect_espn()
        if args.teams:
            num_teams = args.teams
    except Exception as e:
        print(f"\n  WARNING: ESPN connection failed ({e})")
        print("  Falling back to manual mode with 10 teams.")
        print("  Fix ESPN credentials in .env if this is unexpected.\n")
        client = None
        num_teams = args.teams or 10

    # ── Draft position setup ───────────────────────────────────────────────────
    draft_pos = args.pos if args.pos else prompt_draft_position(num_teams)
    my_picks = get_snake_pick_numbers(draft_pos, num_teams, args.rounds)
    total_picks = num_teams * args.rounds

    print(f"\n  Your picks: {', '.join(str(p) for p in my_picks[:5])}... "
          f"(every other round)")

    # ── Build maps for tracker ─────────────────────────────────────────────────
    team_id_map = {}
    my_team_id  = None
    if client:
        team_id_map = build_team_id_map(client.league)
        my_team_id  = find_my_team_id(client.league, MY_TEAM_NAME)

    # ── ESPN player ID map (for resolving pick player IDs → names) ─────────────
    # We already fetched player info in draft_data.py; try to reuse it.
    # For simplicity, build a minimal map from what we have in the pool.
    espn_id_map = {}  # Will be populated from ESPN API call in DraftTracker if needed

    # ── Check ESPN API availability ────────────────────────────────────────────
    manual_mode = args.manual
    if not manual_mode:
        tracker = DraftTracker(
            draft_position=draft_pos,
            num_teams=num_teams,
            espn_id_map=espn_id_map,
            team_id_map=team_id_map,
            num_rounds=args.rounds,
            my_team_id=my_team_id,
        )
        print("\n  Testing ESPN draft API connection...")
        api_ok = tracker.test_api_connection()
        if api_ok:
            print("  ESPN API: connected — picks will be auto-detected!")
        else:
            print("  ESPN API: unavailable — switching to manual mode.")
            print("  Type 'p: Player Name' after each pick is announced on ESPN.")
            manual_mode = True
    else:
        tracker = DraftTracker(
            draft_position=draft_pos,
            num_teams=num_teams,
            espn_id_map=espn_id_map,
            team_id_map=team_id_map,
            num_rounds=args.rounds,
            my_team_id=my_team_id,
        )

    # ── Pre-show initial recommendations ──────────────────────────────────────
    print("\n  Initial top picks (before any picks):")
    available = get_available(player_pool)
    top = get_top_picks(available, [], round_num=1, current_pick=1)
    for i, p in enumerate(top[:5], start=1):
        pos = '/'.join(p['positions'][:2])
        adp = f"ADP:{p['adp']:.0f}" if p['adp'] < 900 else ''
        print(f"    {i}. {p['name']:<25} {pos:<8} {adp}")

    # ── Run the draft ──────────────────────────────────────────────────────────
    print()
    try:
        run_draft(tracker, player_pool, manual_mode, num_teams)
    except KeyboardInterrupt:
        tracker.stop_polling()
        print("\n\n  Draft companion stopped. Good luck!")


if __name__ == '__main__':
    main()
