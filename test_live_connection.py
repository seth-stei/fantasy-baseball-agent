#!/usr/bin/env python3
"""
Live ESPN Connection Test + Lineup Setter
==========================================
Connects to your real ESPN league, reads your actual roster, runs the optimizer
on live MLB data, and shows exactly what lineup changes it would make.

This is the final verification step before enabling the daily automation.

Usage:
  python3 test_live_connection.py           # dry run — shows diff, no changes made
  python3 test_live_connection.py --apply   # actually sets the lineup on ESPN
"""
import os
import sys
import argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from espn_agent.espn_client import ESPNClient
from espn_agent.stats_client import (
    get_todays_schedule,
    get_mlb_team_map,
    get_recent_hitting_stats,
    get_recent_pitching_stats,
)
from espn_agent.lineup_optimizer import optimize_lineup, format_lineup_for_ai
from espn_agent.lineup_setter import (
    get_team_id,
    fetch_league_data,
    build_moves,
    post_lineup,
    SLOT_ID_TO_NAME,
    BENCH_SLOT_ID,
    BENCH_SLOT_IDS,
    IL_SLOT_IDS,
)


def _display_current_lineup(roster_entries: list, scoring_period: int):
    """Print the current lineup exactly as ESPN has it."""
    active   = sorted(
        [e for e in roster_entries if e['current_slot_id'] not in BENCH_SLOT_IDS and
         e['current_slot_id'] not in IL_SLOT_IDS],
        key=lambda e: e['current_slot_id'],
    )
    bench    = [e for e in roster_entries if e['current_slot_id'] in BENCH_SLOT_IDS]
    on_il    = [e for e in roster_entries if e['current_slot_id'] in IL_SLOT_IDS]

    print(f"\n  CURRENT ESPN LINEUP  (scoring period {scoring_period}):")
    if active:
        for e in active:
            slot = SLOT_ID_TO_NAME.get(e['current_slot_id'], str(e['current_slot_id']))
            print(f"    {slot:<6}  {e['name']}")
    else:
        print("    (no active players found — season may not have started yet)")

    if bench:
        print(f"\n  BENCH ({len(bench)}):")
        for e in bench:
            print(f"    BE     {e['name']}")

    if on_il:
        print(f"\n  INJURED LIST ({len(on_il)}):")
        for e in on_il:
            print(f"    IL     {e['name']}")


def _display_recommended_lineup(lineup_result: dict):
    """Print the optimizer's recommended lineup."""
    print("\n  RECOMMENDED LINEUP:")
    for slot, value in lineup_result['starters'].items():
        if value:
            player, score = value
            status = getattr(player, 'injuryStatus', 'ACTIVE') or 'ACTIVE'
            status_str = f"  [{status}]" if status != 'ACTIVE' else ''
            print(f"    {slot:<6}  {player.name}{status_str}")
        else:
            print(f"    {slot:<6}  ⚠️  EMPTY — no eligible player")

    if lineup_result['no_game']:
        print(f"\n  NO GAME TODAY ({len(lineup_result['no_game'])}):")
        for p in lineup_result['no_game']:
            print(f"    BE     {p.name}  ({p.proTeam})")

    if lineup_result['injured_out']:
        print(f"\n  INJURED/OUT ({len(lineup_result['injured_out'])}):")
        for p in lineup_result['injured_out']:
            status = getattr(p, 'injuryStatus', 'OUT')
            print(f"    IL     {p.name}  [{status}]")

    if lineup_result['notes']:
        print("\n  OPTIMIZER NOTES:")
        for note in lineup_result['notes']:
            print(f"    {note}")


def _display_moves(moves: list, roster_entries: list):
    """Print the diff — what would actually change on ESPN."""
    name_map = {e['player_id']: e['name'] for e in roster_entries}

    if not moves:
        print("\n  ✓  Lineup is already optimal — no changes needed.")
        return

    print(f"\n  MOVES ({len(moves)}):")
    for m in moves:
        name   = name_map.get(m['playerId'], f"player#{m['playerId']}")
        from_s = SLOT_ID_TO_NAME.get(m['fromLineupSlotId'], str(m['fromLineupSlotId']))
        to_s   = SLOT_ID_TO_NAME.get(m['toLineupSlotId'],   str(m['toLineupSlotId']))
        arrow  = '←  activate' if to_s not in ('BE', 'IL') else '→  bench'
        print(f"    {name:<28}  {from_s:<6} → {to_s:<6}  {arrow}")


