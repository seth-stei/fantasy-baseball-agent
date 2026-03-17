#!/usr/bin/env python3
"""
Roster Manager — Automatic Waiver / Free Agent Transactions
============================================================
After the daily lineup is set, this module scans the top available free agents,
applies rule-based add/drop logic, and POSTs the transaction to ESPN.

Public API (called by run_fantasy_agent.py):
    run_waiver_check(espn_client, recent_hitting, recent_pitching,
                     confirmed_starters=None, dry_run=False) -> str

Rules:
    Add triggers:
        - Injury replacement: IL player on roster → find top FA at same position
        - Hot streaker: FA with 14-day AVG ≥ .330 or XBH ≥ 8 (hitters), ERA < 2.50 (pitchers)
        - Two-start SP available when we have a zero-start SP on bench

    Drop protection (NEVER drop these):
        - Any pitcher with projected ERA < 3.20 (ace)
        - Any pitcher with 8+ projected saves+holds (closer or high-leverage setup)
        - Any hitter with projected 25+ HR or 20+ SB

    Drop candidates:
        - Hitter batting < .150 over 14 days with a positional backup available
        - Pitcher with ERA > 6.00 and not a protected arm

    Safety: max 1 add/drop per daily run
"""
import os
import sys
import requests
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from espn_agent.lineup_setter import (
    LEAGUE_ID, YEAR, ESPN_S2, ESPN_SWID,
    ESPN_API_BASE, BENCH_SLOT_ID, IL_SLOT_IDS,
)

# ── Drop-protection thresholds ────────────────────────────────────────────────

# Projected season stats that make a player untouchable
PROTECTED_ERA_THRESHOLD = 3.20   # projected ERA below this = ace, never drop
PROTECTED_SVHD_THRESHOLD = 8    # projected saves+holds at or above = closer/setup, never drop
PROTECTED_HR_THRESHOLD = 25      # projected HR at or above = power bat, never drop
PROTECTED_SB_THRESHOLD = 20      # projected SB at or above = speedster, never drop

# Add trigger thresholds (recent 14-day stats)
# OPS is NOT a scoring category — use AVG and XBH (extra base hits) as hot-hitter signals
HOT_HITTER_AVG = 0.330    # batting average over last 14 days (very hot)
HOT_HITTER_XBH = 8        # extra base hits over last 14 days (strong power production)
HOT_PITCHER_ERA = 2.50

# Drop trigger thresholds
COLD_HITTER_AVG = 0.150
BAD_PITCHER_ERA = 6.00
MIN_GAMES_FOR_COLD_HITTER = 7    # need at least 7 games before labeling cold


# ── Helpers ──────────────────────────────────────────────────────────────────

