#!/usr/bin/env python3
"""
Claude AI reasoning layer for fantasy baseball decisions.
Turns raw data into clear, actionable recommendations.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

import anthropic
from typing import List, Dict, Optional

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
MODEL = 'claude-haiku-4-5-20251001'  # Fast and cheap for daily use


def _call_claude(prompt: str, max_tokens: int = 1024) -> str:
    """Make a Claude API call and return the response text."""
    if not ANTHROPIC_API_KEY:
        return "[Claude API key not configured - set ANTHROPIC_API_KEY in .env]"
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        return f"[Claude API error: {e}]"


LEAGUE_CONTEXT = """League format: H2H categories, 10 teams, 23 rounds.
Scoring categories:
  Batting:  R, HR, XBH (extra base hits), RBI, SB, AVG
  Pitching: K, QS (quality starts), W, ERA, WHIP, SVHD (saves + holds)
Roster: C, 1B, 2B, 3B, SS, 3×OF, UTIL, 7×P (any mix of SP/RP), 5 Bench, 3 IL.
Key implications: QS rewards workhorse SPs (6+ IP, ≤3 ER). SVHD makes setup men
with 30+ holds as valuable as closers. XBH rewards doubles/extra-base hitters."""


def analyze_lineup(lineup_text: str, team_name: str, matchup_info: str = '') -> str:
    """
    Ask Claude to interpret the lineup recommendation and explain decisions.
    """
    prompt = f"""You are an expert fantasy baseball analyst managing the team "{team_name}".

{LEAGUE_CONTEXT}

Here is today's lineup analysis:
{lineup_text}

{f'Matchup context: {matchup_info}' if matchup_info else ''}

Please provide:
1. A concise summary of today's recommended lineup (2-3 sentences)
2. Any key decisions or notable situations (injured players, QS opportunities, SVHD contributors, XBH threats)
3. Any risks or concerns

Keep your response brief and actionable. Use plain text, no markdown formatting."""

    return _call_claude(prompt, max_tokens=600)


def analyze_trades(
    my_team_name: str,
    my_roster_summary: str,
    category_standings: str,
    other_teams_summary: str,
) -> str:
    """
    Ask Claude to suggest trade opportunities based on category needs.
    """
    prompt = f"""You are an expert fantasy baseball analyst managing "{my_team_name}" in an H2H categories league.

{LEAGUE_CONTEXT}

MY ROSTER:
{my_roster_summary}

CATEGORY STANDINGS (where I rank in the league):
{category_standings}

OTHER TEAMS' KEY PLAYERS:
{other_teams_summary}

Based on my category weaknesses, suggest 2-3 specific trade proposals. For each trade:
- Who I should offer (from my surplus categories)
- Who I should target (addresses my weak categories)
- Why this makes sense for both teams
- Expected category impact (focus on XBH, QS, SVHD, SB where scarce)

Keep it concise and realistic. Only suggest trades that make sense for both sides.
Remember: QS requires 6+ IP / ≤3 ER, so workhorse SPs are more tradeable than finesse arms.
SVHD counts saves AND holds, so elite setup men (30+ holds) are as valuable as closers."""

    return _call_claude(prompt, max_tokens=800)


def analyze_waiver_wire(
    my_team_name: str,
    available_players: str,
    my_roster_summary: str,
    category_needs: str,
) -> str:
    """
    Ask Claude to suggest waiver wire pickups.
    """
    prompt = f"""You are an expert fantasy baseball analyst managing "{my_team_name}" in an H2H categories league.

{LEAGUE_CONTEXT}

TOP AVAILABLE FREE AGENTS:
{available_players}

MY CURRENT ROSTER:
{my_roster_summary}

MY CATEGORY NEEDS: {category_needs}

Suggest the top 2-3 waiver wire adds that would most help my team. For each:
- Player to add
- Who to drop (if roster is full)
- Why this improves my team
- Expected impact on weak categories (prioritize XBH, QS, SVHD, SB as most scarce)

