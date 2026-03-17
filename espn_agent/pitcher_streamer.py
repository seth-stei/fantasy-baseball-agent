#!/usr/bin/env python3
"""
Pitcher Streamer — Weekly Two-Start SP Scanner
===============================================
Every Monday morning, scan available SPs for pitchers with 2+ starts scheduled
this week. Grab them to fill the 7P roster with high-upside arms.

Public API (called by run_weekly.sh via run_weekly_agent.py):
    run_weekly_streaming(espn_client, dry_run=False) -> str

Logic:
    1. Count how many of our current 7P players have 2+ games this week.
    2. If < TARGET_TWO_START_SPS, scan wire for available two-start SPs.
    3. For each target, find the weakest bench pitcher to drop (NOT a protected arm).
    4. Execute up to MAX_STREAMING_ADDS adds per week.
"""
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from espn_agent.roster_manager import (
    _is_pitcher, _is_protected, post_transaction,
    PROTECTED_ERA_THRESHOLD, PROTECTED_SAVES_THRESHOLD,
)
from espn_agent.stats_client import get_games_this_week, get_mlb_team_map, get_recent_pitching_stats

# ── Configuration ─────────────────────────────────────────────────────────────

TARGET_TWO_START_SPS = 4    # Try to have at least this many two-start SPs active
MAX_STREAMING_ADDS = 2      # Max add/drops per weekly run


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_team_game_count(player, team_map: Dict, games_this_week: Dict) -> int:
    """Return the number of games player's MLB team plays this week."""
    pro_team = (getattr(player, 'proTeam', '') or '').lower()
    for key, tid in team_map.items():
        if pro_team in key or key in pro_team:
            return games_this_week.get(tid, 0)
    return 0


def _pitcher_quality_score(player, recent_pitching: Dict) -> float:
    """Lower score = weaker pitcher = safer to drop (for drop ranking)."""
    name_lower = player.name.lower()
    stats = recent_pitching.get(name_lower, {})
    if not stats or stats.get('ip', 0) < 5:
        # No recent stats — give a mediocre base score
        try:
            proj = player.stats.get('projected', {}) or {}
            era = float(proj.get('ERA', 4.5) or 4.5)
            k   = float(proj.get('K', 0) or 0)
            return max(0, (6 - era) * 3) + k * 0.1
        except Exception:
            return 2.0
    era  = stats.get('era', 4.5)
    whip = stats.get('whip', 1.35)
    k    = stats.get('k', 0)
    wins = stats.get('wins', 0)
    qs   = stats.get('qs', 0)
    svhd = stats.get('svhd', 0)
    return (max(0, (6 - era) * 3) + max(0, (1.5 - whip) * 5)
            + k * 0.2 + qs * 3 + wins * 1.5 + svhd * 2)


# ── Core logic ────────────────────────────────────────────────────────────────

def get_my_two_start_count(roster: List, team_map: Dict, games_this_week: Dict) -> int:
    """How many pitchers on my roster have 2+ team games this week?"""
    return sum(
        1 for p in roster
        if _is_pitcher(p) and _get_team_game_count(p, team_map, games_this_week) >= 2
    )


def find_available_two_start_sps(
    free_agents: List,
    team_map: Dict,
    games_this_week: Dict,
    recent_pitching: Dict,
) -> List:
    """
    Return list of available SPs who have 2+ team games this week,
    sorted by quality (best first).
    """
    candidates = []
    for fa in free_agents:
        if 'SP' not in str(getattr(fa, 'position', '')):
            continue
        game_count = _get_team_game_count(fa, team_map, games_this_week)
        if game_count >= 2:
            score = _pitcher_quality_score(fa, recent_pitching)
            candidates.append((fa, score, game_count))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [fa for fa, score, _ in candidates]


def find_droppable_pitcher(roster: List, recent_pitching: Dict) -> Optional[object]:
    """
    Find the weakest pitcher on our roster who is safe to drop.
    Excludes protected arms (aces, closers).
    Prefers pitchers with 0 or 1 team games this week.
    """
    droppable = []
    for player in roster:
        if not _is_pitcher(player):
            continue
        if _is_protected(player):
            continue
        score = _pitcher_quality_score(player, recent_pitching)
        droppable.append((player, score))

    if not droppable:
        return None

    # Drop the weakest (lowest score)
    droppable.sort(key=lambda x: x[1])
    return droppable[0][0]


# ── Public entry point ────────────────────────────────────────────────────────

def run_weekly_streaming(espn_client, dry_run: bool = False) -> str:
    """
    Main entry point — called on Monday mornings by run_weekly_agent.py.

    Scans for two-start SPs and executes up to MAX_STREAMING_ADDS adds.
    Returns a human-readable summary string.
    """
    from espn_agent.lineup_setter import get_team_id, fetch_league_data

    print("\n[Pitcher Streamer] Scanning for two-start SPs...")
    summary_lines = []

    try:
        roster = espn_client.get_my_roster()
        free_agents = espn_client.get_free_agents(size=50)
        team_map = get_mlb_team_map()
        games_this_week = get_games_this_week()
        recent_pitching = get_recent_pitching_stats(days=14)
        team_id = get_team_id()
        _entries, scoring_period = fetch_league_data(team_id)
    except Exception as e:
        msg = f"  ✗ Pitcher streamer setup failed: {e}"
        print(msg)
        return msg

    my_two_start_count = get_my_two_start_count(roster, team_map, games_this_week)
    print(f"  Current two-start SPs on roster: {my_two_start_count} "
          f"(target: {TARGET_TWO_START_SPS})")

    if my_two_start_count >= TARGET_TWO_START_SPS:
        msg = (f"Pitcher streaming: already have {my_two_start_count} two-start SPs "
               f"— no moves needed.")
        print(f"  {msg}")
        return msg

    needed = TARGET_TWO_START_SPS - my_two_start_count
    adds_to_make = min(needed, MAX_STREAMING_ADDS)

    available_two_starters = find_available_two_start_sps(
        free_agents, team_map, games_this_week, recent_pitching
    )

    if not available_two_starters:
        msg = "Pitcher streaming: no two-start SPs available on the wire."
        print(f"  {msg}")
        return msg

    adds_made = 0
    for add_candidate in available_two_starters:
        if adds_made >= adds_to_make:
            break

        drop_candidate = find_droppable_pitcher(roster, recent_pitching)
        if not drop_candidate:
            summary_lines.append("Could not find a safe pitcher to drop — stopping.")
            break

        game_count = _get_team_game_count(add_candidate, team_map, games_this_week)
        print(f"  Add: {add_candidate.name} ({game_count} games this week)  "
              f"Drop: {drop_candidate.name}")

        success = post_transaction(
            add_candidate, drop_candidate, team_id, scoring_period, dry_run
        )

        if success:
            summary_lines.append(
                f"STREAMING ADD: +{add_candidate.name} ({game_count} starts this week) "
                f"/ -{drop_candidate.name}"
            )
            adds_made += 1
            # Remove dropped pitcher from roster list so we don't try to drop them again
            roster = [p for p in roster if p.name != drop_candidate.name]
        else:
            summary_lines.append(
                f"STREAMING ADD FAILED: tried +{add_candidate.name} / -{drop_candidate.name}"
            )
            break

    if not summary_lines:
        summary_lines.append("Pitcher streaming: no moves executed.")

    return '\n'.join(summary_lines)
