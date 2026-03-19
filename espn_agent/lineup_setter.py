#!/usr/bin/env python3
"""
ESPN Lineup Setter
==================
Reads the current roster slot assignments from the ESPN API and POST-s lineup
changes to put the optimizer's recommended starters into their active slots.

Uses the same base URL and S2/SWID auth that draft_tracker.py already uses.

Public API (called by run_fantasy_agent.py):
    set_lineup(optimizer_starters, dry_run=False) -> bool

Internal helpers (used by test_live_connection.py for display):
    get_team_id()
    fetch_league_data(team_id) -> (entries, scoring_period)
    build_moves(optimizer_starters, roster_entries, team_id) -> list
    SLOT_ID_TO_NAME
    BENCH_SLOT_ID
"""
import os
import sys
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from typing import Dict, List, Tuple, Optional


LEAGUE_ID    = int(os.getenv('ESPN_LEAGUE_ID', '1007942881'))
YEAR         = int(os.getenv('ESPN_YEAR', '2026'))
ESPN_S2      = os.getenv('ESPN_S2', '')
ESPN_SWID    = os.getenv('ESPN_SWID', '')
MY_TEAM_NAME = os.getenv('ESPN_TEAM_NAME', '')

ESPN_API_BASE_READ  = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb"
ESPN_API_BASE_WRITE = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/flb"

# ── Slot ID tables ─────────────────────────────────────────────────────────────
# ESPN Baseball (FLB) lineup slot IDs for Sandy Koufax's league:
#   C, 1B, 2B, 3B, SS, 3×OF, UTIL — hitting slots
#   7×P — pitching slots (no SP/RP distinction)
#   5 Bench, 3 IL
#
# NOTE: The P slot IDs (13–19) are the most likely candidates for a 7P league
# but ESPN doesn't publish these. After the first post-draft connection,
# run test_live_connection.py to see the actual slot IDs ESPN returns and
# update these values if needed.
SLOT_ID_TO_NAME = {
    0:  'C',
    1:  '1B',
    2:  '2B',
    3:  '3B',
    4:  'SS',
    5:  'OF',
    12: 'UTIL',
    13: 'P',
    16: 'BE',
    17: 'IL',
}

BENCH_SLOT_ID  = 16
BENCH_SLOT_IDS = {16}
IL_SLOT_IDS    = {17}

# Map optimizer slot name → ESPN target slot ID (confirmed from live API)
OPTIMIZER_TO_ESPN: Dict[str, int] = {
    'C':    0,
    '1B':   1,
    '2B':   2,
    '3B':   3,
    'SS':   4,
    'OF':   5,
    'OF2':  5,
    'OF3':  5,
    'UTIL': 12,
    'P':    13,
    'P2':   13,
    'P3':   13,
    'P4':   13,
    'P5':   13,
    'P6':   13,
    'P7':   13,
}


# ── Internal helpers ───────────────────────────────────────────────────────────

def get_team_id() -> int:
    """
    Find Sandy Koufax's ESPN team_id using the espn-api library.
    Falls back to first team if ESPN_TEAM_NAME is not set.
    """
    from espn_api.baseball import League
    league = League(league_id=LEAGUE_ID, year=YEAR, espn_s2=ESPN_S2, swid=ESPN_SWID)
    for team in league.teams:
        if MY_TEAM_NAME and MY_TEAM_NAME.lower() in team.team_name.lower():
            return int(team.team_id)
    return int(league.teams[0].team_id)