Keep it concise and practical.
For pitchers: flag two-start SPs (2 starts this week = 2 QS chances) and high-hold RPs (SVHD).
For hitters: flag extra-base threats (doubles + power) for XBH category."""

    return _call_claude(prompt, max_tokens=600)


def build_roster_summary(roster) -> str:
    """Build a text summary of a team's roster for AI prompts."""
    lines = []
    hitters = []
    pitchers = []

    for player in roster:
        pos = getattr(player, 'position', 'UTIL')
        is_pitcher = 'SP' in str(pos) or 'RP' in str(pos)
        status = getattr(player, 'injuryStatus', 'ACTIVE') or 'ACTIVE'
        status_str = f" [{status}]" if status != 'ACTIVE' else ''

        if is_pitcher:
            pitchers.append(f"  {player.name} ({pos}){status_str}")
        else:
            hitters.append(f"  {player.name} ({pos}){status_str}")

    lines.append("Hitters:")
    lines.extend(hitters)
    lines.append("Pitchers:")
    lines.extend(pitchers)
    return '\n'.join(lines)


def build_category_standings_summary(teams, my_team) -> str:
    """
    Build a text description of where my team ranks in each category.
    Uses win/loss records as a proxy for category performance.
    """
    lines = []
    total_teams = len(teams)

    # Sort teams by wins to get ranking
    sorted_teams = sorted(teams, key=lambda t: t.wins, reverse=True)

    for i, team in enumerate(sorted_teams):
        marker = " ← MY TEAM" if team == my_team else ""
        lines.append(f"  {i+1}. {team.team_name}: {team.wins}W-{team.losses}L{marker}")

    return '\n'.join(lines)


def build_other_teams_summary(teams, my_team, top_n: int = 3) -> str:
    """Summarize other teams' rosters for trade analysis."""
    lines = []
    for team in teams:
        if team == my_team:
            continue
        lines.append(f"\n{team.team_name}:")
        for player in team.roster[:10]:  # Show first 10 players
            pos = getattr(player, 'position', '')
            lines.append(f"  {player.name} ({pos})")
    return '\n'.join(lines)


def build_il_alert(roster, roster_entries: list) -> str:
    """
    Scan the roster for players who need IL action and return a formatted alert string.

    Three situations flagged:
      1. Player is OUT/INJURY_RESERVE but NOT in an IL slot → should be moved to IL
      2. Player is currently in an IL slot but status returned to ACTIVE → ready to activate
      3. Player is DAY_TO_DAY with 0 recent games → consider IL move

    Args:
        roster: list of ESPN player objects (have .injuryStatus, .name, .position)
        roster_entries: raw slot dicts from fetch_league_data() (have 'current_slot_id', 'name')

    Returns:
        Alert string (empty string if nothing needs attention).
    """
    from espn_agent.lineup_setter import IL_SLOT_IDS

    # Build name → slot_id map from roster_entries
    name_to_slot: dict = {e['name'].lower(): e['current_slot_id'] for e in roster_entries}

    alerts = []

    for player in roster:
        status = (getattr(player, 'injuryStatus', None) or 'ACTIVE').upper()
        name_lower = player.name.lower()
        current_slot = name_to_slot.get(name_lower)

        if current_slot is None:
            continue  # player not in roster_entries (name mismatch)

        on_il = current_slot in IL_SLOT_IDS

        if status in ('OUT', 'INJURY_RESERVE', 'FIFTEEN_DAY_DL', 'SIXTY_DAY_DL') and not on_il:
            alerts.append(
                f"  MOVE TO IL: {player.name} [{status}] — "
                f"currently active, freeing a roster spot requires moving to IL"
            )

        elif on_il and status == 'ACTIVE':
            alerts.append(
                f"  ACTIVATE FROM IL: {player.name} — status is ACTIVE but still on IL"
            )

        elif status == 'DAY_TO_DAY' and not on_il:
            # Just note it — don't push the manager to act, but flag it
            alerts.append(
                f"  MONITOR: {player.name} [DAY_TO_DAY] — "
                f"check if eligible for IL to free a roster spot"
            )

    if not alerts:
        return ''

    return 'IL / INJURY ALERTS:\n' + '\n'.join(alerts)


def build_free_agents_summary(free_agents, limit: int = 20) -> str:
    """Build a text summary of available free agents."""
    lines = []
    for player in free_agents[:limit]:
        pos = getattr(player, 'position', '')
        ownership = getattr(player, 'percent_owned', 0) or 0
        lines.append(f"  {player.name} ({pos}) - {ownership:.0f}% owned")
    return '\n'.join(lines) if lines else "No free agents available"