def main():
    parser = argparse.ArgumentParser(
        description='Test live ESPN connection and optionally set today\'s lineup'
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='Actually set the lineup on ESPN (default: dry run, no changes)'
    )
    args = parser.parse_args()

    print(f"\n{'═'*60}")
    print(f"  LIVE ESPN CONNECTION TEST{'  —  APPLYING LINEUP' if args.apply else ''}")
    print(f"{'═'*60}")

    # 1. Connect to ESPN ────────────────────────────────────────────────────────
    print("\n[1/4] Connecting to ESPN...")
    try:
        espn = ESPNClient()
        print(f"  ✓ Team: {espn.my_team.team_name}  "
              f"({espn.my_team.wins}W - {espn.my_team.losses}L)")
    except Exception as e:
        print(f"  ✗ ESPN connection failed: {e}")
        print("\n  Check that ESPN_S2, ESPN_SWID, and ESPN_LEAGUE_ID are set in .env")
        sys.exit(1)

    # 2. Fetch schedule + stats ─────────────────────────────────────────────────
    print("\n[2/4] Fetching today's schedule and recent stats...")
    teams_playing, confirmed_starters = get_todays_schedule()
    mlb_team_map   = get_mlb_team_map()
    recent_hitting = get_recent_hitting_stats(days=14)
    recent_pitching = get_recent_pitching_stats(days=14)
    print(f"  ✓ {len(teams_playing)} teams playing today  ·  "
          f"{len(confirmed_starters)} confirmed SP starters  ·  "
          f"{len(recent_hitting)} hitters  ·  {len(recent_pitching)} pitchers with stats")

    if not teams_playing:
        print("  ⚠️  No games found today — it may be an off-day or pre-season.")
        print("     Optimizer will still run but may bench most hitters.")

    # 3. Optimize ───────────────────────────────────────────────────────────────
    print("\n[3/4] Running optimizer on live roster...")
    roster = espn.get_my_roster()
    print(f"  ✓ Roster loaded: {len(roster)} players")

    lineup_result = optimize_lineup(
        roster=roster,
        teams_playing=teams_playing,
        mlb_team_map=mlb_team_map,
        recent_hitting=recent_hitting,
        recent_pitching=recent_pitching,
        confirmed_starters=confirmed_starters,
    )

    # 4. Compare & display ──────────────────────────────────────────────────────
    print("\n[4/4] Reading current ESPN lineup and computing moves...")
    try:
        team_id = get_team_id()
        roster_entries, scoring_period = fetch_league_data(team_id)
    except Exception as e:
        print(f"  ✗ Could not read current lineup from ESPN API: {e}")
        print("  Showing optimizer recommendation only (can't compute diff).")
        _display_recommended_lineup(lineup_result)
        sys.exit(1)

    print(f"{'═'*60}")
    _display_current_lineup(roster_entries, scoring_period)
    _display_recommended_lineup(lineup_result)

    moves = build_moves(lineup_result['starters'], roster_entries, team_id)
    _display_moves(moves, roster_entries)

    print(f"\n{'═'*60}")

    if not args.apply:
        print("  (Dry run — run with --apply to set this lineup on ESPN)")
        print(f"{'═'*60}\n")
        return

    # Apply ─────────────────────────────────────────────────────────────────────
    print("\n  Posting lineup to ESPN...")
    success = post_lineup(moves, team_id, scoring_period)
    if success:
        print("  ✓ Lineup set on ESPN!")
    else:
        print("  ✗ Failed to set lineup — see error above.")
        sys.exit(1)

    print(f"{'═'*60}\n")


if __name__ == '__main__':
    main()