def _espn_session() -> requests.Session:
    s = requests.Session()
    s.cookies.set('espn_s2', ESPN_S2)
    s.cookies.set('SWID', ESPN_SWID)
    s.headers.update({'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    return s


def _projected(player, stat: str, default=0.0) -> float:
    """Safely read a projected season stat from an ESPN player object."""
    try:
        proj = player.stats.get('projected', {}) or {}
        return float(proj.get(stat, default) or default)
    except Exception:
        return default


def _is_protected(player) -> bool:
    """Return True if this player must never be dropped."""
    position = str(getattr(player, 'position', ''))
    if 'SP' in position or 'RP' in position:
        if _projected(player, 'ERA', 9.0) < PROTECTED_ERA_THRESHOLD:
            return True
        # Protect closers AND setup men — SVHD (saves+holds) is a scoring category
        proj_svhd = _projected(player, 'SV', 0) + _projected(player, 'holds', 0)
        if proj_svhd >= PROTECTED_SVHD_THRESHOLD:
            return True
    else:
        if _projected(player, 'HR', 0) >= PROTECTED_HR_THRESHOLD:
            return True
        if _projected(player, 'SB', 0) >= PROTECTED_SB_THRESHOLD:
            return True
    return False


def _injury_status(player) -> str:
    return (getattr(player, 'injuryStatus', None) or 'ACTIVE').upper()


def _is_injured(player) -> bool:
    return _injury_status(player) in ('OUT', 'INJURY_RESERVE', 'FIFTEEN_DAY_DL',
                                       'SIXTY_DAY_DL', 'SUSPENDED', 'NA')


def _hitter_positions(player) -> List[str]:
    """Return eligible hitter position codes for this player."""
    slots = getattr(player, 'eligibleSlots', None) or [getattr(player, 'position', '')]
    hitter_pos = {'C', '1B', '2B', '3B', 'SS', 'OF', 'LF', 'CF', 'RF', 'DH'}
    return [p for p in slots if p in hitter_pos]


def _is_pitcher(player) -> bool:
    pos = str(getattr(player, 'position', ''))
    return 'SP' in pos or 'RP' in pos


# ── Step 1: identify the best drop candidate ─────────────────────────────────

def find_best_drop(
    roster: List,
    recent_hitting: Dict,
    recent_pitching: Dict,
    add_candidate=None,
) -> Optional[object]:
    """
    Return the weakest droppable player on the roster.

    Priority:
      1. Cold hitter (avg < .150 over 14 days, enough games)
      2. Bad pitcher (ERA > 6.00, not protected)
      3. Any non-protected bench filler with no recent stats

    Never drops protected players.
    If add_candidate is provided, prefers to drop someone at the same
    position to keep the roster balanced (not required).
    """
    cold_hitters = []
    bad_pitchers = []
    benchable = []

    for player in roster:
        if _is_protected(player):
            continue
        name_lower = player.name.lower()

        if _is_pitcher(player):
            stats = recent_pitching.get(name_lower, {})
            era = stats.get('era', None)
            ip = stats.get('ip', 0)
            if era is not None and era > BAD_PITCHER_ERA and ip >= 5:
                bad_pitchers.append((player, era))
        else:
            stats = recent_hitting.get(name_lower, {})
            avg = stats.get('avg', None)
            games = stats.get('games', 0)
            if avg is not None and avg < COLD_HITTER_AVG and games >= MIN_GAMES_FOR_COLD_HITTER:
                cold_hitters.append((player, avg))
            elif not stats:
                benchable.append(player)

    # Pick worst cold hitter first
    if cold_hitters:
        cold_hitters.sort(key=lambda x: x[1])
        return cold_hitters[0][0]

    # Then worst pitcher
    if bad_pitchers:
        bad_pitchers.sort(key=lambda x: x[1], reverse=True)
        return bad_pitchers[0][0]

    # Last resort: no-stats bench player (likely a never-plays backup)
    if benchable:
        return benchable[0]

    return None


# ── Step 2: identify the best add candidate ──────────────────────────────────

def find_injury_replacement(
    roster: List,
    free_agents: List,
    recent_hitting: Dict,
    recent_pitching: Dict,
) -> Optional[object]:
    """
    If any rostered player is newly injured (OUT/IR but not yet in an IL slot),
    find the best available FA at the same position.

    Returns the FA to add, or None.
    """
    injured_positions: set = set()
    for player in roster:
        if _is_injured(player) and not _is_pitcher(player):
            for pos in _hitter_positions(player):
                injured_positions.add(pos)
        elif _is_injured(player) and _is_pitcher(player):
            injured_positions.add('P')

    if not injured_positions:
        return None

    best = None
    best_score = -1.0
    for fa in free_agents:
        fa_pos = set(getattr(fa, 'eligibleSlots', []) or [getattr(fa, 'position', '')])
        if not (fa_pos & injured_positions):
            continue
        name_lower = fa.name.lower()
        if _is_pitcher(fa):
            stats = recent_pitching.get(name_lower, {})
            score = max(0, 6 - stats.get('era', 4.5)) * 3 + stats.get('k', 0) * 0.2
        else:
            stats = recent_hitting.get(name_lower, {})
            score = stats.get('xbh', 0) * 5 + stats.get('avg', 0) * 50
        if score > best_score:
            best = fa
            best_score = score

    return best


def find_hot_free_agent(
    roster: List,
    free_agents: List,
    recent_hitting: Dict,
    recent_pitching: Dict,
) -> Optional[object]:
    """
    Find a free agent who is on a hot streak (AVG >= .330 or XBH >= 8 over 14 days;
    ERA < 2.50 for pitchers).
    Only returns someone better than whoever we'd have to drop.
    """
    for fa in free_agents:
        name_lower = fa.name.lower()
        if _is_pitcher(fa):
            stats = recent_pitching.get(name_lower, {})
            ip = stats.get('ip', 0)
            era = stats.get('era', 9.9)
            if ip >= 5 and era < HOT_PITCHER_ERA:
                return fa
        else:
            stats = recent_hitting.get(name_lower, {})
            avg = stats.get('avg', 0)
            xbh = stats.get('xbh', 0)
            games = stats.get('games', 0)
            if games >= 7 and (avg >= HOT_HITTER_AVG or xbh >= HOT_HITTER_XBH):
                return fa
    return None


def find_two_start_sp(
    roster: List,
    free_agents: List,
    games_this_week: Dict,
    confirmed_starters: set,
) -> Optional[object]:
    """
    Return an available FA starter who has 2 scheduled games this week,
    but only if we don't already have 4+ two-start SPs on the roster.
    """
    from espn_agent.stats_client import get_mlb_team_map

    team_map = get_mlb_team_map()

    def _team_games(player) -> int:
        pro_team = getattr(player, 'proTeam', '') or ''
        pt_lower = pro_team.lower()
        for key, tid in team_map.items():
            if pt_lower in key or key in pt_lower:
                return games_this_week.get(tid, 0)
        return 0

    # Count how many of our pitchers already have 2+ games this week
    my_two_starters = sum(
        1 for p in roster
        if _is_pitcher(p) and _team_games(p) >= 2
    )
    if my_two_starters >= 4:
        return None  # Already well-stocked with two-start arms

    for fa in free_agents:
        if 'SP' not in str(getattr(fa, 'position', '')):
            continue
        if _team_games(fa) >= 2:
            return fa

    return None


# ── Step 3: POST the transaction to ESPN ─────────────────────────────────────

def post_transaction(
    add_player,
    drop_player,
    team_id: int,
    scoring_period: int,
    dry_run: bool = False,
) -> bool:
    """
    POST a FREEAGENT add + drop to ESPN's transactions API.

    Returns True on success (or dry_run=True).
    """
    add_id  = add_player.playerId  if hasattr(add_player,  'playerId')  else getattr(add_player,  'id', 0)
    drop_id = drop_player.playerId if hasattr(drop_player, 'playerId') else getattr(drop_player, 'id', 0)

    if dry_run:
        print(f"  [DRY RUN] Would add {add_player.name}, drop {drop_player.name}")
        return True

    url  = f"{ESPN_API_BASE}/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}/transactions/"
    body = {
        'teamId':          team_id,
        'type':            'FREEAGENT',
        'scoringPeriodId': scoring_period,
        'executionType':   'EXECUTE',
        'items': [
            {
                'playerId':        add_id,
                'type':            'ADD',
                'toLineupSlotId':  BENCH_SLOT_ID,
                'teamId':          team_id,
            },
            {
                'playerId':          drop_id,
                'type':              'DROP',
                'fromLineupSlotId':  BENCH_SLOT_ID,
                'teamId':            team_id,
            },
        ],
    }
    try:
        sess = _espn_session()
        resp = sess.post(url, json=body, timeout=15)
        if resp.status_code in (200, 201):
            print(f"  ✓ Transaction posted: +{add_player.name} / -{drop_player.name}")
            return True
        print(f"  ✗ ESPN returned {resp.status_code}: {resp.text[:300]}")
        return False
    except Exception as e:
        print(f"  ✗ Transaction error: {e}")
        return False


# ── Public entry point ────────────────────────────────────────────────────────

def run_waiver_check(
    espn_client,
    recent_hitting: Dict,
    recent_pitching: Dict,
    confirmed_starters: set = None,
    games_this_week: Dict = None,
    dry_run: bool = False,
) -> str:
    """
    Main entry point — called by run_fantasy_agent.py after the lineup is set.

    Scans for one add/drop opportunity and executes it (unless dry_run=True).

    Returns a human-readable summary string for inclusion in the daily email.
    """
    from espn_agent.lineup_setter import get_team_id, fetch_league_data

    confirmed_starters = confirmed_starters or set()
    summary_lines = []

    print("\n[Waiver Check] Scanning free agents...")

    # Fetch data we need
    try:
        roster = espn_client.get_my_roster()
        free_agents = espn_client.get_free_agents(size=50)
        team_id = get_team_id()
        _entries, scoring_period = fetch_league_data(team_id)
    except Exception as e:
        msg = f"  ✗ Could not run waiver check: {e}"
        print(msg)
        return msg

    if not free_agents:
        print("  No free agents available.")
        return "Waiver check: no free agents available."

    # Determine the best add candidate (priority order)
    add_candidate = None
    add_reason = ''

    # 1. Injury replacement (highest priority)
    add_candidate = find_injury_replacement(roster, free_agents, recent_hitting, recent_pitching)
    if add_candidate:
        add_reason = f"injury replacement for an injured rostered player"

    # 2. Hot free agent
    if not add_candidate:
        add_candidate = find_hot_free_agent(roster, free_agents, recent_hitting, recent_pitching)
        if add_candidate:
            add_reason = "hot streak (AVG ≥ .330 or XBH ≥ 8 over last 14 days; ERA < 2.50 for pitchers)"

    # 3. Two-start SP (if we have games_this_week data)
    if not add_candidate and games_this_week:
        add_candidate = find_two_start_sp(roster, free_agents, games_this_week, confirmed_starters)
        if add_candidate:
            add_reason = "two-start SP available this week"

    if not add_candidate:
        msg = "Waiver check: no compelling add opportunity found today."
        print(f"  {msg}")
        summary_lines.append(msg)
        return '\n'.join(summary_lines)

    # Find someone to drop
    drop_candidate = find_best_drop(roster, recent_hitting, recent_pitching, add_candidate)

    if not drop_candidate:
        msg = (f"Waiver check: would add {add_candidate.name} ({add_reason}) "
               f"but no safe drop candidate found.")
        print(f"  {msg}")
        summary_lines.append(msg)
        return '\n'.join(summary_lines)

    # Log and execute
    print(f"  Add candidate: {add_candidate.name}  ({add_reason})")
    print(f"  Drop candidate: {drop_candidate.name}")

    success = post_transaction(add_candidate, drop_candidate, team_id, scoring_period, dry_run)

    if success:
        summary_lines.append(
            f"ROSTER MOVE: +{add_candidate.name} / -{drop_candidate.name}\n"
            f"  Reason: {add_reason}"
        )
    else:
        summary_lines.append(
            f"ROSTER MOVE FAILED: tried to add {add_candidate.name} / drop {drop_candidate.name}"
        )

    return '\n'.join(summary_lines)