def fetch_league_data(team_id: int) -> Tuple[List[dict], int]:
    """
    GET roster from ESPN API with current slot assignments.

    Returns:
        (roster_entries, scoring_period_id)

        roster_entries: list of {player_id, name, current_slot_id}
        scoring_period_id: int (day of the season — used in the POST body)
    """
    url     = f"{ESPN_API_BASE_READ}/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
    params  = {'view': 'mRoster'}
    cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}
    headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}

    resp = requests.get(url, params=params, cookies=cookies, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Extract scoring period from top-level response
    scoring_period = data.get('scoringPeriodId', 1)

    # Find our team's roster entries
    entries = []
    for team_data in data.get('teams', []):
        if team_data.get('id') == team_id:
            for entry in team_data.get('roster', {}).get('entries', []):
                pool_entry = entry.get('playerPoolEntry', {})
                player     = pool_entry.get('player', {})
                entries.append({
                    'player_id':       entry.get('playerId', 0),
                    'name':            player.get('fullName', ''),
                    'current_slot_id': entry.get('lineupSlotId', BENCH_SLOT_ID),
                })
            break

    return entries, scoring_period


def build_moves(optimizer_starters: dict, roster_entries: list, team_id: int) -> list:
    """
    Diff current lineup vs. desired lineup and return the list of moves.

    Each move is a dict ESPN's transactions API expects:
        {playerId, type, fromLineupSlotId, toLineupSlotId, teamId}

    Rules:
    - Players the optimizer wants as starters → move to their target active slot
    - Everyone else → move to BENCH_SLOT_ID (if not already benched and not on IL)
    - Players on IL (slot 22/23) → skip entirely (can't be moved in lineup setting)
    """
    name_to_entry = {e['name'].lower(): e for e in roster_entries}
    players_being_started: set = set()
    moves: list = []

    for slot_name, value in optimizer_starters.items():
        if value is None:
            continue
        player_obj, _score = value
        target_slot = OPTIMIZER_TO_ESPN.get(slot_name)
        if target_slot is None:
            continue

        entry = name_to_entry.get(player_obj.name.lower())
        if not entry:
            continue  # player not found in live ESPN roster (name mismatch?)

        if entry['current_slot_id'] in IL_SLOT_IDS:
            continue  # can't activate from IL

        players_being_started.add(entry['player_id'])

        if entry['current_slot_id'] != target_slot:
            moves.append({
                'playerId':         entry['player_id'],
                'type':             'LINEUP',
                'fromLineupSlotId': entry['current_slot_id'],
                'toLineupSlotId':   target_slot,
                'teamId':           team_id,
            })

    # Bench everyone not in the starting lineup (unless already benched or on IL)
    for entry in roster_entries:
        if entry['player_id'] in players_being_started:
            continue
        if entry['current_slot_id'] in IL_SLOT_IDS:
            continue
        if entry['current_slot_id'] == BENCH_SLOT_ID:
            continue

        moves.append({
            'playerId':         entry['player_id'],
            'type':             'LINEUP',
            'fromLineupSlotId': entry['current_slot_id'],
            'toLineupSlotId':   BENCH_SLOT_ID,
            'teamId':           team_id,
        })

    return moves


def post_lineup(moves: list, team_id: int, scoring_period: int) -> bool:
    """POST lineup changes to ESPN's transactions endpoint."""
    if not moves:
        print("  No changes needed — lineup is already optimal.")
        return True

    url     = f"{ESPN_API_BASE_WRITE}/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}/transactions/"
    cookies = {'espn_s2': ESPN_S2, 'SWID': ESPN_SWID}
    headers = {
        'Accept':       'application/json',
        'Content-Type': 'application/json',
        'User-Agent':   'Mozilla/5.0',
    }
    body = {
        'teamId':          team_id,
        'type':            'ROSTER',
        'scoringPeriodId': scoring_period,
        'executionType':   'EXECUTE',
        'items':           moves,
    }

    try:
        resp = requests.post(url, json=body, cookies=cookies, headers=headers, timeout=15)
        if resp.status_code in (200, 201):
            return True
        print(f"  ESPN API returned {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as e:
        print(f"  Error posting lineup: {e}")
        return False


# ── Public entry point ─────────────────────────────────────────────────────────

def set_lineup(optimizer_starters: dict, dry_run: bool = False) -> bool:
    """
    Set the lineup on ESPN based on the optimizer's starters.

    Called by run_fantasy_agent.py:
        from espn_agent.lineup_setter import set_lineup
        set_lineup(lineup_result['starters'])

    Args:
        optimizer_starters: {slot_name: (player_obj, score)} from optimize_lineup()
        dry_run: If True, print moves without posting to ESPN.

    Returns:
        True on success (or dry run), False on API failure.
    """
    team_id = get_team_id()
    roster_entries, scoring_period = fetch_league_data(team_id)
    moves = build_moves(optimizer_starters, roster_entries, team_id)

    if dry_run:
        name_map = {e['player_id']: e['name'] for e in roster_entries}
        if moves:
            print(f"  Dry run — {len(moves)} moves:")
            for m in moves:
                name    = name_map.get(m['playerId'], f"player#{m['playerId']}")
                from_s  = SLOT_ID_TO_NAME.get(m['fromLineupSlotId'], str(m['fromLineupSlotId']))
                to_s    = SLOT_ID_TO_NAME.get(m['toLineupSlotId'],   str(m['toLineupSlotId']))
                print(f"    {name:<28}  {from_s} → {to_s}")
        else:
            print("  Dry run — lineup already optimal.")
        return True

    return post_lineup(moves, team_id, scoring_period)
